"""
wifi_manager.py — WiFi Network Manager
Manages WiFi configuration for both AP and Client modes with automatic fallback.
"""

import subprocess
import json
import os
import re
import time
import threading

DATA_DIR = "/var/lib/dab-radio"
NETWORK_CONFIG_FILE = os.path.join(DATA_DIR, "network.json")
HOSTAPD_CONF = "/etc/hostapd/hostapd.conf"
WPA_SUPPLICANT_CONF = "/etc/wpa_supplicant/wpa_supplicant.conf"
DHCPCD_CONF = "/etc/dhcpcd.conf.d/dabradio.conf"


class WiFiManager:
    def __init__(self):
        self.mode = "ap"  # "ap" or "client"
        self.config = {
            "mode": "ap",
            "ap_ssid": "DAB-Radio",
            "ap_password": "dabradio123",
            "client_ssid": "",
            "client_password": "",
            "fallback_enabled": True,
            "default_volume": 40
        }
        self._lock = threading.Lock()
        os.makedirs(DATA_DIR, exist_ok=True)
        self._load_config()

    def _load_config(self):
        """Load network configuration from JSON file."""
        try:
            with open(NETWORK_CONFIG_FILE, "r") as f:
                loaded = json.load(f)
                self.config.update(loaded)
                self.mode = self.config.get("mode", "ap")
        except (IOError, json.JSONDecodeError):
            # File doesn't exist or is invalid, use defaults
            self._save_config()

    def _save_config(self):
        """Save network configuration to JSON file."""
        try:
            with open(NETWORK_CONFIG_FILE, "w") as f:
                json.dump(self.config, f, indent=2)
        except IOError:
            pass

    def get_config(self):
        """Get current configuration."""
        return self.config.copy()

    def get_status(self):
        """Get current network status."""
        with self._lock:
            status = {
                "mode": self.mode,
                "ap_ssid": self.config["ap_ssid"],
                "connected": False,
                "ssid": "",
                "ip_address": ""
            }

            if self.mode == "client":
                # Check if connected to WiFi
                connected, ssid, ip = self._check_client_connection()
                status["connected"] = connected
                status["ssid"] = ssid
                status["ip_address"] = ip

            return status

    def _check_client_connection(self):
        """Check if connected to WiFi in client mode."""
        try:
            # Get current SSID
            result = subprocess.run(
                ["iwgetid", "-r"],
                capture_output=True,
                text=True,
                timeout=5
            )
            ssid = result.stdout.strip()

            if ssid:
                # Get IP address
                result = subprocess.run(
                    ["ip", "-4", "addr", "show", "wlan0"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                ip_match = re.search(r'inet\s+(\d+\.\d+\.\d+\.\d+)', result.stdout)
                ip = ip_match.group(1) if ip_match else ""

                return True, ssid, ip

            return False, "", ""
        except Exception:
            return False, "", ""

    # ─── AP Mode Management ───

    def get_ap_config(self):
        """Get current AP configuration."""
        return {
            "ssid": self.config["ap_ssid"],
            "password": self.config["ap_password"]
        }

    def set_ap_config(self, ssid, password):
        """Update AP SSID and password."""
        with self._lock:
            if len(password) < 8:
                return False, "Password must be at least 8 characters"

            self.config["ap_ssid"] = ssid
            self.config["ap_password"] = password
            self._save_config()

            # Update hostapd.conf
            self._update_hostapd_conf(ssid, password)

            # Restart hostapd if in AP mode
            if self.mode == "ap":
                self._restart_service("hostapd")

            return True, "AP configuration updated"

    def _update_hostapd_conf(self, ssid, password):
        """Update hostapd configuration file."""
        try:
            with open(HOSTAPD_CONF, "r") as f:
                lines = f.readlines()

            with open(HOSTAPD_CONF, "w") as f:
                for line in lines:
                    if line.startswith("ssid="):
                        f.write(f"ssid={ssid}\n")
                    elif line.startswith("wpa_passphrase="):
                        f.write(f"wpa_passphrase={password}\n")
                    else:
                        f.write(line)
        except IOError:
            pass

    # ─── Client Mode Management ───

    def scan_networks(self):
        """Scan for available WiFi networks."""
        try:
            # Use iwlist to scan
            result = subprocess.run(
                ["iwlist", "wlan0", "scan"],
                capture_output=True,
                text=True,
                timeout=15
            )

            networks = []
            current_network = {}

            for line in result.stdout.split("\n"):
                line = line.strip()

                if "ESSID:" in line:
                    # Extract SSID
                    match = re.search(r'ESSID:"([^"]+)"', line)
                    if match:
                        current_network["ssid"] = match.group(1)

                elif "Quality=" in line:
                    # Extract signal quality
                    match = re.search(r'Quality=(\d+)/(\d+)', line)
                    if match:
                        quality = int(match.group(1))
                        max_quality = int(match.group(2))
                        signal_percent = int((quality / max_quality) * 100)
                        current_network["signal"] = signal_percent

                elif "Encryption key:" in line:
                    encrypted = "on" in line.lower()
                    current_network["encrypted"] = encrypted

                    # If we have SSID, add to list
                    if "ssid" in current_network:
                        networks.append(current_network.copy())
                        current_network = {}

            # Sort by signal strength
            networks.sort(key=lambda x: x.get("signal", 0), reverse=True)

            # Remove duplicates (keep strongest)
            seen = set()
            unique_networks = []
            for net in networks:
                if net["ssid"] not in seen:
                    seen.add(net["ssid"])
                    unique_networks.append(net)

            return unique_networks

        except Exception as e:
            return []

    def connect_to_network(self, ssid, password):
        """Connect to a WiFi network in client mode."""
        with self._lock:
            self.config["client_ssid"] = ssid
            self.config["client_password"] = password
            self._save_config()

            # Create wpa_supplicant config
            self._create_wpa_supplicant_conf(ssid, password)

            # Switch to client mode
            success = self._switch_to_client_mode()

            if success:
                # Wait a bit and check connection
                time.sleep(5)
                connected, _, _ = self._check_client_connection()
                return connected

            return False

    def _create_wpa_supplicant_conf(self, ssid, password):
        """Create wpa_supplicant configuration."""
        config = f"""ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1
country=DE

network={{
    ssid="{ssid}"
    psk="{password}"
    key_mgmt=WPA-PSK
}}
"""
        try:
            with open(WPA_SUPPLICANT_CONF, "w") as f:
                f.write(config)
        except IOError:
            pass

    # ─── Mode Switching ───

    def switch_to_ap_mode(self):
        """Switch to Access Point mode."""
        with self._lock:
            return self._switch_to_ap_mode()

    def _switch_to_ap_mode(self):
        """Internal: Switch to AP mode (no lock)."""
        try:
            self.mode = "ap"
            self.config["mode"] = "ap"
            self._save_config()

            # Stop wpa_supplicant
            subprocess.run(["systemctl", "stop", "wpa_supplicant"], capture_output=True)

            # Configure static IP for AP
            self._set_static_ip()

            # Start hostapd and dnsmasq
            self._restart_service("hostapd")
            self._restart_service("dnsmasq")

            return True
        except Exception:
            return False

    def _switch_to_client_mode(self):
        """Internal: Switch to client mode (no lock)."""
        try:
            self.mode = "client"
            self.config["mode"] = "client"
            self._save_config()

            # Stop hostapd and dnsmasq
            subprocess.run(["systemctl", "stop", "hostapd"], capture_output=True)
            subprocess.run(["systemctl", "stop", "dnsmasq"], capture_output=True)

            # Configure DHCP for client mode
            self._set_dhcp_client()

            # Start wpa_supplicant
            subprocess.run(["systemctl", "restart", "wpa_supplicant"], capture_output=True)
            subprocess.run(["systemctl", "restart", "dhcpcd"], capture_output=True)

            return True
        except Exception:
            return False

    def _set_static_ip(self):
        """Set static IP for AP mode."""
        config = """# DAB Radio AP Configuration
interface wlan0
static ip_address=10.0.0.1/24
nohook wpa_supplicant
"""
        try:
            with open(DHCPCD_CONF, "w") as f:
                f.write(config)
            self._restart_service("dhcpcd")
        except IOError:
            pass

    def _set_dhcp_client(self):
        """Set DHCP client for client mode."""
        config = """# DAB Radio Client Configuration
# Use DHCP for client mode
"""
        try:
            with open(DHCPCD_CONF, "w") as f:
                f.write(config)
            self._restart_service("dhcpcd")
        except IOError:
            pass

    def check_connectivity(self):
        """Check internet connectivity with ping test."""
        try:
            result = subprocess.run(
                ["ping", "-c", "1", "-W", "2", "8.8.8.8"],
                capture_output=True,
                timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False

    def _restart_service(self, service):
        """Restart a systemd service."""
        try:
            subprocess.run(
                ["systemctl", "restart", service],
                capture_output=True,
                timeout=10
            )
        except Exception:
            pass

    # ─── Fallback Settings ───

    def set_fallback_enabled(self, enabled):
        """Enable or disable automatic fallback to AP mode."""
        with self._lock:
            self.config["fallback_enabled"] = enabled
            self._save_config()

    def is_fallback_enabled(self):
        """Check if fallback is enabled."""
        return self.config.get("fallback_enabled", True)

    # ─── Default Volume ───

    def set_default_volume(self, volume):
        """Set default volume."""
        with self._lock:
            self.config["default_volume"] = max(0, min(63, int(volume)))
            self._save_config()

    def get_default_volume(self):
        """Get default volume."""
        return self.config.get("default_volume", 40)
