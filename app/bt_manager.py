"""
bt_manager.py — Bluetooth-Gerätemanager (bluez-alsa)

Verwendet nur bluetoothctl pipe-mode.
Kein PulseAudio, kein PipeWire — Audio läuft über bluez-alsa (ALSA direkt).
"""

import subprocess
import re
import time
import threading
import json
import os

DATA_DIR = "/var/lib/dab-radio"
BT_CONFIG_FILE = os.path.join(DATA_DIR, "bluetooth.json")


def _log(msg):
    print(f"[BT] {msg}", flush=True)


def _sh(cmd, timeout=10):
    """Shell-Befehl ausführen."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""


def _btctl(cmd, timeout=10):
    """bluetoothctl Pipe-Mode."""
    try:
        r = subprocess.run(
            ["bluetoothctl"], input=f"{cmd}\nexit\n",
            capture_output=True, text=True, timeout=timeout
        )
        return re.sub(r'\x1b\[[0-9;]*m', '', r.stdout)
    except Exception:
        return ""


class BluetoothManager:
    def __init__(self):
        self.connected_device = None
        self.connected_device_name = None
        self.scanning = False
        self._discovered = {}
        self._lock = threading.Lock()
        self._check_time = 0
        self._load_config()

    # ─── Helpers ──────────────────────────────────────────

    def _is_connected(self, mac):
        return "Connected: yes" in _btctl(f"info {mac}")

    def _get_name(self, mac):
        out = _btctl(f"info {mac}")
        m = re.search(r"Alias:\s+(.+)", out) or re.search(r"Name:\s+(.+)", out)
        return m.group(1).strip() if m else "Unbekannt"

    def _set_connected(self, mac, name):
        self.connected_device = mac
        self.connected_device_name = name
        self._check_time = time.time()
        self._save_config()

    def _clear_connected(self):
        self.connected_device = None
        self.connected_device_name = None
        self._save_config()

    # ─── Power ────────────────────────────────────────────

    def power_on(self):
        _sh("rfkill unblock bluetooth 2>/dev/null")
        _btctl("power on\nagent NoInputNoOutput\ndefault-agent")

    # ─── Geräteliste ──────────────────────────────────────

    def get_devices(self):
        devs_out = _btctl("devices")
        paired_out = _btctl("paired-devices")

        all_devs = {}
        paired = set()

        for line in devs_out.split("\n"):
            m = re.search(r"Device\s+([0-9A-F:]{17})\s+(.+)", line)
            if m and m.group(2).strip() != m.group(1):
                all_devs[m.group(1)] = m.group(2).strip()

        for line in paired_out.split("\n"):
            m = re.search(r"Device\s+([0-9A-F:]{17})\s+(.+)", line)
            if m:
                paired.add(m.group(1))
                if m.group(1) not in all_devs and m.group(2).strip() != m.group(1):
                    all_devs[m.group(1)] = m.group(2).strip()

        for mac, name in self._discovered.items():
            if mac not in all_devs:
                all_devs[mac] = name

        conn = self._cached_connected()

        result = [{
            "mac": mac, "name": name,
            "paired": mac in paired,
            "connected": mac == conn,
        } for mac, name in all_devs.items()]

        result.sort(key=lambda d: (0 if d["connected"] else 1 if d["paired"] else 2, d["name"]))
        return result

    def _cached_connected(self):
        if not self.connected_device:
            return None
        if time.time() - self._check_time < 10:
            return self.connected_device
        self._check_time = time.time()
        if self._is_connected(self.connected_device):
            return self.connected_device
        _log(f"Nicht mehr verbunden: {self.connected_device}")
        self._clear_connected()
        return None

    # ─── Scan ─────────────────────────────────────────────

    def start_scan(self, duration=12):
        if self.scanning:
            return False
        self.scanning = True
        self._discovered.clear()
        self.power_on()
        _log(f"Scan startet ({duration}s)")

        def _scan():
            try:
                _btctl(f"scan on", timeout=3)
                time.sleep(duration)
                _btctl(f"scan off", timeout=3)
                out = _btctl("devices")
                for line in out.split("\n"):
                    m = re.search(r"Device\s+([0-9A-F:]{17})\s+(.+)", line)
                    if m:
                        mac, name = m.group(1), m.group(2).strip()
                        if name and name != mac and not re.match(r'^[0-9A-F:-]+$', name):
                            self._discovered[mac] = name
                            _log(f"  Gefunden: {name} ({mac})")
            except Exception as e:
                _log(f"Scan-Fehler: {e}")
            finally:
                self.scanning = False
                _log(f"Scan fertig: {len(self._discovered)} Geräte")

        threading.Thread(target=_scan, daemon=True).start()
        return True

    # ─── Connect ──────────────────────────────────────────

    def connect(self, mac):
        """
        Simpel: trust → pair → connect.
        Kein PulseAudio nötig — bluez-alsa übernimmt das Audio-Profil.
        """
        with self._lock:
            _log(f"Connect: {mac}")
            self.power_on()

            # Schon verbunden?
            if self._is_connected(mac):
                name = self._get_name(mac)
                self._set_connected(mac, name)
                _log(f"Bereits verbunden: {name}")
                return {"success": True, "message": f"Verbunden mit {name}", "name": name}

            # Trust (für auto-connect)
            _btctl(f"trust {mac}")

            # Pair falls nötig
            info = _btctl(f"info {mac}")
            if "Paired: yes" not in info:
                _log("Pairing...")
                _btctl("scan on", timeout=3)
                time.sleep(5)
                _btctl("scan off", timeout=3)
                pair_out = _btctl(f"pair {mac}", timeout=20)
                if "Failed" in pair_out and "AlreadyExists" not in pair_out:
                    return {"success": False, "message": "Pairing fehlgeschlagen", "name": None}

            # Versuch 1: bluetoothctl connect
            _log("Verbinde (Versuch 1)...")
            _btctl(f"connect {mac}", timeout=15)
            time.sleep(3)

            if self._is_connected(mac):
                name = self._get_name(mac)
                self._set_connected(mac, name)
                _log(f"Verbunden: {name}")
                return {"success": True, "message": f"Verbunden mit {name}", "name": name}

            # Versuch 2: disconnect + erneut connect
            _log("Versuch 2: disconnect + reconnect...")
            _btctl(f"disconnect {mac}", timeout=5)
            time.sleep(2)
            _btctl(f"connect {mac}", timeout=15)
            time.sleep(3)

            if self._is_connected(mac):
                name = self._get_name(mac)
                self._set_connected(mac, name)
                _log(f"Verbunden: {name}")
                return {"success": True, "message": f"Verbunden mit {name}", "name": name}

            # Versuch 3: bluealsa restart + connect
            _log("Versuch 3: bluealsa restart + connect...")
            _sh("sudo systemctl restart bluealsa", timeout=10)
            time.sleep(2)
            _btctl(f"connect {mac}", timeout=15)
            time.sleep(3)

            if self._is_connected(mac):
                name = self._get_name(mac)
                self._set_connected(mac, name)
                _log(f"Verbunden: {name}")
                return {"success": True, "message": f"Verbunden mit {name}", "name": name}

            _log("Fehlgeschlagen")
            return {"success": False, "message": "Verbindung fehlgeschlagen", "name": None}

    # ─── Disconnect ───────────────────────────────────────

    def disconnect(self, mac=None):
        mac = mac or self.connected_device
        if not mac:
            return False
        _log(f"Trennen: {mac}")
        _btctl(f"disconnect {mac}")
        if self.connected_device == mac:
            self._clear_connected()
        return True

    def remove_device(self, mac):
        _log(f"Entfernen: {mac}")
        _btctl(f"disconnect {mac}")
        time.sleep(1)
        _btctl(f"remove {mac}")
        if self.connected_device == mac:
            self._clear_connected()
        self._discovered.pop(mac, None)
        return True

    # ─── Status ───────────────────────────────────────────

    def get_connected_device(self):
        """Für Audio-Wiedergabe: Echtzeit-Check."""
        if not self.connected_device:
            return None
        if self._is_connected(self.connected_device):
            self._check_time = time.time()
            return self.connected_device
        _log(f"Nicht mehr verbunden: {self.connected_device}")
        self._clear_connected()
        return None

    def auto_reconnect(self):
        """Beim Start: prüfen ob schon verbunden oder reconnecten."""
        # Schon verbunden?
        out = _btctl("devices Connected")
        for line in out.split("\n"):
            m = re.search(r"Device\s+([0-9A-F:]{17})", line)
            if m and self._is_connected(m.group(1)):
                name = self._get_name(m.group(1))
                self._set_connected(m.group(1), name)
                _log(f"Bereits verbunden: {name}")
                return True

        # Letztes Gerät reconnecten
        if self.connected_device:
            _log(f"Reconnect: {self.connected_device}")
            r = self.connect(self.connected_device)
            return r.get("success", False)
        return False

    def get_status(self):
        conn = self._cached_connected()
        return {
            "scanning": self.scanning,
            "connected": conn is not None,
            "connected_mac": conn,
            "connected_name": self.connected_device_name if conn else None,
        }

    # ─── Config ───────────────────────────────────────────

    def _save_config(self):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(BT_CONFIG_FILE, "w") as f:
                json.dump({
                    "last_device": self.connected_device,
                    "last_device_name": self.connected_device_name,
                }, f, indent=2)
        except IOError as e:
            _log(f"Config-Fehler: {e}")

    def _load_config(self):
        try:
            with open(BT_CONFIG_FILE, "r") as f:
                d = json.load(f)
                self.connected_device = d.get("last_device")
                self.connected_device_name = d.get("last_device_name")
        except (IOError, json.JSONDecodeError):
            self.connected_device = None
            self.connected_device_name = None
