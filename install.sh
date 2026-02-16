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

echo "📦 [1/8] System-Pakete aktualisieren & installieren..."
apt-get update -qq
apt-get install -y -qq \
    python3 python3-pip python3-venv \
    hostapd dnsmasq \
    bluez bluez-tools bluealsa \
    alsa-utils \
    libncurses5 \
    unzip wget \
    iptables

echo "📻 [2/8] uGreen DAB Board Software herunterladen..."
if [ ! -d "/usr/local/lib/ugreen-dab+" ]; then
    cd /tmp
    wget -q https://ugreen.eu/wp-content/uploads/files/Files_v12.zip -O Files_v12.zip
    unzip -o -q Files_v12.zip -d /usr/local/lib/
    mv /usr/local/lib/Files_v12 /usr/local/lib/ugreen-dab+
    # Symlinks für radio_cli (32-bit für Pi Zero)
    ln -sf /usr/local/lib/ugreen-dab+/radio_cli_v3.1.0 /usr/local/sbin/radio_cli
    chmod +x /usr/local/sbin/radio_cli
    echo "   ✅ radio_cli installiert"
else
    echo "   ✅ uGreen Software bereits vorhanden"
fi

echo "🔧 [3/8] SPI & I2S aktivieren..."
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

echo "📶 [4/8] WiFi Access Point konfigurieren..."
# hostapd Konfiguration
cp "$SCRIPT_DIR/config/hostapd.conf" /etc/hostapd/hostapd.conf

# hostapd als DAEMON_CONF setzen
if [ -f /etc/default/hostapd ]; then
    sed -i 's|^#DAEMON_CONF=.*|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' /etc/default/hostapd
fi

# dnsmasq Konfiguration
cp "$SCRIPT_DIR/config/dnsmasq.conf" /etc/dnsmasq.d/dabradio.conf

# Statische IP für wlan0
cp "$SCRIPT_DIR/config/dhcpcd.conf" /etc/dhcpcd.conf.d/dabradio.conf 2>/dev/null || true
# Falls dhcpcd.conf.d nicht existiert, direkt in dhcpcd.conf schreiben
if [ ! -d /etc/dhcpcd.conf.d ]; then
    if ! grep -q "# DAB Radio AP" /etc/dhcpcd.conf; then
        cat "$SCRIPT_DIR/config/dhcpcd.conf" >> /etc/dhcpcd.conf
    fi
fi

# NetworkManager deaktivieren falls vorhanden (stört hostapd)
if systemctl is-active --quiet NetworkManager 2>/dev/null; then
    systemctl stop NetworkManager
    systemctl disable NetworkManager
fi

# Hostapd & dnsmasq aktivieren
systemctl unmask hostapd
systemctl enable hostapd
systemctl enable dnsmasq

echo "🔵 [5/8] Bluetooth konfigurieren..."
# BlueALSA Service konfigurieren
mkdir -p /etc/systemd/system/bluealsa.service.d/
cat > /etc/systemd/system/bluealsa.service.d/override.conf << 'EOF'
[Service]
ExecStart=
ExecStart=/usr/bin/bluealsa -p a2dp-source
EOF

# Bluetooth Auto-Power
if ! grep -q "AutoEnable=true" /etc/bluetooth/main.conf 2>/dev/null; then
    sed -i 's/^#AutoEnable.*/AutoEnable=true/' /etc/bluetooth/main.conf 2>/dev/null || true
fi

systemctl enable bluetooth
systemctl enable bluealsa

echo "🐍 [6/8] Python App installieren..."
mkdir -p "$APP_DIR"
cp -r "$SCRIPT_DIR/app/"* "$APP_DIR/"

# Python Virtual Environment
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --quiet flask

echo "⚙️  [7/8] Systemd Service einrichten..."
cp "$SCRIPT_DIR/config/dabradio.service" /etc/systemd/system/dabradio.service
systemctl daemon-reload
systemctl enable dabradio

echo "📁 [8/8] Datenverzeichnis erstellen..."
mkdir -p /var/lib/dab-radio
chown pi:pi /var/lib/dab-radio

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║          ✅ Installation fertig!          ║"
echo "╠══════════════════════════════════════════╣"
echo "║  Bitte jetzt neustarten: sudo reboot     ║"
echo "║                                          ║"
echo "║  Danach:                                 ║"
echo "║  1. WLAN 'DAB-Radio' verbinden           ║"
echo "║     Passwort: dabradio123                ║"
echo "║  2. Browser: http://10.0.0.1             ║"
echo "╚══════════════════════════════════════════╝"
