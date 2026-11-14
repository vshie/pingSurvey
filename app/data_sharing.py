"""Data sharing between vehicles that run this extension on the same network.

Finds the other vehicles on the network (a scan of the vehicle subnet for this extension's /peer_info),
copies their survey logs into this vehicle's log folder, and mirrors the log a vehicle is recording
("sync"), so its soundings arrive here as they are collected.

Everything goes through this vehicle's backend: the browser showing the UI may not be on the vehicles'
network at all (e.g. reached over Tailscale), and other vehicles' extensions don't allow cross-origin
requests. Peers are addressed by their instance id, so only vehicles found by a scan can be reached.

Copied and mirrored files are named after the original with the vehicle as a suffix
(ping_survey_2026-11-11_09-30-00__boat2.csv), so they can be told apart in post-processing and two
vehicles' logs with the same timestamp never collide.
"""

import collections
import csv
import ipaddress
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote

import requests

# The name in /register_service, which BlueOS's helper reports for the extension's port.
EXTENSION_NAME = "Simple Ping Survey"
# Where BlueOS serves this extension on other vehicles.
PEER_PATH = '/' + os.environ.get('PING_SURVEY_PEER_PATH', 'extensionv2/simplepingsurvey/').strip('/') + '/'
PEER_SUBNET = ipaddress.ip_network(os.environ.get('PING_SURVEY_PEER_SUBNET', '192.168.2.0/24'), strict=False)
# Extra extension base URLs that are always probed (non-standard networks, local testing).
EXTRA_PEERS = [u.strip().rstrip('/') + '/' for u in os.environ.get('PING_SURVEY_PEERS', '').split(',') if u.strip()]

SCAN_WORKERS = 48
SCAN_TIMEOUT = (0.8, 2.0)       # (connect, read): most addresses have nothing listening
PEER_TIMEOUT = (3.0, 10.0)
TAIL_CHUNK = 256 * 1024         # bytes per /log_tail call, so catching up over a slow link goes in steps
SYNC_POINTS = 5000              # recent soundings kept per synced vehicle for the live map
# Each vehicle's colour on the maps, distinct from the depth colours on the live map.
SYNC_COLORS = ('#E91E63', '#00BCD4', '#FF9800', '#8BC34A', '#9C27B0', '#FFEB3B')
# Tracking other vehicles' positions (for Cockpit's maps)
POSITION_EVERY_S = 2
TRAIL_POINTS = 60               # about two minutes of positions
LOST_AFTER_S = 20               # no answer for this long: shown as lost contact
FORGET_AFTER_S = 600            # and removed after this long
FIRST_SCAN_AFTER_S = 20
RESCAN_EVERY_S = 120            # so vehicles switched on later appear too


class PeerError(Exception):
    """A user-facing problem reaching or using another vehicle."""


def instance_id(logs_dir):
    """A random id for this installation, kept in the log folder, so a scan recognises this vehicle."""
    path = os.path.join(logs_dir, '.instance_id')
    try:
        with open(path) as f:
            value = f.read().strip()
        if value:
            return value
    except OSError:
        pass
    value = uuid.uuid4().hex
    try:
        with open(path, 'w') as f:
            f.write(value)
    except OSError as e:
        print(f"Could not save the instance id: {e}")
    return value


def vehicle_suffix(info):
    """File-name suffix for a vehicle: its name as a slug, else sys<ID>, else part of its instance id."""
    slug = re.sub(r'[^a-z0-9]+', '-', str(info.get('vehicle_name') or '').lower()).strip('-')[:24].strip('-')
    if slug:
        return slug
    if info.get('sysid') is not None:
        return f"sys{info['sysid']}"
    return str(info.get('instance_id', 'peer'))[:6]


def local_name(remote_name, suffix):
    """This vehicle's name for another vehicle's log, e.g. ping_survey_..._09-30-00__boat2.csv."""
    stem = os.path.splitext(os.path.basename(remote_name))[0]
    stem = re.sub(r'[^A-Za-z0-9_.-]+', '_', stem).lstrip('.') or 'log'
    return f"{stem}__{suffix}.csv"


