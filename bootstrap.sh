#!/bin/bash
set -e

echo "╔══════════════════════════════════════════╗"
echo "║   Pi Zero DAB+ Radio - Quick Install     ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# Prüfe ob als root ausgeführt
if [ "$EUID" -ne 0 ]; then
    echo "❌ Bitte als root ausführen:"
    echo "   curl -sSL https://raw.githubusercontent.com/djtobi24/dab-radio-pi/main/bootstrap.sh | sudo bash"
    exit 1
fi

REPO_URL="https://github.com/djtobi24/dab-radio-pi.git"
INSTALL_DIR="/tmp/dab-radio-pi"

echo "📥 [1/3] Repository klonen..."
# Entferne altes Verzeichnis falls vorhanden
rm -rf "$INSTALL_DIR"

# Git installieren falls nicht vorhanden
if ! command -v git &> /dev/null; then
    echo "   Git wird installiert..."
    apt-get update -qq
    apt-get install -y -qq git
fi

# Repository klonen
git clone "$REPO_URL" "$INSTALL_DIR"

echo "🚀 [2/3] Installer starten..."
cd "$INSTALL_DIR"
chmod +x install.sh
./install.sh

echo ""
echo "✅ [3/3] Installation abgeschlossen!"
echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  Bitte jetzt neustarten: sudo reboot     ║"
echo "║                                          ║"
echo "║  Nach dem Neustart:                      ║"
echo "║  Pi verbindet sich mit konfiguriertem    ║"
echo "║  WLAN (falls im Imager eingerichtet)     ║"
echo "║  SSH via: ssh pi@raspberrypi.local       ║"
echo "╚══════════════════════════════════════════╝"
