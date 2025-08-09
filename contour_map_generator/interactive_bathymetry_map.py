#!/usr/bin/env python3
"""
Interactive Bathymetry Map Generator
Creates an interactive web map with bathymetry contours overlaid on satellite imagery.
Uses Folium for zooming, panning, and interactive features.
"""

import pandas as pd
import numpy as np
import folium
from folium import plugins
import branca.colormap as cm
from scipy.interpolate import griddata
import warnings
warnings.filterwarnings('ignore')
import shapely.geometry
import geojson
import json

def load_and_process_data(csv_file):
    """Load CSV data and convert depth from cm to meters."""
    print("Loading bathymetry data...")
    df = pd.read_csv(csv_file)
    
    # Handle different column names for depth/distance
    depth_column = None
    if 'Depth (cm)' in df.columns:
        depth_column = 'Depth (cm)'
    elif 'Distance (cm)' in df.columns:
        depth_column = 'Distance (cm)'
    else:
        raise ValueError("No 'Depth (cm)' or 'Distance (cm)' column found in CSV file")
    
    # Convert depth from cm to meters
    df['Depth_m'] = df[depth_column] / 100.0
    
    # Filter out any invalid coordinates
    df = df.dropna(subset=['Latitude', 'Longitude', 'Depth_m'])
    df = df[(df['Latitude'] != 0) & (df['Longitude'] != 0)]
    
    # Filter out shallow outliers (shallower than 5m)
    df = df[df['Depth_m'] >= 5.0]
    
    # Filter out low confidence measurements (less than 95%)
    confidence_column = None
    if 'Confidence' in df.columns:
        confidence_column = 'Confidence'
    elif 'Confidence (%)' in df.columns:
        confidence_column = 'Confidence (%)'
    
    if confidence_column:
        original_count = len(df)
        df = df[df[confidence_column] >= 95.0]
        confidence_filtered = original_count - len(df)
        print(f"Low confidence points (<95%) removed: {confidence_filtered}")
    else:
        print("No 'Confidence' or 'Confidence (%)' column found in CSV - skipping confidence filter")
    
    # Calculate average location
    avg_lat = df['Latitude'].mean()
    avg_lon = df['Longitude'].mean()
    print(f"Average location: {avg_lat:.6f}, {avg_lon:.6f}")
    
    # Calculate distance from average location (approximate, using degrees)
    # 1 degree of latitude ≈ 111 km, 1 degree of longitude ≈ 111*cos(latitude) km
    lat_km_per_degree = 111.0
    lon_km_per_degree = 111.0 * np.cos(np.radians(avg_lat))
    
    # Filter points within 1km of average location
    df['lat_distance_km'] = np.abs(df['Latitude'] - avg_lat) * lat_km_per_degree
    df['lon_distance_km'] = np.abs(df['Longitude'] - avg_lon) * lon_km_per_degree
    df['total_distance_km'] = np.sqrt(df['lat_distance_km']**2 + df['lon_distance_km']**2)
    
    # Keep only points within 2.5km (since data is spread over ~2.3km)
    df_filtered = df[df['total_distance_km'] <= 2.5]
    
    print(f"Original data points: {len(df)}")
    print(f"Points within 2.5km: {len(df_filtered)}")
    print(f"Filtered out: {len(df) - len(df_filtered)} points")
    print(f"Shallow points (<1m) removed: {len(df[df['Depth_m'] < 1.0]) if 'Depth_m' in df.columns else 'N/A'}")
    
    # Extract coordinates and depth
    lats = df_filtered['Latitude'].values
    lons = df_filtered['Longitude'].values
    depths = df_filtered['Depth_m'].values
    
    print(f"Depth range: {depths.min():.2f}m to {depths.max():.2f}m")
    print(f"Latitude range: {lats.min():.6f} to {lats.max():.6f}")
    print(f"Longitude range: {lons.min():.6f} to {lons.max():.6f}")
    
    return lats, lons, depths, df_filtered

