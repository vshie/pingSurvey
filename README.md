# BlueOS Ping Survey Extension

A containerized BlueOS extension for collecting, logging, and visualizing bathymetric survey data from Ping sonar sensors. Runs locally on the vehicle to ensure continuous data collection regardless of communication link quality.

## Overview

pingSurvey synchronizes and logs data from Ping sonar sensors and GPS/autopilot systems at 2Hz, providing real-time depth visualization and CSV data export. By running onboard via BlueOS, it eliminates the risk of data loss from poor communication links during survey operations.

### Key Capabilities

- **Real-time sonar depth and confidence monitoring** with color-coded visualization
- **GPS position tracking** with vehicle heading, roll, pitch, and altitude
- **Data logging at 2Hz** to timestamped CSV files
- **Interactive map** with depth circles overlaid on satellite imagery
- **Offline map tile caching** for operations without internet connectivity
- **Cockpit widget** for compact monitoring within the Cockpit interface
- **Simulation mode** for reviewing past surveys at 5x playback speed
- **Automatic system ID detection** for ArduPilot, PX4, and generic autopilots

## Installation

Install the extension through the BlueOS extension manager, or follow the guide at:
https://bluerobotics.com/learn/collecting-creating-bathymetry-blueboat-ping2/

### Requirements

- BlueOS >= 1.1
- Ping sonar connected to the vehicle
- GPS-equipped vehicle with autopilot

## Usage

### Full Interface

Access the full interface by navigating to **Simple Ping Survey** in the BlueOS extensions menu. The interface provides:

- **Map View**: Satellite imagery with real-time depth circles colored by depth
- **Controls**: Start/Stop logging, Download CSV, Center map, Clear markers
- **Status Console**: Live data table showing depth, confidence, heading, position, and attitude
- **Offline Caching**: Cache map tiles for offline operation (see below)

### Data Collection

1. Click **Start** to begin data collection
2. The extension automatically detects the correct system ID and begins logging
3. Depth readings appear as colored circles on the map (confidence >= 90% required)
4. Data is saved to a timestamped CSV file in `/app/logs/`
5. Click **Download** to retrieve the collected data

### Cockpit Widget Integration

To add the compact widget to your Cockpit interface:

1. Open Cockpit's edit interface
2. Add a new IFrame widget
3. Set the iframe URL to:

```
http://<vehicle-ip>/extension/simpleping2survey/widget
```

The widget URL is also displayed at the bottom of the main interface with a copy button.

The widget provides:
- Mini-map with vehicle position and depth overlay
- Start/Stop and Download controls
- Live sensor readouts (depth, confidence, heading, position, altitude)
- Recording status indicator

### Offline Map Caching

For operations without internet connectivity:

1. **Cache Current View**: Click "Cache View" to cache all tiles visible at the current zoom level and higher
2. **Cache Region**: Click "Cache Region" to draw a polygon on the map, then cache all tiles within that area
3. Tiles are cached from zoom level 10 to 19 for comprehensive offline coverage
4. Cache is stored persistently in `/app/logs/offline_maps/` (5GB limit with automatic LRU eviction)
5. Multiple map sources available: Google Maps and ArcGIS World Imagery

### Simulation Mode

Review past survey data or test without hardware:

1. Place a CSV file named `simulation.csv` in the `/app/logs/` directory
2. Click the **Simulate** button in the interface
3. Data plays back at 5x real-time speed with full map visualization
4. Both old (8-column) and new (11-column) CSV formats are supported

To create a simulation file, rename a previously downloaded survey CSV to `simulation.csv`.

## Data Format

### Current Format (12 columns)

| Column | Description | Units |
|--------|-------------|-------|
| Unix Timestamp | Milliseconds since epoch | ms |
| Date | Survey date | MM/DD/YY |
| Time | Survey time | HH:MM:SS |
| Depth (cm) | Sonar depth reading | centimeters |
| Confidence (%) | Signal quality | percentage |
| Vessel heading (deg) | Yaw angle | degrees (0-360) |
| Roll (deg) | Roll angle | degrees |
| Pitch (deg) | Pitch angle | degrees |
| Latitude | GPS latitude | decimal degrees |
| Longitude | GPS longitude | decimal degrees |
| Altitude (m) | GPS altitude MSL | meters |
| Pos/Depth Delta (ms) | Time delta between GPS and depth readings | milliseconds |

The Pos/Depth Delta column records how far apart in time the GPS position fix and depth measurement were received by mavlink2rest. Lower values indicate better temporal synchronization. A value of -1 means the distance sensor was unavailable or the delta could not be computed.

