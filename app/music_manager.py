"""
music_manager.py — Music Library Manager
Manages albums, tracks, file uploads, and music playback.
"""

import os
import json
import time
import threading
import shutil
import re
from werkzeug.utils import secure_filename

DATA_DIR = "/var/lib/dab-radio"
MUSIC_DIR = os.path.join(DATA_DIR, "music")
ALBUMS_FILE = os.path.join(DATA_DIR, "albums.json")

# Allowed audio file extensions
ALLOWED_EXTENSIONS = {".mp3", ".flac", ".ogg", ".wav", ".m4a", ".aac"}

# File size limits
MAX_FILE_SIZE_MB = 100
MAX_ALBUM_SIZE_MB = 2048  # 2GB per album


class MusicManager:
    def __init__(self):
        """Initialize music manager."""
        self.albums = []
        self._lock = threading.Lock()
        os.makedirs(MUSIC_DIR, exist_ok=True)
        self._load_albums()

    def _load_albums(self):
        """Load albums from JSON file."""
        try:
            with open(ALBUMS_FILE, "r") as f:
                data = json.load(f)
                self.albums = data.get("albums", [])
        except (IOError, json.JSONDecodeError):
            self.albums = []

    def _save_albums(self):
        """Save albums to JSON file."""
        try:
            with open(ALBUMS_FILE, "w") as f:
                json.dump({"albums": self.albums}, f, indent=2)
        except IOError:
            pass

    def _generate_id(self, prefix="album"):
        """Generate unique ID based on timestamp."""
        return f"{prefix}_{int(time.time() * 1000)}"

    def _sanitize_filename(self, filename):
        """
        Sanitize filename to prevent path traversal and shell injection.

        Args:
            filename: Original filename

        Returns:
            str: Sanitized filename safe for filesystem
        """
        # Use werkzeug's secure_filename
        safe_name = secure_filename(filename)

        # Additional sanitization: remove any remaining problematic chars
        safe_name = re.sub(r'[<>:"|?*]', '', safe_name)

        # Limit length
        name, ext = os.path.splitext(safe_name)
        if len(name) > 200:
            name = name[:200]

        return name + ext

    def _validate_file(self, file):
        """
        Validate uploaded file.

        Args:
            file: Werkzeug FileStorage object

        Returns:
            tuple: (is_valid, error_message)
        """
        if not file or not file.filename:
            return False, "Keine Datei ausgewählt"

        # Check file extension
        filename = file.filename.lower()
        ext = os.path.splitext(filename)[1]

        if ext not in ALLOWED_EXTENSIONS:
            return False, f"Dateityp {ext} nicht erlaubt. Erlaubt: {', '.join(ALLOWED_EXTENSIONS)}"

        # Note: Size validation happens during upload stream
        return True, None

    def create_album(self, name, description=""):
        """
        Create a new album.

        Args:
            name: Album name
            description: Optional description

        Returns:
            dict: Created album data, or None on failure
        """
        with self._lock:
            if not name or not name.strip():
                return None

            album_id = self._generate_id("album")
            album_dir = os.path.join(MUSIC_DIR, album_id)

            try:
                os.makedirs(album_dir, exist_ok=True)
            except OSError:
                return None

            album = {
                "id": album_id,
                "name": name.strip(),
                "description": description.strip(),
                "created": int(time.time()),
                "tracks": [],
                "track_count": 0,
                "total_size": 0,
                "cover_art": None
            }

            self.albums.append(album)
            self._save_albums()

            return album

    def get_albums(self):
        """
        Get all albums.

        Returns:
            list: List of all albums
        """
        return self.albums.copy()

    def get_album(self, album_id):
        """
        Get specific album by ID.

        Args:
            album_id: Album ID

        Returns:
            dict: Album data, or None if not found
        """
        for album in self.albums:
            if album["id"] == album_id:
                return album.copy()
        return None

    def delete_album(self, album_id):
        """
        Delete album and all its files.

        Args:
            album_id: Album ID

        Returns:
            bool: True if deleted successfully
        """
        with self._lock:
            # Find album
            album = None
            album_idx = None
            for idx, alb in enumerate(self.albums):
                if alb["id"] == album_id:
                    album = alb
                    album_idx = idx
                    break

            if not album:
                return False

            # Delete files from disk
            album_dir = os.path.join(MUSIC_DIR, album_id)
            try:
                if os.path.exists(album_dir):
                    shutil.rmtree(album_dir)
            except OSError:
                pass

            # Remove from list
            self.albums.pop(album_idx)
            self._save_albums()

            return True

    def upload_tracks(self, album_id, files, storage_monitor=None):
        """
        Upload music files to an album.

        Args:
            album_id: Album ID
            files: List of Werkzeug FileStorage objects
            storage_monitor: Optional StorageMonitor instance for space checking

        Returns:
            dict: Result with keys:
                - success: bool
                - uploaded: int (number of files uploaded)
                - errors: list of error messages
                - total_size: int (total bytes uploaded)
        """
        with self._lock:
            # Find album
            album = None
            for alb in self.albums:
                if alb["id"] == album_id:
                    album = alb
                    break

            if not album:
                return {"success": False, "error": "Album nicht gefunden"}

            album_dir = os.path.join(MUSIC_DIR, album_id)
            if not os.path.exists(album_dir):
                return {"success": False, "error": "Album-Verzeichnis nicht gefunden"}

            uploaded_count = 0
            total_uploaded_size = 0
            errors = []

            for file in files:
                # Validate file
                is_valid, error_msg = self._validate_file(file)
                if not is_valid:
                    errors.append(f"{file.filename}: {error_msg}")
                    continue

                # Check storage space if available
                if storage_monitor:
                    if not storage_monitor.has_sufficient_space(required_mb=500):
                        errors.append(f"{file.filename}: Nicht genügend Speicherplatz")
                        continue

                # Sanitize filename
                safe_filename = self._sanitize_filename(file.filename)
                if not safe_filename:
                    errors.append(f"{file.filename}: Ungültiger Dateiname")
                    continue

                # Check if filename already exists in album
                if any(t["filename"] == safe_filename for t in album["tracks"]):
                    # Append timestamp to make unique
                    name, ext = os.path.splitext(safe_filename)
                    safe_filename = f"{name}_{int(time.time())}{ext}"

                file_path = os.path.join(album_dir, safe_filename)

                try:
                    # Save file to disk (streaming to avoid memory issues)
                    file.save(file_path)

                    # Get file size
                    file_size = os.path.getsize(file_path)

                    # Check file size limit (100MB)
                    if file_size > MAX_FILE_SIZE_MB * 1024 * 1024:
                        os.remove(file_path)
                        errors.append(f"{file.filename}: Datei zu groß (max {MAX_FILE_SIZE_MB}MB)")
                        continue

                    # Check album size limit (2GB)
                    if album["total_size"] + file_size > MAX_ALBUM_SIZE_MB * 1024 * 1024:
                        os.remove(file_path)
                        errors.append(f"{file.filename}: Album-Größenlimit erreicht")
                        continue

                    # Create track entry
                    track_id = self._generate_id("track")
                    track = {
                        "id": track_id,
                        "filename": safe_filename,
                        "title": os.path.splitext(safe_filename)[0],  # Use filename as title
                        "artist": "Unbekannt",
                        "duration": 0,  # Could extract from file in future
                        "size": file_size
                    }

                    album["tracks"].append(track)
                    album["total_size"] += file_size
                    album["track_count"] = len(album["tracks"])

                    uploaded_count += 1
                    total_uploaded_size += file_size

                except (IOError, OSError) as e:
                    errors.append(f"{file.filename}: Upload fehlgeschlagen")
                    # Clean up partial file
                    if os.path.exists(file_path):
                        try:
                            os.remove(file_path)
                        except OSError:
                            pass

            # Save updated album metadata
            self._save_albums()

            return {
                "success": uploaded_count > 0,
                "uploaded": uploaded_count,
                "errors": errors,
                "total_size": total_uploaded_size
            }

    def delete_track(self, album_id, track_id):
        """
        Delete a single track from an album.

        Args:
            album_id: Album ID
            track_id: Track ID

        Returns:
            bool: True if deleted successfully
        """
        with self._lock:
            # Find album
            album = None
            for alb in self.albums:
                if alb["id"] == album_id:
                    album = alb
                    break

            if not album:
                return False

            # Find track
            track = None
            track_idx = None
            for idx, trk in enumerate(album["tracks"]):
                if trk["id"] == track_id:
                    track = trk
                    track_idx = idx
                    break

            if not track:
                return False

            # Delete file from disk
            file_path = os.path.join(MUSIC_DIR, album_id, track["filename"])
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except OSError:
                pass

            # Update album metadata
            album["total_size"] -= track["size"]
            album["tracks"].pop(track_idx)
            album["track_count"] = len(album["tracks"])

            self._save_albums()

            return True

    def get_track_path(self, album_id, track_index=0):
        """
        Get absolute file path for a track.

        Args:
            album_id: Album ID
            track_index: Track index in album (default: 0 for first track)

        Returns:
            str: Absolute file path, or None if not found
        """
        album = self.get_album(album_id)
        if not album or not album["tracks"]:
            return None

        if track_index >= len(album["tracks"]):
            return None

        track = album["tracks"][track_index]
        file_path = os.path.join(MUSIC_DIR, album_id, track["filename"])

        if os.path.exists(file_path):
            return file_path

        return None

    def get_random_album(self):
        """
        Get a random album from the library.

        Returns:
            dict: Random album, or None if no albums
        """
        if not self.albums:
            return None

        import random
        return random.choice(self.albums).copy()
