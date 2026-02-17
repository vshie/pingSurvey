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
import warnings
warnings.filterwarnings('ignore')
import shapely.geometry
import geojson
import json

def load_and_process_data(csv_file, tidal_offset=0.0, min_depth=0.5, min_confidence=90.0):
    """Load CSV data and convert depth from cm to meters.
    
    Args:
        csv_file: Path to CSV file
        tidal_offset: Tidal offset in meters. Positive value means water level is
            above chart datum, so corrected_depth = measured_depth - tidal_offset.
        min_depth: Minimum depth in meters to include (filters noise/surface readings)
        min_confidence: Minimum confidence percentage to include
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
    df['Depth_m'] = df[depth_column] / 100.0
    if tidal_offset != 0.0:
        df['Depth_m'] = df['Depth_m'] - tidal_offset
        print(f"Applied tidal offset: {tidal_offset:+.2f}m (corrected = measured - offset)")
    
    # Filter out any invalid coordinates
    df = df.dropna(subset=['Latitude', 'Longitude', 'Depth_m'])
    df = df[(df['Latitude'] != 0) & (df['Longitude'] != 0)]
    
    # Filter out shallow/invalid readings
    original_count = len(df)
    df = df[df['Depth_m'] >= min_depth]
    shallow_removed = original_count - len(df)
    if shallow_removed > 0:
        print(f"Shallow points (<{min_depth}m) removed: {shallow_removed}")
    
    # Filter out low confidence measurements
    confidence_column = None
    if 'Confidence' in df.columns:
        confidence_column = 'Confidence'
    elif 'Confidence (%)' in df.columns:
        confidence_column = 'Confidence (%)'
    
    if confidence_column:
        original_count = len(df)
        df = df[df[confidence_column] >= min_confidence]
        confidence_filtered = original_count - len(df)
        print(f"Low confidence points (<{min_confidence}%) removed: {confidence_filtered}")
    else:
        print("No confidence column found in CSV - skipping confidence filter")
    
    if len(df) == 0:
        raise ValueError("No valid data points remaining after filtering")
    
    # No arbitrary spatial filter -- use all valid data points
    df_filtered = df
    
    # Extract coordinates and depth
    lats = df_filtered['Latitude'].values
    lons = df_filtered['Longitude'].values
    depths = df_filtered['Depth_m'].values
    
    print(f"Valid data points: {len(df_filtered)}")
    print(f"Depth range: {depths.min():.2f}m to {depths.max():.2f}m")
    print(f"Latitude range: {lats.min():.6f} to {lats.max():.6f}")
    print(f"Longitude range: {lons.min():.6f} to {lons.max():.6f}")
    
    return lats, lons, depths, df_filtered

def calculate_survey_area_mask(lats, lons, grid_size=256, bounds=None):
    """Calculate a mask for the surveyed area using point density analysis.

    Optimized for low-memory devices (e.g., Raspberry Pi):
    - Uses KDTree nearest-neighbor distances to estimate average spacing
    - Uses KDTree query_ball_point to count neighbors per grid cell
    - Avoids building an O(N^2) pairwise distance matrix
    - Adaptively relaxes radius/threshold so we don't end up with 0 valid cells
    
    Args:
        bounds: Optional [lon_min, lon_max, lat_min, lat_max]. If provided, uses
            these bounds instead of computing from the data (to align with IDW grid).
    """
    from scipy.spatial import KDTree
    import numpy as np

    # Create grid for analysis -- use provided bounds or compute from data
    if bounds is not None:
        pass  # use caller-supplied bounds
    else:
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

def _build_idw_grid(lats: np.ndarray,
                    lons: np.ndarray,
                    depths: np.ndarray,
                    bounds,
                    grid_size: int = 256,
                    k_neighbors: int = 16,
                    power: float = 2.0,
                    radius_factor: float = 8.0):
    """Build an IDW-interpolated grid using KDTree k-nearest neighbors.

    - Uses at most k_neighbors per grid cell to keep complexity low
    - Limits search to radius_factor * avg nearest-neighbor distance
    - Processes in batches to reduce peak memory
    """
    from scipy.spatial import KDTree

    # Grid
    lon_grid = np.linspace(bounds[0], bounds[1], grid_size)
    lat_grid = np.linspace(bounds[2], bounds[3], grid_size)
    lon_mesh, lat_mesh = np.meshgrid(lon_grid, lat_grid)

    # KDTree on input points (lon, lat)
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
    batch = 8192  # ~8k per batch keeps memory manageable
    out = np.full(total, np.nan, dtype=float)

    eps = 1e-12

    for start in range(0, total, batch):
        end = min(start + batch, total)
        q = grid_pts[start:end]
        dists, idxs = tree.query(q, k=k_neighbors, distance_upper_bound=max_radius)
        # Ensure 2D arrays
        if k_neighbors == 1:
            dists = dists[:, None]
            idxs = idxs[:, None]
        # Mask invalid neighbors (outside radius -> inf index)
        valid = np.isfinite(dists) & (idxs != tree.n)
        # Handle any zero distances (exact point): take exact value
        zero_mask = valid & (dists <= eps)
        row_has_zero = zero_mask.any(axis=1)
        if np.any(row_has_zero):
            rows = np.where(row_has_zero)[0]
            for r in rows:
                # take first exact neighbor's value
                exact_idx = idxs[r, zero_mask[r]].flat[0]
                out[start + r] = depths[exact_idx]
        # For other rows, compute weighted average
        rows = np.where(~row_has_zero)[0]
        if rows.size:
            d = dists[rows]
            idc = idxs[rows]
            vmask = valid[rows]
            # Avoid divide-by-zero (already handled exact zeros)
            w = np.zeros_like(d, dtype=float)
            w[vmask] = 1.0 / np.power(d[vmask] + eps, power)
            vals = np.zeros_like(d, dtype=float)
            vals[vmask] = depths[idc[vmask]]
            wsum = w.sum(axis=1)
            # rows where at least one neighbor is valid
            has = wsum > 0
            out_idx = rows[has]
            if out_idx.size:
                out[start + out_idx] = (w[has] * vals[has]).sum(axis=1) / wsum[has]

    depth_grid = out.reshape(lat_mesh.shape)
    # Mask cells that are too far from any survey point (nearest-neighbor distance)
    dmin, _ = tree.query(grid_pts, k=1)
    # Use a tight mask: cells beyond 3x the average point spacing are blanked
    mask_radius = max(avg_nn * 3.0, 1e-9)
    depth_grid[dmin.reshape(lat_mesh.shape) > mask_radius] = np.nan

    return lon_mesh, lat_mesh, depth_grid, avg_nn, mask_radius


def _extract_contours_from_contour_set(cs, levels, color, weight, opacity):
    """Extract contour line data from a matplotlib ContourSet.
    
    Works with both modern matplotlib (cs.allsegs) and older versions (cs.collections).
    """
    contour_data = []

    # Modern matplotlib (>= 3.8) uses allsegs/allkinds
    if hasattr(cs, 'allsegs'):
        for i, segs in enumerate(cs.allsegs):
            level = levels[i] if i < len(levels) else levels[-1]
            for seg in segs:
                if len(seg) < 2:
                    continue
                contour_coords = [[lat, lon] for lon, lat in seg]
                is_closed = (abs(contour_coords[0][0] - contour_coords[-1][0]) < 1e-10 and
                             abs(contour_coords[0][1] - contour_coords[-1][1]) < 1e-10)
                contour_data.append({
                    'coordinates': contour_coords,
                    'level': float(level),
                    'depth_m': float(abs(level)),
                    'color': color,
                    'weight': weight,
                    'opacity': opacity,
                    'is_closed': is_closed
                })
    elif hasattr(cs, 'collections'):
        for i, collection in enumerate(cs.collections):
            level = levels[i] if i < len(levels) else levels[-1]
            for path_obj in collection.get_paths():
                vertices = path_obj.vertices
                if len(vertices) < 2:
                    continue
                contour_coords = [[lat, lon] for lon, lat in vertices]
                is_closed = (abs(contour_coords[0][0] - contour_coords[-1][0]) < 1e-10 and
                             abs(contour_coords[0][1] - contour_coords[-1][1]) < 1e-10)
                contour_data.append({
                    'coordinates': contour_coords,
                    'level': float(level),
                    'depth_m': float(abs(level)),
                    'color': color,
                    'weight': weight,
                    'opacity': opacity,
                    'is_closed': is_closed
                })
    return contour_data


def _clip_contours_to_hull(contour_data, lats, lons, buffer_factor=2.0):
    """Clip contour lines to the convex hull of survey points (with a small buffer).
    
    This prevents contours from extending into areas with no data.
    """
    from scipy.spatial import ConvexHull

    if len(lats) < 3:
        return contour_data

    points = np.column_stack((lons, lats))
    try:
        hull = ConvexHull(points)
    except Exception:
        return contour_data

    hull_pts = points[hull.vertices]
    hull_polygon = shapely.geometry.Polygon(hull_pts)

    # Buffer the hull slightly so contours aren't hard-clipped at the exact edge
    # Use average nearest-neighbor distance as the buffer amount
    from scipy.spatial import KDTree
    tree = KDTree(points)
    nn_dists, _ = tree.query(points, k=2)
    avg_nn = float(np.mean(nn_dists[:, 1]))
    buffered_hull = hull_polygon.buffer(avg_nn * buffer_factor)

    clipped = []
    for contour in contour_data:
        coords = contour['coordinates']
        # coords are [lat, lon] pairs; convert to [lon, lat] for shapely
        line_coords = [(c[1], c[0]) for c in coords]
        if len(line_coords) < 2:
            continue
        line = shapely.geometry.LineString(line_coords)
        clipped_geom = line.intersection(buffered_hull)
        if clipped_geom.is_empty:
            continue
        # The intersection may produce a MultiLineString
        if clipped_geom.geom_type == 'LineString':
            geoms = [clipped_geom]
        elif clipped_geom.geom_type == 'MultiLineString':
            geoms = list(clipped_geom.geoms)
        else:
            continue
        for g in geoms:
            clipped_coords = [[lat, lon] for lon, lat in g.coords]
            if len(clipped_coords) < 2:
                continue
            entry = dict(contour)
            entry['coordinates'] = clipped_coords
            is_closed = (abs(clipped_coords[0][0] - clipped_coords[-1][0]) < 1e-10 and
                         abs(clipped_coords[0][1] - clipped_coords[-1][1]) < 1e-10)
            entry['is_closed'] = is_closed
            clipped.append(entry)
    return clipped


def extract_contour_data_from_python_map(lats, lons, depths, primary_interval=5.0, secondary_interval=1.0):
    """Extract contour data using IDW grid with convex-hull clipping; TIN fallback.

    IDW with k-nearest via KDTree provides a continuous surface suited for sparse transects,
    while keeping memory and CPU manageable on a Raspberry Pi.
    Contours are clipped to the convex hull of the survey points so they don't
    bleed into unsurveyed areas.
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.tri as mtri

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

    fig, ax = plt.subplots(1, 1, figsize=(15, 12))
    bounds = [np.min(lons), np.max(lons), np.min(lats), np.max(lats)]
    ax.set_xlim(bounds[0], bounds[1])
    ax.set_ylim(bounds[2], bounds[3])

    contour_data_primary = []
    contour_data_secondary = []

    try:
        # Build IDW grid -- radius_factor controls how far from data the grid extends
        lon_mesh, lat_mesh, depth_grid, avg_nn, mask_radius = _build_idw_grid(
            lats, lons, depths, bounds, grid_size=256, k_neighbors=16, power=2.0, radius_factor=5.0
        )
        print(f"IDW grid built: avg_nn={avg_nn:.6f} deg, mask_radius={mask_radius:.6f} deg")

        # Also apply the density-based survey area mask (same bounds/grid as IDW)
        survey_mask, _, _, _, _ = calculate_survey_area_mask(lats, lons, grid_size=256, bounds=bounds)
        depth_grid[~survey_mask] = np.nan

        cs_sec = ax.contour(lon_mesh, lat_mesh, depth_grid, levels=levels_secondary, colors='red', linewidths=2, alpha=0.9)
        cs_pri = ax.contour(lon_mesh, lat_mesh, depth_grid, levels=levels_primary, colors='yellow', linewidths=1, alpha=0.8)

        contour_data_primary = _extract_contours_from_contour_set(cs_pri, levels_primary, 'yellow', 2, 0.8)
        contour_data_secondary = _extract_contours_from_contour_set(cs_sec, levels_secondary, 'red', 3, 0.9)

    except Exception as e:
        print(f"IDW contouring failed, falling back to TIN: {e}")
        try:
            triang = mtri.Triangulation(lons, lats)
            cs_sec = ax.tricontour(triang, depths, levels=levels_secondary, colors='red', linewidths=2, alpha=0.9)
            cs_pri = ax.tricontour(triang, depths, levels=levels_primary, colors='yellow', linewidths=1, alpha=0.8)
            contour_data_primary = _extract_contours_from_contour_set(cs_pri, levels_primary, 'yellow', 2, 0.8)
            contour_data_secondary = _extract_contours_from_contour_set(cs_sec, levels_secondary, 'red', 3, 0.9)
        except Exception as e2:
            print(f"TIN fallback also failed: {e2}")

    plt.close(fig)

    # Clip contours to the convex hull of data points
    contour_data_primary = _clip_contours_to_hull(contour_data_primary, lats, lons)
    contour_data_secondary = _clip_contours_to_hull(contour_data_secondary, lats, lons)

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



