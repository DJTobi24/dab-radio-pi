"""
playback_controller.py — Playback Mode Controller
Orchestrates playback modes: DAB radio, music albums, and auto-start behavior.
"""

import os
import json
import threading

DATA_DIR = "/var/lib/dab-radio"
SETTINGS_FILE = os.path.join(DATA_DIR, "playback_settings.json")


class PlaybackController:
    """
    Manages playback modes and auto-start behavior.

    Modes:
        - off: No auto-start
        - dab_live: Resume last DAB station
        - dab_preset: Play specific preset station
        - album: Play specific preset album
        - album_random: Play random album
    """

    MODES = ["off", "dab_live", "dab_preset", "album", "album_random"]

    def __init__(self, radio_control, music_manager, bluetooth_manager):
        """
        Initialize playback controller.

        Args:
            radio_control: RadioControl instance
            music_manager: MusicManager instance
            bluetooth_manager: BluetoothManager instance
        """
        self.radio = radio_control
        self.music = music_manager
        self.bt = bluetooth_manager
        self._lock = threading.Lock()

        self.settings = {
            "mode": "off",
            "preset_station": None,
            "preset_album_id": None,
            "auto_start_on_boot": False,
            "last_played_album": None,
            "last_played_track_index": 0
        }

        self._load_settings()

    def _load_settings(self):
        """Load playback settings from JSON file."""
        try:
            with open(SETTINGS_FILE, "r") as f:
                loaded = json.load(f)
                self.settings.update(loaded)
        except (IOError, json.JSONDecodeError):
            # File doesn't exist or is invalid, use defaults
            self._save_settings()

    def _save_settings(self):
        """Save playback settings to JSON file."""
        try:
            with open(SETTINGS_FILE, "w") as f:
                json.dump(self.settings, f, indent=2)
        except IOError:
            pass

    def get_settings(self):
        """
        Get current playback settings.

        Returns:
            dict: Current settings
        """
        return self.settings.copy()

    def set_mode(self, mode, **kwargs):
        """
        Set playback mode and configuration.

        Args:
            mode: Playback mode (one of MODES)
            **kwargs: Additional mode-specific settings:
                - preset_station: Station dict for dab_preset mode
                - preset_album_id: Album ID for album mode
                - auto_start_on_boot: bool

        Returns:
            bool: True if mode was set successfully
        """
        with self._lock:
            if mode not in self.MODES:
                return False

            self.settings["mode"] = mode

            # Update mode-specific settings
            if "preset_station" in kwargs:
                self.settings["preset_station"] = kwargs["preset_station"]

            if "preset_album_id" in kwargs:
                self.settings["preset_album_id"] = kwargs["preset_album_id"]

            if "auto_start_on_boot" in kwargs:
                self.settings["auto_start_on_boot"] = bool(kwargs["auto_start_on_boot"])

            self._save_settings()
            return True

    def start_playback(self):
        """
        Start playback based on configured mode.

        This is typically called on system startup if auto_start_on_boot is True.

        Returns:
            bool: True if playback started successfully
        """
        with self._lock:
            mode = self.settings.get("mode", "off")
            auto_start = self.settings.get("auto_start_on_boot", False)

            # Don't start if auto-start disabled
            if not auto_start or mode == "off":
                return False

            # Check if Bluetooth device is connected
            bt_mac = self.bt.get_connected_device()
            if not bt_mac:
                # No Bluetooth device connected, cannot play audio
                return False

            try:
                if mode == "dab_live":
                    # Resume last DAB station if available
                    if self.radio.current_station:
                        self.radio.start_bluetooth_audio(bt_mac)
                        return True
                    return False

                elif mode == "dab_preset":
                    # Play preset station
                    station = self.settings.get("preset_station")
                    if not station:
                        return False

                    success = self.radio.tune_station(station)
                    if success:
                        self.radio.start_bluetooth_audio(bt_mac)
                        return True
                    return False

                elif mode == "album":
                    # Play preset album
                    album_id = self.settings.get("preset_album_id")
                    if not album_id:
                        return False

                    track_index = self.settings.get("last_played_track_index", 0)
                    return self._play_album_internal(album_id, track_index, bt_mac)

                elif mode == "album_random":
                    # Play random album
                    album = self.music.get_random_album()
                    if not album:
                        return False

                    return self._play_album_internal(album["id"], 0, bt_mac)

            except Exception:
                return False

            return False

    def _play_album_internal(self, album_id, track_index, bt_mac):
        """
        Internal method to play an album track.

        Args:
            album_id: Album ID
            track_index: Track index to play
            bt_mac: Bluetooth device MAC address

        Returns:
            bool: True if playback started
        """
        track_path = self.music.get_track_path(album_id, track_index)
        if not track_path:
            return False

        # Get album and track metadata
        album = self.music.get_album(album_id)
        track_title = None
        total_tracks = 0
        album_name = ""
        if album:
            album_name = album.get("name", "")
            total_tracks = len(album.get("tracks", []))
            tracks = album.get("tracks", [])
            if track_index < len(tracks):
                track_title = tracks[track_index].get("title")

        # Use radio control to play music file
        success = self.radio.start_music_playback(
            track_path, bt_mac,
            track_title=track_title,
            album_name=album_name,
            album_id=album_id,
            track_index=track_index,
            total_tracks=total_tracks,
        )

        if success:
            # Update last played info
            self.settings["last_played_album"] = album_id
            self.settings["last_played_track_index"] = track_index
            self._save_settings()

        return success

    def play_album(self, album_id, track_index=0):
        """
        Play an album from the specified track.

        Args:
            album_id: Album ID
            track_index: Track index to start from (default: 0)

        Returns:
            bool: True if playback started
        """
        bt_mac = self.bt.get_connected_device()
        if not bt_mac:
            return False

        return self._play_album_internal(album_id, track_index, bt_mac)

    def set_preset_station(self, station):
        """
        Set preset station for dab_preset mode.

        Args:
            station: Station dict with service_id, ensemble_id, etc.

        Returns:
            bool: True if set successfully
        """
        with self._lock:
            if not station:
                return False

            self.settings["preset_station"] = station
            self._save_settings()
            return True

    def set_preset_album(self, album_id):
        """
        Set preset album for album mode.

        Args:
            album_id: Album ID

        Returns:
            bool: True if set successfully
        """
        with self._lock:
            if not album_id:
                return False

            # Verify album exists
            album = self.music.get_album(album_id)
            if not album:
                return False

            self.settings["preset_album_id"] = album_id
            self._save_settings()
            return True
