from flask import Flask, send_file, jsonify, request, Response
import requests
import csv
import time
import threading
import math
from datetime import datetime, timezone
import json
import os
import hashlib
import re

# Post-mission processing needs numpy/scipy/contourpy/Pillow from the base image. Import it
# defensively so live surveying still works on an image built without them.
try:
    import contour_processing
    _contour_import_error = None
except ImportError as e:
    contour_processing = None
    _contour_import_error = str(e)
    print(f"Post-mission processing unavailable: {e}")

# Logs live on the BlueOS host bind mount; overridable for local development.
LOGS_DIR = os.environ.get('PING_SURVEY_LOGS_DIR', '/app/logs')
PROCESSED_DIR = os.path.join(LOGS_DIR, 'processed')

import tides
tide_service = tides.TideService(os.path.join(LOGS_DIR, 'tides'))

# Offline map tile caching - store in external file system alongside logs
OFFLINE_MAPS_DIR = os.path.join(LOGS_DIR, 'offline_maps')
TILE_CACHE_SIZE_LIMIT = 5 * 1024 * 1024 * 1024  # 5GB cache limit

# Performance optimization: Global state to avoid redundant operations
_offline_maps_dir_verified = False
_system_id_cache = None
_distance_ids_cache = None
_urls_cache = None
_cache_size_bytes = 0
_cache_size_last_check = 0


def _forget_distance_sensor():
    """Re-detect which component publishes DISTANCE_SENSOR on the next logged row."""
    global _distance_ids_cache, _urls_cache
    _distance_ids_cache = None
    _urls_cache = None


import sonar_watchdog
sonar = sonar_watchdog.SonarWatchdog(on_redetected=_forget_distance_sensor)
_CACHE_SIZE_CHECK_INTERVAL = 60  # Only recalculate cache size every 60 seconds

# Map tile sources
MAP_SOURCES = {
    'arcgis': {
        'name': 'Esri World Imagery',
        'url': 'https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        'attribution': '&copy; <a href="https://www.esri.com/">Esri</a> — Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community'
    }
}

print("Ping Survey Extension initializing...")
print(f"Offline map tiles: {OFFLINE_MAPS_DIR} (limit: {TILE_CACHE_SIZE_LIMIT / (1024**3):.1f} GB)")
app = Flask(__name__, static_url_path="/static", static_folder="static")

# Thread lock for global state
_state_lock = threading.Lock()

logging_active = False
simulation_active = False
simulation_index = 0
simulation_data = []
base_url = f'{sonar_watchdog.BLUEOS}/mavlink2rest/mavlink'
log_rate = 2  # Desired rate in Hz
simulation_speed = 5  # Playback speed multiplier
simulation_file = os.path.join(LOGS_DIR, 'simulation.csv')

data = []
row_counter = 0
feedback_interval = 5  # Define the feedback interval (in seconds)
current_log_file = None  # Will be set when logging starts

def ensure_logs_dir():
    """Ensure the logs directory exists."""
    logs_dir = LOGS_DIR
    print(f"Ensuring logs directory exists: {logs_dir}")
    
    try:
        if not os.path.exists(logs_dir):
            print(f"Creating logs directory: {logs_dir}")
            os.makedirs(logs_dir, exist_ok=True)
        else:
            print(f"Logs directory already exists: {logs_dir}")
        
        # Check if we can write to the directory
        test_file = os.path.join(logs_dir, '.test_write')
        with open(test_file, 'w') as f:
            f.write('test')
        os.remove(test_file)
        print(f"✓ Write access confirmed for logs directory")
        
        # List contents to verify
        contents = os.listdir(logs_dir)
        print(f"Logs directory contents: {contents}")
        
    except Exception as e:
        print(f"⚠ Warning: Cannot access logs directory: {e}")
        print(f"  This may be due to volume mount issues on BlueOS")
        print(f"  Expected mount: /usr/blueos/extensions/ping-survey:/app/logs")
        print(f"  Logs will not be saved. Check BlueOS volume configuration.")
        return False
    
    return True

def generate_log_filename():
    """Generate a timestamped log filename."""
    timestamp = datetime.now()
    date_str = timestamp.strftime('%Y-%m-%d')
    time_str = timestamp.strftime('%H-%M-%S')
    return os.path.join(LOGS_DIR, f'ping_survey_{date_str}_{time_str}.csv')

def ensure_offline_maps_dir():
    """Ensure the offline maps directory exists."""
    global _offline_maps_dir_verified
    
    # Skip if already verified
    if _offline_maps_dir_verified:
        return True
    
    if not os.path.exists(OFFLINE_MAPS_DIR):
        os.makedirs(OFFLINE_MAPS_DIR, exist_ok=True)
        print(f"Created offline maps directory: {OFFLINE_MAPS_DIR}")
    else:
        print(f"Using existing offline maps directory: {OFFLINE_MAPS_DIR}")
    
    # Check if we can write to the directory
    try:
        test_file = os.path.join(OFFLINE_MAPS_DIR, '.test_write')
        with open(test_file, 'w') as f:
            f.write('test')
        os.remove(test_file)
        print(f"✓ Write access confirmed for offline maps directory")
        _offline_maps_dir_verified = True
        return True
    except Exception as e:
        print(f"⚠ Warning: Cannot write to offline maps directory: {e}")
        print(f"  Tiles will not be cached. Check file system permissions.")
        return False

def get_tile_cache_path(z, x, y):
    """Get the cache file path for a specific tile."""
    tile_hash = hashlib.md5(f"{z}_{x}_{y}".encode()).hexdigest()
    return os.path.join(OFFLINE_MAPS_DIR, f"{tile_hash}.png")

def is_tile_cached(z, x, y):
    """Check if a tile is cached locally."""
    cache_path = get_tile_cache_path(z, x, y)
    return os.path.exists(cache_path)

