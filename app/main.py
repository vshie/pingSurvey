from flask import Flask, send_file, jsonify, request, Response
import requests
import csv
import time
import threading
import math
from datetime import datetime
import json
import os
import hashlib

# Offline map tile caching - store in external file system alongside logs
OFFLINE_MAPS_DIR = '/app/logs/offline_maps'
TILE_CACHE_SIZE_LIMIT = 5 * 1024 * 1024 * 1024  # 5GB cache limit

# Performance optimization: Global state to avoid redundant operations
_offline_maps_dir_verified = False
_system_id_cache = None
_distance_component_cache = None
_urls_cache = None
_cache_size_bytes = 0
_cache_size_last_check = 0
_CACHE_SIZE_CHECK_INTERVAL = 60  # Only recalculate cache size every 60 seconds

# Map tile sources
MAP_SOURCES = {
    'google': {
        'name': 'Google Maps',
        'url': 'https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}',
        'attribution': '&copy; <a href="https://www.google.com/maps">Google Maps</a>'
    },
    'arcgis': {
        'name': 'ArcGIS World Imagery',
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
base_url = 'http://host.docker.internal/mavlink2rest/mavlink'
log_rate = 2  # Desired rate in Hz
simulation_speed = 5  # Playback speed multiplier
simulation_file = '/app/logs/simulation.csv'

data = []
row_counter = 0
feedback_interval = 5  # Define the feedback interval (in seconds)
current_log_file = None  # Will be set when logging starts

def ensure_logs_dir():
    """Ensure the logs directory exists."""
    logs_dir = '/app/logs'
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
    return f'/app/logs/ping_survey_{date_str}_{time_str}.csv'

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
                print("Warning: No valid autopilot found, using default value of 1")
                _system_id_cache = 1
                return 1
        print("Warning: Could not detect system ID, using default value of 1")
        _system_id_cache = 1
        return 1
    except Exception as e:
        print(f"Warning: Error detecting system ID: {e}, using default value of 1")
        _system_id_cache = 1
        return 1

def get_distance_sensor_component():
    """Detect the correct component ID for the distance sensor."""
    global _distance_component_cache
    
    # Return cached value if available
    if _distance_component_cache is not None:
        return _distance_component_cache
    
    system_id = get_system_id()
    try:
        # Try common component IDs for distance sensors
        common_ids = [194, 195, 196, 197, 198, 199, 200]
        for component_id in common_ids:
            response = requests.get(f"{base_url}/vehicles/{system_id}/components/{component_id}/messages/DISTANCE_SENSOR", timeout=3)
            if response.status_code == 200:
                print(f"Found distance sensor at component ID: {component_id}")
                _distance_component_cache = component_id
                return component_id
        print("Warning: No distance sensor found, using default component ID 194")
        _distance_component_cache = 194
        return 194
    except Exception as e:
        print(f"Warning: Error detecting distance sensor component ID: {e}, using default value of 194")
        _distance_component_cache = 194
        return 194

def get_urls():
    """Get the correct URLs based on the detected system ID."""
    global _urls_cache
    
    # Return cached URLs if available
    if _urls_cache is not None:
        return _urls_cache
    
    system_id = get_system_id()
    distance_component = get_distance_sensor_component()
    _urls_cache = {
        'distance': f"{base_url}/vehicles/{system_id}/components/{distance_component}/messages/DISTANCE_SENSOR",
        'gps': f"{base_url}/vehicles/{system_id}/components/1/messages/GLOBAL_POSITION_INT",
        'yaw': f"{base_url}/vehicles/{system_id}/components/1/messages/ATTITUDE"
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
    
    while (logging_active == True): # Main loop for logging data
        distance_response = requests.get(urls['distance'])
        gps_response = requests.get(urls['gps'])
        yaw_response = requests.get(urls['yaw'])
        
        # Check if GPS and yaw data are available (required)
        if gps_response.status_code == 200 and yaw_response.status_code == 200:
            try:
                gps_data = gps_response.json()['message']
                yaw_data = yaw_response.json()['message']
                
                # Handle distance sensor data (optional)
                distance_data = None
                if distance_response.status_code == 200:
                    try:
                        distance_data = distance_response.json()['message']
                    except json.JSONDecodeError as e:
                        print(f"Warning: Error decoding distance sensor JSON response: {distance_response.text}")
                        distance_data = None
                else:
                    print(f"Warning: Distance sensor not available (status: {distance_response.status_code})")
                
            except json.JSONDecodeError as e:
                print(f"Error decoding JSON response:")
                print(f"GPS response: {gps_response.text}")
                print(f"Yaw response: {yaw_response.text}")
                print(f"Distance response: {distance_response.text}")
                raise e
            column_labels = ['Unix Timestamp', 'Date', 'Time','Depth (cm)', 'Confidence (%)', 'Vessel heading (deg)', 'Roll (deg)', 'Pitch (deg)', 'Latitude', 'Longitude', 'Altitude (m)']
            timestamp = int(time.time() * 1000)  # Convert current time to milliseconds
            dt = datetime.fromtimestamp(timestamp / 1000)  # Convert timestamp to datetime object 
            unix_timestamp = timestamp
            timenow = dt.strftime('%H:%M:%S')
            date = dt.strftime('%m/%d/%y')
            # Handle distance data (use default values if not available)
            if distance_data is not None:
                distance = distance_data['current_distance']
                confidence = distance_data['signal_quality']
            else:
                distance = 0  # Default depth value
                confidence = 0  # Default confidence value
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
            row = [unix_timestamp, date, timenow, distance, confidence, yaw, roll, pitch, latitude, longitude, altitude]
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

@app.route('/start')
def start_logging():
    global logging_active
    with _state_lock:
        if not logging_active:
            logging_active = True
            thread = threading.Thread(target=main, daemon=True)
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
                        # Pad old format (8 columns) to new format (11 columns)
                        if len(row) < 11:
                            row.insert(6, "0")  # Roll
                            row.insert(7, "0")  # Pitch
                            row.append("0")     # Altitude
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
                if len(current_row) >= 11:
                    with _state_lock:
                        data = current_row
                elif len(current_row) >= 8:
                    padded_row = list(current_row)
                    padded_row.insert(6, "0")  # Roll
                    padded_row.insert(7, "0")  # Pitch
                    padded_row.append("0")     # Altitude
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
    "version": "0.5",
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
        # Get the map source from query parameter, default to 'google'
        map_source = request.args.get('source', 'google')
        if map_source not in MAP_SOURCES:
            map_source = 'google'  # Fallback to Google if invalid source
        
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
        return {"logging_active": logging_active, "simulation_active": simulation_active}

@app.route('/generate_contour', methods=['POST'])
def generate_contour():
    """Generate a contour map from a CSV file."""
    try:
        from contour_generator import generate_contour_map
        
        data = request.get_json() if request.is_json else {}
        csv_file = data.get('csv_file', current_log_file)
        primary = data.get('primary_interval', 5.0)
        secondary = data.get('secondary_interval', 1.0)
        tidal_offset = data.get('tidal_offset', 0.0)
        
        if not csv_file or not os.path.exists(csv_file):
            # Try to find the most recent CSV in logs
            logs_dir = '/app/logs'
            csv_files = [f for f in os.listdir(logs_dir) if f.endswith('.csv') and f != 'simulation.csv']
            if csv_files:
                csv_files.sort(key=lambda f: os.path.getmtime(os.path.join(logs_dir, f)), reverse=True)
                csv_file = os.path.join(logs_dir, csv_files[0])
            else:
                return jsonify({'success': False, 'message': 'No CSV files found in logs directory'}), 404
        
        result = generate_contour_map(csv_file, primary_interval=primary, secondary_interval=secondary,
                                      tidal_offset=tidal_offset)
        return jsonify(result)
    except ImportError as e:
        return jsonify({'success': False, 'message': f'Contour generator not available: {str(e)}'}), 500
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500

@app.route('/contour_maps')
def list_contour_maps():
    """List available contour maps."""
    contour_dir = '/app/logs/contour_maps'
    if not os.path.exists(contour_dir):
        return jsonify({'maps': []})
    
    maps = []
    for f in os.listdir(contour_dir):
        if f.endswith('.html'):
            fpath = os.path.join(contour_dir, f)
            maps.append({
                'filename': f,
                'size_mb': round(os.path.getsize(fpath) / (1024 * 1024), 2),
                'created': os.path.getmtime(fpath)
            })
    maps.sort(key=lambda m: m['created'], reverse=True)
    return jsonify({'maps': maps})

@app.route('/contour_map/<filename>')
def serve_contour_map(filename):
    """Serve a generated contour map."""
    contour_dir = '/app/logs/contour_maps'
    filepath = os.path.join(contour_dir, filename)
    if os.path.exists(filepath) and filename.endswith('.html'):
        return send_file(filepath, mimetype='text/html')
    return jsonify({'error': 'Contour map not found'}), 404

@app.route('/log_files')
def list_log_files():
    """List available CSV log files."""
    logs_dir = '/app/logs'
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

if __name__ == '__main__':
    # Ensure logs directory exists at startup
    ensure_logs_dir()
    ensure_offline_maps_dir()
    
    print("Starting Ping Survey Extension...")
    print(f"Logs directory: /app/logs")
    print(f"Offline maps directory: {OFFLINE_MAPS_DIR}")
    print(f"Cache size limit: {TILE_CACHE_SIZE_LIMIT / (1024**3):.1f} GB")
    
    app.run(host='0.0.0.0', port=5420)