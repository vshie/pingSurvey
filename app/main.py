from flask import Flask, render_template, send_file, jsonify, request #used as back-end service for Vue2 WebApp
import requests #used to communicate with Vue2 app
import csv #used to work with output data file
import time #used for getting current OS time
import threading #used to run main app within a thread
import math #used for yaw radian to degree calc
from datetime import datetime #used for timestamps
import json #used for JSON decoding
import os #used for file operations
import re #used for sanitizing survey names / timestamps in filenames

print("hello we are in the script world")
app = Flask(__name__, static_url_path="/static", static_folder="static") #setup flask app

logging_active = False# Global variable to control the logging 
simulation_active = False # Global variable to control simulation mode
simulation_index = 0 # Index to track which row of simulation data we're on
simulation_data = [] # Store simulation data from CSV
base_url = 'http://host.docker.internal/mavlink2rest/mavlink'

LOGS_DIR = '/app/logs'
STATE_FILE = os.path.join(LOGS_DIR, '.current.json')
simulation_file = os.path.join(LOGS_DIR, 'simulation.csv')

# Active survey log (full path) and calendar day key (YYYY-MM-DD) from browser
current_log_file = None
current_log_day = None


def _ensure_logs_dir():
    os.makedirs(LOGS_DIR, exist_ok=True)


def _clear_state_file_only():
    try:
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)
    except OSError:
        pass


def _clear_state():
    global current_log_file, current_log_day
    current_log_file = None
    current_log_day = None
    _clear_state_file_only()


def _save_state(file_path, day):
    _ensure_logs_dir()
    with open(STATE_FILE, 'w') as f:
        json.dump({"file": file_path, "day": day}, f)


def _load_state():
    """Restore current_log_file / current_log_day from disk if valid."""
    global current_log_file, current_log_day
    current_log_file = None
    current_log_day = None
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, 'r') as f:
            st = json.load(f)
        path = st.get('file')
        day = st.get('day')
        if path and day and os.path.isfile(path):
            current_log_file = path
            current_log_day = day
        else:
            _clear_state_file_only()
    except Exception as e:
        print(f"Warning: could not load state: {e}")
        _clear_state_file_only()


def _sanitize_name(raw):
    if not raw or not str(raw).strip():
        return 'Survey'
    s = str(raw).strip().replace('\\', '_').replace('/', '_')
    s = re.sub(r'\s+', '_', s)
    s = re.sub(r'[^A-Za-z0-9_-]+', '', s)
    s = s.strip('_') or 'Survey'
    return s[:60]


def _build_log_path(name, timestamp_iso):
    """Build log path under LOGS_DIR; timestamp from client ISO string (colons -> -)."""
    ts = (timestamp_iso or datetime.now().isoformat()).replace(':', '-')
    for c in '<>:"|?*':
        ts = ts.replace(c, '-')
    safe_name = _sanitize_name(name)
    fname = f"{safe_name}_{ts}.csv"
    return os.path.join(LOGS_DIR, fname)


def _resolve_log_for_start(day, name, timestamp_iso):
    """Reuse same-day existing file if present; otherwise create new path and persist state."""
    global current_log_file, current_log_day
    reuse = (
        current_log_file
        and current_log_day == day
        and os.path.isfile(current_log_file)
    )
    if reuse:
        return
    if not timestamp_iso:
        timestamp_iso = datetime.now().isoformat()
    display_name = name if name and str(name).strip() else 'Survey'
    current_log_file = _build_log_path(display_name, timestamp_iso)
    current_log_day = day
    _ensure_logs_dir()
    # Create the file immediately so same-day reuse works even before first sensor row.
    open(current_log_file, 'a').close()
    _save_state(current_log_file, current_log_day)
log_rate = 2 #Desired rate in Hz
simulation_speed = 5 #Playback speed multiplier for simulation
data = []
row_counter = 0
feedback_interval = 5 # Define the feedback interval (in seconds)