def calculate_survey_area_mask(lats, lons, grid_size=256):
    """Calculate a mask for the surveyed area using point density analysis.

    Optimized for low-memory devices (e.g., Raspberry Pi):
    - Uses KDTree nearest-neighbor distances to estimate average spacing
    - Uses KDTree query_ball_point to count neighbors per grid cell
    - Avoids building an O(N^2) pairwise distance matrix
    - Adaptively relaxes radius/threshold so we don't end up with 0 valid cells
    """
    from scipy.spatial import KDTree
    import numpy as np

    # Create grid for analysis
    lat_padding = (lats.max() - lats.min()) * 0.05
    lon_padding = (lons.max() - lons.min()) * 0.05

    bounds = [
        lons.min() - lon_padding,
        lons.max() + lon_padding,
        lats.min() - lat_padding,
        lats.max() + lat_padding
    ]

    lon_grid = np.linspace(bounds[0], bounds[1], grid_size)
    lat_grid = np.linspace(bounds[2], bounds[3], grid_size)
    lon_mesh, lat_mesh = np.meshgrid(lon_grid, lat_grid)

    # Build KDTree for efficient neighbor queries
    points = np.column_stack((lons, lats))
    tree = KDTree(points)

    # Estimate average nearest-neighbor distance without NxN matrix
    # k=2 returns [self, nearest_non_self]
    nn_dists, _ = tree.query(points, k=2)
    nearest_non_self = nn_dists[:, 1]
    avg_distance = float(np.mean(nearest_non_self)) if nearest_non_self.size else 0.0

    # Initial parameters (slightly looser than before)
    search_radius = max(avg_distance * 0.5, 1e-9)
    min_neighbors = 5

    def build_mask(radius: float, threshold: int) -> tuple:
        mask = np.zeros((grid_size, grid_size), dtype=bool)
        for i in range(grid_size):
            for j in range(grid_size):
                grid_lon = lon_mesh[i, j]
                grid_lat = lat_mesh[i, j]
                nearby_idx = tree.query_ball_point([grid_lon, grid_lat], r=radius)
                mask[i, j] = len(nearby_idx) >= threshold
        valid = int(np.sum(mask))
        total = int(grid_size * grid_size)
        return mask, valid, total

    # Try a few adaptive passes to avoid zero coverage
    attempts = [
        (search_radius, min_neighbors),
        (search_radius * 1.5, max(3, int(min_neighbors * 0.8))),
        (search_radius * 2.25, 3),
        (search_radius * 3.0, 2),
    ]

    chosen_mask = None
    coverage_percent = 0.0
    for idx, (radius, threshold) in enumerate(attempts, start=1):
        mask, valid, total = build_mask(radius, threshold)
        coverage_percent = (valid / total) * 100 if total else 0.0
        print(f"  Mask attempt {idx}: radius={radius:.8f} deg, min_neighbors={threshold}, valid={valid}/{total} ({coverage_percent:.2f}%)")
        if valid > 0:
            chosen_mask = mask
            search_radius = radius  # keep for return/debug
            min_neighbors = threshold
            break

    # Last-resort fallback: if still empty, mark entire bounds as valid
    if chosen_mask is None:
        print("  No valid grid cells after adaptive attempts; falling back to full-bounds mask")
        chosen_mask = np.ones((grid_size, grid_size), dtype=bool)

    print("Survey area analysis:")
    print(f"  Total survey points: {len(lats)}")
    print(f"  Average point distance: {avg_distance:.6f} degrees")
    print(f"  Search radius (final): {search_radius:.6f} degrees")
    print(f"  Min neighbors (final): {min_neighbors}")
    valid_pixels = int(np.sum(chosen_mask))
    total_pixels = int(grid_size * grid_size)
    coverage_percent = (valid_pixels / total_pixels) * 100 if total_pixels else 0.0
    print(f"  Valid grid cells: {valid_pixels}/{total_pixels} ({coverage_percent:.1f}%)")

    return chosen_mask, lon_mesh, lat_mesh, bounds, search_radius

