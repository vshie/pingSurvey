#!/usr/bin/env python3
"""
Interactive Bathymetry Map Generator
Creates an interactive web map with bathymetry contours overlaid on satellite imagery.
Uses Folium for zooming, panning, and interactive features.
Contours are constrained to the surveyed region using a density-based mask.
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


def load_and_process_data(csv_file, tidal_offset=0.0, min_depth=None, min_confidence=90.0, max_distance_km=None):
    """Load CSV data and convert depth from cm to meters.
    
    Args:
        csv_file: Path to CSV with survey data
        tidal_offset: Tidal height offset in meters (positive = add depth, tide was high;
                      negative = subtract depth, tide was low). Applied to all readings.
        min_depth: Minimum depth in meters to include (None = no filter)
        min_confidence: Minimum confidence percentage to include
        max_distance_km: Maximum distance from survey centroid in km (None = no filter)
    """
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
    
    # Convert depth from cm to meters and apply tidal offset
    df['Depth_m'] = df[depth_column] / 100.0 + tidal_offset
    
    if tidal_offset != 0.0:
        print(f"Tidal offset applied: {tidal_offset:+.2f}m")
    
    # Filter out any invalid coordinates
    df = df.dropna(subset=['Latitude', 'Longitude', 'Depth_m'])
    df = df[(df['Latitude'] != 0) & (df['Longitude'] != 0)]
    
    # Filter by minimum depth if specified
    if min_depth is not None and min_depth > 0:
        before = len(df)
        df = df[df['Depth_m'] >= min_depth]
        print(f"Shallow points (<{min_depth}m) removed: {before - len(df)}")
    
    # Filter out low confidence measurements
    confidence_column = None
    if 'Confidence' in df.columns:
        confidence_column = 'Confidence'
    elif 'Confidence (%)' in df.columns:
        confidence_column = 'Confidence (%)'
    
    if confidence_column and min_confidence > 0:
        original_count = len(df)
        df = df[df[confidence_column] >= min_confidence]
        print(f"Low confidence points (<{min_confidence}%) removed: {original_count - len(df)}")
    
    # Calculate average location for optional distance filter
    avg_lat = df['Latitude'].mean()
    avg_lon = df['Longitude'].mean()
    
    # Filter by distance from centroid if specified
    if max_distance_km is not None:
        lat_km_per_degree = 111.0
        lon_km_per_degree = 111.0 * np.cos(np.radians(avg_lat))
        df['total_distance_km'] = np.sqrt(
            (np.abs(df['Latitude'] - avg_lat) * lat_km_per_degree)**2 +
            (np.abs(df['Longitude'] - avg_lon) * lon_km_per_degree)**2
        )
        before = len(df)
        df = df[df['total_distance_km'] <= max_distance_km]
        print(f"Points beyond {max_distance_km}km from centroid removed: {before - len(df)}")
    
    # Extract coordinates and depth
    lats = df['Latitude'].values
    lons = df['Longitude'].values
    depths = df['Depth_m'].values
    
    print(f"Data points after filtering: {len(lats)}")
    if len(depths) > 0:
        print(f"Depth range: {depths.min():.2f}m to {depths.max():.2f}m")
        print(f"Latitude range: {lats.min():.6f} to {lats.max():.6f}")
        print(f"Longitude range: {lons.min():.6f} to {lons.max():.6f}")
    
    return lats, lons, depths, df


def calculate_survey_area_mask(lats, lons, grid_size=256):
    """Calculate a mask for the surveyed area using point density analysis.

    Returns a boolean mask where True = surveyed area, constraining contours
    to only appear where data points were actually collected.

    Optimized for low-memory devices (e.g., Raspberry Pi):
    - Uses KDTree nearest-neighbor distances to estimate average spacing
    - Uses KDTree query_ball_point to count neighbors per grid cell
    - Avoids building an O(N^2) pairwise distance matrix
    """
    from scipy.spatial import KDTree

    # Create grid with small padding around data extent
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

    # Estimate average nearest-neighbor distance
    nn_dists, _ = tree.query(points, k=2)
    nearest_non_self = nn_dists[:, 1]
    avg_distance = float(np.mean(nearest_non_self)) if nearest_non_self.size else 0.0

    # Use 3x average spacing as the survey coverage radius
    # This constrains contours to areas with actual data coverage
    search_radius = max(avg_distance * 3.0, 1e-9)
    min_neighbors = 2

    # Build the mask: grid cells with enough nearby data points are "surveyed"
    grid_pts = np.column_stack((lon_mesh.ravel(), lat_mesh.ravel()))
    counts = tree.query_ball_point(grid_pts, r=search_radius, return_length=True)
    mask = (counts >= min_neighbors).reshape(grid_size, grid_size)

    valid_pixels = int(np.sum(mask))
    total_pixels = grid_size * grid_size
    coverage = (valid_pixels / total_pixels) * 100 if total_pixels else 0
    print(f"Survey mask: {valid_pixels}/{total_pixels} cells ({coverage:.1f}% coverage), "
          f"radius={search_radius:.6f} deg")

    return mask, lon_mesh, lat_mesh, bounds, search_radius


def _build_idw_grid(lats, lons, depths, bounds, grid_size=256,
                    k_neighbors=16, power=2.0, radius_factor=5.0):
    """Build an IDW-interpolated grid using KDTree k-nearest neighbors.

    Args:
        radius_factor: Controls how far from data points interpolation extends.
                       Lower values = tighter constraint to surveyed area.
    """
    from scipy.spatial import KDTree

    lon_grid = np.linspace(bounds[0], bounds[1], grid_size)
    lat_grid = np.linspace(bounds[2], bounds[3], grid_size)
    lon_mesh, lat_mesh = np.meshgrid(lon_grid, lat_grid)

    points = np.column_stack((lons, lats))
    tree = KDTree(points)

    # Average nearest-neighbor distance for adaptive radius
    nn_dists, _ = tree.query(points, k=2)
    nn = nn_dists[:, 1]
    avg_nn = float(np.mean(nn)) if nn.size else 0.0
    max_radius = max(avg_nn * radius_factor, 1e-9)

    # Flatten grid points for batched querying
    grid_pts = np.column_stack((lon_mesh.ravel(), lat_mesh.ravel()))
    total = grid_pts.shape[0]
    batch = 8192
    out = np.full(total, np.nan, dtype=float)

    eps = 1e-12

    for start in range(0, total, batch):
        end = min(start + batch, total)
        q = grid_pts[start:end]
        dists, idxs = tree.query(q, k=k_neighbors, distance_upper_bound=max_radius)
        if k_neighbors == 1:
            dists = dists[:, None]
            idxs = idxs[:, None]
        valid = np.isfinite(dists) & (idxs != tree.n)
        # Exact point matches
        zero_mask = valid & (dists <= eps)
        row_has_zero = zero_mask.any(axis=1)
        if np.any(row_has_zero):
            rows = np.where(row_has_zero)[0]
            for r in rows:
                exact_idx = idxs[r, zero_mask[r]].flat[0]
                out[start + r] = depths[exact_idx]
        # Weighted average for other rows
        rows = np.where(~row_has_zero)[0]
        if rows.size:
            d = dists[rows]
            idc = idxs[rows]
            vmask = valid[rows]
            w = np.zeros_like(d, dtype=float)
            w[vmask] = 1.0 / np.power(d[vmask] + eps, power)
            vals = np.zeros_like(d, dtype=float)
            vals[vmask] = depths[idc[vmask]]
            wsum = w.sum(axis=1)
            has = wsum > 0
            out_idx = rows[has]
            if out_idx.size:
                out[start + out_idx] = (w[has] * vals[has]).sum(axis=1) / wsum[has]

    depth_grid = out.reshape(lat_mesh.shape)

    return lon_mesh, lat_mesh, depth_grid, avg_nn, max_radius


def extract_contour_data(lats, lons, depths, primary_interval=5.0, secondary_interval=1.0):
    """Extract contour data from survey points, constrained to the surveyed region.

    Uses IDW interpolation on a grid, then applies a survey-area mask so contours
    only appear where data points exist. Falls back to TIN triangulation if IDW fails.
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.tri as mtri

    # Step 1: Build the survey area mask to constrain contours
    mask, mask_lon, mask_lat, bounds, mask_radius = calculate_survey_area_mask(lats, lons, grid_size=256)

    # Step 2: Compute contour levels from depth range
    dmin = float(np.nanmin(depths))
    dmax = float(np.nanmax(depths))

    def _safe_levels(lo, hi, interval):
        interval = float(abs(interval)) if np.isfinite(interval) else 1.0
        if not (np.isfinite(lo) and np.isfinite(hi)):
            return np.linspace(-1.0, 1.0, num=3, dtype=float)
        start = np.floor(lo / interval) * interval
        stop = np.ceil(hi / interval) * interval
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

    fig, ax = plt.subplots(1, 1, figsize=(15, 12))
    ax.set_xlim(bounds[0], bounds[1])
    ax.set_ylim(bounds[2], bounds[3])

    contour_data_primary = []
    contour_data_secondary = []

    def _extract_from_contour_set(cs, levels, color, weight, opacity):
        """Extract contour line data from a matplotlib contour set."""
        results = []
        for i, collection in enumerate(cs.collections):
            level = levels[i] if i < len(levels) else levels[-1]
            for path_obj in collection.get_paths():
                vertices = path_obj.vertices
                if len(vertices) < 2:
                    continue
                contour_coords = [[lat, lon] for lon, lat in vertices]
                is_closed = (abs(contour_coords[0][0] - contour_coords[-1][0]) < 1e-10 and
                             abs(contour_coords[0][1] - contour_coords[-1][1]) < 1e-10)
                results.append({
                    'coordinates': contour_coords,
                    'level': level,
                    'depth_m': float(abs(level)),
                    'color': color,
                    'weight': weight,
                    'opacity': opacity,
                    'is_closed': is_closed
                })
        return results

    try:
        # Step 3: Build IDW grid with conservative radius
        lon_mesh, lat_mesh, depth_grid, avg_nn, max_radius = _build_idw_grid(
            lats, lons, depths, bounds, grid_size=256, k_neighbors=16,
            power=2.0, radius_factor=5.0
        )
        print(f"IDW grid built: avg_nn={avg_nn:.6f} deg, max_radius={max_radius:.6f} deg")

        # Step 4: Apply survey area mask -- NaN out cells outside surveyed region
        depth_grid[~mask] = np.nan

        # Also NaN cells that are too far from any data point
        from scipy.spatial import KDTree
        pts = np.column_stack((lons, lats))
        tree = KDTree(pts)
        grid_pts = np.column_stack((lon_mesh.ravel(), lat_mesh.ravel()))
        dmin_grid, _ = tree.query(grid_pts, k=1)
        # Use 3x average spacing as hard cutoff
        cutoff = max(avg_nn * 3.0, mask_radius)
        depth_grid[dmin_grid.reshape(depth_grid.shape) > cutoff] = np.nan

        valid_cells = np.sum(np.isfinite(depth_grid))
        print(f"Grid cells with valid depth: {valid_cells}/{depth_grid.size}")

        # Step 5: Generate contours on the masked grid
        cs_sec = ax.contour(lon_mesh, lat_mesh, depth_grid, levels=levels_secondary,
                            colors='red', linewidths=2, alpha=0.9)
        cs_pri = ax.contour(lon_mesh, lat_mesh, depth_grid, levels=levels_primary,
                            colors='yellow', linewidths=1, alpha=0.8)

        contour_data_primary = _extract_from_contour_set(cs_pri, levels_primary, 'yellow', 2, 0.8)
        contour_data_secondary = _extract_from_contour_set(cs_sec, levels_secondary, 'red', 3, 0.9)

    except Exception as e:
        print(f"IDW contouring failed, falling back to TIN: {e}")
        try:
            # TIN fallback with long-edge masking
            triang = mtri.Triangulation(lons, lats)

            # Mask triangles with edges longer than 5x average spacing
            from scipy.spatial import KDTree
            pts = np.column_stack((lons, lats))
            tree = KDTree(pts)
            nn_d, _ = tree.query(pts, k=2)
            avg_spacing = float(np.mean(nn_d[:, 1]))
            max_edge = avg_spacing * 5.0

            # Calculate triangle edge lengths and mask long ones
            triangles = triang.triangles
            x, y = triang.x, triang.y
            edge_mask = np.zeros(len(triangles), dtype=bool)
            for idx in range(len(triangles)):
                i0, i1, i2 = triangles[idx]
                d01 = np.sqrt((x[i0]-x[i1])**2 + (y[i0]-y[i1])**2)
                d12 = np.sqrt((x[i1]-x[i2])**2 + (y[i1]-y[i2])**2)
                d20 = np.sqrt((x[i2]-x[i0])**2 + (y[i2]-y[i0])**2)
                if max(d01, d12, d20) > max_edge:
                    edge_mask[idx] = True
            triang.set_mask(edge_mask)

            cs_sec = ax.tricontour(triang, depths, levels=levels_secondary,
                                   colors='red', linewidths=2, alpha=0.9)
            cs_pri = ax.tricontour(triang, depths, levels=levels_primary,
                                   colors='yellow', linewidths=1, alpha=0.8)

            contour_data_primary = _extract_from_contour_set(cs_pri, levels_primary, 'yellow', 2, 0.8)
            contour_data_secondary = _extract_from_contour_set(cs_sec, levels_secondary, 'red', 3, 0.9)

        except Exception as e2:
            print(f"TIN fallback also failed: {e2}")

    plt.close(fig)

    print(f"{primary_interval}m contours: {len(contour_data_primary)} lines")
    print(f"{secondary_interval}m contours: {len(contour_data_secondary)} lines")

    return contour_data_primary, contour_data_secondary