def get_system_id():
    """Detect the correct system ID by probing vehicle IDs until a valid response is found.

    Strategy:
    1) Try to enumerate vehicles from /vehicles and validate candidates via /info where available.
    2) If that does not yield a valid ID, probe GLOBAL_POSITION_INT on vehicles/{id} incrementally starting from 1.
    """
    try:
        # First attempt: use vehicles listing and autopilot type to identify a valid vehicle ID
        response = requests.get(f"{base_url}/vehicles")
        if response.status_code == 200:
            vehicles = response.json()
            if vehicles and len(vehicles) > 0:
                for vehicle_id in vehicles:
                    try:
                        vehicle_info = requests.get(f"{base_url}/vehicles/{vehicle_id}/info")
                        if vehicle_info.status_code == 200:
                            info = vehicle_info.json()
                            autopilot_type = info.get("autopilot", {}).get("type")
                            valid_autopilots = [
                                "MAV_AUTOPILOT_GENERIC",
                                "MAV_AUTOPILOT_ARDUPILOTMEGA",
                                "MAV_AUTOPILOT_PX4",
                            ]
                            if autopilot_type in valid_autopilots:
                                print(f"Found valid autopilot: {autopilot_type} with system ID: {vehicle_id}")
                                return vehicle_id
                    except Exception:
                        # If querying info fails, continue to probing strategy below
                        pass

        # Fallback: Incrementally probe GLOBAL_POSITION_INT for vehicle IDs starting at 1
        # Limit the search range to avoid infinite loops
        max_vehicle_id = 100
        for candidate_id in range(1, max_vehicle_id + 1):
            gps_url = f"{base_url}/vehicles/{candidate_id}/components/1/messages/GLOBAL_POSITION_INT"
            try:
                gps_response = requests.get(gps_url, timeout=0.75)
                if gps_response.status_code == 200:
                    # Consider it valid if JSON contains a message or a reasonable structure
                    try:
                        payload = gps_response.json()
                        if isinstance(payload, dict) and ("message" in payload or "time_boot_ms" in payload):
                            print(f"Detected system ID by probing: {candidate_id}")
                            return candidate_id
                    except ValueError:
                        # Non-JSON or invalid payload, treat as not valid
                        pass
            except Exception:
                # Timeout/connection errors: try next candidate
                pass

        print("Warning: Could not detect system ID via probing, using default value of 1")
        return 1
    except Exception as e:
        print(f"Warning: Error detecting system ID: {e}, using default value of 1")
        return 1

def get_distance_system_id():
    """Detect the system ID for the Ping distance sensor, which may differ from GPS/yaw.

    Probes the DISTANCE_SENSOR endpoint across vehicle IDs until a valid response is found.
    """
    try:
        max_vehicle_id = 100
        for candidate_id in range(1, max_vehicle_id + 1):
            distance_url = f"{base_url}/vehicles/{candidate_id}/components/194/messages/DISTANCE_SENSOR"
            try:
                distance_response = requests.get(distance_url, timeout=0.75)
                if distance_response.status_code == 200:
                    try:
                        payload = distance_response.json()
                        if isinstance(payload, dict) and ("message" in payload):
                            # Basic plausibility check for expected keys
                            message = payload.get("message", {})
                            if isinstance(message, dict) and ("current_distance" in message or "min_distance" in message or "max_distance" in message):
                                print(f"Detected distance system ID by probing: {candidate_id}")
                                return candidate_id
                    except ValueError:
                        pass
            except Exception:
                pass
        print("Warning: Could not detect distance system ID via probing, defaulting to 1")
        return 1
    except Exception as e:
        print(f"Warning: Error detecting distance system ID: {e}, using default value of 1")
        return 1

def get_system_ids():
    """Detect IDs for navigation (GPS/yaw) and distance sensor separately."""
    nav_id = get_system_id()
    distance_id = get_distance_system_id()
    return {"nav": nav_id, "distance": distance_id}

def get_urls():
    """Get the correct URLs based on detected system IDs for nav and distance."""
    ids = get_system_ids()
    nav_id = ids["nav"]
    distance_id = ids["distance"]
    return {
        'distance': f"{base_url}/vehicles/{distance_id}/components/194/messages/DISTANCE_SENSOR",
        'gps': f"{base_url}/vehicles/{nav_id}/components/1/messages/GLOBAL_POSITION_INT",
        'yaw': f"{base_url}/vehicles/{nav_id}/components/1/messages/ATTITUDE"
    }

