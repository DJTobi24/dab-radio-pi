#!/usr/bin/env python3
"""
server.py — DAB+ Bluetooth Radio Web-Server
Flask-basiertes Web-Interface zur Steuerung des uGreen DAB Boards
mit Bluetooth-Audio-Ausgabe.
"""

from flask import Flask, render_template, jsonify, request
from radio_control import RadioControl
from bt_manager import BluetoothManager
from wifi_manager import WiFiManager
from music_manager import MusicManager
from storage_monitor import StorageMonitor
from playback_controller import PlaybackController
import threading
import time
import os
import sys

app = Flask(__name__,
            template_folder="templates",
            static_folder="static")

radio = RadioControl()
bt = BluetoothManager()
wifi = WiFiManager()
music = MusicManager()
storage = StorageMonitor()
playback = PlaybackController(radio, music, bt)

DEFAULT_VOLUME = wifi.get_default_volume()


# ─── Seiten ──────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


# ─── Radio API ───────────────────────────────────────

@app.route("/api/status")
def api_status():
    """Gesamtstatus (Radio + Bluetooth)."""
    return jsonify({
        "radio": radio.get_status(),
        "bluetooth": bt.get_status(),
    })


@app.route("/api/scan", methods=["POST"])
def api_scan():
    """DAB-Sendersuchlauf starten."""
    def _do_scan():
        radio.scan_stations()

    thread = threading.Thread(target=_do_scan, daemon=True)
    thread.start()
    return jsonify({"status": "scanning"})


@app.route("/api/scan/status")
def api_scan_status():
    """Scan-Status und gefundene Sender."""
    return jsonify({
        "stations": radio.stations,
        "count": len(radio.stations),
    })


@app.route("/api/stations")
def api_stations():
    """Alle bekannten Sender."""
    return jsonify({"stations": radio.stations})


@app.route("/api/play", methods=["POST"])
def api_play():
    """Sender abspielen."""
    station = request.json
    if not station:
        return jsonify({"error": "Kein Sender angegeben"}), 400

    # Zuerst auf den Sender tunen
    success = radio.tune_station(station)
    if not success:
        return jsonify({"error": "Tuning fehlgeschlagen"}), 500

    # Dann Audio via Bluetooth starten (falls BT verbunden)
    bt_mac = bt.get_connected_device()
    if bt_mac:
        radio.start_bluetooth_audio(bt_mac)

    return jsonify({"status": "playing", "station": station})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    """Wiedergabe stoppen."""
    radio.stop()
    return jsonify({"status": "stopped"})


@app.route("/api/volume", methods=["POST"])
def api_volume():
    """Lautstärke setzen."""
    data = request.json or {}
    level = data.get("level", DEFAULT_VOLUME)
    actual = radio.set_volume(level)
    return jsonify({"volume": actual})


@app.route("/api/volume", methods=["GET"])
def api_get_volume():
    return jsonify({"volume": radio.volume})


# ─── Favoriten API ───────────────────────────────────

@app.route("/api/favorites")
def api_favorites():
    """Alle Favoriten."""
    return jsonify({"favorites": radio.get_favorites()})


@app.route("/api/favorites", methods=["POST"])
def api_add_favorite():
    """Favorit hinzufügen."""
    station = request.json
    if not station:
        return jsonify({"error": "Kein Sender"}), 400
    added = radio.add_favorite(station)
    return jsonify({"added": added, "favorites": radio.get_favorites()})


@app.route("/api/favorites/<int:idx>", methods=["DELETE"])
def api_remove_favorite(idx):
    """Favorit entfernen."""
    removed = radio.remove_favorite(idx)
    return jsonify({"removed": removed, "favorites": radio.get_favorites()})


# ─── Bluetooth API ───────────────────────────────────

@app.route("/api/bt/devices")
def api_bt_devices():
    """Alle bekannten Bluetooth-Geräte (mit paired/connected Status)."""
    devices = bt.get_devices()
    return jsonify({"devices": devices})


@app.route("/api/bt/scan", methods=["POST"])
def api_bt_scan():
    """Bluetooth-Scan starten."""
    bt.start_scan(duration=12)
    return jsonify({"status": "scanning"})


@app.route("/api/bt/scan/status")
def api_bt_scan_status():
    """Bluetooth-Scan Status."""
    return jsonify({"scanning": bt.scanning})


@app.route("/api/bt/connect", methods=["POST"])
def api_bt_connect():
    """Mit Bluetooth-Gerät verbinden."""
    data = request.json or {}
    mac = data.get("mac")
    if not mac:
        return jsonify({"error": "Keine MAC-Adresse"}), 400

    result = bt.connect(mac)

    # Falls Radio gerade spielt, Audio-Stream neu starten
    if result["success"] and radio.is_playing:
        radio.start_bluetooth_audio(mac)

    return jsonify({
        "connected": result["success"],
        "mac": mac,
        "name": result.get("name"),
        "message": result.get("message", ""),
    })