def _valid_remote_name(name):
    return (isinstance(name, str) and name == os.path.basename(name) and not name.startswith('.')
            and name.lower().endswith('.csv') and '\\' not in name and '\x00' not in name)


def _peer_info(base):
    """A vehicle's /peer_info, or None when this extension isn't answering at base."""
    try:
        r = requests.get(base + 'peer_info', timeout=SCAN_TIMEOUT)
        if r.status_code != 200:
            return None
        info = r.json()
    except (requests.RequestException, ValueError):
        return None
    return info if isinstance(info, dict) and info.get('app') == 'pingsurvey' else None


def _probe_ip(ip):
    """(base URL, peer_info) of this extension on ip, or None."""
    base = f"http://{ip}{PEER_PATH}"
    try:
        r = requests.get(base + 'peer_info', timeout=SCAN_TIMEOUT)
    except requests.RequestException:
        return None                     # nothing there: most of the subnet
    try:
        info = r.json() if r.status_code == 200 else None
    except ValueError:
        info = None
    if isinstance(info, dict) and info.get('app') == 'pingsurvey':
        return base, info
    # Something answers (BlueOS) but the extension isn't at the usual path: ask BlueOS's helper
    # which port it is on.
    try:
        services = requests.get(f"http://{ip}/helper/v1.0/web_services", timeout=SCAN_TIMEOUT).json()
    except (requests.RequestException, ValueError):
        return None
    for s in services if isinstance(services, list) else []:
        if isinstance(s, dict) and (s.get('metadata') or {}).get('name') == EXTENSION_NAME and s.get('port'):
            base = f"http://{ip}:{int(s['port'])}/"
            info = _peer_info(base)
            if info:
                return base, info
    return None


def _probe_base(base):
    """(base URL, peer_info) when this extension answers at base, or None."""
    info = _peer_info(base)
    return (base, info) if info else None


def _host(base):
    return re.sub(r'^https?://([^/]+).*$', r'\1', base)