def main():
    global row_counter
    global data
    print("main started")
    
    # Get the correct URLs based on system ID
    ids = get_system_ids()
    urls = {
        'distance': f"{base_url}/vehicles/{ids['distance']}/components/194/messages/DISTANCE_SENSOR",
        'gps': f"{base_url}/vehicles/{ids['nav']}/components/1/messages/GLOBAL_POSITION_INT",
        'yaw': f"{base_url}/vehicles/{ids['nav']}/components/1/messages/ATTITUDE"
    }
    print(f"Using system IDs - nav: {ids['nav']} distance: {ids['distance']}")
    
    while (logging_active == True): # Main loop for logging data
        distance_response = requests.get(urls['distance'])
        gps_response = requests.get(urls['gps'])
        yaw_response = requests.get(urls['yaw'])
        if distance_response.status_code == 200 and gps_response.status_code == 200 and yaw_response.status_code == 200: # Check if the requests were successful
            try:
                # Parse JSON safely; some endpoints may return 200 with empty body until first message arrives
                distance_payload = distance_response.json()
                gps_payload = gps_response.json()
                yaw_payload = yaw_response.json()

                if not isinstance(distance_payload, dict) or 'message' not in distance_payload:
                    print("Distance endpoint returned no message yet; retrying...")
                    time.sleep(1 / log_rate)
                    continue
                if not isinstance(gps_payload, dict) or 'message' not in gps_payload:
                    print("GPS endpoint returned no message yet; retrying...")
                    time.sleep(1 / log_rate)
                    continue
                if not isinstance(yaw_payload, dict) or 'message' not in yaw_payload:
                    print("Attitude endpoint returned no message yet; retrying...")
                    time.sleep(1 / log_rate)
                    continue

                distance_data = distance_payload['message']
                gps_data = gps_payload['message']
                yaw_data = yaw_payload['message']
            except (json.JSONDecodeError, ValueError) as e:
                print("Error decoding JSON response; will retry:")
                print(f"Distance response: {getattr(distance_response, 'text', None)}")
                print(f"GPS response: {getattr(gps_response, 'text', None)}")
                print(f"Yaw response: {getattr(yaw_response, 'text', None)}")
                time.sleep(1 / log_rate)
                continue
            column_labels = ['Unix Timestamp', 'Date', 'Time','Depth (cm)', 'Confidence (%)', 'Vessel heading (deg)', 'Roll (deg)', 'Pitch (deg)', 'Latitude', 'Longitude', 'Altitude (m)']
            timestamp = int(time.time() * 1000)  # Convert current time to milliseconds
            dt = datetime.fromtimestamp(timestamp / 1000)  # Convert timestamp to datetime object 
            unix_timestamp = timestamp
            timenow = dt.strftime('%H:%M:%S')
            date = dt.strftime('%m/%d/%y')
            distance = distance_data['current_distance']
            confidence = distance_data['signal_quality']
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
            altitude = gps_data['alt'] / 1e7
            data = [unix_timestamp, date, timenow, distance, confidence, yaw, roll, pitch, latitude, longitude, altitude]
            target_file = current_log_file
            if not target_file:
                # Defensive: should not happen because /start sets this before launching the thread
                print("No active log file set; skipping write")
                time.sleep(1 / log_rate)
                continue
            _ensure_logs_dir()
            with open(target_file, 'a', newline='') as csvfile: # Create or append to the log file and write the data
                writer = csv.writer(csvfile)
                if csvfile.tell() == 0: # Write the column labels as the header row (only for the first write)
                    writer.writerow(column_labels)
                writer.writerow(data) # Write the data as a new row
                row_counter += 1 # Increment the row counter

        else:
            # Print an error message if any of the requests were unsuccessful
            print(f"Error: Ping2 Data - {distance_response.status_code} - {distance_response.reason}")
            print(f"Error: GPS Data - {gps_response.status_code} - {gps_response.reason}")
            print(f"Error: Attitude Data - {yaw_response.status_code} - {yaw_response.reason}")

        if row_counter % (log_rate * feedback_interval) == 0:
            print(f"Rows added to CSV: {row_counter}")
                
        time.sleep(1 / log_rate)

        #except Exception as e:
        #    print(f"An error occurred: {e}")
        #    break

