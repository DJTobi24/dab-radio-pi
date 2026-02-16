"""
storage_monitor.py — SD Card Storage Monitor
Provides disk usage information for the root filesystem.
"""

import subprocess
import re


class StorageMonitor:
    def __init__(self, mount_point="/"):
        """
        Initialize storage monitor.

        Args:
            mount_point: Filesystem mount point to monitor (default: "/")
        """
        self.mount_point = mount_point
        self._cache = None
        self._cache_time = 0
        self._cache_duration = 10  # Cache for 10 seconds

    def get_storage_info(self):
        """
        Get disk usage for the specified filesystem.

        Returns:
            dict: Storage information with keys:
                - total_mb: Total space in megabytes
                - used_mb: Used space in megabytes
                - available_mb: Available space in megabytes
                - total_gb: Total space in gigabytes (rounded to 1 decimal)
                - used_gb: Used space in gigabytes (rounded to 1 decimal)
                - available_gb: Available space in gigabytes (rounded to 1 decimal)
                - percent_used: Percentage of space used (0-100)
                - mount_point: The mount point being monitored

            None if unable to retrieve storage information
        """
        import time

        # Return cached result if recent
        current_time = time.time()
        if self._cache and (current_time - self._cache_time) < self._cache_duration:
            return self._cache

        try:
            # Run df command with -BM flag (output in megabytes)
            result = subprocess.run(
                ["df", "-BM", self.mount_point],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode != 0:
                return None

            # Parse df output
            lines = result.stdout.strip().split("\n")
            if len(lines) < 2:
                return None

            # Output format: Filesystem Size Used Avail Use% Mounted
            # Example: /dev/root 29421M 12845M 15303M 46% /
            parts = lines[1].split()
            if len(parts) < 6:
                return None

            # Extract values (remove 'M' suffix)
            total_mb = int(parts[1].rstrip('M'))
            used_mb = int(parts[2].rstrip('M'))
            avail_mb = int(parts[3].rstrip('M'))
            percent_str = parts[4].rstrip('%')

            # Parse percentage
            try:
                percent_used = int(percent_str)
            except ValueError:
                percent_used = 0

            # Convert to GB
            info = {
                "total_mb": total_mb,
                "used_mb": used_mb,
                "available_mb": avail_mb,
                "total_gb": round(total_mb / 1024, 1),
                "used_gb": round(used_mb / 1024, 1),
                "available_gb": round(avail_mb / 1024, 1),
                "percent_used": percent_used,
                "mount_point": self.mount_point
            }

            # Cache the result
            self._cache = info
            self._cache_time = current_time

            return info

        except (subprocess.TimeoutExpired, FileNotFoundError, ValueError, IndexError) as e:
            # Silent failure - return None
            return None

    def has_sufficient_space(self, required_mb=500):
        """
        Check if there is sufficient free space.

        Args:
            required_mb: Minimum required free space in megabytes (default: 500)

        Returns:
            bool: True if sufficient space available, False otherwise
        """
        info = self.get_storage_info()
        if not info:
            return False

        return info["available_mb"] >= required_mb

    def get_available_mb(self):
        """
        Get available space in megabytes.

        Returns:
            int: Available megabytes, or 0 if unable to retrieve
        """
        info = self.get_storage_info()
        return info["available_mb"] if info else 0
