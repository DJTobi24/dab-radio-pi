#!/usr/bin/env python3
"""
server.py — DAB+ Bluetooth Radio Web-Server
Flask-basiertes Web-Interface zur Steuerung des uGreen DAB Boards
mit Bluetooth-Audio-Ausgabe.
"""

from flask import Flask, render_template, jsonify, request
from radio_control import RadioControl
from bt_manager import BluetoothManager
import threading
import time
import os
import sys

app = Flask(__name__,
            template_folder="templates",
            static_folder="static")

radio = RadioControl()
bt = BluetoothManager()

DEFAULT_VOLUME = 40


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
    """Alle bekannten Bluetooth-Geräte."""
    devices = bt.get_devices()
    paired = bt.get_paired_devices()
    paired_macs = {d["mac"] for d in paired}

    for d in devices:
        d["paired"] = d["mac"] in paired_macs

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

    success = bt.connect(mac)

    # Falls Radio gerade spielt, Audio-Stream neu starten
    if success and radio.is_playing:
        radio.start_bluetooth_audio(mac)

    return jsonify({"connected": success, "mac": mac})


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


# ─── Startup ─────────────────────────────────────────

def startup_tasks():
    """Hintergrund-Tasks beim Start."""
    time.sleep(3)
    bt.power_on()
    # Auto-Reconnect zum letzten BT-Gerät
    bt.auto_reconnect()


if __name__ == "__main__":
    # Startup im Hintergrund
    threading.Thread(target=startup_tasks, daemon=True).start()

    app.run(
        host="0.0.0.0",
        port=80,
        debug=False,
        threaded=True
    )
