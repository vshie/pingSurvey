"""WebSocket that pushes survey results onto Cockpit's maps.

Cockpit learns about the socket from cockpit_extras.json, offers it to the operator and, once accepted,
connects to it as a client. Cockpit never sends anything back: every message goes from here to Cockpit,
and each is one of Cockpit's map overlay commands (cockpit:setMapOverlay, cockpit:appendMapOverlayFeatures,
...). Overlays are replaced by id, so re-sending the current state to every new connection is safe.
"""
import json
import threading

# flask-sock comes from the base image. Import it defensively so the extension still runs on an image built
# without it, just without the Cockpit link.
try:
    from flask_sock import Sock
    _import_error = None
except ImportError as e:
    Sock = None
    _import_error = str(e)


class CockpitLink:
    """Clients connected from Cockpit, and the messages they are sent."""

    def __init__(self, app, path, snapshot):
        """Serve the socket at `path` on `app`.

        `snapshot` returns the messages that bring a newly connected Cockpit up to date.
        """
        self._clients = set()
        self._lock = threading.Lock()
        self._snapshot = snapshot
        self.available = Sock is not None
        if not self.available:
            print(f"Cockpit link unavailable: {_import_error}")
            return
        Sock(app).route(path)(self._serve)

    def send(self, message):
        """Send one command to every connected Cockpit, dropping the ones that went away."""
        payload = json.dumps(message)
        with self._lock:
            clients = list(self._clients)
        for ws in clients:
            try:
                ws.send(payload)
            except Exception:
                with self._lock:
                    self._clients.discard(ws)

    def _serve(self, ws):
        # Registered before the snapshot is sent, so a message sent meanwhile is not missed. Getting one twice
        # is harmless, since Cockpit replaces overlays by id.
        with self._lock:
            self._clients.add(ws)
        try:
            for message in self._snapshot():
                ws.send(json.dumps(message))
            while True:
                ws.receive()
        finally:
            with self._lock:
                self._clients.discard(ws)