def extract_contour_data_from_python_map(lats, lons, depths, primary_interval=5.0, secondary_interval=1.0):
    """Extract exact contour data from Python matplotlib version using TIN-based contours.

    Uses matplotlib.tri.Triangulation to better handle sparse, line-based surveys.
    Masks triangles with long edges to avoid bridging between widely spaced transects.
    Falls back to gridded interpolation if triangulation/contouring fails.
    """
    import matplotlib.pyplot as plt
    import matplotlib.tri as mtri
    from scipy.spatial import KDTree

    # Compute contour levels from raw depths (handle negatives)
    dmin = float(np.nanmin(depths))
    dmax = float(np.nanmax(depths))

    def _safe_levels(dmin: float, dmax: float, interval: float) -> np.ndarray:
        interval = float(abs(interval)) if np.isfinite(interval) else 1.0
        if not (np.isfinite(dmin) and np.isfinite(dmax)):
            return np.linspace(-1.0, 1.0, num=3, dtype=float)
        start = np.floor(dmin / interval) * interval
        stop = np.ceil(dmax / interval) * interval
        if stop <= start:
            stop = start + interval
        try:
            levels = np.arange(start, stop + interval * 0.5, interval, dtype=float)
            if levels.size < 2:
                levels = np.array([start, start + interval], dtype=float)
        except Exception:
            count = int(max(2, np.ceil((stop - start) / interval)))
            levels = np.linspace(start, stop, num=count, dtype=float)
        return np.unique(np.round(levels, 6))

    levels_primary = _safe_levels(dmin, dmax, primary_interval)
    levels_secondary = _safe_levels(dmin, dmax, secondary_interval)

    # Figure and bounds from data
    fig, ax = plt.subplots(1, 1, figsize=(15, 12))
    bounds = [np.min(lons), np.max(lons), np.min(lats), np.max(lats)]
    ax.set_xlim(bounds[0], bounds[1])
    ax.set_ylim(bounds[2], bounds[3])

    contours_primary = None
    contours_secondary = None

    try:
        # Build triangulation
        triang = mtri.Triangulation(lons, lats)

        # Compute average nearest-neighbor distance
        tree = KDTree(np.column_stack((lons, lats)))
        nn_dists, _ = tree.query(np.column_stack((lons, lats)), k=2)
        nn = nn_dists[:, 1]
        avg_nn = float(np.mean(nn)) if nn.size else 0.0

        # Mask triangles with any edge longer than a threshold (avoid bridging gaps)
        # Loosen factor to keep more triangles and produce denser contours on sparse transects
        long_edge_factor = 10.0
        triangles = triang.triangles
        p0 = np.column_stack((lons[triangles[:, 0]], lats[triangles[:, 0]]))
        p1 = np.column_stack((lons[triangles[:, 1]], lats[triangles[:, 1]]))
        p2 = np.column_stack((lons[triangles[:, 2]], lats[triangles[:, 2]]))
        e01 = np.sqrt(np.sum((p0 - p1) ** 2, axis=1))
        e12 = np.sqrt(np.sum((p1 - p2) ** 2, axis=1))
        e20 = np.sqrt(np.sum((p2 - p0) ** 2, axis=1))
        max_edge = np.maximum(e01, np.maximum(e12, e20))
        mask = max_edge > (long_edge_factor * max(avg_nn, 1e-9))
        if np.any(mask):
            triang.set_mask(mask)

        # Refine triangulation for denser, smoother contours without full grids
        refiner = mtri.UniformTriRefiner(triang)
        tri_refined, depth_refined = refiner.refine_field(depths, subdiv=2)

        # Generate tricontours on refined triangulation
        contours_secondary = ax.tricontour(tri_refined, depth_refined, levels=levels_secondary, colors='red', linewidths=2, alpha=0.9)
        contours_primary = ax.tricontour(tri_refined, depth_refined, levels=levels_primary, colors='yellow', linewidths=1, alpha=0.8)

    except Exception as e:
        print(f"Triangulation contouring failed, falling back to gridded method: {e}")
        # Fallback to gridded interpolation if triangulation fails
        from scipy.interpolate import griddata
        # Create a modest grid
        grid_size = 256
        lon_grid = np.linspace(bounds[0], bounds[1], grid_size)
        lat_grid = np.linspace(bounds[2], bounds[3], grid_size)
        lon_mesh, lat_mesh = np.meshgrid(lon_grid, lat_grid)
        points = np.column_stack((lons, lats))
        depth_grid = griddata(points, depths, (lon_mesh, lat_mesh), method='linear', fill_value=np.nan)
        contours_secondary = ax.contour(lon_mesh, lat_mesh, depth_grid, levels=levels_secondary, colors='red', linewidths=2, alpha=0.9)
        contours_primary = ax.contour(lon_mesh, lat_mesh, depth_grid, levels=levels_primary, colors='yellow', linewidths=1, alpha=0.8)

    contour_data_primary = []
    contour_data_secondary = []

    # Extract primary interval contours and their labels
    if contours_primary is not None:
        for i, collection in enumerate(contours_primary.collections):
            level = levels_primary[i] if i < len(levels_primary) else levels_primary[-1]
            for path_obj in collection.get_paths():
                vertices = path_obj.vertices
                if len(vertices) < 2:
                    continue
                # Convert to lat,lon format (path vertices are [[x, y]] == [[lon, lat]])
                contour_coords = [[lat, lon] for lon, lat in vertices]
                # Check if this is a closed contour
                is_closed = (abs(contour_coords[0][0] - contour_coords[-1][0]) < 1e-10 and 
                             abs(contour_coords[0][1] - contour_coords[-1][1]) < 1e-10)
                contour_data_primary.append({
                    'coordinates': contour_coords,
                    'level': level,
                    'depth_m': float(abs(level)),
                    'color': 'yellow',
                    'weight': 2,
                    'opacity': 0.8,
                    'is_closed': is_closed
                })

    # Extract secondary interval contours and their labels
    if contours_secondary is not None:
        for i, collection in enumerate(contours_secondary.collections):
            level = levels_secondary[i] if i < len(levels_secondary) else levels_secondary[-1]
            for path_obj in collection.get_paths():
                vertices = path_obj.vertices
                if len(vertices) < 2:
                    continue
                contour_coords = [[lat, lon] for lon, lat in vertices]
                is_closed = (abs(contour_coords[0][0] - contour_coords[-1][0]) < 1e-10 and 
                             abs(contour_coords[0][1] - contour_coords[-1][1]) < 1e-10)
                contour_data_secondary.append({
                    'coordinates': contour_coords,
                    'level': level,
                    'depth_m': float(abs(level)),
                    'color': 'red',
                    'weight': 3,
                    'opacity': 0.9,
                    'is_closed': is_closed
                })

    plt.close(fig)

    print(f"{primary_interval}m contours: {len(contour_data_primary)} total")
    print(f"{secondary_interval}m contours: {len(contour_data_secondary)} total")

    return contour_data_primary, contour_data_secondary, [], []

