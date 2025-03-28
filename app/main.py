from flask import Flask, render_template, send_file, jsonify #used as back-end service for Vue2 WebApp
import requests #used to communicate with Vue2 app
import csv #used to work with output data file
import time #used for getting current OS time
import threading #used to run main app within a thread
import math #used for yaw radian to degree calc
from datetime import datetime #used for timestamps
import json #used for JSON decoding
print("hello we are in the script world")
app = Flask(__name__, static_url_path="/static", static_folder="static") #setup flask app

logging_active = False# Global variable to control the logging 
base_url = 'http://host.docker.internal/mavlink2rest/mavlink'
log_file = '/app/logs/sensordata.csv'
log_rate = 2 #Desired rate in Hz
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

def get_urls():
    """Get the correct URLs based on the detected system ID."""
    system_id = get_system_id()
    return {
        'distance': f"{base_url}/vehicles/{system_id}/components/194/messages/DISTANCE_SENSOR",
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
        if distance_response.status_code == 200 and gps_response.status_code == 200 and yaw_response.status_code == 200: # Check if the requests were successful
            try:
                distance_data = distance_response.json()['message'] # Extract the data from the responses
                gps_data = gps_response.json()['message']
                yaw_data = yaw_response.json()['message']
            except json.JSONDecodeError as e:
                print(f"Error decoding JSON response:")
                print(f"Distance response: {distance_response.text}")
                print(f"GPS response: {gps_response.text}")
                print(f"Yaw response: {yaw_response.text}")
                raise e
            column_labels = ['Unix Timestamp', 'Date', 'Time','Depth (cm)', 'Confidence (%)', 'Vessel heading (deg)','Latitude', 'Longitude']
            timestamp = int(time.time() * 1000)  # Convert current time to milliseconds
            dt = datetime.fromtimestamp(timestamp / 1000)  # Convert timestamp to datetime object 
            unix_timestamp = timestamp
            timenow = dt.strftime('%H:%M:%S')
            date = dt.strftime('%m/%d/%y')
            distance = distance_data['current_distance']
            confidence = distance_data['signal_quality']
            yawRad = yaw_data['yaw']
            yawDeg = math.degrees(yawRad)
            yaw = round(((yawDeg + 360) % 360),2)
            latitude = gps_data['lat'] / 1e7
            longitude = gps_data['lon'] / 1e7
            data = [unix_timestamp, date, timenow, distance, confidence, yaw, latitude, longitude]
            with open(log_file, 'a', newline='') as csvfile: # Create or append to the log file and write the data
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
    return {"logging_active": logging_active}

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5420)