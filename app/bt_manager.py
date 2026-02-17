"""
bt_manager.py — Bluetooth-Gerätemanager
Verwaltet Bluetooth-Verbindungen für DAB+ Radio auf Raspberry Pi.
Komplett neu geschrieben für Zuverlässigkeit auf Pi Zero.
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

    def _btctl_query(self, command, timeout=10):
        """
        Run a fast query command via bluetoothctl pipe mode.
        For instant commands: info, devices, paired-devices, trust.
        """
        try:
            result = subprocess.run(
                ["bluetoothctl"],
                input=f"{command}\nexit\n",
                capture_output=True,
                text=True,
                timeout=timeout
            )
            return _clean_ansi(result.stdout)
        except subprocess.TimeoutExpired:
            _log(f"Query timeout: {command}")
            return ""
        except Exception as e:
            _log(f"Query error ({command}): {e}")
            return ""

    # ─── Power ──────────────────────────────────────────

    def power_on(self):
        """Bluetooth adapter einschalten und Agent registrieren."""
        try:
            subprocess.run(
                ["rfkill", "unblock", "bluetooth"],
                capture_output=True, timeout=5
            )
        except Exception:
            pass

        try:
            subprocess.run(
                ["bluetoothctl"],
                input="power on\nagent NoInputNoOutput\ndefault-agent\nexit\n",
                capture_output=True, text=True, timeout=10
            )
        except Exception:
            pass

    # ─── Device Listing ─────────────────────────────────

    def get_devices(self):
        """
        Alle bekannten + gescannten Bluetooth-Geräte.
        Gibt Liste mit mac, name, paired, connected zurück.
        """
        # Zwei schnelle Abfragen
        devices_output = self._btctl_query("devices")
        paired_output = self._btctl_query("paired-devices")

        all_devices = {}
        paired_macs = set()

        # Bekannte Geräte parsen
        for line in devices_output.split("\n"):
            match = re.search(r"Device\s+([0-9A-F:]{17})\s+(.+)", line)
            if match:
                mac = match.group(1)
                name = match.group(2).strip()
                if name and name != mac:
                    all_devices[mac] = name

        # Gepaarte Geräte parsen
        for line in paired_output.split("\n"):
            match = re.search(r"Device\s+([0-9A-F:]{17})\s+(.+)", line)
            if match:
                mac = match.group(1)
                name = match.group(2).strip()
                paired_macs.add(mac)
                if mac not in all_devices and name and name != mac:
                    all_devices[mac] = name

        # Gescannte Geräte hinzufügen
        for mac, name in self._discovered_devices.items():
            if mac not in all_devices:
                all_devices[mac] = name

        # Verbindungsstatus (gecached, schnell)
        connected_mac = self._get_connected_cached()

        # Liste bauen
        devices = []
        for mac, name in all_devices.items():
            devices.append({
                "mac": mac,
                "name": name,
                "paired": mac in paired_macs,
                "connected": mac == connected_mac,
            })

        # Sortierung: verbunden > gepaart > entdeckt
        devices.sort(key=lambda d: (
            0 if d["connected"] else (1 if d["paired"] else 2),
            d["name"]
        ))

        return devices

    def _get_connected_cached(self):
        """
        Verbindungsstatus mit Cache (prüft nur alle 30 Sekunden).
        Verhindert, dass der Pi Zero bei jedem Poll belastet wird.
        """
        if not self.connected_device:
            return None

        now = time.time()
        if now - self._last_check_time < 30:
            return self.connected_device

        self._last_check_time = now
        output = self._btctl_query(f"info {self.connected_device}")
        if "Connected: yes" in output:
            return self.connected_device

        _log(f"Gerät {self.connected_device} nicht mehr verbunden (Cache-Check)")
        self.connected_device = None
        self.connected_device_name = None
        self._save_config()
        return None

    # ─── Scanning ───────────────────────────────────────

    def start_scan(self, duration=12):
        """Bluetooth-Gerätesuche im Hintergrund starten."""
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
                    text=True,
                    bufsize=1
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
                            mac = match.group(1)
                            name = match.group(2).strip()
                            # Unbenannte Geräte (nur MAC als Name) überspringen
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
                _log(f"Scan fertig: {len(self._discovered_devices)} Geräte gefunden")

        threading.Thread(target=_scan, daemon=True).start()
        return True

    # ─── Connect (Herzstück) ────────────────────────────

    def connect(self, mac):
        """
        Kompletter Bluetooth-Verbindungsablauf:
        power on → trust → pair (falls nötig) → connect → verify

        Einfach und robust: nur bluetoothctl für Verbindungsprüfung.
        PulseAudio erstellt den Audio-Sink automatisch im Hintergrund.

        Returns: {success: bool, message: str, name: str|None}
        """
        with self._lock:
            _log(f"Verbindungsanfrage: {mac}")
            self.power_on()

            # 1. Prüfe ob bereits verbunden
            info = self._btctl_query(f"info {mac}")
            if "Connected: yes" in info:
                name = self._parse_device_name(info)
                self._set_connected(mac, name)
                _log(f"Bereits verbunden: {name}")
                return {
                    "success": True,
                    "message": f"Bereits verbunden mit {name}",
                    "name": name
                }

            already_paired = "Paired: yes" in info

            # 2. Interaktive Session für Pair + Connect
            session_result = self._interactive_connect(
                mac, needs_pair=not already_paired
            )

            # 3. Ergebnis auswerten
            if session_result.get("success"):
                # Session hat "Connected: yes" gesehen → vertrauen!
                time.sleep(2)
                info = self._btctl_query(f"info {mac}")
                name = self._parse_device_name(info)
                self._set_connected(mac, name)
                _log(f"Erfolgreich verbunden: {name}")
                return {
                    "success": True,
                    "message": f"Verbunden mit {name}",
                    "name": name
                }

            # Session hat keinen Erfolg gemeldet → trotzdem prüfen
            # (manche Geräte verbinden sich verzögert)
            time.sleep(3)
            info = self._btctl_query(f"info {mac}")
            if "Connected: yes" in info:
                name = self._parse_device_name(info)
                self._set_connected(mac, name)
                _log(f"Verzögert verbunden: {name}")
                return {
                    "success": True,
                    "message": f"Verbunden mit {name}",
                    "name": name
                }

            error_msg = session_result.get(
                "error", "Verbindung fehlgeschlagen"
            )
            _log(f"Verbindung fehlgeschlagen: {error_msg}")
            return {
                "success": False,
                "message": error_msg,
                "name": None
            }

    def _interactive_connect(self, mac, needs_pair=False):
        """
        Führt Pair + Connect in einer einzigen interaktiven
        bluetoothctl-Session aus. Liest Output und wartet auf
        Erfolgs-/Fehlermeldungen.
        """
        proc = None
        try:
            proc = subprocess.Popen(
                ["bluetoothctl"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )

            def send(cmd):
                proc.stdin.write(cmd + "\n")
                proc.stdin.flush()
                _log(f"  > {cmd}")

            def wait_for(keywords, timeout_sec=15):
                """Lese Output bis ein Keyword gefunden wird oder Timeout."""
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
                                    return kw, lines
                return None, lines

            def cleanup():
                send("exit")
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()

            # Agent registrieren (für Auto-Accept beim Pairing)
            send("agent NoInputNoOutput")
            send("default-agent")
            time.sleep(1)

            # Trust setzen (für Auto-Reconnect beim nächsten Boot)
            send(f"trust {mac}")
            time.sleep(0.5)

            # Pairing falls nötig
            if needs_pair:
                _log(f"  Pairing starten...")

                # Kurzer Scan damit das Gerät sichtbar wird
                send("scan on")
                time.sleep(4)
                send("scan off")
                time.sleep(0.5)

                send(f"pair {mac}")
                kw, lines = wait_for([
                    "Pairing successful",
                    "Failed to pair",
                    "org.bluez.Error",
                    "not available",
                    "AlreadyExists",
                    "Connected: yes",
                ], timeout_sec=20)

                # Manche Geräte verbinden automatisch beim Pairing
                if kw and "connected: yes" in kw.lower():
                    _log(f"  Direkt beim Pairing verbunden!")
                    cleanup()
                    return {"success": True}

                if kw and any(x in kw.lower() for x in ["failed", "error", "not available"]):
                    _log(f"  Pairing fehlgeschlagen: {kw}")
                    cleanup()
                    return {"error": "Pairing fehlgeschlagen - ist das Gerät im Pairing-Modus?"}

                time.sleep(1)

            # Connect
            _log(f"  Verbinde...")
            send(f"connect {mac}")
            kw, lines = wait_for([
                "Connection successful",
                "Connected: yes",
                "Failed to connect",
                "not available",
                "AlreadyConnected",
                "profile-unavailable",
            ], timeout_sec=15)

            cleanup()

            if kw and any(x in kw.lower() for x in [
                "successful", "connected: yes", "alreadyconnected"
            ]):
                return {"success": True}

            if kw and "profile-unavailable" in kw.lower():
                return {"error": "profile-unavailable - PulseAudio nicht bereit"}

            return {"error": "Verbindung konnte nicht hergestellt werden"}

        except Exception as e:
            _log(f"  Session-Fehler: {e}")
            if proc and proc.poll() is None:
                proc.kill()
            return {"error": f"Fehler: {str(e)}"}

    def _parse_device_name(self, info_output):
        """Gerätename aus bluetoothctl info Output extrahieren."""
        for pattern in [r"Alias:\s+(.+)", r"Name:\s+(.+)"]:
            match = re.search(pattern, info_output)
            if match:
                return match.group(1).strip()
        return "Unbekannt"

    def _set_connected(self, mac, name=None):
        """Internen Verbindungsstatus setzen und speichern."""
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
        self._btctl_query(f"disconnect {mac}")

        if self.connected_device == mac:
            self.connected_device = None
            self.connected_device_name = None
            self._save_config()

        return True

    def remove_device(self, mac):
        """Gerät komplett entfernen (unpair)."""
        _log(f"Gerät entfernen: {mac}")
        self._btctl_query(f"remove {mac}")

        if self.connected_device == mac:
            self.connected_device = None
            self.connected_device_name = None
            self._save_config()

        self._discovered_devices.pop(mac, None)
        return True

    # ─── Status ─────────────────────────────────────────

    def get_connected_device(self):
        """
        MAC des verbundenen Geräts (mit Echtzeit-Prüfung).
        Wird nur für Musik-Wiedergabe aufgerufen, nicht beim Polling.
        """
        if not self.connected_device:
            return None

        output = self._btctl_query(f"info {self.connected_device}")
        if "Connected: yes" in output:
            self._last_check_time = time.time()
            return self.connected_device

        _log(f"Gerät {self.connected_device} nicht mehr verbunden")
        self.connected_device = None
        self.connected_device_name = None
        self._save_config()
        return None

    def auto_reconnect(self):
        """Automatisch mit dem letzten Gerät verbinden (beim Start)."""
        # Erst prüfen ob bereits ein Gerät verbunden ist
        self._detect_connected_device()
        if self.connected_device:
            _log(f"Auto-Reconnect: {self.connected_device}")
            result = self.connect(self.connected_device)
            return result.get("success", False)
        return False

    def _detect_connected_device(self):
        """Prüft ob bereits ein BT-Gerät verbunden ist (z.B. nach Service-Neustart)."""
        try:
            output = self._btctl_query("devices Connected")
            for line in output.split("\n"):
                match = re.search(r"Device\s+([0-9A-F:]{17})\s+(.+)", line)
                if match:
                    mac = match.group(1)
                    info = self._btctl_query(f"info {mac}")
                    if "Connected: yes" in info:
                        name = self._parse_device_name(info)
                        self._set_connected(mac, name)
                        _log(f"Bereits verbundenes Gerät erkannt: {name} ({mac})")
                        return
        except Exception as e:
            _log(f"Erkennung fehlgeschlagen: {e}")

    def get_status(self):
        """Status für API (mit Cache - schnell)."""
        connected_mac = self._get_connected_cached()
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