def calculate_optimal_zoom(lats, lons, max_zoom=20):
    """Calculate the optimal zoom level to fit the data bounds in the view."""
    # Calculate the bounds of the data
    lat_min, lat_max = np.min(lats), np.max(lats)
    lon_min, lon_max = np.min(lons), np.max(lons)
    
    # Add some padding (10% of the range)
    lat_padding = (lat_max - lat_min) * 0.1
    lon_padding = (lon_max - lon_min) * 0.1
    
    lat_min -= lat_padding
    lat_max += lat_padding
    lon_min -= lon_padding
    lon_max += lon_padding
    
    # Calculate the span of the data
    lat_span = lat_max - lat_min
    lon_span = lon_max - lon_min
    
    # Use the larger span to determine zoom level
    max_span = max(lat_span, lon_span)
    
    # Calculate zoom level based on span
    # Adjusted to be more aggressive (closer zoom)
    if max_span > 1.0:
        zoom = 11
    elif max_span > 0.5:
        zoom = 12
    elif max_span > 0.25:
        zoom = 13
    elif max_span > 0.1:
        zoom = 14
    elif max_span > 0.05:
        zoom = 15
    elif max_span > 0.025:
        zoom = 16
    elif max_span > 0.01:
        zoom = 17
    elif max_span > 0.005:
        zoom = 18
    elif max_span > 0.0025:
        zoom = 19
    elif max_span > 0.001:
        zoom = 20
    elif max_span > 0.0005:
        zoom = 20
    elif max_span > 0.00025:
        zoom = 20
    else:
        zoom = 20
    
    # Ensure zoom doesn't exceed max_zoom
    zoom = min(zoom, max_zoom)
    
    print(f"Data span: {max_span:.6f} degrees")
    print(f"Calculated optimal zoom level: {zoom}")
    
    return zoom



