from flask import Flask, render_template, send_file, jsonify #used as back-end service for Vue2 WebApp
import requests #used to communicate with Vue2 app
import csv #used to work with output data file
import time #used for getting current OS time
import threading #used to run main app within a thread
import math #used for yaw radian to degree calc
from datetime import datetime #used for timestamps
print("hello we are in the script world")
app = Flask(__name__, static_url_path="/static", static_folder="static") #setup flask app

logging_active = False# Global variable to control the logging 
distance_url = 'http://host.docker.internal/mavlink2rest/mavlink/vehicles/1/components/194/messages/DISTANCE_SENSOR' #10.144.19.16
gps_url = 'http://host.docker.internal/mavlink2rest/mavlink/vehicles/1/components/1/messages/GLOBAL_POSITION_INT'
yaw_url= 'http://host.docker.internal/mavlink2rest/mavlink/vehicles/1/components/1/messages/ATTITUDE'
log_file = '/app/logs/sensordata.csv'
log_rate = 2 #Desired rate in Hz
data = []
row_counter = 0
feedback_interval = 5 # Define the feedback interval (in seconds)
def main():
    global row_counter
    global data
    print("main started")
    while (logging_active == True): # Main loop for logging data
        distance_response = requests.get(distance_url)
        gps_response = requests.get(gps_url)
        yaw_response = requests.get(yaw_url)
        if distance_response.status_code == 200 and gps_response.status_code == 200 and yaw_response.status_code == 200: # Check if the requests were successful
            distance_data = distance_response.json()['message'] # Extract the data from the responses
            gps_data = gps_response.json()['message']
            yaw_data = yaw_response.json()['message']
            column_labels = ['Unix Timestamp', 'Date', 'Time','Depth (cm)', 'Confidence (%)', 'Vessel heading (deg)', 'Roll (deg)', 'Pitch (deg)' ,'Latitude', 'Longitude', 'Altitude (m)']
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
    {"name": "Simple Ping2 Survey ",
    "description": "This extension makes it easy to record data from the ping2 sonar and gps onbaord the vehicle, keeping a poor communications link from interfering with the quality of collected survey data. When connected, the extension displays a data preview that is intended to aide in survey grid spacing determination while logging at 2Hz. Happy motoring!",
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