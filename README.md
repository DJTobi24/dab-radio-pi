# 🎵 Pi Zero DAB+ Bluetooth Radio

DAB+ Radio mit Bluetooth-Audioausgabe und Web-Interface für Raspberry Pi Zero WH + uGreen DAB Board v9.

## Features

- **DAB+ Empfang** über uGreen DAB Board (Si4684/Si4688)
- **Bluetooth Audio** Ausgabe an beliebige Bluetooth-Box
- **WiFi Access Point** — Pi erstellt eigenes WLAN
- **Web-Interface** — Sender scannen, auswählen, Lautstärke, Favoriten
- **Autostart** — Radio startet automatisch beim Booten

## Hardware

- Raspberry Pi Zero WH
- uGreen DAB Board v9
- DAB-Antenne (Wire oder SMA)
- Bluetooth-Lautsprecher

## 🚀 Quick Install (One Command)

> **⚠️ Wichtig:** Verwende **Raspberry Pi OS (Legacy, 32-bit) Lite - Bookworm**
> Der Pi Zero WH hat einen ARMv6-Prozessor und benötigt die Legacy-Version!
> Trixie und Standard-Bookworm funktionieren NICHT auf dem Pi Zero!

Flashe Raspberry Pi OS Lite (32-bit, Legacy) auf eine SD-Karte, aktiviere SSH, verbinde dich mit dem Pi und führe aus:

```bash
curl -sSL https://raw.githubusercontent.com/djtobi24/dab-radio-pi/main/bootstrap.sh | sudo bash
```

Das wars! Nach einem Reboot (`sudo reboot`) verbinde dich mit dem WLAN **"DAB-Radio"** (Passwort: `dabradio123`) und öffne **http://10.0.0.1** im Browser.

---

## 📖 Manuelle Installation

<details>
<summary>Klicke hier für detaillierte Installationsschritte</summary>

### 1. Raspberry Pi OS auf SD-Karte flashen

Verwende den **Raspberry Pi Imager**:

1. **Raspberry Pi Device**: Wähle `Raspberry Pi Zero`
2. **Operating System**: Wähle `Raspberry Pi OS (Legacy, 32-bit) Lite`
   - Das ist Debian Bookworm für ARMv6
3. **Storage**: Wähle deine SD-Karte

> ⚠️ **Nicht verwenden:**
> - ❌ Raspberry Pi OS (32-bit) - Trixie (funktioniert nicht auf Pi Zero!)
> - ❌ Raspberry Pi OS (64-bit) (Pi Zero ist 32-bit only)
> - ✅ Nur **Legacy, 32-bit, Lite** mit Bookworm!

### 2. SSH aktivieren

Erstelle eine leere Datei `ssh` auf der Boot-Partition.

### 3. Erstmalige Verbindung per USB-OTG oder Ethernet

```bash
ssh pi@raspberrypi.local
```

### 4. Repository klonen

```bash
git clone https://github.com/djtobi24/dab-radio-pi.git
cd dab-radio-pi
```

### 5. Installer ausführen

```bash
chmod +x install.sh
sudo ./install.sh
```

### 6. Neustart

```bash
sudo reboot
```

### 7. Verbinden

Verbinde dich mit dem WLAN **"DAB-Radio"** (Passwort: `dabradio123`).
Öffne im Browser: **http://10.0.0.1**

</details>

## Dateistruktur

```
dab-radio/
├── install.sh              # Hauptinstaller
├── config/
│   ├── hostapd.conf        # WiFi AP Konfiguration
│   ├── dnsmasq.conf        # DHCP Server
│   ├── dhcpcd.conf         # Statische IP für wlan0
│   └── dabradio.service    # Systemd Service
├── app/
│   ├── server.py           # Flask Web-Server + Radio-Backend
│   ├── radio_control.py    # radio_cli Wrapper
│   ├── bt_manager.py       # Bluetooth Manager
│   ├── static/
│   │   ├── app.css         # Styles
│   │   └── app.js          # Frontend JS
│   └── templates/
│       └── index.html       # Web UI
└── README.md
```

## Konfiguration ändern

### WLAN-Name/Passwort

Editiere `config/hostapd.conf`:
```
ssid=DAB-Radio
wpa_passphrase=dabradio123
```

### Standard-Lautstärke

In `app/server.py` → `DEFAULT_VOLUME = 40`