def create_interactive_map(lats, lons, depths, df_filtered, output_file='interactive_bathymetry_map.html', primary_interval=5.0, secondary_interval=1.0):
    """Create an interactive web map with bathymetry contours."""
    
    # Generate histogram if it doesn't exist
    import os
    if not os.path.exists('depth_histogram.png'):
        print("Generating depth histogram...")
        import matplotlib.pyplot as plt
        plt.figure(figsize=(10, 6))
        plt.hist(depths, bins=50, alpha=0.7, color='blue', edgecolor='black')
        plt.xlabel('Depth (meters)')
        plt.ylabel('Number of measurements')
        plt.title('Depth Distribution - Bathymetry Data')
        plt.grid(True, alpha=0.3)
        plt.savefig('depth_histogram.png', dpi=300, bbox_inches='tight')
        plt.close()  # Close the figure to free memory
        print("Depth histogram saved as: depth_histogram.png")
    
    # Calculate center and bounds
    center_lat = np.mean(lats)
    center_lon = np.mean(lons)
    
    # Calculate optimal zoom level to fit the data
    optimal_zoom = calculate_optimal_zoom(lats, lons)
    
    # Create the base map with Google satellite imagery as default
    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=optimal_zoom,
        max_zoom=20,
        tiles='https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}',
        attr='Google Satellite'
    )
    
    # Google Satellite is the only base layer option
    

    

    

    
    # Extract exact contour data from Python matplotlib version
    print("Extracting contour data from Python matplotlib version...")
    contour_data_primary, contour_data_secondary, _, _ = extract_contour_data_from_python_map(lats, lons, depths, primary_interval, secondary_interval)
    
    print(f"Extracted {len(contour_data_primary)} {primary_interval}m contours and {len(contour_data_secondary)} {secondary_interval}m contours")
    
    # Convert contour data to GeoJSON and embed directly in HTML
    print(f"Embedding {secondary_interval}m interval contours...")
    contours_secondary_layer = folium.FeatureGroup(name=f'{secondary_interval}m Contours', show=True)
    
    # Create GeoJSON data for secondary contours
    secondary_features = []
    for contour in contour_data_secondary:
        coords = contour['coordinates']
        if len(coords) < 2:
            continue
        # GeoJSON expects [lon, lat]
        geojson_coords = [tuple(reversed(pt)) for pt in coords]
        geom = shapely.geometry.LineString(geojson_coords)
        feature = geojson.Feature(
            geometry=geom,
            properties={
                'level': str(contour['level']),
                'depth_m': str(contour.get('depth_m', abs(contour['level']))),
                'color': 'red'
            }
        )
        secondary_features.append(feature)
    
    secondary_fc = geojson.FeatureCollection(secondary_features)
    
    # Add secondary contours using embedded GeoJSON (guard against empty)
    if secondary_features:
        folium.GeoJson(
            secondary_fc,
            name=f'{secondary_interval}m Contours',
            style_function=lambda feature: {
                'color': 'red',
                'weight': 3,
                'opacity': 0.9
            },
            tooltip=folium.GeoJsonTooltip(fields=['depth_m'], aliases=['Depth (m):'])
        ).add_to(contours_secondary_layer)
    else:
        print(f"No {secondary_interval}m contours to embed (empty feature collection)")
    contours_secondary_layer.add_to(m)
    
    # Create GeoJSON data for primary contours
    print(f"Embedding {primary_interval}m interval contours...")
    contours_primary_layer = folium.FeatureGroup(name=f'{primary_interval}m Contours', show=True)
    
    primary_features = []
    for contour in contour_data_primary:
        coords = contour['coordinates']
        if len(coords) < 2:
            continue
        # GeoJSON expects [lon, lat]
        geojson_coords = [tuple(reversed(pt)) for pt in coords]
        geom = shapely.geometry.LineString(geojson_coords)
        feature = geojson.Feature(
            geometry=geom,
            properties={
                'level': str(contour['level']),
                'depth_m': str(contour.get('depth_m', abs(contour['level']))),
                'color': 'yellow'
            }
        )
        primary_features.append(feature)
    
    primary_fc = geojson.FeatureCollection(primary_features)
    
    # Add primary contours using embedded GeoJSON (guard against empty)
    if primary_features:
        folium.GeoJson(
            primary_fc,
            name=f'{primary_interval}m Contours',
            style_function=lambda feature: {
                'color': 'yellow',
                'weight': 2,
                'opacity': 0.8
            },
            tooltip=folium.GeoJsonTooltip(fields=['depth_m'], aliases=['Depth (m):'])
        ).add_to(contours_primary_layer)
    else:
        print(f"No {primary_interval}m contours to embed (empty feature collection)")
    contours_primary_layer.add_to(m)
    
    # Add JavaScript to prevent zoom reset, fix contour rendering, and add click-to-mark functionality
    # Use a global event-based approach that doesn't need the map object
    global_js = '''
    <script>
    console.log('Global event script loaded');
    
    var clickToMarkMode = false;
    var tempMarkers = [];
    var currentMarker = null; // Track the current persistent marker
    
    function copyToClipboard(text) {
        if (navigator.clipboard && window.isSecureContext) {
            navigator.clipboard.writeText(text).then(function() {
                console.log('Coordinates copied to clipboard');
            }).catch(function(err) {
                console.error('Failed to copy: ', err);
                fallbackCopyTextToClipboard(text);
            });
        } else {
            fallbackCopyTextToClipboard(text);
        }
    }
    function fallbackCopyTextToClipboard(text) {
        var textArea = document.createElement("textarea");
        textArea.value = text;
        textArea.style.top = "0";
        textArea.style.left = "0";
        textArea.style.position = "fixed";
        document.body.appendChild(textArea);
        textArea.focus();
        textArea.select();
        try {
            document.execCommand('copy');
            console.log('Coordinates copied to clipboard (fallback)');
        } catch (err) {
            console.error('Fallback copy failed: ', err);
        }
        document.body.removeChild(textArea);
    }
    function updateLegendButton() {
        var legendButton = document.getElementById('click-to-mark-button');
        if (legendButton) {
            if (clickToMarkMode) {
                legendButton.style.backgroundColor = '#ff4444';
                legendButton.style.color = 'white';
                legendButton.innerHTML = '<i class="fa fa-mouse-pointer"></i> Click to Mark (ACTIVE)';
            } else {
                legendButton.style.backgroundColor = '#0066cc';
                legendButton.style.color = 'white';
                legendButton.innerHTML = '<i class="fa fa-mouse-pointer"></i> Click to Mark';
            }
        }
    }
    function setupLegendButton() {
        var legendButton = document.getElementById('click-to-mark-button');
        if (legendButton) {
            console.log('Legend button found, setting up click handler');
            legendButton.removeEventListener('click', legendButtonClickHandler);
            legendButton.addEventListener('click', legendButtonClickHandler);
            updateLegendButton();
        } else {
            console.log('Legend button not found, retrying...');
            setTimeout(setupLegendButton, 500);
        }
    }
    function legendButtonClickHandler() {
        clickToMarkMode = !clickToMarkMode;
        updateLegendButton();
        var mapContainer = document.querySelector('.leaflet-container');
        if (mapContainer) {
            mapContainer.style.cursor = clickToMarkMode ? 'crosshair' : '';
        }
        
        // Clear marker when exiting mark mode
        if (!clickToMarkMode && currentMarker) {
            var map = findLeafletMapInstance();
            if (map) {
                map.removeLayer(currentMarker);
            }
            currentMarker = null;
            hideCopyFeedback(); // Hide feedback when exiting mark mode
        }
    }
    // Helper to find a Leaflet map instance from window
    function findLeafletMapInstance() {
        for (var key in window) {
            if (window.hasOwnProperty(key)) {
                var obj = window[key];
                if (obj && typeof obj === 'object' && obj._container && obj.setView && obj.on && obj.containerPointToLatLng) {
                    // Looks like a Leaflet map
                    return obj;
                }
            }
        }
        return null;
    }
    // Add a direct click listener to the map container as a fallback
    function setupContainerClickHandler() {
        var mapContainers = document.querySelectorAll('.leaflet-container');
        for (var i = 0; i < mapContainers.length; i++) {
            mapContainers[i].addEventListener('click', function(e) {
                console.log('Direct container click detected');
                if (!clickToMarkMode) return;
                var map = findLeafletMapInstance();
                if (!map) {
                    console.log('No Leaflet map instance found on window!');
                    return;
                }
                var rect = this.getBoundingClientRect();
                var x = e.clientX - rect.left;
                var y = e.clientY - rect.top;
                var containerPoint = L.point(x, y);
                var latlng = map.containerPointToLatLng(containerPoint);
                if (!latlng) {
                    console.log('Could not convert point to latlng');
                    return;
                }
                var lat = latlng.lat;
                var lng = latlng.lng;
                
                // Format coordinates for clipboard
                var coordText = lat.toFixed(6) + ', ' + lng.toFixed(6);
                copyToClipboard(coordText);
                
                // Remove previous marker if it exists
                if (currentMarker) {
                    map.removeLayer(currentMarker);
                }
                
                // Create a persistent purple marker (no border, no label)
                currentMarker = L.marker([lat, lng], {
                    icon: L.divIcon({
                        className: 'persistent-marker',
                        html: '<div style="background-color: #800080; border-radius: 50%; width: 16px; height: 16px; box-shadow: 0 2px 4px rgba(0,0,0,0.3);"></div>',
                        iconSize: [16, 16],
                        iconAnchor: [8, 8]
                    })
                });
                
                // Add popup with coordinates and copy confirmation
                currentMarker.bindPopup(
                    '<b>Coordinates Copied!</b><br>' +
                    'Latitude: ' + lat.toFixed(6) + '<br>' +
                    'Longitude: ' + lng.toFixed(6) + '<br>' +
                    '<small>✓ Coordinates copied to clipboard</small>'
                );
                
                currentMarker.addTo(map);
                
                // Show feedback message in the legend area
                showCopyFeedback(coordText);
            });
        }
    }
    
    // Function to show copy feedback
    function showCopyFeedback(coordText) {
        // Create or update feedback element
        var feedbackEl = document.getElementById('copy-feedback');
        if (!feedbackEl) {
            feedbackEl = document.createElement('div');
            feedbackEl.id = 'copy-feedback';
            feedbackEl.style.cssText = 'position: fixed; bottom: 280px; left: 50px; background-color: #4CAF50; color: white; padding: 10px; border-radius: 5px; font-size: 12px; z-index: 10000; box-shadow: 0 2px 8px rgba(0,0,0,0.3); pointer-events: none;';
            document.body.appendChild(feedbackEl);
        }
        
        feedbackEl.innerHTML = '<b>✓ Coordinates Copied!</b><br>' + coordText;
        feedbackEl.style.display = 'block';
        
        // Don't auto-hide - let it stay until next marker or mode clear
    }
    
    // Function to hide copy feedback
    function hideCopyFeedback() {
        var feedbackEl = document.getElementById('copy-feedback');
        if (feedbackEl) {
            feedbackEl.style.display = 'none';
        }
    }
    function initializeGlobalFeatures() {
        console.log('Initializing global features...');
        setupLegendButton();
        setupContainerClickHandler();
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initializeGlobalFeatures);
    } else {
        initializeGlobalFeatures();
    }
    </script>
    '''
    m.get_root().html.add_child(folium.Element(global_js))
    
    # Add individual data points with popups (as a separate layer)
    print("Adding data points...")
    data_points_layer = folium.FeatureGroup(name='Data Points', show=False)
    
    for lat, lon, depth in zip(lats, lons, depths):
        folium.CircleMarker(
            location=[lat, lon],
            radius=2,
            popup=f'Depth: {depth:.1f}m<br>Lat: {lat:.6f}<br>Lon: {lon:.6f}',
            color='white',
            fill=True,
            fillColor='blue',
            fillOpacity=0.7
        ).add_to(data_points_layer)
    
    data_points_layer.add_to(m)
    
    # Add layer control with specific position to avoid conflicts
    folium.LayerControl(
        position='topright',
        collapsed=False
    ).add_to(m)
    
    # Add fullscreen option
    folium.plugins.Fullscreen().add_to(m)
    
    # Add measure tool
    folium.plugins.MeasureControl(
        position='topleft',
        primary_length_unit='meters',
        secondary_length_unit='kilometers',
        primary_area_unit='sqmeters',
        secondary_area_unit='acres'
    ).add_to(m)
    
    # Add legend with histogram link and clickable click-to-mark button
    legend_html = f'''
    <div style="position: fixed; 
                bottom: 50px; left: 50px; width: 220px; height: 220px; 
                background-color: white; border:2px solid grey; z-index:9999; 
                font-size:14px; padding: 10px">
    <p><b>Bathymetry Map Layers</b></p>
    <p><i class="fa fa-circle" style="color:yellow"></i> {primary_interval}m contours</p>
    <p><i class="fa fa-circle" style="color:red"></i> {secondary_interval}m contours</p>
    <p><i class="fa fa-circle" style="color:blue"></i> Data points</p>
    <p><a href="depth_histogram.png" target="_blank" style="color: #0066cc; text-decoration: none;">
       <i class="fa fa-bar-chart"></i> View Depth Histogram</a></p>
    <hr style="margin: 8px 0; border: 1px solid #ccc;">
    <p><b>Coordinate Tool</b></p>
    <button id="click-to-mark-button" style="background-color: #0066cc; color: white; border: none; padding: 8px 12px; border-radius: 4px; cursor: pointer; font-size: 12px; width: 100%;">
        <i class="fa fa-mouse-pointer"></i> Click to Mark
    </button>
    <p style="font-size: 11px; margin: 4px 0 0 0; color: #666;">Click button, then click map to get coordinates</p>
    </div>
    '''
    
    # Add layer control instructions near the layer selector
    layer_instructions_html = '''
    <div style="position: fixed; 
                top: 180px; right: 10px; width: 200px; 
                background-color: white; border:2px solid grey; z-index:9999; 
                font-size:12px; padding: 8px; border-radius: 5px;">
    <p><b><i class="fa fa-info-circle"></i> Layer Controls</b></p>
    <p>Use the layer selector to toggle data points and contours on/off</p>
    </div>
    '''
    m.get_root().html.add_child(folium.Element(legend_html))
    m.get_root().html.add_child(folium.Element(layer_instructions_html))
    
    # Save the map
    m.save(output_file)
    print(f"Interactive map saved as: {output_file}")
    
    return m

