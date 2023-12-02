from flask import Flask, render_template, send_file, jsonify
import requests
import csv
import time
import threading

from datetime import datetime

app = Flask(__name__, static_url_path="/static", static_folder="static")

# Global variable to control the logging thread
logging_active = False
distance_url = 'http://192.168.1.52:6040/mavlink/vehicles/1/components/194/messages/DISTANCE_SENSOR'
gps_url = 'http://192.168.1.52:6040/mavlink/vehicles/1/components/1/messages/GLOBAL_POSITION_INT'
log_file = 'sensor_data.csv'
log_rate = 2

# Define the feedback interval (in seconds)
feedback_interval = 5

# Initialize a counter for the number of rows added
row_counter = 0
data = []
def fetch_data():
    global data
    # Main loop for logging data
    while (logging_active == True):
        try:
            # Send GET requests to the REST APIs
            distance_response = requests.get(distance_url)
            gps_response = requests.get(gps_url)

            # Check if the requests were successful
            if distance_response.status_code == 200 and gps_response.status_code == 200:
                # Extract the data from the responses
                distance_data = distance_response.json()['message']
                gps_data = gps_response.json()['message']

                # Define the column labels for the log file
                column_labels = ['Unix Timestamp', 'Year', 'Month', 'Day', 'Hour', 'Minute', 'Second', 'Distance (cm)', 'Latitude', 'Longitude']

                # Extract the values for each column
                timestamp = int(time.time() * 1000)  # Convert current time to milliseconds
                dt = datetime.fromtimestamp(timestamp / 1000)  # Convert timestamp to datetime object 
                unix_timestamp = timestamp
                year, month, day, hour, minute, second = dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second
                distance = distance_data['current_distance']
                latitude = gps_data['lat'] / 1e7
                longitude = gps_data['lon'] / 1e7
                column_values = [unix_timestamp, year, month, day, hour, minute, second, distance, latitude, longitude]
                data = column_values
                # Create or append to the log file and write the data
                with open(log_file, 'a', newline='') as csvfile:
                    writer = csv.writer(csvfile)

                    # Write the column labels as the header row (only for the first write)
                    if csvfile.tell() == 0:
                        writer.writerow(column_labels)

                    # Write the data as a new row
                    writer.writerow(column_values)

                # Increment the row counter
                row_counter += 1

            else:
                # Print an error message if any of the requests were unsuccessful
                print(f"Error: Distance - {distance_response.status_code} - {distance_response.reason}")
                print(f"Error: GPS - {gps_response.status_code} - {gps_response.reason}")

            # Provide feedback every 5 seconds
            if row_counter % (log_rate * feedback_interval) == 0:
                print(f"Rows added to CSV: {row_counter}")
                 

            # Wait for the specified log rate
            time.sleep(1 / log_rate)

        except Exception as e:
            print(f"An error occurred: {e}")
            break

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
        threading.Thread(target=fetch_data).start()# Start the logging script directly
    return 'Started'

@app.route('/stop')
def stop_logging():
    global logging_active
    logging_active = False
    return 'Stopped'

@app.route('/download')
def download_file():
    return send_file('sensor_data.csv', as_attachment=True)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000)
