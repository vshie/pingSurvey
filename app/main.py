from flask import Flask, render_template, send_file, jsonify #used as back-end service for Vue2 WebApp
import requests #used to communicate with Vue2 app
import csv #used to work with output data file
import time #used for getting current OS time
import threading #used to run main app within a thread
import math #used for yaw radian to degree calc
from datetime import datetime #used for timestamps
import json #used for JSON decoding
import os #used for file operations
print("hello we are in the script world")
app = Flask(__name__, static_url_path="/static", static_folder="static") #setup flask app

logging_active = False# Global variable to control the logging 
simulation_active = False # Global variable to control simulation mode
simulation_index = 0 # Index to track which row of simulation data we're on
simulation_data = [] # Store simulation data from CSV
base_url = 'http://host.docker.internal/mavlink2rest/mavlink'
log_file = '/app/logs/sensordata.csv'
simulation_file = '/app/logs/simulation.csv'
log_rate = 2 #Desired rate in Hz
simulation_speed = 5 #Playback speed multiplier for simulation
data = []
row_counter = 0
feedback_interval = 5 # Define the feedback interval (in seconds)

def get_system_id():
    """Detect the correct system ID by checking available vehicles and their autopilot types."""
    try:
        response = requests.get(f"{base_url}/vehicles")
        if response.status_code == 200:
            vehicles = response.json()
            if vehicles and len(vehicles) > 0:
                # Get vehicle info for each ID
                for vehicle_id in vehicles:
                    vehicle_info = requests.get(f"{base_url}/vehicles/{vehicle_id}/info")
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
                            return vehicle_id
                print("Warning: No valid autopilot found, using default value of 1")
                return 1
        print("Warning: Could not detect system ID, using default value of 1")
        return 1
    except Exception as e:
        print(f"Warning: Error detecting system ID: {e}, using default value of 1")
        return 1

def get_distance_sensor_component():
    """Detect the correct component ID for the distance sensor."""
    system_id = get_system_id()
    try:
        # Try common component IDs for distance sensors
        common_ids = [194, 195, 196, 197, 198, 199, 200]
        for component_id in common_ids:
            response = requests.get(f"{base_url}/vehicles/{system_id}/components/{component_id}/messages/DISTANCE_SENSOR")
            if response.status_code == 200:
                print(f"Found distance sensor at component ID: {component_id}")
                return component_id
        print("Warning: No distance sensor found, using default component ID 194")
        return 194
    except Exception as e:
        print(f"Warning: Error detecting distance sensor component ID: {e}, using default value of 194")
        return 194

def get_urls():
    """Get the correct URLs based on the detected system ID."""
    system_id = get_system_id()
    distance_component = get_distance_sensor_component()
    return {
        'distance': f"{base_url}/vehicles/{system_id}/components/{distance_component}/messages/DISTANCE_SENSOR",
        'gps': f"{base_url}/vehicles/{system_id}/components/1/messages/GLOBAL_POSITION_INT",
        'yaw': f"{base_url}/vehicles/{system_id}/components/1/messages/ATTITUDE"
    }

def main():
    global row_counter
    global data
    print("main started")
    
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
            altitude = gps_data['alt'] / 1e7
            data = [unix_timestamp, date, timenow, distance, confidence, yaw, roll, pitch, latitude, longitude, altitude]
            with open(log_file, 'a', newline='') as csvfile: # Create or append to the log file and write the data
                writer = csv.writer(csvfile)
                if csvfile.tell() == 0: # Write the column labels as the header row (only for the first write)
                    writer.writerow(column_labels)
                writer.writerow(data) # Write the data as a new row
                row_counter += 1 # Increment the row counter

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

@app.route('/start')
def start_logging():
    global logging_active
    if not logging_active:
        logging_active = True
        thread = threading.Thread(target=main)
        thread.start()
    return 'Started'

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
    "version": "0.5",
    "webpage": "https://github.com/vshie/pingSurvey",
    "api": "https://github.com/bluerobotics/BlueOS-docker"}
    '''
    

@app.route('/download')
def download_file():
    return send_file(log_file, as_attachment=True, cache_timeout=0)

@app.route('/data')
def get_data():
    global data
    return jsonify(data)

@app.route('/status', methods=['GET'])
def status():
    return {"logging_active": logging_active, "simulation_active": simulation_active}

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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5420)