@app.route('/')
def home():
    return app.send_static_file("index.html")

@app.route('/widget')
def widget():
    return app.send_static_file("widget.html")

@app.route('/new')
def new_interface():
    return app.send_static_file("new_index.html")

def get_data():
    return jsonify(data)

@app.route('/current_log', methods=['GET'])
def current_log_info():
    """Report whether there is an active log file for the given browser-local day."""
    day = request.args.get('day', '').strip()
    if (
        day
        and current_log_file
        and current_log_day == day
        and os.path.isfile(current_log_file)
    ):
        return jsonify({
            "has_current": True,
            "filename": os.path.basename(current_log_file),
            "day": current_log_day,
        })
    return jsonify({"has_current": False})


@app.route('/start', methods=['GET', 'POST'])
def start_logging():
    """Start logging.

    POST body (preferred): {"name": "...", "day": "YYYY-MM-DD", "timestamp_iso": "..."}
    - If state already has a file for the given `day`, reuse it (ignore name/timestamp).
    - Otherwise create a new file named `<sanitized name>_<timestamp>.csv` and persist state.

    A bare GET (legacy) reuses the current file if present, else creates an auto-named
    file using server-local time/day.
    """
    global logging_active
    payload = {}
    if request.method == 'POST':
        try:
            payload = request.get_json(silent=True) or {}
        except Exception:
            payload = {}

    now = datetime.now()
    day = (payload.get('day') or now.strftime('%Y-%m-%d')).strip()
    name = payload.get('name')
    timestamp_iso = payload.get('timestamp_iso') or now.isoformat()

    _resolve_log_for_start(day, name, timestamp_iso)

    if not logging_active:
        logging_active = True
        thread = threading.Thread(target=main)
        thread.start()

    return jsonify({
        "status": "started",
        "filename": os.path.basename(current_log_file) if current_log_file else None,
        "day": current_log_day,
    })


@app.route('/stop')
def stop_logging():
    global logging_active
    logging_active = False
    return 'Stopped'

@app.route('/register_service')
def servicenames():
    return '''
    {"name": "Simple Ping Survey",
    "description": "This extension makes it easy to record data from the Ping sonar and gps onboard the vehicle, keeping a poor communications link from interfering with the quality of collected survey data. When connected, the extension displays a data preview that is intended to aide in survey grid spacing determination while logging at 2Hz. Happy motoring!",
    "icon": "mdi-map-plus",
    "company": "Blue Robotics",
    "version": "1.2.1",
    "webpage": "https://github.com/vshie/pingSurvey",
    "api": "https://github.com/bluerobotics/BlueOS-docker"}
    '''
    

@app.route('/download')
def download_file():
    if not current_log_file or not os.path.isfile(current_log_file):
        return jsonify({"success": False, "message": "No active log file to download"}), 404
    return send_file(
        current_log_file,
        as_attachment=True,
        attachment_filename=os.path.basename(current_log_file),
        cache_timeout=0,
    )


@app.route('/delete_old_logs', methods=['POST'])
def delete_old_logs():
    """Delete every CSV in LOGS_DIR except the active log file and simulation.csv."""
    _ensure_logs_dir()
    deleted = []
    errors = []
    keep = os.path.basename(current_log_file) if current_log_file else None
    try:
        entries = os.listdir(LOGS_DIR)
    except OSError as e:
        return jsonify({"success": False, "message": f"Could not read logs dir: {e}"}), 500
    for entry in entries:
        if not entry.lower().endswith('.csv'):
            continue
        if entry == 'simulation.csv':
            continue
        if keep and entry == keep:
            continue
        full_path = os.path.join(LOGS_DIR, entry)
        try:
            os.remove(full_path)
            deleted.append(entry)
        except OSError as e:
            errors.append({"file": entry, "error": str(e)})
    return jsonify({
        "success": True,
        "deleted": deleted,
        "kept": keep,
        "errors": errors,
    })


@app.route('/data')
def get_data():
    global data
    return jsonify(data)

