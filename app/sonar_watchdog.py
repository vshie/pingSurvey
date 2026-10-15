"""Watch for missing sonar data while logging, and ask BlueOS to look for the Ping again.

BlueOS's Ping service probes each serial port only 3 times after it starts. If the Ping
doesn't answer in those first seconds (late power-up, loose connector), the service gives
up for the rest of the session and no DISTANCE_SENSOR messages are ever sent. A built-in
UART such as /dev/ttyAMA2 never disappears, so the service never tries again on its own.

Restarting the service makes it probe again. BlueOS runs each core service under a
run-service loop that restarts it when it exits with an error, so stopping the Ping
service's Python process (through Commander's host-command API) is enough.
"""

import os
import threading
import time
from datetime import datetime, timezone

import requests

# BlueOS's web server as seen from the extension container (overridable for local testing).
BLUEOS = os.environ.get('PING_SURVEY_BLUEOS_URL', 'http://host.docker.internal').rstrip('/')
PING_SENSORS_URL = f'{BLUEOS}/ping/v1.0/sensors'
COMMANDER_URL = f'{BLUEOS}/commander/v1.0/command/host'

# Only the Ping service's own Python process; not the run-service / sudo / bash wrappers
# around it, whose command lines also contain the script path. Stopping the wrapper
# would leave the service stopped.
RESTART_COMMAND = "docker exec blueos-core pkill -f '^[^ ]*python[^ ]* [^ ]*/services/ping/main[.]py'"

STALE_AFTER_S = 3.0        # DISTANCE_SENSOR older than this is not live data
AUTO_REDETECT_AFTER_S = 6  # seconds of logging without depth before re-detecting automatically
REDETECT_WAIT_S = 45       # run-service waits 5 s, then the service starts and probes


def _get_json(url, timeout=3):
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.json()


def ping_service_sensors():
    """(sensors, error). sensors is BlueOS's list of detected Ping devices."""
    try:
        sensors = _get_json(PING_SENSORS_URL)
        return (sensors if isinstance(sensors, list) else []), None
    except Exception as e:
        return None, f"Could not reach the BlueOS Ping service ({type(e).__name__})"


def distance_age_s(distance_json):
    """Seconds since mavlink2rest last received this DISTANCE_SENSOR message, or None."""
    try:
        last = datetime.fromisoformat(distance_json['status']['time']['last_update'].replace('Z', '+00:00'))
        return max(0.0, (datetime.now(timezone.utc) - last).total_seconds())
    except (KeyError, TypeError, ValueError):
        return None


