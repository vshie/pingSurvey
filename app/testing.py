"""
Simple test server for pingSurvey development.
Serves static files and replays data from a CSV file.
Usage: python testing.py
"""
from flask import Flask, jsonify, send_file
import csv
import os

app = Flask(__name__, static_url_path="/static", static_folder="static")

row_num = 0
logging_active = False
simulation_active = False
csv_file = 'sensordata.csv'


def read_row(filename, row_num):
    with open(filename, 'r') as file:
        reader = csv.reader(file)
        next(reader)  # Skip header
        for _ in range(row_num):
            try:
                next(reader)
            except StopIteration:
                return None
        try:
            return next(reader)
        except StopIteration:
            return None


@app.route('/')
def home():
    return app.send_static_file("index.html")


@app.route('/widget')
def widget():
    return app.send_static_file("widget.html")


@app.route('/data')
def get_data():
    global row_num
    data = read_row(csv_file, row_num)
    if data is None:
        return jsonify([])
    row_num += 1
    return jsonify(data)


@app.route('/start')
def start_logging():
    global logging_active
    if not logging_active:
        logging_active = True
    return 'Started'


@app.route('/stop')
def stop_logging():
    global logging_active
    logging_active = False
    return 'Stopped'


@app.route('/status', methods=['GET'])
def status():
    return {"logging_active": logging_active, "simulation_active": simulation_active}


@app.route('/download')
def download_file():
    if os.path.exists(csv_file):
        return send_file(csv_file, as_attachment=True, max_age=0)
    return jsonify({'error': 'No log file available'}), 404


@app.route('/cache_stats')
def cache_stats():
    return jsonify({'cached_tiles': 0, 'cache_size_mb': 0, 'cache_limit_mb': 5120, 'cache_location': 'N/A'})


@app.route('/map_sources')
def map_sources():
    return jsonify({
        'arcgis': {'name': 'Esri World Imagery',
                   'url': 'https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
                   'attribution': '&copy; Esri, Maxar, Earthstar Geographics'},
    })


@app.route('/recent_cached_area')
def recent_cached_area():
    return jsonify({'has_cached_tiles': False, 'center_lat': 9.2, 'center_lon': -133, 'zoom': 10})


@app.route('/log_files')
def log_files():
    return jsonify({'files': []})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000)
