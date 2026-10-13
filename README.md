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
- **Post-mission processing** that turns one or more survey logs into a transparent contour GeoTIFF for use as a Cockpit map background, with no QGIS needed
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

The Cockpit IFrame widget must point at the extension's **direct service port**, not
the reverse-proxy path. Find the port in BlueOS:

1. Enable **Pirate Mode** in BlueOS (bottom of the main menu).
2. Go to **Available Services** and locate the **Simple Ping Survey** service. Note
   the port it is exposed on (for example `32772` — the port is assigned by BlueOS and
   may change if the extension is reinstalled/updated).
3. In Cockpit's edit interface, add a new **IFrame** widget and set its URL to:

```
http://<vehicle-ip>:<port>/widget
```

For example: `http://192.168.1.68:32772/widget`

> Tip: The widget URL shown at the bottom of the main extension interface is only
> correct if you opened the extension via its direct port. If you reached it through
> the BlueOS reverse proxy, use the Pirate Mode / Available Services port instead.

The widget provides:
- Beam width and depth as prominent readouts
- Full sensor readings (confidence, heading, roll/pitch, altitude, position)
- Start/Stop and Download controls
- Recording status indicator

### Offline Map Caching

Map imagery is served by **Esri World Imagery**. Before heading somewhere without
internet (most survey sites), pre-cache the tiles for your area **while you still
have a connection**. Cached tiles are then served locally, so the map keeps working
fully offline.

**How to cache an area:**

1. **Pan/zoom to your survey area** on the map while connected to the internet.
2. Choose one of the two caching methods:
   - **Cache View** — caches every tile currently visible on screen, plus all higher
     zoom levels above it. Quick way to grab exactly what you're looking at.
   - **Cache Region** — click it, draw a polygon on the map by tapping/clicking the
     corners of the area you want, then confirm. All tiles inside that polygon are
     cached. Best for large or irregularly-shaped sites.
3. A progress bar shows the current zoom level, tiles processed, and how many were
   newly downloaded vs. already cached. Caching runs zoom levels **10 through 19**
   for full detail on the water.
4. Use **Refresh** to update the cache statistics, and **Recent Area** to jump the
   map back to the last region you cached.

**Managing the cache:**

- Cached tiles are stored persistently at `/app/logs/offline_maps/` (survives
  extension restarts and updates).
- The cache is capped at **5 GB** with automatic LRU (least-recently-used) eviction,
  so the oldest tiles are dropped first once the limit is reached.
- **Clear Cache** removes all cached tiles if you need to reclaim space or refresh
  stale imagery.

**Using cached tiles offline:** No action required — once tiles are cached, the map
automatically serves them from local storage. Any tile not in the cache simply won't
render until you reconnect and cache it. If the map is blank offline, it means that
area/zoom level was never cached.

### Post-Mission Processing

The **Post-Mission Processing** tab turns recorded survey logs into a contour map you can load as a background layer in Cockpit.

1. **Survey logs**: tick one or more logs to combine them. Their soundings appear on the map, coloured by depth. Soundings below the **minimum confidence** (default 95%) show in grey and are left out of processing.
2. **Processing area**: click **Draw area** and click on the map to place polygon vertices, then **Finish**. Drag a vertex to adjust it. **Fit to data** draws a polygon around all accepted soundings. If no polygon is drawn, every accepted sounding is used.
3. **Contours**: set the minor and major intervals in metres (the major interval must be a whole multiple of the minor one). For each line type you can set the width in pixels, the colour, the opacity and whether it is labelled. Labels have their own size, colour, halo and spacing. The **Advanced** section covers surface smoothing, the maximum mesh triangle edge, and the GeoTIFF pixel size.
4. **Generate contours**: a preview is drawn on the map; **Hide contours** / **Show contours** on the map toggles it. **Download GeoTIFF** saves the file.

**Auto-tune** (on by default) works out settings from the selected soundings and shows its reasoning in the Contours card:
- **Contour intervals:** about 40 levels or fewer across the depth range, and never finer than the measured scatter between passes.
- **Maximum mesh edge:** from the spacing between survey lines.
- **Line widths and labels:** scaled to the size of the survey.
- **Data-quality tolerances:** from the beam footprint and the scatter between passes.

