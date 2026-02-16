"""
bt_manager.py — Bluetooth-Gerätemanager
Verwaltet Bluetooth-Verbindungen via bluetoothctl / D-Bus.
"""

import subprocess
import re
import time
import threading
import json
import os

DATA_DIR = "/var/lib/dab-radio"
BT_CONFIG_FILE = os.path.join(DATA_DIR, "bluetooth.json")


class BluetoothManager:
    def __init__(self):
        self.connected_device = None
        self.scanning = False
        self._scan_thread = None
        self._discovered_devices = {}  # Cache für gescannte Geräte
        self._load_config()

    def _run_btctl(self, commands, timeout=15):
        """Führt bluetoothctl Befehle aus."""
        try:
            input_str = "\n".join(commands) + "\nquit\n"
            result = subprocess.run(
                ["bluetoothctl"],
                input=input_str,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            return result.stdout
        except subprocess.TimeoutExpired:
            return ""
        except Exception as e:
            return ""

    def power_on(self):
        """Bluetooth einschalten."""
        # Rfkill unblock falls blockiert
        try:
            subprocess.run(["sudo", "rfkill", "unblock", "bluetooth"],
                         capture_output=True, timeout=5)
        except Exception:
            pass
        self._run_btctl(["power on", "agent on", "default-agent"])

    def get_devices(self):
        """Gibt Liste bekannter Bluetooth-Geräte zurück."""
        output = self._run_btctl(["devices"])
        devices = []
        seen = set()

        # Bekannte Geräte aus bluetoothctl
        for line in output.split("\n"):
            match = re.search(r"Device\s+([0-9A-F:]{17})\s+(.+)", line)
            if match:
                mac = match.group(1)
                name = match.group(2).strip()
                if mac not in seen:
                    seen.add(mac)
                    devices.append({
                        "mac": mac,
                        "name": name,
                        "connected": self._is_connected(mac),
                    })

        # Gescannte Geräte hinzufügen (die noch nicht bekannt sind)
        for mac, name in self._discovered_devices.items():
            if mac not in seen:
                seen.add(mac)
                devices.append({
                    "mac": mac,
                    "name": name,
                    "connected": False,
                })

        return devices

    def get_paired_devices(self):
        """Gibt Liste der gepaarten Geräte zurück."""
        output = self._run_btctl(["paired-devices"])
        devices = []

        for line in output.split("\n"):
            match = re.search(r"Device\s+([0-9A-F:]{17})\s+(.+)", line)
            if match:
                mac = match.group(1)
                name = match.group(2).strip()
                devices.append({
                    "mac": mac,
                    "name": name,
                    "connected": self._is_connected(mac),
                    "paired": True,
                })

        return devices

    def _is_connected(self, mac):
        """Prüft ob ein Gerät verbunden ist."""
        output = self._run_btctl([f"info {mac}"])
        return "Connected: yes" in output

    def start_scan(self, duration=15):
        """Startet Bluetooth-Scan im Hintergrund."""
        if self.scanning:
            return False

        self.scanning = True
        self.power_on()  # Bluetooth erst einschalten

        def _scan():
            try:
                # Interaktiver Scan mit Output-Parsing
                proc = subprocess.Popen(
                    ["bluetoothctl"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1
                )

                # Scan starten
                proc.stdin.write("scan on\n")
                proc.stdin.flush()

                # Scan-Output für duration Sekunden lesen
                import select
                end_time = time.time() + duration
                while time.time() < end_time:
                    if proc.stdout in select.select([proc.stdout], [], [], 0.5)[0]:
                        line = proc.stdout.readline()
                        # Parse NEW/CHG Device lines
                        match = re.search(r'Device\s+([0-9A-F:]{17})\s+(.+)', line)
                        if match:
                            mac = match.group(1)
                            name = match.group(2).strip()
                            # Entferne ANSI Escape Codes
                            name = re.sub(r'\x1b\[[0-9;]*m', '', name)
                            self._discovered_devices[mac] = name

                # Scan beenden
                proc.stdin.write("scan off\n")
                proc.stdin.write("quit\n")
                proc.stdin.flush()
                proc.wait(timeout=5)
            except Exception as e:
                pass
            finally:
                self.scanning = False

        self._scan_thread = threading.Thread(target=_scan, daemon=True)
        self._scan_thread.start()
        return True

    def pair(self, mac):
        """Gerät paaren mit interaktiver Session."""
        try:
            # Interaktive bluetoothctl Session
            proc = subprocess.Popen(
                ["bluetoothctl"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )

            # Bluetooth einschalten und Agent registrieren
            proc.stdin.write("power on\n")
            proc.stdin.write("agent NoInputNoOutput\n")  # Auto-accept Pairing
            proc.stdin.write("default-agent\n")
            proc.stdin.flush()
            time.sleep(1)  # Warten bis Agent bereit ist

            # Kurzer Scan damit Gerät sichtbar wird
            proc.stdin.write("scan on\n")
            proc.stdin.flush()
            time.sleep(3)  # Scan laufen lassen
            proc.stdin.write("scan off\n")
            proc.stdin.flush()
            time.sleep(0.5)

            # Pairing starten
            proc.stdin.write(f"pair {mac}\n")
            proc.stdin.flush()

            # Output lesen und auf Erfolg warten
            success = False
            timeout = time.time() + 20
            output_lines = []

            import select
            while time.time() < timeout:
                if proc.stdout in select.select([proc.stdout], [], [], 0.5)[0]:
                    line = proc.stdout.readline()
                    output_lines.append(line)

                    if "Pairing successful" in line or "Connection successful" in line:
                        success = True
                        break
                    elif "Failed to pair" in line or "not available" in line:
                        break

            if success:
                # Trust setzen für Auto-Reconnect
                proc.stdin.write(f"trust {mac}\n")
                proc.stdin.flush()
                time.sleep(0.5)

            # Session beenden
            proc.stdin.write("quit\n")
            proc.stdin.flush()
            proc.wait(timeout=2)

            return success

        except Exception as e:
            return False

    def connect(self, mac):
        """Mit Bluetooth-Gerät verbinden."""
        self.power_on()

        # Prüfe ob bereits verbunden
        if self._is_connected(mac):
            self.connected_device = mac
            self._save_config()
            return True

        # Erst pairen falls nötig
        if not self._is_paired(mac):
            if not self.pair(mac):
                # Prüfe nochmal ob verbunden (Pairing könnte verbunden haben)
                if self._is_connected(mac):
                    self.connected_device = mac
                    self._save_config()
                    return True
                return False

        output = self._run_btctl([f"connect {mac}"], timeout=20)
        success = "Connection successful" in output or "Connected: yes" in output or "AlreadyConnected" in output

        # Falls connect fehlschlägt, prüfe ob trotzdem verbunden
        if not success:
            success = self._is_connected(mac)

        if success:
            self.connected_device = mac
            self._save_config()

        # Kurz warten bis A2DP Profil bereit ist
        time.sleep(2)
        return success

    def disconnect(self, mac=None):
        """Bluetooth-Gerät trennen."""
        if mac is None:
            mac = self.connected_device
        if mac:
            self._run_btctl([f"disconnect {mac}"])
            if self.connected_device == mac:
                self.connected_device = None
                self._save_config()
            return True
        return False

    def _is_paired(self, mac):
        """Prüft ob ein Gerät gepaart ist."""
        output = self._run_btctl([f"info {mac}"])
        return "Paired: yes" in output

    def remove_device(self, mac):
        """Gerät komplett entfernen (unpair)."""
        self._run_btctl([f"remove {mac}"])
        if self.connected_device == mac:
            self.connected_device = None
            self._save_config()
        return True

    def get_connected_device(self):
        """Gibt das aktuell verbundene Gerät zurück."""
        if self.connected_device and self._is_connected(self.connected_device):
            return self.connected_device
        self.connected_device = None
        return None

    def auto_reconnect(self):
        """Versucht das zuletzt verbundene Gerät automatisch zu verbinden."""
        if self.connected_device:
            return self.connect(self.connected_device)
        return False

    def get_status(self):
        """Status-Zusammenfassung."""
        connected = self.get_connected_device()
        return {
            "scanning": self.scanning,
            "connected_mac": connected,
            "connected": connected is not None,
        }

    def _save_config(self):
        try:
            with open(BT_CONFIG_FILE, "w") as f:
                json.dump({"last_device": self.connected_device}, f)
        except IOError:
            pass

    def _load_config(self):
        try:
            with open(BT_CONFIG_FILE, "r") as f:
                data = json.load(f)
                self.connected_device = data.get("last_device")
        except (IOError, json.JSONDecodeError):
            self.connected_device = None
