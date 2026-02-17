"""
bt_manager.py — Bluetooth-Gerätemanager
Verwaltet Bluetooth-Verbindungen für DAB+ Radio auf Raspberry Pi.

Kernprinzip: Immer sauber trennen vor Neuverbindung.
bluetoothctl + PulseAudio module-bluetooth-discover.
"""

import subprocess
import re
import time
import threading
import json
import os
import select

DATA_DIR = "/var/lib/dab-radio"
BT_CONFIG_FILE = os.path.join(DATA_DIR, "bluetooth.json")


def _log(msg):
    """Logging to stdout → systemd journal."""
    print(f"[BT] {msg}", flush=True)


def _clean_ansi(text):
    """Remove ANSI escape codes from bluetoothctl output."""
    return re.sub(r'\x1b\[[0-9;]*m', '', text)


def _run_cmd(cmd, timeout=10):
    """Shell-Befehl ausführen, Output zurückgeben."""
    try:
        result = subprocess.run(
            cmd, shell=True,
            capture_output=True, text=True, timeout=timeout
        )
        return result.stdout.strip()
    except Exception:
        return ""


class BluetoothManager:
    def __init__(self):
        self.connected_device = None
        self.connected_device_name = None
        self.scanning = False
        self._discovered_devices = {}
        self._lock = threading.Lock()
        self._last_check_time = 0
        self._load_config()

    # ─── Low-level bluetoothctl ─────────────────────────

    def _btctl(self, command, timeout=10):
        """Schnelle bluetoothctl-Abfrage im Pipe-Modus."""
        try:
            result = subprocess.run(
                ["bluetoothctl"],
                input=f"{command}\nexit\n",
                capture_output=True, text=True,
                timeout=timeout
            )
            return _clean_ansi(result.stdout)
        except subprocess.TimeoutExpired:
            _log(f"Timeout: {command}")
            return ""
        except Exception as e:
            _log(f"Fehler ({command}): {e}")
            return ""

    def _is_connected(self, mac):
        """Echtzeit-Prüfung ob Gerät verbunden ist."""
        output = self._btctl(f"info {mac}")
        return "Connected: yes" in output

    def _get_name(self, mac):
        """Gerätename aus bluetoothctl info auslesen."""
        output = self._btctl(f"info {mac}")
        for pattern in [r"Alias:\s+(.+)", r"Name:\s+(.+)"]:
            match = re.search(pattern, output)
            if match:
                return match.group(1).strip()
        return "Unbekannt"

    def _has_audio_sink(self, mac):
        """Prüft ob PulseAudio einen Bluetooth-Sink für das Gerät hat."""
        mac_underscored = mac.replace(":", "_")
        output = _run_cmd("pactl list sinks short 2>/dev/null")
        return mac_underscored in output

    # ─── Power ──────────────────────────────────────────

    def power_on(self):
        """Bluetooth adapter einschalten."""
        try:
            subprocess.run(
                ["rfkill", "unblock", "bluetooth"],
                capture_output=True, timeout=5
            )
        except Exception:
            pass

        self._btctl("power on\nagent NoInputNoOutput\ndefault-agent")

    # ─── Device Listing ─────────────────────────────────

    def get_devices(self):
        """Alle bekannten + gescannten Bluetooth-Geräte."""
        devices_output = self._btctl("devices")
        paired_output = self._btctl("paired-devices")

        all_devices = {}
        paired_macs = set()

        for line in devices_output.split("\n"):
            match = re.search(r"Device\s+([0-9A-F:]{17})\s+(.+)", line)
            if match:
                mac, name = match.group(1), match.group(2).strip()
                if name and name != mac:
                    all_devices[mac] = name

        for line in paired_output.split("\n"):
            match = re.search(r"Device\s+([0-9A-F:]{17})\s+(.+)", line)
            if match:
                mac, name = match.group(1), match.group(2).strip()
                paired_macs.add(mac)
                if mac not in all_devices and name and name != mac:
                    all_devices[mac] = name

        for mac, name in self._discovered_devices.items():
            if mac not in all_devices:
                all_devices[mac] = name

        connected_mac = self._get_connected_live()

        devices = []
        for mac, name in all_devices.items():
            devices.append({
                "mac": mac,
                "name": name,
                "paired": mac in paired_macs,
                "connected": mac == connected_mac,
            })

        devices.sort(key=lambda d: (
            0 if d["connected"] else (1 if d["paired"] else 2),
            d["name"]
        ))

        return devices

    def _get_connected_live(self):
        """
        Echte Verbindungsprüfung mit 10s Cache.
        Kurzer Cache damit die UI schnell aktualisiert,
        aber nicht bei jedem Poll bluetoothctl aufruft.
        """
        if not self.connected_device:
            return None

        now = time.time()
        if now - self._last_check_time < 10:
            return self.connected_device

        self._last_check_time = now
        if self._is_connected(self.connected_device):
            return self.connected_device

        _log(f"Gerät {self.connected_device} nicht mehr verbunden")
        self.connected_device = None
        self.connected_device_name = None
        self._save_config()
        return None

    # ─── Scanning ───────────────────────────────────────

    def start_scan(self, duration=12):
        """Bluetooth-Gerätesuche im Hintergrund."""
        if self.scanning:
            return False

        self.scanning = True
        self._discovered_devices.clear()
        self.power_on()
        _log(f"Scan gestartet ({duration}s)")

        def _scan():
            proc = None
            try:
                proc = subprocess.Popen(
                    ["bluetoothctl"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True, bufsize=1
                )
                proc.stdin.write("scan on\n")
                proc.stdin.flush()

                end_time = time.time() + duration
                while time.time() < end_time:
                    readable, _, _ = select.select([proc.stdout], [], [], 1.0)
                    if readable:
                        line = proc.stdout.readline()
                        if not line:
                            break
                        line = _clean_ansi(line)
                        match = re.search(r'Device\s+([0-9A-F:]{17})\s+(.+)', line)
                        if match:
                            mac, name = match.group(1), match.group(2).strip()
                            if name and name != mac and not re.match(r'^[0-9A-F:-]+$', name):
                                self._discovered_devices[mac] = name
                                _log(f"  Gefunden: {name} ({mac})")

                proc.stdin.write("scan off\nexit\n")
                proc.stdin.flush()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            except Exception as e:
                _log(f"Scan-Fehler: {e}")
                if proc and proc.poll() is None:
                    proc.kill()
            finally:
                self.scanning = False
                _log(f"Scan fertig: {len(self._discovered_devices)} Geräte")

        threading.Thread(target=_scan, daemon=True).start()
        return True

    # ─── Connect ────────────────────────────────────────

    def connect(self, mac):
        """
        Bluetooth-Verbindung herstellen.

        Strategie:
        1. Schnellversuch: disconnect → connect (funktioniert wenn PA Profile hat)
        2. Bei Fehler: PA neu starten → PA verbindet automatisch

        Returns: {success: bool, message: str, name: str|None}
        """
        with self._lock:
            _log(f"=== Verbindungsanfrage: {mac} ===")
            self.power_on()

            # Bereits verbunden?
            if self._is_connected(mac):
                name = self._get_name(mac)
                self._set_connected(mac, name)
                _log(f"Bereits verbunden: {name}")
                return {
                    "success": True,
                    "message": f"Bereits verbunden mit {name}",
                    "name": name
                }

            # Info holen
            info = self._btctl(f"info {mac}")
            already_paired = "Paired: yes" in info

            # Trust setzen
            self._btctl(f"trust {mac}")

            # Pair falls nötig
            if not already_paired:
                _log(f"Pairing starten...")
                pair_result = self._do_pair(mac)
                if pair_result.get("error"):
                    return {
                        "success": False,
                        "message": pair_result["error"],
                        "name": None
                    }

            # === Versuch 1: Sauber disconnect → connect ===
            _log(f"Versuch 1: Disconnect + Connect...")
            self._btctl(f"disconnect {mac}", timeout=5)
            time.sleep(2)

            connect_result = self._do_connect(mac)
            if connect_result.get("success"):
                return self._finalize_connect(mac)

            # Prüfe ob es trotzdem geklappt hat
            time.sleep(3)
            if self._is_connected(mac):
                return self._finalize_connect(mac)

            # === Versuch 2: PulseAudio neu starten, PA auto-connect nutzen ===
            _log(f"Versuch 2: PulseAudio Neustart...")
            self._btctl(f"disconnect {mac}", timeout=5)
            time.sleep(1)
            _run_cmd("systemctl --user restart pulseaudio", timeout=10)

            # PA auto-connected zu trusted Geräten → warten und prüfen
            _log(f"Warte auf PulseAudio Auto-Connect...")
            for i in range(10):
                time.sleep(2)
                if self._is_connected(mac):
                    _log(f"PA Auto-Connect erfolgreich nach {(i+1)*2}s")
                    return self._finalize_connect(mac)

            # === Versuch 3: Expliziter Connect nach PA-Neustart ===
            _log(f"Versuch 3: Expliziter Connect...")
            connect_result = self._do_connect(mac)
            if connect_result.get("success"):
                return self._finalize_connect(mac)

            time.sleep(3)
            if self._is_connected(mac):
                return self._finalize_connect(mac)

            error = connect_result.get("error", "Verbindung fehlgeschlagen")
            _log(f"=== Alle Versuche fehlgeschlagen: {error} ===")
            return {
                "success": False,
                "message": error,
                "name": None
            }

    def _finalize_connect(self, mac):
        """Verbindung bestätigen und Status setzen."""
        name = self._get_name(mac)
        self._set_connected(mac, name)
        _log(f"=== Erfolgreich verbunden: {name} ===")
        return {
            "success": True,
            "message": f"Verbunden mit {name}",
            "name": name
        }

    def _do_pair(self, mac):
        """Pairing in interaktiver bluetoothctl-Session."""
        proc = None
        try:
            proc = subprocess.Popen(
                ["bluetoothctl"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True, bufsize=1
            )

            # Agent + Scan + Pair
            proc.stdin.write("agent NoInputNoOutput\ndefault-agent\n")
            proc.stdin.flush()
            time.sleep(1)

            proc.stdin.write("scan on\n")
            proc.stdin.flush()
            time.sleep(4)

            proc.stdin.write("scan off\n")
            proc.stdin.flush()
            time.sleep(0.5)

            proc.stdin.write(f"pair {mac}\n")
            proc.stdin.flush()

            # Warte auf Ergebnis
            result = self._read_until(proc, [
                "Pairing successful",
                "Failed to pair",
                "org.bluez.Error",
                "not available",
                "AlreadyExists",
                "Connected: yes",
            ], timeout_sec=20)

            proc.stdin.write("exit\n")
            proc.stdin.flush()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

            kw = result.get("keyword")
            if kw and any(x in kw.lower() for x in ["failed", "error", "not available"]):
                return {"error": "Pairing fehlgeschlagen - ist das Gerät im Pairing-Modus?"}

            return {"success": True}

        except Exception as e:
            _log(f"Pair-Fehler: {e}")
            if proc and proc.poll() is None:
                proc.kill()
            return {"error": f"Pair-Fehler: {e}"}

    def _do_connect(self, mac):
        """Connect in interaktiver bluetoothctl-Session."""
        proc = None
        try:
            proc = subprocess.Popen(
                ["bluetoothctl"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True, bufsize=1
            )

            proc.stdin.write(f"connect {mac}\n")
            proc.stdin.flush()

            result = self._read_until(proc, [
                "Connection successful",
                "Connected: yes",
                "Failed to connect",
                "not available",
                "AlreadyConnected",
                "profile-unavailable",
                "br-connection",
            ], timeout_sec=15)

            proc.stdin.write("exit\n")
            proc.stdin.flush()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

            kw = result.get("keyword")
            if kw and any(x in kw.lower() for x in [
                "successful", "connected: yes", "alreadyconnected"
            ]):
                return {"success": True}

            if kw and "profile-unavailable" in kw.lower():
                return {"error": "Audio-Profil nicht verfügbar - PulseAudio neu starten"}

            if kw and "br-connection" in kw.lower():
                return {"error": f"Bluetooth-Fehler: {kw}"}

            return {"error": "Verbindung konnte nicht hergestellt werden"}

        except Exception as e:
            _log(f"Connect-Fehler: {e}")
            if proc and proc.poll() is None:
                proc.kill()
            return {"error": f"Connect-Fehler: {e}"}

    def _read_until(self, proc, keywords, timeout_sec=15):
        """Lese bluetoothctl Output bis Keyword gefunden oder Timeout."""
        lines = []
        end = time.time() + timeout_sec
        while time.time() < end:
            readable, _, _ = select.select([proc.stdout], [], [], 0.5)
            if readable:
                line = proc.stdout.readline()
                if not line:
                    break
                line_clean = _clean_ansi(line.strip())
                if line_clean:
                    lines.append(line_clean)
                    _log(f"  < {line_clean}")
                    for kw in keywords:
                        if kw.lower() in line_clean.lower():
                            return {"keyword": kw, "lines": lines}
        return {"keyword": None, "lines": lines}

    def _set_connected(self, mac, name=None):
        """Internen Status setzen und speichern."""
        self.connected_device = mac
        self.connected_device_name = name or "Unbekannt"
        self._last_check_time = time.time()
        self._save_config()

    # ─── Disconnect ─────────────────────────────────────

    def disconnect(self, mac=None):
        """Bluetooth-Gerät trennen."""
        mac = mac or self.connected_device
        if not mac:
            return False

        _log(f"Trennen: {mac}")
        self._btctl(f"disconnect {mac}")

        if self.connected_device == mac:
            self.connected_device = None
            self.connected_device_name = None
            self._save_config()

        return True

    def remove_device(self, mac):
        """Gerät komplett entfernen (unpair)."""
        _log(f"Gerät entfernen: {mac}")
        self._btctl(f"disconnect {mac}")
        time.sleep(1)
        self._btctl(f"remove {mac}")

        if self.connected_device == mac:
            self.connected_device = None
            self.connected_device_name = None
            self._save_config()

        self._discovered_devices.pop(mac, None)
        return True

    # ─── Status ─────────────────────────────────────────

    def get_connected_device(self):
        """MAC des verbundenen Geräts (Echtzeit-Prüfung für Audio)."""
        if not self.connected_device:
            return None

        if self._is_connected(self.connected_device):
            self._last_check_time = time.time()
            return self.connected_device

        _log(f"Gerät {self.connected_device} nicht mehr verbunden")
        self.connected_device = None
        self.connected_device_name = None
        self._save_config()
        return None

    def auto_reconnect(self):
        """Beim Start: verbundenes Gerät erkennen oder letztes reconnecten."""
        # Schon verbunden? (z.B. nach Service-Neustart)
        self._detect_connected_device()
        if self.connected_device:
            _log(f"Auto-Reconnect: {self.connected_device}")
            result = self.connect(self.connected_device)
            return result.get("success", False)
        return False

    def _detect_connected_device(self):
        """Bereits verbundene Geräte erkennen."""
        try:
            output = self._btctl("devices Connected")
            for line in output.split("\n"):
                match = re.search(r"Device\s+([0-9A-F:]{17})\s+(.+)", line)
                if match:
                    mac = match.group(1)
                    if self._is_connected(mac):
                        name = self._get_name(mac)
                        self._set_connected(mac, name)
                        _log(f"Bereits verbunden: {name} ({mac})")
                        return
        except Exception as e:
            _log(f"Erkennung fehlgeschlagen: {e}")

    def get_status(self):
        """Status für API (mit 10s Cache)."""
        connected_mac = self._get_connected_live()
        return {
            "scanning": self.scanning,
            "connected": connected_mac is not None,
            "connected_mac": connected_mac,
            "connected_name": self.connected_device_name if connected_mac else None,
        }

    # ─── Config ─────────────────────────────────────────

    def _save_config(self):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(BT_CONFIG_FILE, "w") as f:
                json.dump({
                    "last_device": self.connected_device,
                    "last_device_name": self.connected_device_name,
                }, f, indent=2)
        except IOError as e:
            _log(f"Config speichern fehlgeschlagen: {e}")

    def _load_config(self):
        try:
            with open(BT_CONFIG_FILE, "r") as f:
                data = json.load(f)
                self.connected_device = data.get("last_device")
                self.connected_device_name = data.get("last_device_name")
        except (IOError, json.JSONDecodeError):
            self.connected_device = None
            self.connected_device_name = None