@app.route('/status', methods=['GET'])
def status():
    return jsonify({
        "logging_active": logging_active,
        "simulation_active": simulation_active,
        "current_log_file": os.path.basename(current_log_file) if current_log_file else None,
        "current_log_day": current_log_day,
    })

@app.route('/start_simulation')
def start_simulation():
    global simulation_active, simulation_data, simulation_index, logging_active, data
    
    # Stop normal logging if it's running
    if logging_active:
        logging_active = False
    
    # Load simulation data from CSV file
    if os.path.exists(simulation_file):
        simulation_data = []
        try:
            with open(simulation_file, 'r') as csvfile:
                reader = csv.reader(csvfile)
                # Skip header row
                next(reader, None)
                for row in reader:
                    # Handle both old format (8 columns) and new format (11 columns)
                    if len(row) >= 8:
                        # If using old format (without roll, pitch, altitude), add default values
                        if len(row) < 11:
                            # Original format: [timestamp, date, time, depth, confidence, yaw, lat, lon]
                            # Add default values for roll(0), pitch(0), and altitude(0) at the correct positions
                            # New format: [timestamp, date, time, depth, confidence, yaw, roll, pitch, lat, lon, alt]
                            lat = row[6]
                            lon = row[7]
                            # Insert roll and pitch after yaw, and before lat/lon
                            row.insert(6, "0")  # Roll
                            row.insert(7, "0")  # Pitch
                            # Add altitude at the end
                            row.append("0")     # Altitude
                        simulation_data.append(row)
            
            if simulation_data:
                simulation_active = True
                simulation_index = 0
                
                # Initialize data with first row
                if simulation_index < len(simulation_data):
                    data = simulation_data[simulation_index]
                    simulation_index = (simulation_index + 1) % len(simulation_data)
                
                # Start simulation thread
                thread = threading.Thread(target=simulation_loop)
                thread.start()
                return jsonify({"success": True, "data_rows": len(simulation_data), "message": f"Loaded {len(simulation_data)} rows from simulation file"})
            else:
                return jsonify({"success": False, "message": "Simulation file is empty or contains invalid data format. The file needs at least 8 columns of data."})
        except Exception as e:
            print(f"Simulation error: {str(e)}")
            return jsonify({"success": False, "message": f"Error loading simulation file: {str(e)}. Check if your CSV has at least 8 columns of data."})
    else:
        return jsonify({"success": False, "message": f"Simulation file not found: {simulation_file}"})

def simulation_loop():
    global simulation_active, simulation_data, simulation_index, data
    
    while simulation_active:
        if simulation_index < len(simulation_data):
            try:
                current_row = simulation_data[simulation_index]
                
                # Ensure data has the required format
                if len(current_row) >= 11:
                    # Good to go, use as is
                    data = current_row
                elif len(current_row) >= 8:
                    # Old format - need to add roll, pitch, altitude
                    # This should have been handled during loading, but just in case
                    padded_row = list(current_row)
                    lat = padded_row[6]
                    lon = padded_row[7]
                    # Insert roll and pitch after yaw, before lat/lon
                    padded_row.insert(6, "0")  # Roll
                    padded_row.insert(7, "0")  # Pitch
                    # Add altitude at the end
                    padded_row.append("0")     # Altitude
                    data = padded_row
                else:
                    # Invalid format, skip this row
                    print(f"Warning: Skipping invalid data row at index {simulation_index}")
                    
                simulation_index = (simulation_index + 1) % len(simulation_data)
            except Exception as e:
                print(f"Error in simulation loop: {str(e)}")
                # Move to next row
                simulation_index = (simulation_index + 1) % len(simulation_data)
        
        # Use adjusted sleep time for faster playback
        time.sleep((1 / log_rate) / simulation_speed)

@app.route('/stop_simulation')
def stop_simulation():
    global simulation_active
    simulation_active = False
    return jsonify({"success": True, "message": "Simulation stopped"})

@app.route('/simulation_status')
def simulation_status():
    return jsonify({
        "simulation_active": simulation_active, 
        "data_rows": len(simulation_data) if simulation_data else 0,
        "current_index": simulation_index,
        "data_format": "enhanced" if simulation_data and len(simulation_data[0]) >= 11 else "legacy"
    })

_load_state()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5420)
