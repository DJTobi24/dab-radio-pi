# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

DAB+ Radio with Bluetooth audio output and web interface for Raspberry Pi Zero WH + uGreen DAB Board v9. The system creates a WiFi access point and serves a web interface for controlling DAB radio playback over Bluetooth speakers.

## Architecture

### Core Components

**Backend (Python/Flask):**
- [server.py](app/server.py) - Flask web server with REST API endpoints for radio control, Bluetooth management, and favorites
- [radio_control.py](app/radio_control.py) - Wrapper around uGreen's `radio_cli` binary. Manages DAB tuning, station scanning, and audio routing
- [bt_manager.py](app/bt_manager.py) - Bluetooth device management via `bluetoothctl` commands

**Frontend:**
- Vanilla JavaScript ([app.js](app/static/app.js)) with REST API polling
- Three-tab interface: Favorites, Stations, Bluetooth devices

### Audio Pipeline

DAB Board (I2S) → `arecord` → `aplay` → BlueALSA → Bluetooth Speaker

The audio routing is handled in [radio_control.py:187-203](app/radio_control.py#L187-L203) using subprocess pipes between `arecord` (capturing from I2S) and `aplay` (outputting to BlueALSA).

### External Dependencies

- **radio_cli** - uGreen's proprietary binary for Si4684/Si4688 DAB chipset control (installed to `/usr/local/sbin/radio_cli`)
- Commands are executed via sudo subprocess calls in [radio_control.py:34-55](app/radio_control.py#L34-L55)

### Data Persistence

All data stored in `/var/lib/dab-radio/`:
- `stations.json` - Cached DAB station scan results
- `favorites.json` - User-saved favorite stations
- `bluetooth.json` - Last connected Bluetooth device for auto-reconnect

### Deployment

- Runs as systemd service (`dabradio.service`) as root user
- Installed to `/opt/dab-radio` with Python venv
- Creates WiFi AP (SSID: "DAB-Radio") via hostapd + dnsmasq
- Web interface accessible at http://10.0.0.1 (port 80)

## Development Commands

### Installation
```bash
sudo ./install.sh
```
Installs system dependencies, uGreen DAB software, configures WiFi AP, Bluetooth, and systemd service.

### Service Management
```bash
# View service status
sudo systemctl status dabradio

# Restart service after code changes
sudo systemctl restart dabradio

# View live logs
sudo journalctl -u dabradio -f

# Stop service for manual testing
sudo systemctl stop dabradio
```

### Manual Testing
```bash
# Run Flask server directly (for debugging)
cd /opt/dab-radio
sudo venv/bin/python server.py

# Test radio_cli manually
sudo /usr/local/sbin/radio_cli -b D -u    # Boot DAB and scan stations
sudo /usr/local/sbin/radio_cli -b D -o 1 -f 21686 -e 49001 -c 1 -p    # Tune to specific station

# Test Bluetooth
bluetoothctl
> scan on
> devices
> pair [MAC]
> connect [MAC]

# Check audio routing
arecord -D sysdefault:CARD=dabboard -c 2 -r 48000 -f S16_LE -t raw | aplay -D bluealsa:DEV=[BT_MAC],PROFILE=a2dp
```

### Configuration Files
- [config/hostapd.conf](config/hostapd.conf) - WiFi AP settings (SSID, password)
- [config/dnsmasq.conf](config/dnsmasq.conf) - DHCP server for WiFi clients
- [config/dabradio.service](config/dabradio.service) - Systemd service definition
- `/boot/firmware/config.txt` - Raspberry Pi hardware config (SPI, I2S, DAB overlay)

## Key Technical Details

### DAB Station Scanning
Station scan ([radio_control.py:62-102](app/radio_control.py#L62-L102)) uses `radio_cli -b D -u -k` which:
1. Boots DAB firmware on Si468x chip
2. Scans all DAB frequencies
3. Outputs ensemble/service data to `ensemblescan_*.json`
4. Parser extracts station metadata (name, service_id, component_id, ensemble_id, frequency)

### Station Tuning
Tuning requires ([radio_control.py:151-185](app/radio_control.py#L151-L185)):
- Frequency index
- Ensemble ID
- Component ID (audio service within ensemble)
- Output mode (-o 1 for I2S)

### Bluetooth Audio
Audio streaming only starts after:
1. DAB station is tuned (outputs to I2S)
2. Bluetooth device is connected
3. Audio pipeline is spawned as subprocess ([radio_control.py:187-203](app/radio_control.py#L187-L203))

### Threading
- Scan operations run in background threads to avoid blocking Flask
- Audio process runs as separate subprocess
- Lock (`_lock`) protects audio process state in RadioControl

## Hardware Requirements

- Raspberry Pi Zero WH (32-bit ARMv6)
- uGreen DAB Board v9 (Si4684/Si4688 chipset)
- SPI enabled (DAB board communication)
- I2S enabled (digital audio from DAB board)
- I2C enabled (volume control)
- Onboard audio must be disabled (conflicts with I2S)

## Common Issues

- **No audio**: Check Bluetooth connection state and ensure audio process is running (`ps aux | grep arecord`)
- **Scan fails**: Ensure DAB antenna is connected and SPI/I2S are enabled in `/boot/firmware/config.txt`
- **Can't connect to WiFi AP**: Check hostapd service status, ensure NetworkManager is disabled
- **Bluetooth pairing fails**: Check BlueALSA service is running (`systemctl status bluealsa`)