def _get_cache_size():
    """Get the cache size, recalculating only periodically."""
    global _cache_size_bytes, _cache_size_last_check
    now = time.time()
    if now - _cache_size_last_check > _CACHE_SIZE_CHECK_INTERVAL:
        if os.path.exists(OFFLINE_MAPS_DIR):
            _cache_size_bytes = sum(
                os.path.getsize(os.path.join(OFFLINE_MAPS_DIR, f))
                for f in os.listdir(OFFLINE_MAPS_DIR) if f.endswith('.png')
            )
        else:
            _cache_size_bytes = 0
        _cache_size_last_check = now
    return _cache_size_bytes

def cache_tile(z, x, y, tile_data):
    """Cache a tile locally."""
    global _cache_size_bytes
    if z < 17:  # Only cache high zoom levels (17-19) to save space
        return
    
    try:
        if not ensure_offline_maps_dir():
            return
            
        cache_path = get_tile_cache_path(z, x, y)
        
        if os.path.exists(cache_path):
            return
        
        # Check cache size limit periodically
        if _get_cache_size() > TILE_CACHE_SIZE_LIMIT:
            files = [(f, os.path.getmtime(os.path.join(OFFLINE_MAPS_DIR, f))) 
                    for f in os.listdir(OFFLINE_MAPS_DIR) if f.endswith('.png')]
            files.sort(key=lambda x: x[1])
            files_to_remove = files[:max(1, len(files) // 10)]
            removed_size = 0
            for f, _ in files_to_remove:
                fpath = os.path.join(OFFLINE_MAPS_DIR, f)
                removed_size += os.path.getsize(fpath)
                os.remove(fpath)
            _cache_size_bytes -= removed_size
            print(f"Cleaned up {len(files_to_remove)} old cached tiles")
        
        with open(cache_path, 'wb') as f:
            f.write(tile_data)
        _cache_size_bytes += len(tile_data)
        
        store_tile_metadata(z, x, y)
        
    except Exception as e:
        print(f"Error caching tile: {e}")

def store_tile_metadata(z, x, y):
    """Store metadata about cached tiles for recent area tracking."""
    try:
        # Convert tile coordinates to lat/lon
        lat, lon = tile_to_lat_lon(z, x, y)
        
        metadata_file = os.path.join(OFFLINE_MAPS_DIR, 'recent_tiles.json')
        metadata = []
        
        # Load existing metadata
        if os.path.exists(metadata_file):
            try:
                with open(metadata_file, 'r') as f:
                    metadata = json.load(f)
            except (json.JSONDecodeError, IOError):
                metadata = []
        
        # Add new tile info
        tile_info = {
            'z': z,
            'x': x,
            'y': y,
            'lat': lat,
            'lon': lon,
            'timestamp': time.time()
        }
        
        # Remove duplicate entries for same tile
        metadata = [t for t in metadata if not (t['z'] == z and t['x'] == x and t['y'] == y)]
        
        # Add new entry
        metadata.append(tile_info)
        
        # Keep only last 100 entries to prevent file from growing too large
        if len(metadata) > 100:
            metadata = metadata[-100:]
        
        # Save metadata
        with open(metadata_file, 'w') as f:
            json.dump(metadata, f)
            
    except Exception as e:
        print(f"Error storing tile metadata: {e}")

def tile_to_lat_lon(z, x, y):
    """Convert tile coordinates to latitude/longitude."""
    n = 2.0 ** z
    lon_deg = x / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
    lat_deg = math.degrees(lat_rad)
    return lat_deg, lon_deg

def get_cached_tile(z, x, y):
    """Get a cached tile if it exists."""
    if not is_tile_cached(z, x, y):
        return None
    
    try:
        cache_path = get_tile_cache_path(z, x, y)
        with open(cache_path, 'rb') as f:
            return f.read()
    except Exception as e:
        print(f"Error reading cached tile: {e}")
        return None

def _probe_system_id_via_gps(max_vehicle_id=100):
    """Probe GLOBAL_POSITION_INT across vehicle IDs to find a live system ID."""
    for candidate_id in range(1, max_vehicle_id + 1):
        try:
            r = requests.get(
                f"{base_url}/vehicles/{candidate_id}/components/1/messages/GLOBAL_POSITION_INT",
                timeout=0.75)
            if r.status_code == 200:
                payload = r.json()
                if isinstance(payload, dict) and ("message" in payload or "time_boot_ms" in payload):
                    print(f"Detected system ID by probing GPS: {candidate_id}")
                    return candidate_id
        except Exception:
            pass
    return None


def _distance_payload_valid(payload):
    """Basic plausibility check that a DISTANCE_SENSOR payload is real."""
    if not isinstance(payload, dict):
        return False
    message = payload.get("message", {})
    return isinstance(message, dict) and (
        "current_distance" in message or "min_distance" in message or "max_distance" in message)


def get_system_id():
    """Detect the correct system ID by checking available vehicles and their autopilot types."""
    global _system_id_cache
    
    # Return cached value if available
    if _system_id_cache is not None:
        return _system_id_cache
    
    try:
        response = requests.get(f"{base_url}/vehicles", timeout=5)
        if response.status_code == 200:
            vehicles = response.json()
            if vehicles and len(vehicles) > 0:
                # Get vehicle info for each ID
                for vehicle_id in vehicles:
                    vehicle_info = requests.get(f"{base_url}/vehicles/{vehicle_id}/info", timeout=5)
                    if vehicle_info.status_code == 200:
                        info = vehicle_info.json()
                        # Check if this is a valid autopilot
                        autopilot_type = info.get("autopilot", {}).get("type")
                        valid_autopilots = [
                            "MAV_AUTOPILOT_GENERIC", 
                            "MAV_AUTOPILOT_ARDUPILOTMEGA",
                            "MAV_AUTOPILOT_PX4"
                        ]
                        if autopilot_type in valid_autopilots:
                            print(f"Found valid autopilot: {autopilot_type} with system ID: {vehicle_id}")
                            _system_id_cache = vehicle_id
                            return vehicle_id
                print("Warning: No valid autopilot found; probing GLOBAL_POSITION_INT across vehicle IDs...")
        # Fallback: probe GLOBAL_POSITION_INT across vehicle IDs. Handles setups where
        # /vehicles/<id>/info does not report a recognized autopilot type.
        probed = _probe_system_id_via_gps()
        if probed is not None:
            _system_id_cache = probed
            return probed
        print("Warning: Could not detect system ID, using default value of 1")
        _system_id_cache = 1
        return 1
    except Exception as e:
        print(f"Warning: Error detecting system ID: {e}; probing GLOBAL_POSITION_INT as fallback...")
        try:
            probed = _probe_system_id_via_gps()
            if probed is not None:
                _system_id_cache = probed
                return probed
        except Exception:
            pass
        print("Using default system ID of 1")
        _system_id_cache = 1
        return 1

def get_distance_sensor_ids():
    """Detect the (vehicle_id, component_id) that publishes DISTANCE_SENSOR.

    Best-of-both detection:
      1) Fast path: on the nav system ID, probe common component IDs (194-200) --
         handles a Ping published under a non-standard component on the same vehicle.
      2) Fallback: probe vehicle IDs 1-100 on component 194 -- handles a Ping
         published under a different system ID than the autopilot.
    """
    global _distance_ids_cache

    # Return cached value if available
    if _distance_ids_cache is not None:
        return _distance_ids_cache

    system_id = get_system_id()
    common_components = [194, 195, 196, 197, 198, 199, 200]

    try:
        # 1) Same vehicle, varying component (fast: up to 7 probes)
        for component_id in common_components:
            try:
                response = requests.get(
                    f"{base_url}/vehicles/{system_id}/components/{component_id}/messages/DISTANCE_SENSOR",
                    timeout=3)
                if response.status_code == 200 and _distance_payload_valid(response.json()):
                    print(f"Found distance sensor at vehicle {system_id}, component {component_id}")
                    _distance_ids_cache = (system_id, component_id)
                    return _distance_ids_cache
            except Exception:
                pass

        # 2) Different vehicle ID, standard component 194
        for candidate_id in range(1, 101):
            if candidate_id == system_id:
                continue
            try:
                response = requests.get(
                    f"{base_url}/vehicles/{candidate_id}/components/194/messages/DISTANCE_SENSOR",
                    timeout=0.75)
                if response.status_code == 200 and _distance_payload_valid(response.json()):
                    print(f"Found distance sensor at vehicle {candidate_id}, component 194")
                    _distance_ids_cache = (candidate_id, 194)
                    return _distance_ids_cache
            except Exception:
                pass

        print("Warning: No distance sensor found, defaulting to nav system ID with component 194")
        _distance_ids_cache = (system_id, 194)
        return _distance_ids_cache
    except Exception as e:
        print(f"Warning: Error detecting distance sensor: {e}, defaulting to component 194")
        _distance_ids_cache = (system_id, 194)
        return _distance_ids_cache

def get_urls():
    """Get the correct URLs based on the detected nav and distance-sensor IDs."""
    global _urls_cache
    
    # Return cached URLs if available
    if _urls_cache is not None:
        return _urls_cache
    
    nav_id = get_system_id()
    distance_vehicle, distance_component = get_distance_sensor_ids()
    _urls_cache = {
        'distance': f"{base_url}/vehicles/{distance_vehicle}/components/{distance_component}/messages/DISTANCE_SENSOR",
        'gps': f"{base_url}/vehicles/{nav_id}/components/1/messages/GLOBAL_POSITION_INT",
        'yaw': f"{base_url}/vehicles/{nav_id}/components/1/messages/ATTITUDE"
    }
    return _urls_cache

def main():
    global row_counter
    global data
    global current_log_file
    print("main started")
    
    # Ensure logs directory exists
    ensure_logs_dir()
    
    # Generate new log file name for this session
    current_log_file = generate_log_filename()
    print(f"Logging to: {current_log_file}")
    
    # Get the correct URLs based on system ID
    urls = get_urls()
    print(f"Using system ID: {get_system_id()}")
    sonar.start_session()

    while (logging_active == True): # Main loop for logging data
        urls = get_urls()  # cached; re-detected after the Ping service finds the Ping again
        # Timeouts keep one stalled mavlink2rest request from freezing the log.
        try:
            distance_response = requests.get(urls['distance'], timeout=2)
            gps_response = requests.get(urls['gps'], timeout=2)
            yaw_response = requests.get(urls['yaw'], timeout=2)
        except requests.RequestException as e:
            print(f"Warning: mavlink2rest request failed: {e}")
            time.sleep(1 / log_rate)
            continue
        
        # Check if GPS and yaw data are available (required)
        if gps_response.status_code == 200 and yaw_response.status_code == 200:
            try:
                gps_json = gps_response.json()
                yaw_json = yaw_response.json()
                gps_data = gps_json['message']
                yaw_data = yaw_json['message']
                
                # Handle distance sensor data (optional)
                distance_data = None
                distance_json = None
                if distance_response.status_code == 200:
                    try:
                        distance_json = distance_response.json()
                        distance_data = distance_json['message']
                    except json.JSONDecodeError as e:
                        print(f"Warning: Error decoding distance sensor JSON response: {distance_response.text}")
                        distance_data = None
                        distance_json = None
                else:
                    print(f"Warning: Distance sensor not available (status: {distance_response.status_code})")
                
            except json.JSONDecodeError as e:
                print(f"Error decoding JSON response:")
                print(f"GPS response: {gps_response.text}")
                print(f"Yaw response: {yaw_response.text}")
                print(f"Distance response: {distance_response.text}")
                raise e

            # Compute position/depth delta time using mavlink2rest reception timestamps.
            # Both last_update values are on the companion computer's wall clock,
            # so the delta is meaningful even though GPS and sonar are different components.
            position_depth_delta_ms = -1
            if distance_json is not None:
                try:
                    gps_last = datetime.fromisoformat(gps_json['status']['time']['last_update'])
                    dist_last = datetime.fromisoformat(distance_json['status']['time']['last_update'])
                    position_depth_delta_ms = round(abs((gps_last - dist_last).total_seconds() * 1000), 1)
                except (KeyError, ValueError) as e:
                    print(f"Warning: Could not compute position/depth delta: {e}")

            column_labels = ['Unix Timestamp', 'Date', 'Time','Depth (cm)', 'Confidence (%)', 'Vessel heading (deg)', 'Roll (deg)', 'Pitch (deg)', 'Latitude', 'Longitude', 'Altitude (m)', 'Pos/Depth Delta (ms)']
            timestamp = int(time.time() * 1000)
            dt = datetime.fromtimestamp(timestamp / 1000)
            unix_timestamp = timestamp
            timenow = dt.strftime('%H:%M:%S')
            date = dt.strftime('%m/%d/%y')
            # Handle distance data (use default values if not available)
            if distance_data is not None:
                distance = distance_data['current_distance']
                confidence = distance_data['signal_quality']
            else:
                distance = 0
                confidence = 0
            # Yaw
            yawRad = yaw_data['yaw']
            yawDeg = math.degrees(yawRad)
            yaw = round(((yawDeg + 360) % 360),2)
            # Roll
            rollRad = yaw_data['roll']
            rollDeg = math.degrees(rollRad)
            roll = round(((rollDeg + 360) % 360),2)
            # Pitch
            pitchRad = yaw_data['pitch']
            pitchDeg = math.degrees(pitchRad)
            pitch = round(((pitchDeg + 360) % 360),2)
            # Coordinates
            latitude = gps_data['lat'] / 1e7
            longitude = gps_data['lon'] / 1e7
            altitude = gps_data['alt'] / 1000  # GLOBAL_POSITION_INT.alt is in mm
            row = [unix_timestamp, date, timenow, distance, confidence, yaw, roll, pitch, latitude, longitude, altitude, position_depth_delta_ms]
            sonar.record_row(distance_json, distance)
            with _state_lock:
                data = row
            with open(current_log_file, 'a', newline='') as csvfile:
                writer = csv.writer(csvfile)
                if csvfile.tell() == 0:
                    writer.writerow(column_labels)
                writer.writerow(row)
                row_counter += 1

        else:
            # Print an error message if any of the required requests were unsuccessful
            if gps_response.status_code != 200:
                print(f"Error: GPS Data - {gps_response.status_code} - {gps_response.reason}")
            if yaw_response.status_code != 200:
                print(f"Error: Attitude Data - {yaw_response.status_code} - {yaw_response.reason}")
            if distance_response.status_code != 200:
                print(f"Warning: Distance sensor not available - {distance_response.status_code} - {distance_response.reason}")

        if row_counter % (log_rate * feedback_interval) == 0:
            print(f"Rows added to CSV: {row_counter}")
                
        time.sleep(1 / log_rate)

@app.route('/')
def home():
    return app.send_static_file("index.html")

@app.route('/widget')
def widget():
    return app.send_static_file("widget.html")

def _run_logger():
    try:
        main()
    finally:
        sonar.stop_session()


@app.route('/start')
def start_logging():
    global logging_active
    with _state_lock:
        if not logging_active:
            logging_active = True
            thread = threading.Thread(target=_run_logger, daemon=True)
            thread.start()
    return 'Started'

@app.route('/stop')
def stop_logging():
    global logging_active
    with _state_lock:
        logging_active = False
    return 'Stopped'

@app.route('/start_simulation')
def start_simulation():
    global simulation_active, simulation_data, simulation_index, logging_active, data
    
    with _state_lock:
        # Stop normal logging if it's running
        if logging_active:
            logging_active = False
    
    if os.path.exists(simulation_file):
        simulation_data = []
        try:
            with open(simulation_file, 'r') as csvfile:
                reader = csv.reader(csvfile)
                next(reader, None)  # Skip header
                for row in reader:
                    if len(row) >= 8:
                        # Pad old format (8 columns) to 11-column format
                        if len(row) < 11:
                            row.insert(6, "0")  # Roll
                            row.insert(7, "0")  # Pitch
                            row.append("0")     # Altitude
                        # Pad 11-column format to 12 columns
                        if len(row) < 12:
                            row.append("-1")    # Pos/Depth Delta (ms) unknown
                        simulation_data.append(row)
            
            if simulation_data:
                with _state_lock:
                    simulation_active = True
                    simulation_index = 0
                    data = simulation_data[0]
                
                thread = threading.Thread(target=simulation_loop, daemon=True)
                thread.start()
                return jsonify({"success": True, "data_rows": len(simulation_data),
                                "message": f"Loaded {len(simulation_data)} rows from simulation file"})
            else:
                return jsonify({"success": False, "message": "Simulation file is empty or invalid format."})
        except Exception as e:
            print(f"Simulation error: {str(e)}")
            return jsonify({"success": False, "message": f"Error loading simulation file: {str(e)}"})
    else:
        return jsonify({"success": False, "message": f"Simulation file not found: {simulation_file}"})

def simulation_loop():
    global simulation_active, simulation_data, simulation_index, data
    
    while simulation_active:
        if simulation_index < len(simulation_data):
            try:
                current_row = simulation_data[simulation_index]
                if len(current_row) >= 12:
                    with _state_lock:
                        data = current_row
                elif len(current_row) >= 11:
                    padded_row = list(current_row)
                    padded_row.append("-1")    # Pos/Depth Delta (ms) unknown
                    with _state_lock:
                        data = padded_row
                elif len(current_row) >= 8:
                    padded_row = list(current_row)
                    padded_row.insert(6, "0")  # Roll
                    padded_row.insert(7, "0")  # Pitch
                    padded_row.append("0")     # Altitude
                    padded_row.append("-1")    # Pos/Depth Delta (ms) unknown
                    with _state_lock:
                        data = padded_row
                
                with _state_lock:
                    simulation_index = (simulation_index + 1) % len(simulation_data)
            except Exception as e:
                print(f"Error in simulation loop: {str(e)}")
                with _state_lock:
                    simulation_index = (simulation_index + 1) % len(simulation_data)
        
        time.sleep((1 / log_rate) / simulation_speed)

@app.route('/stop_simulation')
def stop_simulation():
    global simulation_active
    with _state_lock:
        simulation_active = False
    return jsonify({"success": True, "message": "Simulation stopped"})

@app.route('/simulation_status')
def simulation_status():
    return jsonify({
        "simulation_active": simulation_active,
        "data_rows": len(simulation_data) if simulation_data else 0,
        "current_index": simulation_index
    })

@app.route('/register_service')
def servicenames():
    return '''
    {"name": "Simple Ping Survey",
    "description": "This extension makes it easy to record data from the Ping sonar and gps onboard the vehicle, keeping a poor communications link from interfering with the quality of collected survey data. When connected, the extension displays a data preview that is intended to aide in survey grid spacing determination while logging at 2Hz. Happy motoring!",
    "icon": "mdi-map-plus",
    "company": "Blue Robotics",
    "version": "1.3.1",
    "webpage": "https://github.com/vshie/pingSurvey",
    "api": "https://github.com/bluerobotics/BlueOS-docker"}
    '''
    

@app.route('/download')
def download_file():
    """Download the current log file."""
    global current_log_file
    if current_log_file and os.path.exists(current_log_file):
        # Flask 3: use max_age instead of cache_timeout
        return send_file(current_log_file, as_attachment=True, max_age=0)
    else:
        return jsonify({'error': 'No log file available'}), 404



@app.route('/data')
def get_data():
    global data
    with _state_lock:
        # Return empty array if no data has been collected yet
        if not data or len(data) == 0:
            return jsonify([])
        return jsonify(list(data))

@app.route('/tiles/<int:z>/<int:x>/<int:y>.png')
def serve_tile(z, x, y):
    """Serve map tiles with offline caching support."""
    try:
        # Get the map source from query parameter, default to 'arcgis' (Esri)
        map_source = request.args.get('source', 'arcgis')
        if map_source not in MAP_SOURCES:
            map_source = 'arcgis'  # Fallback to Esri if invalid source
        
        # First check if we have the tile cached (fast path)
        cached_tile = get_cached_tile(z, x, y)
        if cached_tile:
            return Response(cached_tile, mimetype='image/png')
        
        # If not cached, try to fetch from the selected map source
        source_config = MAP_SOURCES[map_source]
        tile_url = source_config['url'].format(x=x, y=y, z=z)
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'image/png,image/*,*/*;q=0.8',
        }
        
        response = requests.get(tile_url, timeout=10, headers=headers)
        
        if response.status_code == 200:
            tile_data = response.content
            cache_tile(z, x, y, tile_data)
            return Response(tile_data, mimetype='image/png')
        else:
            return Response(status=404)
            
    except Exception as e:
        print(f"Error serving tile {z}/{x}/{y}: {e}")
        return Response(status=500)

@app.route('/cache_stats')
def cache_stats():
    """Get offline cache statistics."""
    try:
        ensure_offline_maps_dir()
        
        if not os.path.exists(OFFLINE_MAPS_DIR):
            return jsonify({
                'cached_tiles': 0,
                'cache_size_mb': 0,
                'cache_limit_mb': TILE_CACHE_SIZE_LIMIT / (1024 * 1024),
                'cache_location': OFFLINE_MAPS_DIR
            })
        
        files = [f for f in os.listdir(OFFLINE_MAPS_DIR) if f.endswith('.png')]
        total_size = sum(os.path.getsize(os.path.join(OFFLINE_MAPS_DIR, f)) for f in files)
        
        stats = {
            'cached_tiles': len(files),
            'cache_size_mb': round(total_size / (1024 * 1024), 2),
            'cache_limit_mb': round(TILE_CACHE_SIZE_LIMIT / (1024 * 1024), 2),
            'cache_location': OFFLINE_MAPS_DIR
        }
        
        return jsonify(stats)
        
    except Exception as e:
        print(f"Error getting cache stats: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/clear_cache')
def clear_cache():
    """Clear the offline tile cache."""
    try:
        if os.path.exists(OFFLINE_MAPS_DIR):
            files = [f for f in os.listdir(OFFLINE_MAPS_DIR) if f.endswith('.png')]
            for f in files:
                os.remove(os.path.join(OFFLINE_MAPS_DIR, f))
            
            # Also clear metadata file
            metadata_file = os.path.join(OFFLINE_MAPS_DIR, 'recent_tiles.json')
            if os.path.exists(metadata_file):
                os.remove(metadata_file)
            
            print(f"Cleared {len(files)} cached tiles and metadata")
            return jsonify({'message': f'Cleared {len(files)} cached tiles and metadata'})
        else:
            return jsonify({'message': 'No cache to clear'})
            
    except Exception as e:
        print(f"Error clearing cache: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/map_sources')
def get_map_sources():
    """Get available map sources."""
    try:
        return jsonify(MAP_SOURCES)
    except Exception as e:
        print(f"Error getting map sources: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/tile_cached/<int:z>/<int:x>/<int:y>')
def check_tile_cached(z, x, y):
    """Check if a specific tile is already cached."""
    try:
        cached = is_tile_cached(z, x, y)
        return jsonify({'cached': cached})
    except Exception as e:
        print(f"Error checking if tile is cached: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/recent_cached_area')
def get_recent_cached_area():
    """Get the most recently cached tile area to center the map on."""
    try:
        ensure_offline_maps_dir()
        
        if not os.path.exists(OFFLINE_MAPS_DIR):
            return jsonify({
                'has_cached_tiles': False,
                'center_lat': 9.2,
                'center_lon': -133,
                'zoom': 10
            })
        
        # Get all cached tile files
        files = [f for f in os.listdir(OFFLINE_MAPS_DIR) if f.endswith('.png')]
        
        if not files:
            return jsonify({
                'has_cached_tiles': False,
                'center_lat': 9.2,
                'center_lon': -133,
                'zoom': 10
            })
        
        # Find the most recently modified file
        latest_file = max(files, key=lambda f: os.path.getmtime(os.path.join(OFFLINE_MAPS_DIR, f)))
        latest_time = os.path.getmtime(os.path.join(OFFLINE_MAPS_DIR, latest_file))
        
        # Extract coordinates from filename (hash format)
        # We need to reverse-engineer the hash to get coordinates
        # For now, we'll use a different approach - store metadata
        
        # Look for metadata file with recent tile info
        metadata_file = os.path.join(OFFLINE_MAPS_DIR, 'recent_tiles.json')
        if os.path.exists(metadata_file):
            try:
                with open(metadata_file, 'r') as f:
                    metadata = json.load(f)
                    if metadata and len(metadata) > 0:
                        # Get the most recent tile info
                        latest_tile = metadata[-1]  # Assuming it's stored in order
                        return jsonify({
                            'has_cached_tiles': True,
                            'center_lat': latest_tile['lat'],
                            'center_lon': latest_tile['lon'],
                            'zoom': latest_tile['z'],
                            'cached_tiles_count': len(files)
                        })
            except Exception as e:
                print(f"Error reading metadata: {e}")
        
        # Fallback: return default coordinates
        return jsonify({
            'has_cached_tiles': True,
            'center_lat': 9.2,
            'center_lon': -133,
            'zoom': 10,
            'cached_tiles_count': len(files)
        })
        
    except Exception as e:
        print(f"Error getting recent cached area: {e}")
        return jsonify({
            'has_cached_tiles': False,
            'center_lat': 9.2,
            'center_lon': -133,
            'zoom': 10
        })

@app.route('/status', methods=['GET'])
def status():
    with _state_lock:
        state = {"logging_active": logging_active, "simulation_active": simulation_active}
    state["sonar"] = sonar.status()
    return state


@app.route('/sonar_check')
def sonar_check():
    """Is BlueOS detecting the Ping, and is live DISTANCE_SENSOR data arriving? Works while idle."""
    sensors, error = sonar_watchdog.ping_service_sensors()
    age = None
    try:
        response = requests.get(get_urls()['distance'], timeout=3)
        if response.status_code == 200:
            age = sonar_watchdog.distance_age_s(response.json())
    except Exception:
        pass
    return jsonify({
        'ping_service_error': error,
        'sensors': sensors,
        'distance_age_s': None if age is None else round(age, 1),
        'receiving': age is not None and age <= sonar_watchdog.STALE_AFTER_S,
        'redetect': sonar.status()['redetect'],
    })


@app.route('/sonar_redetect', methods=['POST'])
def sonar_redetect():
    """Ask BlueOS to look for the Ping again (restarts its Ping service if no Ping is detected)."""
    started = sonar.redetect()
    return jsonify({'started': started, 'redetect': sonar.status()['redetect']}), (202 if started else 409)

@app.route('/log_files')
def list_log_files():
    """List available CSV log files."""
    logs_dir = LOGS_DIR
    if not os.path.exists(logs_dir):
        return jsonify({'files': []})
    
    files = []
    for f in os.listdir(logs_dir):
        if f.endswith('.csv') and f != 'simulation.csv':
            fpath = os.path.join(logs_dir, f)
            files.append({
                'filename': f,
                'path': fpath,
                'size_mb': round(os.path.getsize(fpath) / (1024 * 1024), 2),
                'modified': os.path.getmtime(fpath)
            })
    files.sort(key=lambda f: f['modified'], reverse=True)
    return jsonify({'files': files})


# ---------------------------------------------------------------------------
# Post-mission processing
# ---------------------------------------------------------------------------

MAX_PREVIEW_POINTS = 20000


def _safe_path(directory, filename, extensions):
    """Resolve filename inside directory, rejecting traversal and unexpected types.

    Names are matched as-is (logs downloaded from older versions contain spaces), but
    must be a bare file name that resolves to a file directly inside directory.
    """
    name = filename or ''
    if (not name or name != os.path.basename(name) or '\\' in name or name.startswith('.')
            or '\x00' in name or not name.lower().endswith(extensions)):
        return None
    path = os.path.join(directory, name)
    if os.path.dirname(os.path.realpath(path)) != os.path.realpath(directory):
        return None
    return path if os.path.isfile(path) else None


def _processing_unavailable():
    return jsonify({'error': f'Post-mission processing is unavailable on this image: {_contour_import_error}'}), 503


def _resolve_log_files(names):
    paths = []
    for n in names:
        p = _safe_path(LOGS_DIR, n, ('.csv',))
        if p is None:
            raise ValueError(f"Log file not found: {n}")
        paths.append(p)
    if not paths:
        raise ValueError("Select at least one survey log file")
    return paths


@app.route('/log_file/<path:filename>')
def download_log_file(filename):
    """Download a raw survey CSV by name."""
    path = _safe_path(LOGS_DIR, filename, ('.csv',))
    if path is None:
        return jsonify({'error': 'File not found'}), 404
    return send_file(path, as_attachment=True, max_age=0)


@app.route('/log_points')
def log_points():
    """Soundings for the selected files as [lat, lon, measured depth m, confidence, unix ms, file index]
    rows, plus per-file metadata (start/end time, altitude availability) for tide entry."""
    if contour_processing is None:
        return _processing_unavailable()
    names = [n for n in request.args.get('files', '').split(',') if n]
    try:
        points, files = contour_processing.load_survey(_resolve_log_files(names))
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    cp = contour_processing
    total = int(points.shape[0])
    stride = max(1, math.ceil(total / MAX_PREVIEW_POINTS))
    sample = points[::stride]
    rows = [[round(float(r[cp.LAT]), 7), round(float(r[cp.LON]), 7), round(float(r[cp.DEPTH]), 2),
             int(r[cp.CONF]), int(round(r[cp.TIME] * 1000)), int(r[cp.FILE])] for r in sample]
    bounds = None
    if total:
        bounds = [[float(points[:, cp.LAT].min()), float(points[:, cp.LON].min())],
                  [float(points[:, cp.LAT].max()), float(points[:, cp.LON].max())]]
    return jsonify({'points': rows, 'files': files, 'total': total, 'stride': stride, 'bounds': bounds})


def _online_tides(paths, settings, refresh=False):
    """Fetch (or load cached) online tide series for each log when online tides are selected.

    Returns {file name: series} or None. Raises ValueError with a user-facing message.
    """
    if settings.vertical_reference != 'tide' or settings.tide_source != 'online':
        return None
    points, files = contour_processing.load_survey(paths)
    series = {}
    for log in contour_processing.log_summaries(points, files):
        try:
            series[log['name']] = tide_service.series_for_log(
                log['name'], log['lat'], log['lon'], log['start'], log['end'],
                settings.prefer_measured, settings.max_station_km, refresh)
        except tides.TideError as e:
            raise ValueError(f"{log['name']}: {e}")
    return series


def _tide_summary(series, settings):
    """What went into the depth reduction, for the output record and UI."""
    if not series:
        return None
    out = {}
    for name, s in series.items():
        st = s.get('station') or {}
        out[name] = {
            'provider': s['provider'], 'station_id': st.get('id'), 'station_name': st.get('name'),
            'station_type': st.get('type'), 'distance_km': st.get('distance_km'), 'product': s['product'],
            'datum': settings.tide_datum.upper(), 'msl_above_datum_m': s['datums'].get(settings.tide_datum),
            'approximate': s.get('approximate', False), 'attribution': s.get('attribution'),
        }
    return out


def _processing_inputs(body):
    """Common request fields for /log_qc and /process_contours."""
    paths = _resolve_log_files(body.get('files') or [])
    settings = contour_processing.ContourSettings.from_dict(body.get('settings') or {})
    tides = body.get('tides') or {}
    if not isinstance(tides, dict):
        raise ValueError("tides must be an object keyed by file name")
    overlaps = body.get('exclude_overlaps') or []
    if not isinstance(overlaps, list) or not all(isinstance(r, dict) for r in overlaps):
        raise ValueError("exclude_overlaps must be a list of {file, other} objects")
    return paths, settings, body.get('polygon') or None, tides, body.get('exclude') or [], overlaps


@app.route('/log_qc', methods=['POST'])
def log_qc():
    """Data-quality report for the soundings that would be meshed with these inputs."""
    if contour_processing is None:
        return _processing_unavailable()
    try:
        paths, settings, polygon, manual_tides, exclude, overlaps = _processing_inputs(request.get_json(silent=True) or {})
        series = _online_tides(paths, settings)
        return jsonify(contour_processing.analyse(paths, polygon, settings, manual_tides, exclude, overlaps, series))
    except ValueError as e:  # includes ProcessingError
        return jsonify({'error': str(e)}), 400


def _output_basename(paths, settings):
    """Name outputs after the survey and the settings a user is likely to vary.

    e.g. ping_survey_2026-10-01_16-10-00_contours_0.5m-2m_conf95
    """
    stem = os.path.splitext(os.path.basename(paths[0]))[0]
    if len(paths) > 1:
        stem += f"_plus{len(paths) - 1}"
    stem = re.sub(r'[^A-Za-z0-9_.-]+', '_', stem)[:80]
    if settings.vertical_reference == 'gps':
        ref = '_gps'
    elif settings.tide_source == 'online':
        ref = '_' + settings.tide_datum      # depths below LAT / MLLW / MSL
    else:
        ref = ''
    return f"{stem}_contours_{settings.minor_m:g}m-{settings.major_m:g}m_conf{settings.min_conf:g}{ref}"


def _remove_other_outputs(keep_base):
    """Only the latest output is kept, so the files on disk always match what the UI shows."""
    for f in os.listdir(PROCESSED_DIR):
        if f.startswith(keep_base + '.') or f.startswith(keep_base + '_preview.'):
            continue
        try:
            os.remove(os.path.join(PROCESSED_DIR, f))
        except OSError as e:
            print(f"Could not remove old output {f}: {e}")


@app.route('/process_contours', methods=['POST'])
def process_contours():
    """Build a TIN mesh from the selected logs and write a transparent contour GeoTIFF."""
    if contour_processing is None:
        return _processing_unavailable()
    body = request.get_json(silent=True) or {}
    try:
        paths, settings, polygon, manual_tides, exclude, overlaps = _processing_inputs(body)
        series = _online_tides(paths, settings)

        os.makedirs(PROCESSED_DIR, exist_ok=True)
        # Write under a temporary name: with auto-tune the intervals in the final name are only
        # known once processing has run.
        tmp = os.path.join(PROCESSED_DIR, f".processing_{os.getpid()}_{threading.get_ident()}")
        started = time.time()
        summary = contour_processing.process(paths, polygon, settings, tmp + '.tif', tmp + '.png',
                                             manual_tides, exclude, overlaps, series)
        effective = contour_processing.ContourSettings.from_dict(summary['settings'])
        base = _output_basename(paths, effective)
        tif = os.path.join(PROCESSED_DIR, base + '.tif')
        preview = os.path.join(PROCESSED_DIR, base + '_preview.png')
        os.replace(tmp + '.tif', tif)
        os.replace(tmp + '.png', preview)
    except ValueError as e:  # includes ProcessingError
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        print(f"Contour processing failed: {e}")
        return jsonify({'error': f'Processing failed: {e}'}), 500

    summary.update({
        'tif': os.path.basename(tif),
        'preview': os.path.basename(preview),
        'source_files': [os.path.basename(p) for p in paths],
        'polygon': polygon,
        'tides': manual_tides,
        'tide_sources': _tide_summary(series, settings),
        'exclude': exclude,
        'exclude_overlaps': overlaps,
        'ignored': body.get('ignored') or [],
        'created': time.time(),
        'elapsed_s': round(time.time() - started, 2),
        'size_mb': round(os.path.getsize(tif) / (1024 * 1024), 2),
    })
    with open(os.path.join(PROCESSED_DIR, base + '.json'), 'w') as f:
        json.dump(summary, f)
    _remove_other_outputs(base)
    print(f"Generated {summary['tif']} in {summary['elapsed_s']}s")
    return jsonify(summary)


@app.route('/tide_fetch', methods=['POST'])
def tide_fetch():
    """Online tide series for each selected log, shown in the UI before processing."""
    if contour_processing is None:
        return _processing_unavailable()
    body = request.get_json(silent=True) or {}
    try:
        paths = _resolve_log_files(body.get('files') or [])
        settings = contour_processing.ContourSettings.from_dict(body.get('settings') or {})
        points, files = contour_processing.load_survey(paths)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    datum = settings.tide_datum
    results = {}
    for log in contour_processing.log_summaries(points, files):
        try:
            s = tide_service.series_for_log(log['name'], log['lat'], log['lon'], log['start'], log['end'],
                                            settings.prefer_measured, settings.max_station_km,
                                            bool(body.get('refresh')))
        except tides.TideError as e:
            results[log['name']] = {'error': str(e)}
            continue
        offset = s['datums'].get(datum)
        times, heights = s['times'], s['heights']
        in_log = [(t, h) for t, h in zip(times, heights) if log['start'] - 60 <= t <= log['end'] + 60]
        step = max(1, len(in_log) // 60)
        spark = in_log[::step]
        level = lambda t: round(float(_interp(t, times, heights)) + (offset or 0), 3)
        results[log['name']] = {
            'provider': s['provider'], 'station': s.get('station'), 'product': s['product'],
            'approximate': s.get('approximate', False), 'warnings': s.get('warnings', []),
            'attribution': s.get('attribution'), 'cached': s.get('cached', False), 'fetched_at': s.get('fetched_at'),
            'datums': s['datums'], 'datum': datum, 'datum_available': offset is not None,
            'start': log['start'], 'end': log['end'],
            'start_m': level(log['start']) if offset is not None else None,
            'end_m': level(log['end']) if offset is not None else None,
            'min_m': round(min([h + offset for _, h in in_log] + [level(log['start']), level(log['end'])]), 3)
            if offset is not None else None,
            'max_m': round(max([h + offset for _, h in in_log] + [level(log['start']), level(log['end'])]), 3)
            if offset is not None else None,
            'series': [[t, round(h + (offset or 0), 3)] for t, h in spark],
        }
    return jsonify({'files': results})


def _interp(t, times, heights):
    import bisect
    i = bisect.bisect_left(times, t)
    if i <= 0:
        return heights[0]
    if i >= len(times):
        return heights[-1]
    t0, t1 = times[i - 1], times[i]
    return heights[i - 1] + (heights[i] - heights[i - 1]) * (t - t0) / (t1 - t0)


@app.route('/tide_config', methods=['GET', 'POST'])
def tide_config():
    """WorldTides API key (stored on the vehicle; only a masked form is ever returned)."""
    if request.method == 'POST':
        body = request.get_json(silent=True) or {}
        tide_service.set_worldtides_key(str(body.get('worldtides_key') or ''))
    return jsonify(tide_service.public_config())


@app.route('/tide_config/test', methods=['POST'])
def tide_config_test():
    body = request.get_json(silent=True) or {}
    key = str(body.get('worldtides_key') or '') or tide_service.worldtides_key()
    if not key:
        return jsonify({'error': 'Enter a WorldTides API key first'}), 400
    try:
        return jsonify(tide_service.test_worldtides_key(key))
    except tides.TideError as e:
        return jsonify({'error': str(e)}), 400


@app.route('/processed_files')
def processed_files():
    """Return the most recently generated contour GeoTIFF (only one is kept)."""
    latest = None
    if os.path.isdir(PROCESSED_DIR):
        for f in os.listdir(PROCESSED_DIR):
            if not f.endswith('.json'):
                continue
            try:
                with open(os.path.join(PROCESSED_DIR, f)) as fh:
                    meta = json.load(fh)
            except (OSError, ValueError):
                continue
            if not os.path.isfile(os.path.join(PROCESSED_DIR, meta.get('tif', ''))):
                continue
            if latest is None or meta.get('created', 0) > latest.get('created', 0):
                latest = meta
    return jsonify({'files': [latest] if latest else []})


@app.route('/processed/<path:filename>')
def download_processed(filename):
    """Serve a generated GeoTIFF (as a download) or its PNG preview."""
    path = _safe_path(PROCESSED_DIR, filename, ('.tif', '.png'))
    if path is None:
        return jsonify({'error': 'File not found'}), 404
    return send_file(path, as_attachment=path.endswith('.tif'), max_age=0)


if __name__ == '__main__':
    # Ensure logs directory exists at startup
    ensure_logs_dir()
    ensure_offline_maps_dir()
    
    print("Starting Ping Survey Extension...")
    print(f"Logs directory: {LOGS_DIR}")
    print(f"Offline maps directory: {OFFLINE_MAPS_DIR}")
    print(f"Cache size limit: {TILE_CACHE_SIZE_LIMIT / (1024**3):.1f} GB")
    
    app.run(host='0.0.0.0', port=5420)