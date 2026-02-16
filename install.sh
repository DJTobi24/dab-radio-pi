#!/bin/bash
set -e

echo "╔══════════════════════════════════════════╗"
echo "║   Pi Zero DAB+ Bluetooth Radio Installer ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# Prüfe ob als root ausgeführt
if [ "$EUID" -ne 0 ]; then
    echo "❌ Bitte als root ausführen: sudo ./install.sh"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="/opt/dab-radio"

# OS Version erkennen
OS_VERSION=$(cat /etc/os-release | grep VERSION_CODENAME | cut -d'=' -f2)
echo "📋 Erkannte OS-Version: $OS_VERSION"

echo "📦 [1/9] System-Pakete aktualisieren & installieren..."
apt-get update -qq
apt-get install -y -qq \
    python3 python3-pip python3-venv \
    hostapd dnsmasq \
    bluez pulseaudio pulseaudio-module-bluetooth \
    alsa-utils \
    libncurses5 \
    unzip wget \
    iptables \
    wpasupplicant wireless-tools

echo "📻 [2/9] uGreen DAB Board Software installieren (v16, radio_cli v3.2.1)..."
if [ ! -d "/usr/local/lib/ugreen-dab+" ]; then
    # Kopiere lokale uGreen-Dateien (enthalten im Repository)
    mkdir -p /usr/local/lib/ugreen-dab+
    cp -r "$SCRIPT_DIR/ugreen-dab/"* /usr/local/lib/ugreen-dab+/

    # Symlinks für radio_cli v3.2.1 (32-bit für Pi Zero)
    ln -sf /usr/local/lib/ugreen-dab+/radio_cli_v3.2.1 /usr/local/sbin/radio_cli
    chmod +x /usr/local/sbin/radio_cli
    chmod +x /usr/local/lib/ugreen-dab+/DABBoardRadio_v0.17.2

    echo "   ✅ radio_cli v3.2.1 installiert"
else
    echo "   ✅ uGreen Software bereits vorhanden"
fi

echo "🔧 [3/9] SPI & I2S aktivieren..."
CONFIG_FILE="/boot/firmware/config.txt"
# Fallback für ältere Versionen
[ ! -f "$CONFIG_FILE" ] && CONFIG_FILE="/boot/config.txt"

# SPI aktivieren
if ! grep -q "^dtparam=spi=on" "$CONFIG_FILE"; then
    echo "dtparam=spi=on" >> "$CONFIG_FILE"
fi

# I2S / DABBoard Overlay aktivieren
if ! grep -q "^dtoverlay=ugreen-dabboard" "$CONFIG_FILE"; then
    echo "dtoverlay=ugreen-dabboard" >> "$CONFIG_FILE"
fi

# I2C aktivieren (für Volume-Control)
if ! grep -q "^dtparam=i2c_arm=on" "$CONFIG_FILE"; then
    echo "dtparam=i2c_arm=on" >> "$CONFIG_FILE"
fi

# Onboard Audio deaktivieren (I2S Konflikt vermeiden)
if grep -q "^dtparam=audio=on" "$CONFIG_FILE"; then
    sed -i 's/^dtparam=audio=on/dtparam=audio=off/' "$CONFIG_FILE"
fi

echo "📡 [4/9] WLAN-Konfiguration prüfen..."
# Prüfe ob bereits WLAN konfiguriert ist (z.B. via Raspberry Pi Imager)
if [ -f /etc/wpa_supplicant/wpa_supplicant.conf ] && grep -q "network=" /etc/wpa_supplicant/wpa_supplicant.conf; then
    echo "   ✅ Bestehende WLAN-Konfiguration gefunden - wird verwendet"
else
    echo "   ⚠️  Keine WLAN-Konfiguration gefunden!"
    echo "   Bitte WLAN über Raspberry Pi Imager konfigurieren oder manuell einrichten."
    echo "   Installation wird fortgesetzt, aber WLAN muss nachträglich konfiguriert werden."
fi

