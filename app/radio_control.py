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
QUALITY_CACHE_FILE = os.path.join(DATA_DIR, "dab_quality_cache.json")


class RadioControl:
    def __init__(self):
        self.current_station = None
        self.current_ensemble = None
        self.volume = 40
        self.is_playing = False
        self.stations = []
        self.favorites = []
        self.audio_process = None
        self.playback_mode = "dab"  # "dab" or "music"
        self.current_track = None  # Path to current music file (if playing music)
        self.music_info = None  # Track metadata for music mode
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
        Zwei-Phasen DAB-Frequenzscan für zuverlässigere Ergebnisse.

        Phase 1: Vollscan (-u) identifiziert Frequenzen mit Signal
        Phase 2: Für jede erkannte Frequenz einzeln tunen und Service-Liste holen
                 (löst das Problem bei schwachem Signal, wo -u zu kurz verweilt)
        """
        # Phase 1: Vollscan — identifiziert Frequenzen mit Signal
        print("[SCAN] Phase 1: Starte Vollscan...")
        stdout, stderr, rc = self._run_cli(["-b", "D", "-u", "-j"], timeout=120)

        if rc != 0:
            print(f"[SCAN] Fehler: rc={rc}, stderr={stderr}")
            return {"error": f"Scan fehlgeschlagen: {stderr}"}

        stations = []
        scan_data = None

        # Versuche zuerst den normalen Parse (funktioniert bei starkem Signal)
        if stdout.strip():
            try:
                scan_data = json.loads(stdout)
                stations = self._parse_scan_data(scan_data)
                print(f"[SCAN] Phase 1 Ergebnis: {len(stations)} Sender gefunden")
            except json.JSONDecodeError as e:
                print(f"[SCAN] JSON Parse-Fehler: {e}")

        # Phase 2: Falls keine Sender gefunden, Frequenzen mit Signal einzeln abtasten
        if not stations and scan_data is not None:
            print("[SCAN] Phase 2: Einzelne Frequenzen mit Signal abtasten...")
            try:
                stations = self._deep_scan_frequencies(scan_data)
                print(f"[SCAN] Phase 2 Ergebnis: {len(stations)} Sender gefunden")
            except Exception as e:
                print(f"[SCAN] Phase 2 Fehler: {e}")

        if not stations:
            stations = self._parse_scan_stdout(stdout)

        self.stations = stations
        self._save_stations()
        print(f"[SCAN] Fertig: {len(stations)} Sender gespeichert")
        return {"stations": stations, "count": len(stations)}

    def _deep_scan_frequencies(self, scan_data):
        """
        Phase 2: Einzeln auf jede Frequenz tunen, die im Vollscan Signal zeigte.
        Wartet länger auf FIC-Dekodierung und holt Service-Liste per -g.
        """
        # Finde Frequenzen mit Signal aus dem Vollscan
        ensemble_list = None
        if isinstance(scan_data, dict):
            ensemble_list = scan_data.get("ensembleList", scan_data.get("ensembles"))
        elif isinstance(scan_data, list):
            ensemble_list = scan_data

        if not ensemble_list:
            return []

        # Frequenzen mit Signal sammeln
        # RSSI allein reicht nicht (Rauschen ist 13-25), nur echte Indikatoren nutzen:
        # - acq > 0: Ensemble wurde akquiriert
        # - fast_dect > 0: Signal erkannt aber FIC noch nicht dekodiert
        # - FIC_quality > 0: FIC wird dekodiert
        signal_freqs = []
        for ens in ensemble_list:
            digrad = ens.get("DigradStatus", {})
            freq_index = digrad.get("tune_index", ens.get("EnsembleNo", 0))
            acq = digrad.get("acq", 0)
            fast_dect = digrad.get("fast_dect", 0)
            fic_quality = digrad.get("FIC_quality", 0)
            rssi = digrad.get("RSSI", 0)

            if acq > 0 or fast_dect > 0 or fic_quality > 0:
                print(f"[SCAN] Freq {freq_index}: Signal erkannt (acq={acq}, fast_dect={fast_dect}, FIC={fic_quality}, RSSI={rssi})")
                signal_freqs.append(freq_index)

        if not signal_freqs:
            return []

        # Für jede Frequenz mit Signal: einzeln tunen und Service-Liste holen
        all_stations = []
        for freq in signal_freqs:
            services = self._get_services_for_frequency(freq)
            all_stations.extend(services)

        return all_stations

    def _get_services_for_frequency(self, freq_index):
        """
        Tune auf eine Frequenz und hole die Service-Liste.
        Wartet auf FIC-Dekodierung bei schwachem Signal.
        """
        print(f"[SCAN] Phase 2: Tune auf Frequenz {freq_index}...")

        # Boot und auf Frequenz tunen
        stdout, stderr, rc = self._run_cli(
            ["-b", "D", "-f", str(freq_index)], timeout=15
        )
        if rc != 0:
            print(f"[SCAN] Freq {freq_index}: Tune fehlgeschlagen (rc={rc})")
            return []

        # Warte auf Ensemble-Akquisition (FIC braucht Zeit bei schwachem Signal)
        time.sleep(4)

        # Service-Liste holen (bis zu 3 Versuche)
        for attempt in range(3):
            stdout, stderr, rc = self._run_cli(["-g", "-j"], timeout=10)
            if rc == 0 and stdout.strip():
                try:
                    svc_data = json.loads(stdout)
                    services = self._parse_service_list(svc_data, freq_index)
                    if services:
                        print(f"[SCAN] Freq {freq_index}: {len(services)} Sender gefunden (Versuch {attempt+1})")
                        return services
                except json.JSONDecodeError:
                    pass
            # Mehr Zeit geben
            if attempt < 2:
                print(f"[SCAN] Freq {freq_index}: Versuch {attempt+1} keine Services, warte...")
                time.sleep(3)

        print(f"[SCAN] Freq {freq_index}: Keine Sender gefunden nach 3 Versuchen")
        return []

    def _parse_service_list(self, data, freq_index):
        """
        Parst die Service-Liste von radio_cli -g -j für eine Frequenz.
        """
        stations = []

        # -g -j kann verschiedene Formate haben
        service_list = []
        if isinstance(data, dict):
            # Format: {"ServiceList": [...]} oder {"DigitalServiceList": {"ServiceList": [...]}}
            if "ServiceList" in data:
                service_list = data["ServiceList"]
            elif "DigitalServiceList" in data:
                dsl = data["DigitalServiceList"]
                service_list = dsl.get("ServiceList", [])
            elif "ensembleList" in data:
                # Volles Ensemble-Format — nutze bestehenden Parser
                return self._parse_scan_data(data)
        elif isinstance(data, list):
            service_list = data

        for svc in service_list:
            name = svc.get("Label", svc.get("label", svc.get("name", "Unbekannt")))
            service_id = svc.get("ServId", svc.get("id", svc.get("service_id", 0)))

            comp_id = 0
            comp_list = svc.get("ComponentList", [])
            if comp_list:
                comp_id = comp_list[0].get("comp_ID", comp_list[0].get("component_id", 0))
            else:
                comp_id = svc.get("component_id", svc.get("comp_id", 0))

            if service_id:  # Nur Sender mit gültiger Service-ID
                stations.append({
                    "name": name.strip() if isinstance(name, str) else str(name),
                    "service_id": service_id,
                    "component_id": comp_id,
                    "ensemble_id": 0,
                    "ensemble_label": "",
                    "frequency": freq_index,
                })

        return stations

    def _parse_scan_data(self, data):
        """
        Parst radio_cli Scan-Daten (JSON) in eine einheitliche Stationsliste.

        radio_cli -j Format:
        {
          "ensembleList": [{
            "EnsembleNo": 23,
            "Label": "antenne de",
            "DigradStatus": {"tune_freq": 213360, "tune_index": 23, ...},
            "DigitalServiceList": {
              "ServiceList": [{
                "ServId": 4231, "Label": "ENERGY NUERNBERG",
                "ComponentList": [{"comp_ID": 12, ...}]
              }, ...]
            }
          }, ...]
        }
        """
        stations = []

        # radio_cli Format: {"ensembleList": [...]}
        ensemble_list = None
        if isinstance(data, dict):
            if "ensembleList" in data:
                ensemble_list = data["ensembleList"]
            elif "ensembles" in data:
                ensemble_list = data["ensembles"]
        elif isinstance(data, list):
            ensemble_list = data

        if not ensemble_list:
            return stations

        for ensemble in ensemble_list:
            # Ensemble-Nummer (wird für -e beim Tunen gebraucht)
            ens_no = ensemble.get("EnsembleNo", ensemble.get("id", 0))
            ens_label = ensemble.get("Label", ensemble.get("label", f"Ensemble {ens_no}"))

            # Frequenz aus DigradStatus
            digrad = ensemble.get("DigradStatus", {})
            freq_index = digrad.get("tune_index", ensemble.get("frequency", 0))

            # Services aus DigitalServiceList
            dsl = ensemble.get("DigitalServiceList", {})
            service_list = dsl.get("ServiceList", [])

            # Fallback für andere Formate
            if not service_list:
                service_list = ensemble.get("services", ensemble.get("stations", []))

            for svc in service_list:
                # Datendienste (EPG, TPEG etc.) überspringen
                if svc.get("AudioOrDataFlag", 0) == 1:
                    continue

                name = svc.get("Label", svc.get("label", svc.get("name", "Unbekannt")))
                service_id = svc.get("ServId", svc.get("id", svc.get("service_id", 0)))

                # Component ID aus ComponentList
                comp_id = 0
                comp_list = svc.get("ComponentList", [])
                if comp_list:
                    comp_id = comp_list[0].get("comp_ID", comp_list[0].get("component_id", 0))
                else:
                    comp_id = svc.get("component_id", svc.get("comp_id", 0))

                stations.append({
                    "name": name.strip() if isinstance(name, str) else str(name),
                    "service_id": service_id,
                    "component_id": comp_id,
                    "ensemble_id": ens_no,
                    "ensemble_label": ens_label.strip() if isinstance(ens_label, str) else str(ens_label),
                    "frequency": freq_index,
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
        station: dict mit frequency/service_id/component_id
        """
        with self._lock:
            self.stop_audio()

            freq = station.get("frequency", 0)
            service_id = station.get("service_id", 0)
            comp_id = station.get("component_id", 0)

            # radio_cli: -b D (boot DAB) -o 1 (I2S output)
            # -f <freq_index> -e <service_id> -c <component_id> -p (play)
            args = ["-b", "D", "-o", "1"]

            if freq:
                args += ["-f", str(freq)]
            if service_id:
                args += ["-e", str(service_id)]
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
        """Starte Audio-Streaming vom I2S zum Bluetooth-Gerät via bluez-alsa."""
        with self._lock:
            self.stop_audio()

            # arecord von I2S (dabboard) | aplay zu BlueALSA Bluetooth device
            cmd = (
                f"arecord -D sysdefault:CARD=dabboard -c 2 -r 48000 -f S16_LE -t raw -q "
                f"| aplay -D bluealsa:DEV={bt_mac},PROFILE=a2dp -c 2 -r 48000 -f S16_LE -t raw"
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

        # Auch evtl. laufende Audio-Prozesse beenden
        subprocess.run(["pkill", "-f", "arecord.*dabboard"], capture_output=True)
        subprocess.run(["pkill", "-f", "aplay.*bluealsa"], capture_output=True)
        subprocess.run(["pkill", "-f", "mpg123"], capture_output=True)

    def set_volume(self, level):
        """Lautstärke setzen (0-63). Steuert DAB-Board UND Bluetooth."""
        level = max(0, min(63, int(level)))
        self.volume = level
        # radio_cli volume (DAB Board)
        self._run_cli(["-l", str(level)])
        # Bluetooth volume via bluez-alsa (0-127)
        self._set_bt_volume(level)
        return level

    def _set_bt_volume(self, level):
        """Bluetooth-Lautstärke über bluez-alsa setzen. level: 0-63 → 0-127."""
        bt_vol = int(level * 127 / 63)
        try:
            # Finde aktive PCMs
            r = subprocess.run(
                ["bluealsa-cli", "list-pcms"],
                capture_output=True, text=True, timeout=5
            )
            for pcm in r.stdout.strip().split("\n"):
                pcm = pcm.strip()
                if pcm:
                    subprocess.run(
                        ["bluealsa-cli", "volume", pcm, str(bt_vol), str(bt_vol)],
                        capture_output=True, timeout=5
                    )
        except Exception:
            pass

    def stop(self):
        """Radio komplett stoppen."""
        self.stop_audio()
        self._run_cli(["-x"])  # Si468x stoppen
        self.is_playing = False
        self.current_station = None
        self.music_info = None
        self.current_track = None
        self.playback_mode = "dab"

    def get_status(self):
        """Aktuellen Status zurückgeben."""
        # Check if music process is still running
        if self.playback_mode == "music" and self.audio_process:
            if self.audio_process.poll() is not None:
                # mpg123 has finished
                self.is_playing = False
                self.audio_process = None

        music = None
        if self.music_info and self.playback_mode == "music":
            elapsed = time.time() - self.music_info["start_time"]
            duration = self.music_info["duration"]
            progress = min(elapsed / duration, 1.0) if duration > 0 else 0
            music = {
                "title": self.music_info["title"],
                "album_name": self.music_info["album_name"],
                "album_id": self.music_info["album_id"],
                "track_index": self.music_info["track_index"],
                "total_tracks": self.music_info["total_tracks"],
                "duration": round(duration),
                "elapsed": round(elapsed),
                "progress": round(progress, 3),
            }

        return {
            "is_playing": self.is_playing,
            "current_station": self.current_station,
            "volume": self.volume,
            "station_count": len(self.stations),
            "playback_mode": self.playback_mode,
            "current_track": self.current_track,
            "music": music,
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

    # --- Music Playback ---

    def start_music_playback(self, file_path, bt_mac, track_title=None,
                             album_name=None, album_id=None,
                             track_index=0, total_tracks=0):
        """
        Play music file through bluez-alsa to Bluetooth.

        Args:
            file_path: Absolute path to audio file
            bt_mac: Bluetooth device MAC address
            track_title: Display name of the track
            album_name: Album name
            album_id: Album ID
            track_index: Current track number (0-based)
            total_tracks: Total number of tracks in album

        Returns:
            bool: True if playback started successfully
        """
        with self._lock:
            self.stop_audio()

            if not os.path.exists(file_path):
                return False

            # Get track duration
            duration = self._get_audio_duration(file_path)

            # Use mpg123 for audio files (supports MP3, FLAC, etc.)
            # -o alsa: Use ALSA output (bluez-alsa)
            # -a: Specify ALSA device (BlueALSA PCM)
            # -q: Quiet mode
            cmd = f'mpg123 -o alsa -a "bluealsa:DEV={bt_mac},PROFILE=a2dp" -q "{file_path}"'

            self.audio_process = subprocess.Popen(
                cmd, shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

            self.is_playing = True
            self.playback_mode = "music"
            self.current_track = file_path

            # Store track metadata
            if not track_title:
                track_title = os.path.splitext(os.path.basename(file_path))[0]
            self.music_info = {
                "title": track_title,
                "album_name": album_name or "",
                "album_id": album_id or "",
                "track_index": track_index,
                "total_tracks": total_tracks,
                "duration": duration,
                "start_time": time.time(),
            }

            return True

    def _get_audio_duration(self, file_path):
        """Get audio file duration in seconds using mutagen."""
        try:
            from mutagen import File as MutagenFile
            audio = MutagenFile(file_path)
            if audio and audio.info:
                return audio.info.length
        except Exception:
            pass
        return 0

    # --- Quality Metrics ---

    def extract_quality_metrics(self, scan_data=None):
        """
        Extract signal quality metrics from scan data.

        Args:
            scan_data: Parsed JSON scan data (if None, tries to load from cached stations)
        """
        if not scan_data:
            return

        quality_data = {}

        # Parse using same logic as _parse_scan_data
        ensemble_list = None
        if isinstance(scan_data, dict):
            if "ensembleList" in scan_data:
                ensemble_list = scan_data["ensembleList"]
            elif "ensembles" in scan_data:
                ensemble_list = scan_data["ensembles"]
        elif isinstance(scan_data, list):
            ensemble_list = scan_data

        if not ensemble_list:
            return

        for ensemble in ensemble_list:
            ens_no = ensemble.get("EnsembleNo", ensemble.get("id", 0))
            digrad = ensemble.get("DigradStatus", {})

            # Get services
            dsl = ensemble.get("DigitalServiceList", {})
            service_list = dsl.get("ServiceList", [])
            if not service_list:
                service_list = ensemble.get("services", [])

            for service in service_list:
                svc_id = service.get("ServId", service.get("id", 0))
                key = f"{svc_id}_{ens_no}"

                quality_data[key] = {
                    "service_id": svc_id,
                    "ensemble_id": ens_no,
                    "signal_quality": digrad.get("RSSI", service.get("quality", 0)),
                    "rssi": digrad.get("RSSI", -99),
                    "cnr": digrad.get("CNR", 0),
                    "ber": digrad.get("FIB_error_count", 0),
                    "last_updated": int(time.time())
                }

        self._save_quality_cache(quality_data)

    def get_stations_with_quality(self):
        """
        Get stations list merged with quality metrics.

        Returns:
            list: Stations with quality data added
        """
        quality_cache = self._load_quality_cache()

        stations_with_quality = []
        for station in self.stations:
            key = f"{station['service_id']}_{station['ensemble_id']}"
            quality = quality_cache.get(key, {})

            station_copy = station.copy()
            station_copy["quality"] = quality.get("signal_quality", 0)
            station_copy["rssi"] = quality.get("rssi", -99)
            station_copy["cnr"] = quality.get("cnr", 0)
            station_copy["ber"] = quality.get("ber", 0)

            stations_with_quality.append(station_copy)

        return stations_with_quality

    def _save_quality_cache(self, quality_data):
        """Save quality cache to JSON file."""
        try:
            cache = {
                "last_updated": int(time.time()),
                "stations": quality_data
            }
            with open(QUALITY_CACHE_FILE, "w") as f:
                json.dump(cache, f, indent=2)
        except IOError:
            pass

    def _load_quality_cache(self):
        """Load quality cache from JSON file."""
        try:
            with open(QUALITY_CACHE_FILE, "r") as f:
                cache = json.load(f)
                return cache.get("stations", {})
        except (IOError, json.JSONDecodeError):
            return {}

    # --- DAB Board Detection ---

    def check_board_detected(self):
        """
        Check if DAB board is detected and responsive.

        Returns:
            dict: Detection status with keys:
                - detected: bool
                - status: str ("OK" or "ERROR")
                - message: str (descriptive message)
        """
        try:
            # Try to boot DAB firmware
            stdout, stderr, rc = self._run_cli(["-b", "D"], timeout=10)

            if rc == 0:
                return {
                    "detected": True,
                    "status": "OK",
                    "message": "DAB Board erfolgreich erkannt und funktionsfähig"
                }
            else:
                return {
                    "detected": False,
                    "status": "ERROR",
                    "message": f"Board nicht erkannt: {stderr if stderr else 'Unbekannter Fehler'}"
                }

        except Exception as e:
            return {
                "detected": False,
                "status": "ERROR",
                "message": f"Prüfung fehlgeschlagen: {str(e)}"
            }