def main():
    """Main function to run the interactive bathymetry map generator."""
    import sys
    import argparse
    import os
    
    # Set up command line argument parsing
    parser = argparse.ArgumentParser(description='Generate an interactive bathymetry map from CSV data.')
    parser.add_argument('csv_file', 
                       help='Path to the CSV file containing bathymetry data')
    parser.add_argument('-o', '--output', default='interactive_bathymetry_map.html',
                       help='Output HTML file name (default: interactive_bathymetry_map.html)')
    parser.add_argument('-p', '--primary', type=float, default=5.0,
                       help='Primary contour interval in meters (default: 5.0)')
    parser.add_argument('-s', '--secondary', type=float, default=1.0,
                       help='Secondary contour interval in meters (default: 1.0)')

    
    args = parser.parse_args()
    
    print("Interactive bathymetry map generation started...")
    print(f"Input CSV file: {args.csv_file}")
    print(f"Output HTML file: {args.output}")
    print(f"Primary contour interval: {args.primary}m")
    print(f"Secondary contour interval: {args.secondary}m")

    
    # Check if CSV file exists
    if not os.path.exists(args.csv_file):
        print(f"Error: CSV file '{args.csv_file}' not found!")
        print("Please make sure the file exists and the path is correct.")
        sys.exit(1)
    
    try:
        # Load and process data
        lats, lons, depths, df_filtered = load_and_process_data(args.csv_file)
        
        # Create interactive map
        m = create_interactive_map(lats, lons, depths, df_filtered, args.output, args.primary, args.secondary)
        
        print("\nInteractive bathymetry map generation completed successfully!")
        print("Features:")
        print("- Interactive web map with zoom and pan")
        print("- Google satellite imagery background")
        print(f"- Yellow lines: {args.primary}m depth intervals")
        print(f"- Red lines: {args.secondary}m depth intervals")
        print("- Data points as toggleable layer")
        print("- Layer controls for different map types")
        print("- Measurement tools")
        print("- Fullscreen option")
        print("- Clickable depth histogram link")
        print(f"\nOpen '{args.output}' in your web browser to view the map!")
        
    except Exception as e:
        print(f"Error: {e}")
        print("Please check your data and try again.")
        sys.exit(1)

if __name__ == "__main__":
    main() 