class _Sync:
    """Mirrors the log another vehicle is recording into this vehicle's log folder."""

    def __init__(self, sharing, peer, color):
        self.sharing = sharing
        self.peer_id = peer['id']
        self.base = peer['base']
        self.name = peer['name']
        self.suffix = peer['suffix']
        self.color = color
        self.stop_event = threading.Event()
        self.state = 'starting'
        self.error = ''
        self.remote_file = None
        self.local_file = None
        self.offset = 0
        self.size = 0
        self.rows = 0
        self.logging_active = False
        self.last_row = None            # [lat, lon, depth_m, confidence]
        self.last_received = None
        self.columns = None
        self.points = collections.deque(maxlen=SYNC_POINTS)   # (seq, lat, lon, depth_m, confidence)
        self.thread = threading.Thread(target=self._run, daemon=True)

    def status(self):
        now = time.time()
        return {
            'id': self.peer_id, 'name': self.name, 'color': self.color, 'state': self.state, 'error': self.error,
            'remote_file': self.remote_file, 'local_file': self.local_file, 'rows': self.rows,
            'bytes': self.offset, 'remote_bytes': self.size, 'logging_active': self.logging_active,
            'last_row': self.last_row,
            'last_received_s': None if self.last_received is None else round(now - self.last_received, 1),
        }

    def _tail(self, offset, limit):
        params = {'offset': offset, 'max': limit}
        if self.remote_file:
            params['file'] = self.remote_file
        r = requests.get(self.base + 'log_tail', params=params, timeout=PEER_TIMEOUT)
        if r.status_code == 404:
            raise PeerError(f"{self.remote_file} is no longer on {self.name}")
        r.raise_for_status()
        return r.json()

    def _open(self, remote_file, remote_size):
        """Start mirroring remote_file, resuming from what is already here."""
        self.remote_file = remote_file
        self.local_file = local_name(remote_file, self.suffix)
        self.columns = None
        path = os.path.join(self.sharing.logs_dir, self.local_file)
        size = os.path.getsize(path) if os.path.exists(path) else 0
        if size > remote_size:
            # Not a copy of this file (it would be no longer than the original): start it again.
            print(f"Sync: {self.local_file} is longer than {remote_file} on {self.name}; mirroring it again")
            open(path, 'wb').close()
            size = 0
        self.offset = size
        if size:
            with open(path, newline='') as f:
                self._set_columns(f.readline())

    def _set_columns(self, header_line):
        header = next(csv.reader([header_line]), [])
        idx = {name.strip(): i for i, name in enumerate(header)}
        need = ('Latitude', 'Longitude', 'Depth (cm)', 'Confidence (%)')
        self.columns = [idx[n] for n in need] if all(n in idx for n in need) else None

    def _append(self, text):
        path = os.path.join(self.sharing.logs_dir, self.local_file)
        with open(path, 'ab') as f:
            f.write(text.encode('latin-1'))     # /log_tail decodes bytes as latin-1: an exact round trip
        lines = text.splitlines()
        if self.offset == 0 and lines:
            self._set_columns(lines[0])
            lines = lines[1:]
        new_points = []
        for row in csv.reader(lines):
            self.rows += 1
            if not self.columns:
                continue
            try:
                lat, lon, depth_cm, conf = (float(row[i]) for i in self.columns)
            except (IndexError, ValueError):
                continue
            if lat == 0 and lon == 0:
                continue
            self.last_row = [lat, lon, round(depth_cm / 100, 2), conf]
            new_points.append((lat, lon, round(depth_cm / 100, 2), conf))
        self.sharing._add_points(self, new_points)

    def _run(self):
        backoff = 1.0
        while not self.stop_event.is_set():
            delay = 1.0
            try:
                if self.remote_file is None:
                    tail = self._tail(0, 0)             # which file is the vehicle recording?
                    self.logging_active = bool(tail.get('logging_active'))
                    if not tail.get('file'):
                        self.state = 'waiting'          # nothing recorded since its extension started
                        self.stop_event.wait(3)
                        continue
                    self.remote_file = tail['file']
                    self._open(tail['file'], int(tail.get('size') or 0))
                tail = self._tail(self.offset, TAIL_CHUNK)
                size = int(tail.get('size') or 0)
                if size < self.offset:
                    # The file on the vehicle got shorter, so it's not the one we were mirroring.
                    self._open(self.remote_file, size)
                    continue
                data = tail.get('data') or ''
                if data:
                    self._append(data)
                    self.last_received = time.time()
                self.offset = int(tail.get('next_offset', self.offset))
                self.size = size
                self.logging_active = bool(tail.get('logging_active'))
                current = tail.get('current_log')
                caught_up = self.offset >= size
                if current and current != self.remote_file and caught_up:
                    # The vehicle started a new log: mirror that one next.
                    self.remote_file = None
                    continue
                if not caught_up:
                    self.state, delay = 'catching_up', 0
                elif self.logging_active:
                    self.state = 'syncing'
                else:
                    self.state, delay = 'idle', 3.0
                self.error = ''
                backoff = 1.0
            except (requests.RequestException, ValueError, OSError, PeerError) as e:
                self.state = 'reconnecting'
                self.error = str(e) if isinstance(e, PeerError) else f"Can't reach {self.name} ({type(e).__name__})"
                delay = backoff
                backoff = min(5.0, backoff * 2)
            if delay:
                self.stop_event.wait(delay)
        self.state = 'stopped'