class SonarWatchdog:
    """Tracks whether the logger is receiving live depth, and runs re-detection."""

    def __init__(self, on_redetected=None):
        self._lock = threading.Lock()
        self._on_redetected = on_redetected  # called after BlueOS finds the Ping again
        self._session_start = None
        self._last_depth_at = None
        self._last_row_at = None
        self._rows = 0
        self._rows_without_depth = 0
        self._auto_tried = False
        self._redetect = {'state': 'idle', 'message': '', 'at': None, 'automatic': False}

    # --- logging session -------------------------------------------------------------
    def start_session(self):
        with self._lock:
            self._session_start = time.time()
            self._last_depth_at = None
            self._last_row_at = None
            self._rows = 0
            self._rows_without_depth = 0
            self._auto_tried = False
            if self._redetect['state'] != 'running':
                self._redetect = {'state': 'idle', 'message': '', 'at': None, 'automatic': False}

    def stop_session(self):
        with self._lock:
            self._session_start = None

    def record_row(self, distance_json, distance_cm):
        """Call once per logged row. distance_json is the mavlink2rest reply or None."""
        age = distance_age_s(distance_json) if distance_json is not None else None
        live = age is not None and age <= STALE_AFTER_S and distance_cm and distance_cm > 0
        auto = False
        with self._lock:
            self._rows += 1
            self._last_row_at = time.time()
            if live:
                self._last_depth_at = time.time()
            else:
                self._rows_without_depth += 1
            if (not self._auto_tried and self._session_start is not None and self._last_depth_at is None
                    and time.time() - self._session_start >= AUTO_REDETECT_AFTER_S
                    and self._redetect['state'] != 'running'):
                self._auto_tried = True
                auto = True
        if auto:
            self.redetect(automatic=True)

    def status(self):
        with self._lock:
            now = time.time()
            active = self._session_start is not None
            since = self._last_depth_at if self._last_depth_at is not None else self._session_start
            without = round(now - since, 1) if active and since is not None else None
            last_row = self._last_row_at if self._last_row_at is not None else self._session_start
            return {
                'logging': active,
                'receiving_depth': bool(active and self._last_depth_at is not None
                                        and now - self._last_depth_at <= STALE_AFTER_S),
                'ever_received': self._last_depth_at is not None,
                'seconds_without_depth': without,
                # Rows stop entirely when GPS or attitude is unavailable (both are required).
                'seconds_without_rows': round(now - last_row, 1) if active and last_row is not None else None,
                'rows': self._rows,
                'rows_without_depth': self._rows_without_depth,
                'redetect': dict(self._redetect,
                                 age_s=None if self._redetect['at'] is None else round(now - self._redetect['at'], 1)),
            }

    # --- re-detection ----------------------------------------------------------------
    def _set(self, state, message):
        with self._lock:
            self._redetect.update(state=state, message=message, at=time.time())

    def redetect(self, automatic=False):
        """Start re-detection in the background. Returns False if one is already running."""
        with self._lock:
            if self._redetect['state'] == 'running':
                return False
            self._redetect = {'state': 'running', 'message': 'Checking the BlueOS Ping service…',
                              'at': time.time(), 'automatic': automatic}
        threading.Thread(target=self._run_redetect, daemon=True).start()
        return True

    def _run_redetect(self):
        sensors, error = ping_service_sensors()
        if error:
            self._set('failed', f"{error}. Restart BlueOS to make it look for the Ping again.")
            return
        if sensors:
            self._already_detected(sensors)
            return

        self._set('running', 'BlueOS has not detected a Ping. Restarting the BlueOS Ping service so it looks again…')
        try:
            response = requests.post(COMMANDER_URL, timeout=20, params={
                'command': RESTART_COMMAND, 'i_know_what_i_am_doing': 'true'})
            response.raise_for_status()
            result = response.json()
        except Exception as e:
            self._set('failed', f"Could not ask BlueOS to restart the Ping service ({type(e).__name__}). "
                                "Restart BlueOS to make it look for the Ping again.")
            return
        if result.get('return_code') not in (0, '0'):
            self._set('failed', "BlueOS did not restart the Ping service (the service process was not found). "
                                "Restart BlueOS to make it look for the Ping again.")
            return

        self._set('running', 'Ping service restarted; waiting for it to find the Ping…')
        deadline = time.time() + REDETECT_WAIT_S
        while time.time() < deadline:
            time.sleep(2)
            sensors, _ = ping_service_sensors()
            if sensors:
                if self._on_redetected:
                    self._on_redetected()
                self._set('found', f"BlueOS found the Ping on {self._ports(sensors)}. Depth data should resume "
                                   "within a few seconds.")
                return
        self._set('failed', "BlueOS restarted the Ping service but still could not find a Ping. Check the Ping "
                            "has power and its cable is seated, then press Re-detect Ping again.")

    def _already_detected(self, sensors):
        disabled = [s for s in sensors if not (s.get('driver_status') or {}).get('mavlink_driver_enabled', True)]
        if disabled:
            self._set('failed', f"BlueOS detects the Ping on {self._ports(disabled)} but its MAVLink output is "
                                "off. Enable it in BlueOS > Ping Sonar Devices.")
        else:
            if self._on_redetected:
                self._on_redetected()
            self._set('found', f"BlueOS already detects the Ping on {self._ports(sensors)}. If depth stays at "
                               "zero, check the Ping is in the water and its cable is seated.")

    @staticmethod
    def _ports(sensors):
        return ', '.join(str(s.get('port') or s.get('ethernet_discovery_info') or '?') for s in sensors)