def calculate_optimal_zoom(lats, lons, max_zoom=20):
    """Calculate the optimal zoom level to fit the data bounds in the view."""
    lat_span = (np.max(lats) - np.min(lats)) * 1.2
    lon_span = (np.max(lons) - np.min(lons)) * 1.2
    max_span = max(lat_span, lon_span)

    zoom_table = [
        (1.0, 11), (0.5, 12), (0.25, 13), (0.1, 14), (0.05, 15),
        (0.025, 16), (0.01, 17), (0.005, 18), (0.0025, 19), (0.001, 20),
    ]
    zoom = 20
    for threshold, z in zoom_table:
        if max_span > threshold:
            zoom = z
            break

    return min(zoom, max_zoom)


def generate_histogram(depths, output_dir, basename, tidal_offset=0.0):
    """Generate a depth distribution histogram and save it as a PNG.
    
    Args:
        depths: Array of depth values in meters
        output_dir: Directory to save the histogram image
        basename: Base filename (without extension) for the histogram
        tidal_offset: Tidal offset applied (shown in title for reference)
    
    Returns:
        Path to the saved histogram image, or None on failure
    """
    import os
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    try:
        hist_path = os.path.join(output_dir, f'{basename}_histogram.png')

        fig, ax = plt.subplots(figsize=(10, 6))
        ax.hist(depths, bins=50, alpha=0.7, color='steelblue', edgecolor='black')
        ax.set_xlabel('Depth (meters)')
        ax.set_ylabel('Number of measurements')
        title = 'Depth Distribution - Bathymetry Data'
        if tidal_offset != 0.0:
            title += f' (tidal offset: {tidal_offset:+.2f}m)'
        ax.set_title(title)
        ax.grid(True, alpha=0.3)

        # Add summary stats
        stats_text = (f'n={len(depths)}\n'
                      f'min={depths.min():.1f}m\n'
                      f'max={depths.max():.1f}m\n'
                      f'mean={depths.mean():.1f}m\n'
                      f'median={np.median(depths):.1f}m')
        ax.text(0.97, 0.95, stats_text, transform=ax.transAxes, fontsize=9,
                verticalalignment='top', horizontalalignment='right',
                bbox=dict(boxstyle='round', facecolor='white', alpha=0.8))

        fig.savefig(hist_path, dpi=150, bbox_inches='tight')
        plt.close(fig)
        print(f"Depth histogram saved as: {hist_path}")
        return hist_path
    except Exception as e:
        print(f"Error generating histogram: {e}")
        return None