echo "🔵 [5/9] Bluetooth konfigurieren..."
# Bluetooth Auto-Power
if ! grep -q "AutoEnable=true" /etc/bluetooth/main.conf 2>/dev/null; then
    sed -i 's/^#AutoEnable.*/AutoEnable=true/' /etc/bluetooth/main.conf 2>/dev/null || true
fi

systemctl enable bluetooth
systemctl enable pulseaudio

echo "🐍 [6/9] Python App installieren..."
mkdir -p "$APP_DIR"
cp -r "$SCRIPT_DIR/app/"* "$APP_DIR/"

# Python Virtual Environment
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --quiet flask

echo "⚙️  [7/9] Systemd Service einrichten..."
cp "$SCRIPT_DIR/config/dabradio.service" /etc/systemd/system/dabradio.service
systemctl daemon-reload
systemctl enable dabradio

echo "📁 [8/9] Datenverzeichnis erstellen..."
mkdir -p /var/lib/dab-radio

# Erstelle Standard-Netzwerkkonfiguration
cat > /var/lib/dab-radio/network.json << 'EOF'
{
  "mode": "client",
  "ap_ssid": "DAB-Radio",
  "ap_password": "dabradio123",
  "client_ssid": "",
  "client_password": "",
  "fallback_enabled": true,
  "default_volume": 40
}
EOF

chown -R pi:pi /var/lib/dab-radio

# Create music storage directory
mkdir -p /var/lib/dab-radio/music
chown pi:pi /var/lib/dab-radio/music

# Create initial empty albums.json
cat > /var/lib/dab-radio/albums.json << 'EOF'
{
  "albums": []
}
EOF
chown pi:pi /var/lib/dab-radio/albums.json

# Create initial playback settings
cat > /var/lib/dab-radio/playback_settings.json << 'EOF'
{
  "mode": "off",
  "auto_start_on_boot": false
}
EOF
chown pi:pi /var/lib/dab-radio/playback_settings.json

echo "📶 [9/9] WiFi Access Point vorbereiten (NICHT aktiviert)..."
# hostapd Konfiguration vorbereiten
cp "$SCRIPT_DIR/config/hostapd.conf" /etc/hostapd/hostapd.conf

# hostapd als DAEMON_CONF setzen
if [ -f /etc/default/hostapd ]; then
    sed -i 's|^#DAEMON_CONF=.*|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' /etc/default/hostapd
fi

# dnsmasq Konfiguration
cp "$SCRIPT_DIR/config/dnsmasq.conf" /etc/dnsmasq.d/dabradio.conf

# Statische IP Konfiguration vorbereiten (aber nicht aktiv)
if [ -d /etc/dhcpcd.conf.d ]; then
    cp "$SCRIPT_DIR/config/dhcpcd.conf" /etc/dhcpcd.conf.d/dabradio.conf.disabled
else
    # Für später, falls AP-Modus aktiviert wird
    cp "$SCRIPT_DIR/config/dhcpcd.conf" /etc/dabradio-ap.conf
fi

# Hostapd & dnsmasq NICHT aktivieren (nur vorbereiten)
systemctl unmask hostapd
systemctl unmask dnsmasq
systemctl disable hostapd
systemctl disable dnsmasq

echo "   ✅ AP-Modus vorbereitet, aber deaktiviert"
echo "   ℹ️  AP kann später über Web-Interface aktiviert werden"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║          ✅ Installation fertig!          ║"
echo "╠══════════════════════════════════════════╣"
echo "║  Bitte jetzt neustarten: sudo reboot     ║"
echo "║                                          ║"
echo "║  Nach dem Neustart:                      ║"
echo "║  Pi verbindet sich mit konfiguriertem    ║"
echo "║  WLAN (vom Raspberry Pi Imager)          ║"
echo "║                                          ║"
echo "║  SSH via Hostname:                       ║"
echo "║  ssh pi@raspberrypi.local                ║"
echo "╚══════════════════════════════════════════╝"