def create_interactive_map(lats, lons, depths, df_filtered, output_file='interactive_bathymetry_map.html',
                           primary_interval=5.0, secondary_interval=1.0, tidal_offset=0.0):
    """Create an interactive web map with bathymetry contours."""
    import os
    import matplotlib
    matplotlib.use('Agg')
    
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
    tidal_info = ''
    if tidal_offset != 0.0:
        tidal_info = f'<p style="font-size: 11px; color: #555;">Tidal offset: {tidal_offset:+.2f}m</p>'

    legend_html = f'''
    <div style="position: fixed; 
                bottom: 50px; left: 50px; width: 220px;
                background-color: white; border:2px solid grey; z-index:9999; 
                font-size:14px; padding: 10px">
    <p><b>Bathymetry Map</b></p>
    <p><i class="fa fa-circle" style="color:yellow"></i> {primary_interval}m contours</p>
    <p><i class="fa fa-circle" style="color:red"></i> {secondary_interval}m contours</p>
    <p><i class="fa fa-circle" style="color:blue"></i> Data points</p>
    <p style="font-size: 11px; color: #555;">Points: {len(lats)} | Range: {depths.min():.1f}–{depths.max():.1f}m</p>
    {tidal_info}
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

def generate_contour_map(csv_file, output_file=None, primary_interval=5.0, secondary_interval=1.0, tidal_offset=0.0):
    """Generate an interactive bathymetry contour map from a CSV file.
    
    Args:
        csv_file: Path to CSV file with survey data
        output_file: Output HTML file path. If None, auto-generated in /app/logs/contour_maps/
        primary_interval: Primary contour interval in meters
        secondary_interval: Secondary contour interval in meters
        tidal_offset: Tidal offset in meters. Positive = water above chart datum,
            so corrected_depth = measured_depth - offset.
    
    Returns:
        dict with 'success', 'output_file', 'message', and 'data_points' keys
    """
    import os
    
    if not os.path.exists(csv_file):
        return {'success': False, 'message': f'CSV file not found: {csv_file}'}
    
    # Auto-generate output path if not provided
    if output_file is None:
        contour_dir = '/app/logs/contour_maps'
        os.makedirs(contour_dir, exist_ok=True)
        basename = os.path.splitext(os.path.basename(csv_file))[0]
        output_file = os.path.join(contour_dir, f'{basename}_contour.html')
    
    try:
        lats, lons, depths, df_filtered = load_and_process_data(csv_file, tidal_offset=tidal_offset)
        
        if len(lats) < 10:
            return {'success': False, 'message': f'Not enough valid data points ({len(lats)}). Need at least 10.'}
        
        m = create_interactive_map(lats, lons, depths, df_filtered, output_file, 
                                   primary_interval, secondary_interval, tidal_offset=tidal_offset)
        
        offset_msg = f' (tidal offset: {tidal_offset:+.2f}m)' if tidal_offset != 0.0 else ''
        return {
            'success': True,
            'output_file': output_file,
            'message': f'Contour map generated with {len(lats)} data points{offset_msg}',
            'data_points': len(lats),
            'depth_range': f'{depths.min():.1f}m - {depths.max():.1f}m',
            'tidal_offset': tidal_offset
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {'success': False, 'message': f'Error generating contour map: {str(e)}'} 