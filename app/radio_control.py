"""
radio_control.py — Wrapper für uGreen radio_cli
Steuert das DAB Board über die radio_cli Kommandozeile.
"""

import subprocess
import json
import os
import glob
import time
import threading

RADIO_CLI = "/usr/local/sbin/radio_cli"
DATA_DIR = "/var/lib/dab-radio"
STATIONS_FILE = os.path.join(DATA_DIR, "stations.json")
FAVORITES_FILE = os.path.join(DATA_DIR, "favorites.json")


class RadioControl:
    def __init__(self):
        self.current_station = None
        self.current_ensemble = None
        self.volume = 40
        self.is_playing = False
        self.stations = []
        self.favorites = []
        self.audio_process = None
        self._lock = threading.Lock()

        os.makedirs(DATA_DIR, exist_ok=True)
        self._load_favorites()
        self._load_cached_stations()

    def _run_cli(self, args, timeout=60):
        """Führt radio_cli aus und gibt stdout zurück."""
        cmd = ["sudo", RADIO_CLI] + args
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout
            )
            return result.stdout, result.stderr, result.returncode
        except subprocess.TimeoutExpired:
            return "", "Timeout", -1
        except Exception as e:
            return "", str(e), -1

    def _run_cli_json(self, args, timeout=60):
        """Führt radio_cli mit -j (JSON output) aus."""
        stdout, stderr, rc = self._run_cli(args + ["-j"], timeout)
        if rc == 0 and stdout.strip():
            try:
                return json.loads(stdout)
            except json.JSONDecodeError:
                pass
        return None

    def boot_dab(self):
        """DAB-Firmware auf Si468x laden."""
        stdout, stderr, rc = self._run_cli(["-b", "D"])
        return rc == 0

    def scan_stations(self):
        """
        Vollständiger DAB-Frequenzscan.
        Gibt Liste der gefundenen Ensembles/Stationen zurück.
        """
        # Boot DAB + Scan
        stdout, stderr, rc = self._run_cli(["-b", "D", "-u", "-k"], timeout=120)

        if rc != 0:
            return {"error": f"Scan fehlgeschlagen: {stderr}"}

        # Suche nach der generierten Scan-Datei
        scan_files = glob.glob("/tmp/ensemblescan_*.json") + \
                     glob.glob(os.path.expanduser("~/ensemblescan_*.json")) + \
                     glob.glob("ensemblescan_*.json")

        stations = []

        if scan_files:
            # Neueste Datei nehmen
            scan_file = max(scan_files, key=os.path.getmtime)
            try:
                with open(scan_file, "r") as f:
                    scan_data = json.load(f)
                stations = self._parse_scan_data(scan_data)
            except (json.JSONDecodeError, IOError):
                pass

        if not stations:
            # Fallback: Versuche radio_cli JSON output
            data = self._run_cli_json(["-b", "D", "-u"], timeout=120)
            if data:
                stations = self._parse_scan_data(data)

        if not stations:
            # Weiterer Fallback: parse stdout
            stations = self._parse_scan_stdout(stdout)

        self.stations = stations
        self._save_stations()
        return {"stations": stations, "count": len(stations)}

    def _parse_scan_data(self, data):
        """Parst Scan-Daten (JSON) in eine einheitliche Stationsliste."""
        stations = []
        if isinstance(data, list):
            ensembles = data
        elif isinstance(data, dict) and "ensembles" in data:
            ensembles = data["ensembles"]
        else:
            return stations

        for ensemble in ensembles:
            ens_freq = ensemble.get("frequency", ensemble.get("freq", 0))
            ens_id = ensemble.get("id", ensemble.get("ensemble_id", 0))
            ens_label = ensemble.get("label", ensemble.get("name", f"Ensemble {ens_id}"))

            services = ensemble.get("services", ensemble.get("stations", []))
            for svc in services:
                stations.append({
                    "name": svc.get("label", svc.get("name", "Unbekannt")),
                    "service_id": svc.get("id", svc.get("service_id", 0)),
                    "component_id": svc.get("component_id", svc.get("comp_id", 0)),
                    "ensemble_id": ens_id,
                    "ensemble_label": ens_label,
                    "frequency": ens_freq,
                })

        return stations

    def _parse_scan_stdout(self, stdout):
        """Fallback: Parst radio_cli Text-Output."""
        stations = []
        # Einfaches Parsing der Textausgabe
        for line in stdout.split("\n"):
            line = line.strip()
            if "Service:" in line or "Station:" in line:
                parts = line.split(",")
                name = parts[0].split(":")[-1].strip() if parts else "Unbekannt"
                stations.append({
                    "name": name,
                    "service_id": 0,
                    "component_id": 0,
                    "ensemble_id": 0,
                    "ensemble_label": "",
                    "frequency": 0,
                })
        return stations

    def tune_station(self, station):
        """
        Tune auf einen Sender.
        station: dict mit frequency/ensemble_id/component_id/service_id
        """
        with self._lock:
            self.stop_audio()

            freq = station.get("frequency", 0)
            ens_id = station.get("ensemble_id", 0)
            comp_id = station.get("component_id", 0)

            # radio_cli: -b D (boot DAB) -o 1 (I2S output) -f <freq_index> -e <ensemble_id> -c <component_id> -p (play)
            args = ["-b", "D", "-o", "1"]

            if freq:
                args += ["-f", str(freq)]
            if ens_id:
                args += ["-e", str(ens_id)]
            if comp_id:
                args += ["-c", str(comp_id)]

            args.append("-p")

            if self.volume is not None:
                args += ["-l", str(self.volume)]

            stdout, stderr, rc = self._run_cli(args)

            if rc == 0:
                self.current_station = station
                self.is_playing = True
                return True

            return False

    def start_bluetooth_audio(self, bt_mac):
        """Starte Audio-Streaming vom I2S zum Bluetooth-Gerät."""
        with self._lock:
            self.stop_audio()

            # arecord von I2S (dabboard) | aplay via bluealsa
            cmd = (
                f"arecord -D sysdefault:CARD=dabboard -c 2 -r 48000 -f S16_LE -t raw -q "
                f"| aplay -D bluealsa:DEV={bt_mac},PROFILE=a2dp -c 2 -r 48000 -f S16_LE -t raw -q"
            )

            self.audio_process = subprocess.Popen(
                cmd, shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            return True

    def stop_audio(self):
        """Stoppt den Audio-Stream."""
        if self.audio_process:
            try:
                self.audio_process.terminate()
                self.audio_process.wait(timeout=3)
            except Exception:
                try:
                    self.audio_process.kill()
                except Exception:
                    pass
            self.audio_process = None

        # Auch evtl. laufende arecord/aplay Prozesse beenden
        subprocess.run(["pkill", "-f", "arecord.*dabboard"], capture_output=True)
        subprocess.run(["pkill", "-f", "aplay.*bluealsa"], capture_output=True)

    def set_volume(self, level):
        """Lautstärke setzen (0-63)."""
        level = max(0, min(63, int(level)))
        self.volume = level
        # radio_cli volume
        self._run_cli(["-l", str(level)])
        return level

    def stop(self):
        """Radio komplett stoppen."""
        self.stop_audio()
        self._run_cli(["-x"])  # Si468x stoppen
        self.is_playing = False
        self.current_station = None

    def get_status(self):
        """Aktuellen Status zurückgeben."""
        return {
            "is_playing": self.is_playing,
            "current_station": self.current_station,
            "volume": self.volume,
            "station_count": len(self.stations),
        }

    # --- Favoriten ---

    def add_favorite(self, station):
        """Sender als Favorit speichern."""
        # Prüfe ob bereits vorhanden
        for fav in self.favorites:
            if fav.get("service_id") == station.get("service_id") and \
               fav.get("ensemble_id") == station.get("ensemble_id"):
                return False
        self.favorites.append(station)
        self._save_favorites()
        return True

    def remove_favorite(self, index):
        """Favorit nach Index entfernen."""
        if 0 <= index < len(self.favorites):
            self.favorites.pop(index)
            self._save_favorites()
            return True
        return False

    def get_favorites(self):
        return self.favorites

    def _save_favorites(self):
        try:
            with open(FAVORITES_FILE, "w") as f:
                json.dump(self.favorites, f, indent=2)
        except IOError:
            pass

    def _load_favorites(self):
        try:
            with open(FAVORITES_FILE, "r") as f:
                self.favorites = json.load(f)
        except (IOError, json.JSONDecodeError):
            self.favorites = []

    def _save_stations(self):
        try:
            with open(STATIONS_FILE, "w") as f:
                json.dump(self.stations, f, indent=2)
        except IOError:
            pass

    def _load_cached_stations(self):
        try:
            with open(STATIONS_FILE, "r") as f:
                self.stations = json.load(f)
        except (IOError, json.JSONDecodeError):
            self.stations = []