### Legacy Formats

Older log files may have 8 columns (without Roll, Pitch, Altitude, and Delta) or 11 columns (without Delta). The extension handles all formats automatically, padding missing fields with defaults.

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Main interface |
| `/widget` | GET | Cockpit widget interface |
| `/start` | GET | Start data logging |
| `/stop` | GET | Stop data logging |
| `/status` | GET | Get logging and simulation status |
| `/data` | GET | Get current sensor data point |
| `/download` | GET | Download current CSV log file |
| `/start_simulation` | GET | Start simulation playback |
| `/stop_simulation` | GET | Stop simulation playback |
| `/simulation_status` | GET | Get simulation state |
| `/tiles/<z>/<x>/<y>.png` | GET | Serve map tiles (with offline caching) |
| `/cache_stats` | GET | Get offline cache statistics |
| `/clear_cache` | GET | Clear offline tile cache |
| `/map_sources` | GET | Get available map tile sources |
| `/tile_cached/<z>/<x>/<y>` | GET | Check if a tile is cached |
| `/recent_cached_area` | GET | Get most recently cached area |
| `/log_files` | GET | List available CSV log files |
| `/register_service` | GET | BlueOS service registration metadata |

## Architecture

```
pingSurvey/
├── app/
│   ├── main.py                 # Flask backend (data collection, tile proxy, API)
│   ├── testing.py              # Development test server
│   ├── pyproject.toml          # Python project metadata
│   └── static/
│       ├── index.html          # Main Vue.js + Leaflet interface
│       ├── widget.html         # Compact Cockpit widget
│       ├── css/                # Vuetify, Leaflet, MDI stylesheets
│       └── js/                 # Vue.js, Vuetify, Axios, Leaflet scripts
├── Dockerfile                  # Application container (copies app into base image)
├── Dockerfile.base             # Base image with system + Python dependencies
├── .github/workflows/          # CI/CD for Docker image builds
├── BASE_IMAGE_SETUP.md         # Base image build documentation
└── README.md
```

### Data Flow

1. **MAVLink Data Collection**: Flask backend polls MAVLink2Rest at 2Hz for DISTANCE_SENSOR, GLOBAL_POSITION_INT, and ATTITUDE messages
2. **CSV Logging**: Each data point is appended to a timestamped CSV file
3. **Frontend Polling**: Vue.js frontend fetches `/data` at 1Hz (500ms for widget) and updates the map
4. **Tile Proxy**: Map tiles are fetched through the backend, enabling server-side caching for offline use

### MAVLink Data Sources

| Source | MAVLink Message | Component ID | Purpose |
|--------|----------------|-------------|---------|
| Ping Sonar | DISTANCE_SENSOR | 194 | Depth and confidence |
| GPS | GLOBAL_POSITION_INT | 1 | Latitude, longitude, altitude |
| Attitude | ATTITUDE | 1 | Yaw, roll, pitch |

## Development

### Local Development

```bash
cd app
python testing.py
# Open http://localhost:8000
```

### Docker Build

The project uses a two-stage Docker build:

1. **Base image** (`Dockerfile.base`): Contains system libraries and Python scientific packages (numpy, scipy, matplotlib, etc.) with architecture-specific optimizations for ARM32/ARM64/AMD64
2. **Application image** (`Dockerfile`): Copies application code into the base image

See [BASE_IMAGE_SETUP.md](BASE_IMAGE_SETUP.md) for base image build instructions.

### Container Details

| Setting | Value |
|---------|-------|
| Port | 5420 |
| Base Image | `vshie/simplepingsurvey-base:latest` |
| MAVLink Host | `host.docker.internal` |
| Log Volume | `/usr/blueos/extensions/ping-survey` -> `/app/logs/` |
| Tile Cache | `/app/logs/offline_maps/` (5GB limit) |

## Troubleshooting

- **No depth readings**: Verify the Ping sonar is connected and detected. Check that DISTANCE_SENSOR messages are available in MAVLink2Rest.
- **GPS data missing**: Ensure the vehicle has a GPS fix. The extension requires GLOBAL_POSITION_INT messages.
- **Map tiles not loading**: Check internet connectivity. For offline use, pre-cache tiles using the Cache View or Cache Region buttons while connected.
- **Simulation not starting**: Verify `simulation.csv` exists in `/app/logs/` with the correct CSV format.

## Discussion

For more information and updates, visit the [Blue Robotics discussion thread](https://discuss.bluerobotics.com/t/alpha-release-simple-ping2-survey-extension/15794).

## License

See [LICENSE](LICENSE) for details.