@app.route("/api/bt/disconnect", methods=["POST"])
def api_bt_disconnect():
    """Bluetooth-Gerät trennen."""
    radio.stop_audio()
    bt.disconnect()
    return jsonify({"status": "disconnected"})


@app.route("/api/bt/remove", methods=["POST"])
def api_bt_remove():
    """Bluetooth-Gerät entfernen."""
    data = request.json or {}
    mac = data.get("mac")
    if mac:
        radio.stop_audio()
        bt.remove_device(mac)
    return jsonify({"status": "removed"})


# ─── Network/WiFi API ────────────────────────────────

@app.route("/api/network/status")
def api_network_status():
    """Netzwerkstatus abrufen."""
    return jsonify(wifi.get_status())


@app.route("/api/wifi/scan", methods=["POST"])
def api_wifi_scan():
    """WLAN-Netzwerke scannen."""
    def _do_scan():
        return wifi.scan_networks()

    # Scan in background thread
    networks = []
    def scan_thread():
        nonlocal networks
        networks = wifi.scan_networks()

    thread = threading.Thread(target=scan_thread, daemon=True)
    thread.start()
    thread.join(timeout=15)  # Wait max 15 seconds

    return jsonify({"networks": networks})


@app.route("/api/wifi/connect", methods=["POST"])
def api_wifi_connect():
    """Mit WLAN-Netzwerk verbinden."""
    data = request.json or {}
    ssid = data.get("ssid")
    password = data.get("password")

    if not ssid:
        return jsonify({"error": "Kein SSID angegeben"}), 400

    success = wifi.connect_to_network(ssid, password)
    return jsonify({"connected": success})


@app.route("/api/wifi/disconnect", methods=["POST"])
def api_wifi_disconnect():
    """Zurück zum AP-Modus wechseln."""
    wifi.switch_to_ap_mode()
    return jsonify({"status": "ap_mode"})


# ─── Settings API ────────────────────────────────────

@app.route("/api/settings")
def api_get_settings():
    """Alle Einstellungen abrufen."""
    ap_config = wifi.get_ap_config()
    return jsonify({
        "ap_ssid": ap_config["ssid"],
        "ap_password": ap_config["password"],
        "mode": wifi.mode,
        "fallback_enabled": wifi.is_fallback_enabled(),
        "default_volume": wifi.get_default_volume()
    })


@app.route("/api/settings/ap", methods=["POST"])
def api_update_ap_settings():
    """AP-Einstellungen aktualisieren."""
    data = request.json or {}
    ssid = data.get("ssid")
    password = data.get("password")

    if not ssid or not password:
        return jsonify({"error": "SSID und Passwort erforderlich"}), 400

    success, message = wifi.set_ap_config(ssid, password)
    if success:
        return jsonify({"status": "success", "message": message})
    else:
        return jsonify({"error": message}), 400


@app.route("/api/settings/volume", methods=["POST"])
def api_set_default_volume():
    """Standard-Lautstärke setzen."""
    data = request.json or {}
    volume = data.get("volume", 40)
    wifi.set_default_volume(volume)
    global DEFAULT_VOLUME
    DEFAULT_VOLUME = volume
    return jsonify({"volume": volume})


@app.route("/api/settings/fallback", methods=["POST"])
def api_set_fallback():
    """Fallback-Modus aktivieren/deaktivieren."""
    data = request.json or {}
    enabled = data.get("enabled", True)
    wifi.set_fallback_enabled(enabled)
    return jsonify({"fallback_enabled": enabled})


# ─── Music API ───────────────────────────────────────

@app.route("/api/albums")
def api_get_albums():
    """Alle Alben abrufen."""
    albums = music.get_albums()
    return jsonify({"albums": albums})


@app.route("/api/albums", methods=["POST"])
def api_create_album():
    """Neues Album erstellen."""
    data = request.json or {}
    name = data.get("name", "").strip()
    description = data.get("description", "").strip()

    if not name:
        return jsonify({"error": "Album-Name erforderlich"}), 400

    album = music.create_album(name, description)
    if album:
        return jsonify({"album": album})
    else:
        return jsonify({"error": "Album konnte nicht erstellt werden"}), 500


@app.route("/api/albums/<album_id>")
def api_get_album(album_id):
    """Album-Details abrufen."""
    album = music.get_album(album_id)
    if album:
        return jsonify({"album": album})
    else:
        return jsonify({"error": "Album nicht gefunden"}), 404


@app.route("/api/albums/<album_id>", methods=["DELETE"])
def api_delete_album(album_id):
    """Album löschen."""
    success = music.delete_album(album_id)
    if success:
        return jsonify({"status": "deleted"})
    else:
        return jsonify({"error": "Album nicht gefunden"}), 404


