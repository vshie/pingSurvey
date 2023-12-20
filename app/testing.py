from flask import Flask, render_template, send_file, jsonify
import csv
import json

app = Flask(__name__, static_url_path="/static", static_folder="static")

row_num = 0

@app.route('/')
def home():
 return app.send_static_file("index.html")

def read_row(filename, row_num):
 with open(filename, 'r') as file:
     reader = csv.reader(file)
     # Skip the first row (header)
     next(reader)
     # Skip to the desired row
     for _ in range(row_num):
         next(reader)
     return next(reader)

@app.route('/data')
def get_data():
 global row_num
 data = read_row('sensordata.csv', row_num)
 row_num += 1
 return jsonify(data)

if __name__ == '__main__':
 app.run(host='0.0.0.0', port=8000)