def create_interactive_map(lats, lons, depths, df_filtered, output_file='interactive_bathymetry_map.html',
                           primary_interval=5.0, secondary_interval=1.0, tidal_offset=0.0):
    """Create an interactive web map with bathymetry contours constrained to the survey region."""

    import os

    # Generate depth histogram alongside the map
    output_dir = os.path.dirname(output_file) or '.'
    basename = os.path.splitext(os.path.basename(output_file))[0]
    hist_path = generate_histogram(depths, output_dir, basename, tidal_offset)
    hist_filename = os.path.basename(hist_path) if hist_path else None

    # Calculate center and zoom
    center_lat = np.mean(lats)
    center_lon = np.mean(lons)
    optimal_zoom = calculate_optimal_zoom(lats, lons)

    # Create base map
    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=optimal_zoom,
        max_zoom=20,
        tiles='https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}',
        attr='Google Satellite'
    )

    # Extract contour data (constrained to survey region)
    print("Extracting contours constrained to survey region...")
    contour_data_primary, contour_data_secondary = extract_contour_data(
        lats, lons, depths, primary_interval, secondary_interval
    )

    print(f"Extracted {len(contour_data_primary)} primary and {len(contour_data_secondary)} secondary contour lines")

    # Add secondary contours layer
    contours_secondary_layer = folium.FeatureGroup(name=f'{secondary_interval}m Contours', show=True)
    secondary_features = []
    for contour in contour_data_secondary:
        coords = contour['coordinates']
        if len(coords) < 2:
            continue
        geojson_coords = [tuple(reversed(pt)) for pt in coords]
        geom = shapely.geometry.LineString(geojson_coords)
        feature = geojson.Feature(
            geometry=geom,
            properties={
                'level': str(contour['level']),
                'depth_m': str(contour['depth_m']),
                'color': 'red'
            }
        )
        secondary_features.append(feature)

    if secondary_features:
        folium.GeoJson(
            geojson.FeatureCollection(secondary_features),
            name=f'{secondary_interval}m Contours',
            style_function=lambda feature: {'color': 'red', 'weight': 3, 'opacity': 0.9},
            tooltip=folium.GeoJsonTooltip(fields=['depth_m'], aliases=['Depth (m):'])
        ).add_to(contours_secondary_layer)
    contours_secondary_layer.add_to(m)

    # Add primary contours layer
    contours_primary_layer = folium.FeatureGroup(name=f'{primary_interval}m Contours', show=True)
    primary_features = []
    for contour in contour_data_primary:
        coords = contour['coordinates']
        if len(coords) < 2:
            continue
        geojson_coords = [tuple(reversed(pt)) for pt in coords]
        geom = shapely.geometry.LineString(geojson_coords)
        feature = geojson.Feature(
            geometry=geom,
            properties={
                'level': str(contour['level']),
                'depth_m': str(contour['depth_m']),
                'color': 'yellow'
            }
        )
        primary_features.append(feature)

    if primary_features:
        folium.GeoJson(
            geojson.FeatureCollection(primary_features),
            name=f'{primary_interval}m Contours',
            style_function=lambda feature: {'color': 'yellow', 'weight': 2, 'opacity': 0.8},
            tooltip=folium.GeoJsonTooltip(fields=['depth_m'], aliases=['Depth (m):'])
        ).add_to(contours_primary_layer)
    contours_primary_layer.add_to(m)

    # Add data points layer (hidden by default)
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

    # Add layer control
    folium.LayerControl(position='topright', collapsed=False).add_to(m)
    folium.plugins.Fullscreen().add_to(m)
    folium.plugins.MeasureControl(
        position='topleft',
        primary_length_unit='meters',
        secondary_length_unit='kilometers',
        primary_area_unit='sqmeters',
        secondary_area_unit='acres'
    ).add_to(m)

    # Build optional legend lines
    offset_text = f'<p style="color: #666; font-size: 12px;">Tidal offset: {tidal_offset:+.2f}m</p>' if tidal_offset != 0.0 else ''
    hist_link = f'<p><a href="{hist_filename}" target="_blank" style="color: #0066cc; text-decoration: none;"><i class="fa fa-bar-chart"></i> View Depth Histogram</a></p>' if hist_filename else ''

    # Add legend with histogram link and coordinate tool
    legend_html = f'''
    <div style="position: fixed; 
                bottom: 50px; left: 50px; width: 220px;
                background-color: white; border:2px solid grey; z-index:9999; 
                font-size:14px; padding: 10px">
    <p><b>Bathymetry Map</b></p>
    <p><i class="fa fa-circle" style="color:yellow"></i> {primary_interval}m contours</p>
    <p><i class="fa fa-circle" style="color:red"></i> {secondary_interval}m contours</p>
    <p><i class="fa fa-circle" style="color:blue"></i> Data points</p>
    {hist_link}
    {offset_text}
    <hr style="margin: 8px 0; border: 1px solid #ccc;">
    <p><b>Coordinate Tool</b></p>
    <button id="click-to-mark-button" style="background-color: #0066cc; color: white; border: none; padding: 8px 12px; border-radius: 4px; cursor: pointer; font-size: 12px; width: 100%;">
        <i class="fa fa-mouse-pointer"></i> Click to Mark
    </button>
    <p style="font-size: 11px; margin: 4px 0 0 0; color: #666;">Click button, then click map to get coordinates</p>
    </div>
    '''
    m.get_root().html.add_child(folium.Element(legend_html))

    # Add click-to-mark JavaScript
    click_js = '''
    <script>
    var clickToMarkMode = false;
    var currentMarker = null;

    function copyToClipboard(text) {
        if (navigator.clipboard && window.isSecureContext) {
            navigator.clipboard.writeText(text);
        } else {
            var ta = document.createElement("textarea");
            ta.value = text;
            ta.style.position = "fixed";
            ta.style.top = "0";
            ta.style.left = "0";
            document.body.appendChild(ta);
            ta.focus();
            ta.select();
            document.execCommand('copy');
            document.body.removeChild(ta);
        }
    }

    function findLeafletMap() {
        for (var k in window) {
            if (window.hasOwnProperty(k)) {
                var o = window[k];
                if (o && typeof o === 'object' && o._container && o.setView && o.containerPointToLatLng) return o;
            }
        }
        return null;
    }

    function setupClickToMark() {
        var btn = document.getElementById('click-to-mark-button');
        if (!btn) { setTimeout(setupClickToMark, 500); return; }
        btn.addEventListener('click', function() {
            clickToMarkMode = !clickToMarkMode;
            btn.style.backgroundColor = clickToMarkMode ? '#ff4444' : '#0066cc';
            btn.innerHTML = clickToMarkMode
                ? '<i class="fa fa-mouse-pointer"></i> Click to Mark (ACTIVE)'
                : '<i class="fa fa-mouse-pointer"></i> Click to Mark';
            var mc = document.querySelector('.leaflet-container');
            if (mc) mc.style.cursor = clickToMarkMode ? 'crosshair' : '';
            if (!clickToMarkMode && currentMarker) {
                var map = findLeafletMap();
                if (map) map.removeLayer(currentMarker);
                currentMarker = null;
            }
        });

        var containers = document.querySelectorAll('.leaflet-container');
        for (var i = 0; i < containers.length; i++) {
            containers[i].addEventListener('click', function(e) {
                if (!clickToMarkMode) return;
                var map = findLeafletMap();
                if (!map) return;
                var rect = this.getBoundingClientRect();
                var pt = L.point(e.clientX - rect.left, e.clientY - rect.top);
                var ll = map.containerPointToLatLng(pt);
                if (!ll) return;
                var txt = ll.lat.toFixed(6) + ', ' + ll.lng.toFixed(6);
                copyToClipboard(txt);
                if (currentMarker) map.removeLayer(currentMarker);
                currentMarker = L.marker([ll.lat, ll.lng], {
                    icon: L.divIcon({
                        className: '',
                        html: '<div style="background:#800080;border-radius:50%;width:16px;height:16px;box-shadow:0 2px 4px rgba(0,0,0,.3)"></div>',
                        iconSize: [16, 16], iconAnchor: [8, 8]
                    })
                }).bindPopup('<b>Copied!</b><br>Lat: ' + ll.lat.toFixed(6) + '<br>Lon: ' + ll.lng.toFixed(6)).addTo(map);
            });
        }
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', setupClickToMark);
    else setupClickToMark();
    </script>
    '''
    m.get_root().html.add_child(folium.Element(click_js))

    # Save the map
    m.save(output_file)
    print(f"Interactive map saved as: {output_file}")

    return m