@app.route("/api/albums/<album_id>/upload", methods=["POST"])
def api_upload_tracks(album_id):
    """Musik-Dateien hochladen."""
    if 'files' not in request.files:
        return jsonify({"error": "Keine Dateien vorhanden"}), 400

    files = request.files.getlist('files')
    if not files:
        return jsonify({"error": "Keine Dateien ausgewählt"}), 400

    # Upload mit Storage-Check
    result = music.upload_tracks(album_id, files, storage_monitor=storage)

    if result["success"]:
        return jsonify(result)
    else:
        return jsonify(result), 400


@app.route("/api/albums/<album_id>/tracks/<track_id>", methods=["DELETE"])
def api_delete_track(album_id, track_id):
    """Track löschen."""
    success = music.delete_track(album_id, track_id)
    if success:
        return jsonify({"status": "deleted"})
    else:
        return jsonify({"error": "Track nicht gefunden"}), 404


@app.route("/api/albums/<album_id>/play", methods=["POST"])
def api_play_album(album_id):
    """Album abspielen."""
    data = request.json or {}
    track_index = data.get("track_index", 0)

    # Check BT connection
    bt_mac = bt.get_connected_device()
    if not bt_mac:
        return jsonify({"error": "Kein Bluetooth-Gerät verbunden"}), 400

    # Play album via playback controller
    success = playback.play_album(album_id, track_index)

    if success:
        return jsonify({"status": "playing", "album_id": album_id})
    else:
        return jsonify({"error": "Album konnte nicht abgespielt werden"}), 500


# ─── Storage API ─────────────────────────────────────

@app.route("/api/storage")
def api_get_storage():
    """Speicherplatz-Informationen abrufen."""
    info = storage.get_storage_info()
    if info:
        return jsonify(info)
    else:
        return jsonify({"error": "Speicherinformationen nicht verfügbar"}), 500


# ─── Playback Mode API ───────────────────────────────

@app.route("/api/playback/settings")
def api_get_playback_settings():
    """Wiedergabe-Einstellungen abrufen."""
    settings = playback.get_settings()
    return jsonify(settings)


@app.route("/api/playback/mode", methods=["POST"])
def api_set_playback_mode():
    """Wiedergabe-Modus setzen."""
    data = request.json or {}
    mode = data.get("mode", "off")

    if mode not in playback.MODES:
        return jsonify({"error": "Ungültiger Modus"}), 400

    # Extract mode-specific settings
    kwargs = {}
    if "preset_station" in data:
        kwargs["preset_station"] = data["preset_station"]
    if "preset_album_id" in data:
        kwargs["preset_album_id"] = data["preset_album_id"]
    if "auto_start_on_boot" in data:
        kwargs["auto_start_on_boot"] = data["auto_start_on_boot"]

    success = playback.set_mode(mode, **kwargs)

    if success:
        return jsonify({"status": "ok", "mode": mode})
    else:
        return jsonify({"error": "Modus konnte nicht gesetzt werden"}), 500


# ─── Enhanced Radio API ──────────────────────────────

@app.route("/api/stations/quality")
def api_stations_quality():
    """Sender mit Qualitäts-Metriken abrufen."""
    stations = radio.get_stations_with_quality()
    return jsonify({"stations": stations})


@app.route("/api/stations/quality/refresh", methods=["POST"])
def api_refresh_quality():
    """Qualitäts-Metriken aus letztem Scan extrahieren."""
    radio.extract_quality_metrics()
    return jsonify({"status": "ok"})


@app.route("/api/dab/board/status")
def api_dab_board_status():
    """DAB Board Status prüfen."""
    status = radio.check_board_detected()
    return jsonify(status)


# ─── Startup ─────────────────────────────────────────

def startup_tasks():
    """Hintergrund-Tasks beim Start."""
    time.sleep(3)
    bt.power_on()
    # Auto-Reconnect zum letzten BT-Gerät
    bt.auto_reconnect()
    # Set default volume
    radio.set_volume(DEFAULT_VOLUME)
    # Start playback if configured
    time.sleep(2)  # Wait for BT to connect
    playback.start_playback()


def network_monitor():
    """Überwacht Netzwerkverbindung und wechselt bei Bedarf zu AP-Modus."""
    while True:
        time.sleep(30)  # Check every 30 seconds

        if wifi.mode == "client" and wifi.is_fallback_enabled():
            # Check if we're still connected
            connected, _, _ = wifi._check_client_connection()

            if not connected:
                # Check internet connectivity
                has_internet = wifi.check_connectivity()

                if not has_internet:
                    print("⚠️ Client-Verbindung verloren, wechsle zu AP-Modus...")
                    wifi.switch_to_ap_mode()
                    time.sleep(10)  # Wait a bit after switching


if __name__ == "__main__":
    # Startup im Hintergrund
    threading.Thread(target=startup_tasks, daemon=True).start()

    # Network monitor im Hintergrund
    threading.Thread(target=network_monitor, daemon=True).start()

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False,
        threaded=True
    )