class DataSharing:
    def __init__(self, logs_dir, local_info, on_tracks=None):
        """local_info() returns this vehicle's /peer_info.

        on_tracks(tracks, removed_ids) is called after each round of position updates (see tracks()), with the
        ids of vehicles no longer tracked.
        """
        self.logs_dir = logs_dir
        self._local_info = local_info
        self._on_tracks = on_tracks
        self._lock = threading.Lock()
        self._colors = {}               # instance id -> colour, kept for the session
        self._tracks = {}               # instance id -> {name, color, host, position, trail, seen, ...}
        self.tracking = True
        threading.Thread(target=self._run_tracking, daemon=True).start()
        self._scan = {'state': 'idle', 'done': 0, 'total': 0, 'started': None, 'finished': None,
                      'hidden_same_id': 0, 'vehicles': []}
        self._peers = {}                # instance id -> peer, from the latest scan
        self._copies = []               # copy jobs, oldest first
        self._copy_worker = None
        self._syncs = {}                # instance id -> _Sync
        self._seq = 0

    # --- discovery --------------------------------------------------------------------
    def start_scan(self):
        with self._lock:
            if self._scan['state'] == 'running':
                return False
            hosts = [str(h) for h in PEER_SUBNET.hosts()]
            self._scan.update(state='running', done=0, total=len(hosts) + len(EXTRA_PEERS), started=time.time())
        threading.Thread(target=self._run_scan, args=(hosts,), daemon=True).start()
        return True

    def _run_scan(self, hosts):
        me = self._local_info()
        found = {}
        with ThreadPoolExecutor(SCAN_WORKERS) as pool:
            futures = [pool.submit(_probe_ip, h) for h in hosts]
            futures += [pool.submit(_probe_base, b) for b in EXTRA_PEERS]
            for future in as_completed(futures):
                result = future.result()
                with self._lock:
                    self._scan['done'] += 1
                if result:
                    base, info = result
                    if info.get('instance_id'):
                        found[info['instance_id']] = (base, info)
        vehicles, hidden = [], 0
        for pid, (base, info) in found.items():
            if pid == me['instance_id']:
                continue                # this vehicle
            if (info.get('sysid_detected') and me.get('sysid_detected')
                    and info.get('sysid') == me.get('sysid')):
                hidden += 1             # same vehicle ID as this one
                continue
            vehicles.append(dict(info, id=pid, base=base, host=_host(base),
                                 name=info.get('vehicle_name') or vehicle_suffix(info),
                                 suffix=vehicle_suffix(info), version_matches=info.get('version') == me.get('version')))
        vehicles.sort(key=lambda v: (v['name'].lower(), v['host']))
        with self._lock:
            for v in vehicles:
                v['color'] = self._color_for(v['id'])
            self._peers = {v['id']: v for v in vehicles}
            self._scan.update(state='done', finished=time.time(), hidden_same_id=hidden, sysid=me.get('sysid'),
                              vehicles=[{k: v for k, v in p.items() if k != 'base'} for p in vehicles])

    def scan_status(self):
        with self._lock:
            out = dict(self._scan, subnet=str(PEER_SUBNET), tracking=self.tracking,
                       vehicles=[dict(v, syncing=v['id'] in self._syncs) for v in self._scan['vehicles']])
        return out

    def _color_for(self, peer_id):
        """Call with the lock held."""
        if peer_id not in self._colors:
            used = set(self._colors.values())
            self._colors[peer_id] = next((c for c in SYNC_COLORS if c not in used), SYNC_COLORS[len(self._colors) % len(SYNC_COLORS)])
        return self._colors[peer_id]

    def _peer(self, peer_id):
        with self._lock:
            peer = self._peers.get(peer_id)
        if not peer:
            raise KeyError(peer_id)
        return peer

    # --- logs -------------------------------------------------------------------------
    def log_files(self, peer_id):
        peer = self._peer(peer_id)
        try:
            r = requests.get(peer['base'] + 'log_files', timeout=PEER_TIMEOUT)
            r.raise_for_status()
            files = r.json().get('files') or []
        except (requests.RequestException, ValueError) as e:
            raise PeerError(f"Couldn't list {peer['name']}'s logs ({type(e).__name__})")
        peer_now = _peer_info(peer['base']) or {}       # which log it is recording right now
        if peer_now:
            # Keep the vehicle list current between scans.
            fresh = {k: peer_now.get(k) for k in ('logging_active', 'current_log', 'log_count', 'last_fix')}
            with self._lock:
                peer.update(fresh)
                for v in self._scan['vehicles']:
                    if v['id'] == peer_id:
                        v.update(fresh)
        out = []
        for f in files:
            name = f.get('filename')
            if not _valid_remote_name(name):
                continue
            local = local_name(name, peer['suffix'])
            path = os.path.join(self.logs_dir, local)
            out.append({
                'filename': name, 'size_mb': f.get('size_mb'), 'modified': f.get('modified'),
                'remote_bytes': f.get('size_bytes'), 'local_name': local,
                'local_bytes': os.path.getsize(path) if os.path.exists(path) else None,
                'recording': bool(peer_now.get('logging_active')) and peer_now.get('current_log') == name,
            })
        return {'vehicle': {k: v for k, v in peer.items() if k != 'base'}, 'files': out}

    def copy(self, peer_id, names):
        peer = self._peer(peer_id)
        added = 0
        with self._lock:
            for name in names:
                if not _valid_remote_name(name):
                    continue
                if any(j['peer'] == peer_id and j['filename'] == name and j['state'] in ('queued', 'copying')
                       for j in self._copies):
                    continue
                self._copies.append({'peer': peer_id, 'vehicle': peer['name'], 'filename': name,
                                     'local_name': local_name(name, peer['suffix']), 'state': 'queued',
                                     'bytes': 0, 'total': None, 'error': '', 'snapshot': False,
                                     'queued': time.time()})
                added += 1
            self._copies = self._copies[-200:]
            if added and not (self._copy_worker and self._copy_worker.is_alive()):
                self._copy_worker = threading.Thread(target=self._run_copies, daemon=True)
                self._copy_worker.start()
        return added

    def copy_status(self):
        with self._lock:
            return [dict(j) for j in self._copies]

    def _run_copies(self):
        while True:
            with self._lock:
                job = next((j for j in self._copies if j['state'] == 'queued'), None)
                if job is None:
                    return
                job['state'] = 'copying'
            try:
                self._copy_one(job)
            except Exception as e:
                with self._lock:
                    job.update(state='error', error=str(e) if isinstance(e, PeerError) else f"Copy failed ({type(e).__name__}: {e})")

    def _copy_one(self, job):
        peer = self._peer(job['peer'])
        path = os.path.join(self.logs_dir, job['local_name'])
        tmp = os.path.join(self.logs_dir, '.incoming_' + job['local_name'])
        with self._lock:
            synced = any(s.local_file == job['local_name'] for s in self._syncs.values())
        if synced:
            # Replacing the file would cut it off from the sync appending to it; the sync has it anyway.
            with self._lock:
                job.update(state='syncing')
            return
        try:
            info = _peer_info(peer['base']) or {}
            snapshot = bool(info.get('logging_active')) and info.get('current_log') == job['filename']
            with requests.get(peer['base'] + 'log_file/' + quote(job['filename']), stream=True,
                              timeout=PEER_TIMEOUT) as r:
                if r.status_code == 404:
                    raise PeerError(f"{job['filename']} is no longer on {peer['name']}")
                r.raise_for_status()
                total = int(r.headers.get('Content-Length') or 0) or None
                with self._lock:
                    job.update(total=total, snapshot=snapshot)
                if total and os.path.exists(path) and os.path.getsize(path) == total:
                    with self._lock:
                        job.update(state='exists', bytes=total)
                    return
                done = 0
                with open(tmp, 'wb') as f:
                    for chunk in r.iter_content(64 * 1024):
                        f.write(chunk)
                        done += len(chunk)
                        with self._lock:
                            job['bytes'] = done
            if total and done != total:
                raise PeerError(f"Copy of {job['filename']} was cut short ({done} of {total} bytes)")
            os.replace(tmp, path)
            with self._lock:
                job['state'] = 'done'
        except requests.RequestException as e:
            raise PeerError(f"Lost connection to {peer['name']} while copying ({type(e).__name__})")
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    # --- sync -------------------------------------------------------------------------
    def start_sync(self, peer_id):
        peer = self._peer(peer_id)
        with self._lock:
            if peer_id in self._syncs:
                return False
            sync = self._syncs[peer_id] = _Sync(self, peer, self._color_for(peer_id))
        sync.thread.start()
        return True

    def stop_sync(self, peer_id):
        with self._lock:
            sync = self._syncs.pop(peer_id, None)
        if sync:
            sync.stop_event.set()
        return sync is not None

    def sync_status(self):
        with self._lock:
            syncs = list(self._syncs.values())
        return [s.status() for s in syncs]

    def _add_points(self, sync, points):
        with self._lock:
            for p in points:
                self._seq += 1
                sync.points.append((self._seq,) + p)

    # --- positions -----------------------------------------------------------------------
    def set_tracking(self, on):
        with self._lock:
            self.tracking = bool(on)
            removed = [] if on else list(self._tracks)
            if not on:
                self._tracks.clear()
        if removed and self._on_tracks:
            self._on_tracks([], removed)

    def tracks(self):
        """Vehicles being tracked: [{id, name, color, host, position, trail, seen_s, lost, logging_active}]."""
        now = time.time()
        with self._lock:
            return [{
                'id': pid, 'name': t['name'], 'color': t['color'], 'host': t['host'], 'position': t['position'],
                'trail': list(t['trail']), 'logging_active': t['logging_active'],
                'seen_s': round(now - t['seen'], 1), 'lost': now - t['seen'] > LOST_AFTER_S,
            } for pid, t in self._tracks.items()]

    def _run_tracking(self):
        started = time.time()
        next_scan = started + FIRST_SCAN_AFTER_S
        while True:
            time.sleep(POSITION_EVERY_S)
            if not self.tracking:
                continue
            if time.time() >= next_scan:
                next_scan = time.time() + RESCAN_EVERY_S
                self.start_scan()           # returns at once; does nothing if a scan is running
            with self._lock:
                peers = list(self._peers.values())
            if peers or self._tracks:
                self._update_tracks(peers)

    def _update_tracks(self, peers):
        with ThreadPoolExecutor(min(8, max(1, len(peers)))) as pool:
            answers = list(pool.map(lambda p: (p, _peer_info(p['base'])), peers))
        now = time.time()
        removed = []
        with self._lock:
            if not self.tracking:
                return
            for peer, info in answers:
                if not info or not info.get('position'):
                    continue
                pos = info['position']
                t = self._tracks.setdefault(peer['id'], {'trail': collections.deque(maxlen=TRAIL_POINTS)})
                t.update(name=peer['name'], color=self._color_for(peer['id']), host=peer['host'], position=pos,
                         seen=now, logging_active=bool(info.get('logging_active')))
                point = [round(pos['lon'], 7), round(pos['lat'], 7)]
                if not t['trail'] or t['trail'][-1] != point:
                    t['trail'].append(point)
            for pid in [pid for pid, t in self._tracks.items() if now - t['seen'] > FORGET_AFTER_S]:
                del self._tracks[pid]
                removed.append(pid)
        if self._on_tracks:
            self._on_tracks(self.tracks(), removed)

    def sync_points(self, since):
        """Soundings received since seq `since`, per synced vehicle, for the live map."""
        with self._lock:
            syncs = list(self._syncs.values())
            seq = self._seq
            out = []
            for s in syncs:
                out.append({'id': s.peer_id, 'name': s.name, 'color': s.color, 'last_row': s.last_row,
                            'points': [list(p[1:]) for p in s.points if p[0] > since]})
        return {'seq': seq, 'vehicles': out}