def generate_contour_map(csv_file, output_file=None, primary_interval=5.0, secondary_interval=1.0,
                         tidal_offset=0.0, min_depth=None, min_confidence=90.0):
    """Generate an interactive bathymetry contour map from a CSV file.

    Args:
        csv_file: Path to CSV file with survey data
        output_file: Output HTML file path. If None, auto-generated in /app/logs/contour_maps/
        primary_interval: Primary contour interval in meters (default: 5m)
        secondary_interval: Secondary contour interval in meters (default: 1m)
        tidal_offset: Tidal height offset in meters applied to all depths.
                      Positive = tide was high (adds depth), negative = tide was low.
        min_depth: Minimum depth filter in meters (None = include all)
        min_confidence: Minimum confidence percentage filter (default: 90%)

    Returns:
        dict with 'success', 'output_file', 'message', and 'data_points' keys
    """
    import os

    if not os.path.exists(csv_file):
        return {'success': False, 'message': f'CSV file not found: {csv_file}'}

    if output_file is None:
        contour_dir = '/app/logs/contour_maps'
        os.makedirs(contour_dir, exist_ok=True)
        basename = os.path.splitext(os.path.basename(csv_file))[0]
        offset_tag = f'_offset{tidal_offset:+.1f}m' if tidal_offset != 0.0 else ''
        output_file = os.path.join(contour_dir, f'{basename}_contour{offset_tag}.html')

    try:
        lats, lons, depths, df_filtered = load_and_process_data(
            csv_file, tidal_offset=tidal_offset, min_depth=min_depth, min_confidence=min_confidence
        )

        if len(lats) < 10:
            return {'success': False, 'message': f'Not enough valid data points ({len(lats)}). Need at least 10.'}

        m = create_interactive_map(lats, lons, depths, df_filtered, output_file,
                                   primary_interval, secondary_interval, tidal_offset)

        msg = f'Contour map generated with {len(lats)} data points'
        if tidal_offset != 0.0:
            msg += f' (tidal offset: {tidal_offset:+.2f}m)'

        return {
            'success': True,
            'output_file': output_file,
            'message': msg,
            'data_points': len(lats),
            'depth_range': f'{depths.min():.1f}m - {depths.max():.1f}m',
            'tidal_offset': tidal_offset
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {'success': False, 'message': f'Error generating contour map: {str(e)}'}