Typing a value into any tuned field switches auto-tune off, so your value is kept.

How it works: accepted soundings are triangulated into a TIN mesh. Triangles with an edge longer than the maximum (default 30 m) are dropped, so unsurveyed gaps stay blank. The mesh is interpolated onto a 0.5 m grid, lightly smoothed and contoured. The GeoTIFF is RGBA with a fully transparent background, georeferenced in the survey's WGS 84 / UTM zone, and contains only the contour lines and labels. Only the latest output is kept, in `/app/logs/processed/`, and generating again replaces it. It is named after the survey and its settings, for example `ping_survey_2026-10-01_16-10-00_contours_0.5m-2m_conf95.tif`, where `_plus1` means one extra log was combined. Reopening the tab restores the latest output along with the logs, area and settings that produced it. If any of those change, a warning appears until you generate again.

#### Tides and vertical reference

**Online tides (default, internet required).** For each ticked log, the extension fetches the water level through the survey at the log's location:
- **US waters:** [NOAA CO-OPS](https://tidesandcurrents.noaa.gov/) (free, no account). The nearest tide station within 30 km is used. That distance can be changed.
  - **Full ("reference") stations** give 6-minute **measured water level**, which includes surge and weather. This is preferred for past surveys; predicted tide is used otherwise.
  - **Partial ("subordinate") stations** only publish high/low predictions. These are joined with the standard cosine interpolation, and their datum offsets are derived from the full station they're tied to, so they're marked approximate.
- **Elsewhere:** [WorldTides](https://www.worldtides.info/), using your own API key:
  1. Create a free account at <https://www.worldtides.info/register>. New accounts get 100 free credits.
  2. Log in and open <https://www.worldtides.info/developer>, then copy your API key from there. Buy credits or a plan there if you need more.
  3. Paste the key into **Outside the US: WorldTides API key** in the Post-Mission Processing tab, then press **Save** and **Test key**.

  The key is stored on the vehicle (`/app/logs/tides/config.json`) and only shown masked. Each log uses about 1–2 credits.
- **Datum:** depths are reduced to the chosen datum. Corrected depth = measured depth − water level above MSL − height of MSL above the datum.
  - **LAT** (Lowest Astronomical Tide) is the default.
  - **MLLW** and **MSL** are also available.
  - Datum offsets come from the station's datum table (NOAA) or from WorldTides.
- **Every sounding is corrected** from the fetched curve.
- **Each log shows** the provider, station, distance, start/end water levels at the exact local and UTC times, the range during the log, and a sparkline.
- **Caching:** results are cached in `/app/logs/tides/cache/`, so reprocessing works offline. **Refresh** re-fetches, and **Use as manual values** copies them into the manual fields.
- **Output:** the output filename gets the datum (`_lat`, `_mllw` or `_msl`), and the result summary names the provider, station, product and datum.

**Manual tides.** Choose **Manual (tide table)** to enter heights yourself.

With manual tides, each ticked log has tide-height fields. Each field is labelled with the exact moment to look up in a tide table, in local time and UTC. Enter heights in metres above chart datum:
- the height at the log's first sounding
- if the log is longer than an hour, also the height at its last sounding

Corrected depth = measured depth − tide. Logs longer than an hour are interpolated linearly between their start and end heights, which is an approximation because real tides aren't linear. Blank fields count as 0.

**GPS altitude (RTK)** is an alternative vertical reference for logs recorded with an accurate vertical source. Depth below the GPS datum (MSL) = measured depth + antenna height above the transducer − logged GPS altitude. The antenna height is required. This option is unavailable for older 8-column logs, which have no altitude column. If a log's altitude varies by more than 0.5 m, a warning says it isn't RTK quality. Outputs made this way get `_gps` in the filename.

#### Data quality

The **Data quality** card checks exactly the soundings that would be meshed: after confidence filtering, exclusions and tide/GPS correction, and inside the drawn area. It re-checks whenever any of those change. Warnings never block generation.

| Alert | Typical cause | Ways to resolve it |
|---|---|---|
| **Logs disagree by a constant amount** | Surveys made at different tides, or a position or vertical offset | **Apply as tide difference** (fills in the suggested value); **Remove overlapping soundings** from either log (drops that log's soundings within the overlap-removal distance, default 5 m, of the other); **Deselect** a log; or **Ignore** |
| **Overlapping sounding pairs disagree** (by more than a percentage of depth, with a minimum in metres) | A steep or uneven seabed within the 25° beam footprint, a position error, or a bad return | Click a red marker to compare the two soundings (depth, confidence, time, log, distance apart horizontally and vertically, slope, beam footprint) and **Exclude A** or **Exclude B**; or **Ignore** |
| **Spikes along track** (soundings further from their neighbours' median than 5× those neighbours' scatter, so a rough seabed isn't reported) | Fish, bubbles, wake or multipath | **Exclude spikes from the mesh**, exclude individual spikes from their markers, or **Ignore** |
| **GPS altitude isn't RTK quality** | Standard GPS vertical noise | **Use depth + tide**, or **Ignore** |

A hint says whether raising the minimum confidence would remove the flagged soundings, or whether they all have 100% confidence. Excluded soundings show as hollow grey on the map and can be restored from the **Excluded soundings** list. Exclusions and ignored warnings apply to the current set of logs. They're saved with the output, and the result summary lists them, for example "Generated with 1 ignored warning, 1 excluded sounding, 3 spikes excluded". The search radius, tolerances and overlap-removal distance can be changed under **Advanced**.

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
| `/log_file/<filename>` | GET | Download a survey CSV log |
| `/log_points?files=a.csv,b.csv` | GET | Soundings (`[lat, lon, depth_m, confidence, unix_ms, file_index]`) and per-log start/end times for the selected logs |
| `/tide_fetch` | POST | Online tide series for the selected logs: `{files, settings: {tide_datum, prefer_measured, max_station_km}, refresh}` |
| `/tide_config` | GET / POST | WorldTides API key (masked on GET); `/tide_config/test` checks a key |
| `/log_qc` | POST | Data-quality report for the soundings that would be meshed (same body as `/process_contours`) |
| `/process_contours` | POST | Generate a contour GeoTIFF. JSON body: `{files, polygon: [[lat, lon], ...] or null, settings, tides: {file: {start, end}}, exclude: [{key: [file, unix_ms]}], exclude_overlaps: [{file, other}], ignored}` |
| `/processed_files` | GET | The most recent contour GeoTIFF (only the latest is kept) |
| `/processed/<filename>` | GET | Download the GeoTIFF or its preview PNG |
| `/register_service` | GET | BlueOS service registration metadata |

## Architecture

```
pingSurvey/
├── app/
│   ├── main.py                 # Flask backend (data collection, tile proxy, API)
│   ├── contour_processing.py   # Post-mission processing: TIN mesh -> contour GeoTIFF
│   ├── tides.py                # Online tides: NOAA CO-OPS and WorldTides, cached per log
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

### Running locally in Docker

Post-mission processing doesn't need a vehicle, so it can be tested on a laptop:

```bash
docker build -f Dockerfile.base -t vshie/simplepingsurvey-base:latest .
docker build -t pingsurvey:dev .
mkdir -p devlogs && cp /path/to/survey.csv devlogs/
docker run --rm -p 5420:5420 -v "$PWD/devlogs:/app/logs" pingsurvey:dev
# Open http://localhost:5420 -> Post-Mission Processing
```

Add `-v "$PWD/app:/app"` to pick up source changes without rebuilding. The logs directory can also be set with the `PING_SURVEY_LOGS_DIR` environment variable.

### Docker Build

The project uses a two-stage Docker build:

1. **Base image** (`Dockerfile.base`): Python 3.11 with Flask, requests, numpy, scipy, contourpy and Pillow. amd64 and arm64 use PyPI wheels; armv7 uses prebuilt wheels from piwheels
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
