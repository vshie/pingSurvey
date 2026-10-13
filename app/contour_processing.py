"""Post-mission processing: survey CSVs -> transparent contour GeoTIFF.

Single-beam Ping data is a set of sparse transect lines, so instead of binning
points into a dense grid (the multibeam approach used by point-cloud-trimmer) we
build a TIN mesh (Delaunay triangulation) over the accepted soundings, linearly
interpolate it onto a regular grid, and trace contours on that.

The output GeoTIFF is an RGBA image with a fully transparent background holding
only the contour lines and their labels, georeferenced in the survey's WGS 84 /
UTM zone. GeoTIFF tags are written by hand with Pillow (the same approach as
point-cloud-trimmer's write_geotiff), so no GDAL/PROJ is required.
"""

import csv
import math
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Optional

import numpy as np

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

SMOOTHING_SIGMA_M = {"none": 0.0, "light": 1.0, "medium": 2.0, "strong": 4.0}


@dataclass
class ContourSettings:
    min_conf: float = 95.0
    minor_m: float = 0.5
    major_m: float = 2.0
    minor_width_px: float = 1.0
    major_width_px: float = 2.5
    minor_color: str = "#ffd800"
    minor_opacity: float = 0.8
    major_color: str = "#00ff00"
    major_opacity: float = 1.0
    label_major: bool = True
    label_minor: bool = False
    label_size_px: int = 22
    label_color: str = "#000000"
    label_opacity: float = 1.0
    label_halo: bool = True
    label_halo_color: str = "#ffffff"
    label_spacing_m: float = 40.0
    max_edge_m: float = 30.0
    smoothing: str = "light"
    pixel_size_m: float = 0.1
    # Vertical reference: "tide" = measured depth minus interpolated tide height;
    # "gps" = depth below the GPS vertical datum using the logged altitude (RTK).
    vertical_reference: str = "tide"
    antenna_height_m: Optional[float] = None   # GPS antenna height above the transducer (gps mode)
    # Tide (vertical_reference == "tide"): "manual" heights per log, or "online" (NOAA / WorldTides)
    tide_source: str = "manual"
    tide_datum: str = "lat"           # online: depths below LAT, MLLW or MSL
    prefer_measured: bool = True      # online: NOAA measured water level when available
    max_station_km: float = 30.0      # online: furthest NOAA station to use
    # Data-quality checks
    exclude_spikes: bool = False
    qc_radius_m: float = 2.0
    qc_depth_tol_m: float = 0.5     # overlap tolerance: max(this, qc_depth_tol_pct % of depth)
    qc_depth_tol_pct: float = 0.0
    qc_spike_tol_m: float = 0.5     # spike: further than max(this, qc_spike_k x local scatter) from neighbours
    qc_spike_k: float = 5.0
    overlap_buffer_m: float = 5.0   # "remove overlapping soundings" trims within this distance of the other log
    # Derive intervals, mesh edge, label/line sizes and quality tolerances from the data.
    auto_tune: bool = True

    @classmethod
    def from_dict(cls, d):
        """Build settings from a (partial) JSON dict, coercing types and ignoring unknown keys."""
        s = cls()
        for f in fields(cls):
            if d is None or f.name not in d or d[f.name] is None or d[f.name] == "":
                continue
            v = d[f.name]
            if f.type == Optional[float]:
                v = float(v)
            elif f.type is bool or isinstance(getattr(s, f.name), bool):
                v = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
            elif isinstance(getattr(s, f.name), int):
                v = int(round(float(v)))
            elif isinstance(getattr(s, f.name), float):
                v = float(v)
            else:
                v = str(v)
            setattr(s, f.name, v)
        s.validate()
        return s

    def validate(self):
        if self.minor_m <= 0 or self.major_m <= 0:
            raise ValueError("Contour intervals must be greater than 0")
        ratio = self.major_m / self.minor_m
        if ratio < 1 or abs(ratio - round(ratio)) > 1e-4:
            raise ValueError(
                f"Major interval ({self.major_m:g} m) must be a whole multiple of the "
                f"minor interval ({self.minor_m:g} m)"
            )
        if self.smoothing not in SMOOTHING_SIGMA_M:
            raise ValueError(f"Unknown smoothing level: {self.smoothing}")
        if self.pixel_size_m <= 0 or self.max_edge_m <= 0:
            raise ValueError("Pixel size and max triangle edge must be greater than 0")
        for name in ("minor_color", "major_color", "label_color", "label_halo_color"):
            _rgb_from_hex(getattr(self, name))
        if self.vertical_reference not in ("tide", "gps"):
            raise ValueError(f"Unknown vertical reference: {self.vertical_reference}")
        if self.tide_source not in ("manual", "online"):
            raise ValueError(f"Unknown tide source: {self.tide_source}")
        if self.tide_datum not in ("lat", "mllw", "msl"):
            raise ValueError(f"Unknown tide datum: {self.tide_datum}")
        if self.max_station_km <= 0:
            raise ValueError("Maximum tide station distance must be greater than 0")
        if self.vertical_reference == "gps" and self.antenna_height_m is None:
            raise ValueError("Enter the GPS antenna height above the sonar transducer to use GPS altitude")
        if min(self.qc_radius_m, self.qc_depth_tol_m, self.qc_spike_tol_m, self.qc_spike_k, self.overlap_buffer_m) <= 0 \
                or self.qc_depth_tol_pct < 0:
            raise ValueError("Data-quality tolerances must be greater than 0")


class ProcessingError(ValueError):
    """User-facing processing problem (bad input, not enough data, ...)."""


# ---------------------------------------------------------------------------
# CSV loading
# ---------------------------------------------------------------------------

# Columns of the soundings array returned by load_survey()
LAT, LON, DEPTH, CONF, TIME, FILE, ALT = range(7)

# Minimum log duration that needs a second (end) tide height.
TIDE_END_REQUIRED_S = 3600
# Soundings further apart in time than this belong to different passes.
PASS_GAP_S = 30.0
# Ping sonar beam width (degrees); footprint diameter = 2 tan(width / 2) * depth.
BEAM_WIDTH_DEG = 25.0


def beam_footprint_m(depth_m):
    return 2 * math.tan(math.radians(BEAM_WIDTH_DEG / 2)) * depth_m


def load_survey(paths):
    """Load soundings from one or more survey CSVs.

    Returns (points, files). points is an (n, 7) array with columns
    LAT, LON, DEPTH (measured, m), CONF (%), TIME (unix s), FILE (index into
    files) and ALT (GPS altitude m, NaN when the log has no altitude column),
    for every row with a valid fix and a non-zero depth. Confidence filtering
    is left to the caller so the UI can show rejected soundings too.
    files is a list of {name, start, end, count, has_altitude}.
    """
    rows = []
    files = []
    for file_idx, path in enumerate(paths):
        name = Path(path).name
        with open(path, newline="") as f:
            reader = csv.reader(f)
            header = next(reader, None) or []
            idx = {h.strip(): i for i, h in enumerate(header)}
            try:
                i_time = idx["Unix Timestamp"]
                # Early (8-column) logs called the depth column "Distance (cm)".
                i_depth = idx["Depth (cm)"] if "Depth (cm)" in idx else idx["Distance (cm)"]
                i_conf = idx["Confidence (%)"]
                i_lat = idx["Latitude"]
                i_lon = idx["Longitude"]
            except KeyError:
                raise ProcessingError(f"{name} is not a Ping Survey log (missing columns)")
            i_alt = idx.get("Altitude (m)")
            n_before = len(rows)
            for r in reader:
                try:
                    t = float(r[i_time]) / 1000.0
                    depth_cm = float(r[i_depth])
                    conf = float(r[i_conf])
                    lat = float(r[i_lat])
                    lon = float(r[i_lon])
                    alt = float(r[i_alt]) if i_alt is not None else math.nan
                except (ValueError, IndexError):
                    continue
                if depth_cm <= 0 or (lat == 0 and lon == 0):
                    continue
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    continue
                rows.append((lat, lon, depth_cm / 100.0, conf, t, file_idx, alt))
        times = [r[TIME] for r in rows[n_before:]]
        files.append({
            "name": name,
            "start": min(times) if times else None,
            "end": max(times) if times else None,
            "count": len(times),
            "has_altitude": i_alt is not None,
        })
    return np.array(rows, dtype=np.float64).reshape(-1, 7), files


def load_points(paths):
    """Soundings only (see load_survey)."""
    return load_survey(paths)[0]


def accepted_mask(points, min_conf):
    return points[:, CONF] >= min_conf


def tide_at(points, files, tides):
    """Tide height (m above datum) for each sounding.

    tides maps file name -> {"start": m, "end": m}. A log longer than an hour
    is linearly interpolated between its start and end heights; shorter logs
    (or a missing end) use the start height throughout. Missing values are 0.
    """
    tides = tides or {}
    out = np.zeros(points.shape[0])
    for i, meta in enumerate(files):
        sel = points[:, FILE] == i
        if not sel.any():
            continue
        entry = tides.get(meta["name"]) or {}
        start = entry.get("start")
        end = entry.get("end")
        start = float(start) if start not in (None, "") else 0.0
        duration = (meta["end"] or 0) - (meta["start"] or 0)
        if end not in (None, "") and duration > TIDE_END_REQUIRED_S:
            frac = (points[sel, TIME] - meta["start"]) / duration
            out[sel] = start + (float(end) - start) * frac
        else:
            out[sel] = start
    return out


def log_summaries(points, files):
    """Per log: centroid and time span of its soundings (used to fetch online tides)."""
    out = []
    for i, meta in enumerate(files):
        sel = points[:, FILE] == i
        if not sel.any():
            continue
        out.append({"name": meta["name"], "lat": float(np.mean(points[sel, LAT])),
                    "lon": float(np.mean(points[sel, LON])), "start": meta["start"], "end": meta["end"]})
    return out


def online_tide_at(points, files, settings, tide_series):
    """Water level above the chosen datum for each sounding, from fetched online series.

    tide_series maps file name -> {"times": [...], "heights": [... m above MSL], "datums": {...}}.
    """
    out = np.zeros(points.shape[0])
    for i, meta in enumerate(files):
        sel = points[:, FILE] == i
        if not sel.any():
            continue
        series = (tide_series or {}).get(meta["name"])
        if not series:
            raise ProcessingError(f"No online tide data for {meta['name']}")
        offset = series["datums"].get(settings.tide_datum)
        if offset is None:
            raise ProcessingError(
                f"The tide station for {meta['name']} has no {settings.tide_datum.upper()} datum; choose another datum"
            )
        times = np.asarray(series["times"], dtype=np.float64)
        heights = np.asarray(series["heights"], dtype=np.float64)
        t = points[sel, TIME]
        if t.min() < times[0] - 60 or t.max() > times[-1] + 60:
            raise ProcessingError(f"Online tide data doesn't cover all of {meta['name']}; refresh it")
        out[sel] = np.interp(t, times, heights) + offset
    return out


def corrected_depth(points, files, settings, tides=None, tide_series=None):
    """Depth below the chosen vertical datum (positive down) for each sounding."""
    if settings.vertical_reference == "gps":
        missing = [files[int(i)]["name"] for i in np.unique(points[:, FILE])
                   if not files[int(i)]["has_altitude"]]
        if missing:
            raise ProcessingError(
                f"{', '.join(missing)} has no GPS altitude column; use tide-corrected depth instead"
            )
        # seabed elevation = antenna altitude - antenna height above transducer - depth
        return points[:, DEPTH] + settings.antenna_height_m - points[:, ALT]
    if settings.tide_source == "online":
        # water level above datum = level above MSL + height of MSL above the datum
        return points[:, DEPTH] - online_tide_at(points, files, settings, tide_series)
    return points[:, DEPTH] - tide_at(points, files, tides)


@dataclass
class Prepared:
    """Soundings ready for meshing: accepted, not excluded, inside the area, datum-corrected."""
    rows: np.ndarray     # (n, 7) source rows (measured depth in DEPTH)
    xy: np.ndarray       # (n, 2) UTM
    z: np.ndarray        # (n,) corrected depth
    epsg: int
    poly_utm: Optional[np.ndarray]
    n_excluded: int
    overlap_removed: list   # [{file, other, count}] for each applied overlap-removal rule


def exclusion_mask(points, files, exclude):
    """True for soundings listed in exclude: [file name, unix ms] keys, or objects with such a "key"."""
    mask = np.zeros(points.shape[0], dtype=bool)
    if not exclude:
        return mask
    by_file = {}
    for key in exclude:
        if isinstance(key, dict):
            key = key.get("key")
        try:
            by_file.setdefault(str(key[0]), []).append(int(key[1]))
        except (TypeError, ValueError, IndexError):
            continue
    t_ms = np.round(points[:, TIME] * 1000).astype(np.int64)
    for i, meta in enumerate(files):
        if meta["name"] in by_file:
            sel = points[:, FILE] == i
            mask[sel] = np.isin(t_ms[sel], np.array(by_file[meta["name"]], dtype=np.int64))
    return mask


def _near_other_log(xy, file_col, f, other, buffer_m):
    """Soundings of log f within buffer_m of any sounding of log other."""
    from scipy.spatial import cKDTree

    mask = np.zeros(xy.shape[0], dtype=bool)
    sel_f = np.flatnonzero(file_col == f)
    sel_o = file_col == other
    if not sel_f.size or not sel_o.any():
        return mask
    dist, _ = cKDTree(xy[sel_o]).query(xy[sel_f], k=1, distance_upper_bound=buffer_m)
    mask[sel_f] = np.isfinite(dist)
    return mask


def prepare(points, files, polygon_latlon, settings, tides=None, exclude=None, exclude_overlaps=None,
            tide_series=None):
    excluded = exclusion_mask(points, files, exclude)
    pts = points[accepted_mask(points, settings.min_conf) & ~excluded]
    if pts.shape[0] < 3:
        raise ProcessingError(
            f"Only {pts.shape[0]} soundings meet the {settings.min_conf:g}% confidence threshold"
        )
    poly = np.asarray(polygon_latlon, dtype=np.float64).reshape(-1, 2) if polygon_latlon else None
    if poly is not None and poly.shape[0] < 3:
        raise ProcessingError("The processing area needs at least 3 vertices")

    epsg = utm_epsg_for(float(np.mean(pts[:, LAT])), float(np.mean(pts[:, LON])))
    e, n = to_utm(pts[:, LAT], pts[:, LON], epsg)
    xy = np.column_stack([e, n])
    z = corrected_depth(pts, files, settings, tides, tide_series)
    poly_utm = None
    if poly is not None:
        pe, pn = to_utm(poly[:, 0], poly[:, 1], epsg)
        poly_utm = np.column_stack([pe, pn])
        keep = _points_in_polygon(xy[:, 0], xy[:, 1], poly_utm)
        pts, xy, z = pts[keep], xy[keep], z[keep]

    # Overlap-removal rules: drop log `file`'s soundings near log `other` (both evaluated
    # against the same pre-rule set, so the order of rules doesn't matter).
    index = {meta["name"]: i for i, meta in enumerate(files)}
    drop = np.zeros(pts.shape[0], dtype=bool)
    overlap_removed = []
    for rule in exclude_overlaps or []:
        f, other = index.get(str(rule.get("file"))), index.get(str(rule.get("other")))
        if f is None or other is None or f == other:
            continue
        near = _near_other_log(xy, pts[:, FILE], f, other, settings.overlap_buffer_m)
        drop |= near
        overlap_removed.append({"file": files[f]["name"], "other": files[other]["name"], "count": int(near.sum())})
    if drop.any():
        pts, xy, z = pts[~drop], xy[~drop], z[~drop]

    if pts.shape[0] < 3:
        raise ProcessingError("Fewer than 3 accepted soundings fall inside the processing area")
    return Prepared(rows=pts, xy=xy, z=z, epsg=epsg, poly_utm=poly_utm,
                    n_excluded=int(excluded.sum()), overlap_removed=overlap_removed)


# ---------------------------------------------------------------------------
# Data-quality checks
# ---------------------------------------------------------------------------

MAX_QC_ITEMS = 300
MIN_OFFSET_PAIRS = 20


def _qc_item(prep, files, i):
    r = prep.rows[i]
    return {
        "key": [files[int(r[FILE])]["name"], int(round(r[TIME] * 1000))],
        "lat": round(float(r[LAT]), 7),
        "lon": round(float(r[LON]), 7),
        "depth_m": round(float(prep.z[i]), 2),
        "measured_m": round(float(r[DEPTH]), 2),
        "conf": int(r[CONF]),
        "time": float(r[TIME]),
        "file": files[int(r[FILE])]["name"],
        "footprint_m": round(beam_footprint_m(float(r[DEPTH])), 1),
    }


def _spike_mask(prep, tol, k=5.0, half_window=7):
    """Along-track spikes (a Hampel filter).

    A sounding is a spike when it differs from the median of its neighbours on the
    same pass by more than max(tol, k x their robust scatter). Scaling by the local
    scatter keeps a rough seabed (reef, rubble) from being reported as spikes while
    still catching isolated bad returns on a smooth one.
    """
    from numpy.lib.stride_tricks import sliding_window_view

    n = prep.rows.shape[0]
    deviation = np.zeros(n)
    median = np.full(n, np.nan)
    threshold = np.full(n, np.inf)
    order = np.lexsort((prep.rows[:, TIME], prep.rows[:, FILE]))
    f = prep.rows[order, FILE]
    t = prep.rows[order, TIME]
    breaks = np.flatnonzero((np.diff(f) != 0) | (np.diff(t) > PASS_GAP_S)) + 1
    for seg in np.split(np.arange(n), breaks):
        if seg.size < half_window + 1:
            continue
        z = prep.z[order[seg]]
        padded = np.concatenate([np.full(half_window, np.nan), z, np.full(half_window, np.nan)])
        windows = sliding_window_view(padded, 2 * half_window + 1)
        med = np.nanmedian(windows, axis=1)
        scatter = 1.4826 * np.nanmedian(np.abs(windows - med[:, None]), axis=1)
        median[order[seg]] = med
        deviation[order[seg]] = z - med
        threshold[order[seg]] = np.maximum(tol, k * scatter)
    return np.abs(deviation) > threshold, deviation, median


def quality_check(prep, files, settings):
    """Find soundings that disagree with their neighbours.

    Returns (report, spike_mask). The report is JSON-serialisable and bounded
    in size; spike_mask flags every along-track spike in prep.
    """
    from scipy.spatial import cKDTree

    report = {
        "points": int(prep.rows.shape[0]),
        "excluded": prep.n_excluded,
        "overlap_removed": prep.overlap_removed,
        "file_offsets": [],
        "altitude_warnings": [],
        "conflicts": [],
        "conflict_count": 0,
        "spikes": [],
        "spike_count": 0,
    }

    # 1. Overlapping soundings from different passes (or different files).
    pairs = cKDTree(prep.xy).query_pairs(settings.qc_radius_m, output_type="ndarray")
    if pairs.size:
        i, j = pairs[:, 0], pairs[:, 1]
        fi, fj = prep.rows[i, FILE], prep.rows[j, FILE]
        other_pass = (fi != fj) | (np.abs(prep.rows[i, TIME] - prep.rows[j, TIME]) > PASS_GAP_S)
        i, j, fi, fj = i[other_pass], j[other_pass], fi[other_pass], fj[other_pass]
    else:
        i = j = fi = fj = np.array([], dtype=int)

    # 2. Constant offset between two logs where they overlap.
    explained = np.zeros(i.size, dtype=bool)
    for a in range(len(files)):
        for b in range(a + 1, len(files)):
            sel = ((fi == a) & (fj == b)) | ((fi == b) & (fj == a))
            if sel.sum() < MIN_OFFSET_PAIRS:
                continue
            ia = np.where(fi[sel] == a, i[sel], j[sel])
            ib = np.where(fi[sel] == a, j[sel], i[sel])
            diff = prep.z[ib] - prep.z[ia]          # + means log b reads deeper
            med = float(np.median(diff))
            if abs(med) > settings.qc_depth_tol_m:
                buf = settings.overlap_buffer_m
                report["file_offsets"].append({
                    "file_a": files[a]["name"],
                    "file_b": files[b]["name"],
                    "median_m": round(med, 2),
                    "pairs": int(sel.sum()),
                    # soundings each "remove overlapping soundings" choice would drop
                    "remove_a_count": int(_near_other_log(prep.xy, prep.rows[:, FILE], a, b, buf).sum()),
                    "remove_b_count": int(_near_other_log(prep.xy, prep.rows[:, FILE], b, a, buf).sum()),
                })
                explained |= sel

    # 3. Along-track spikes (computed first: pairs involving a spike are reported as the spike).
    spikes, deviation, median = _spike_mask(prep, settings.qc_spike_tol_m, settings.qc_spike_k)
    explained |= spikes[i] | spikes[j]

    # 4. Scattered disagreements not explained by a file offset or a spike.
    dz = np.abs(prep.z[i] - prep.z[j])
    pair_tol = np.maximum(settings.qc_depth_tol_m,
                          settings.qc_depth_tol_pct / 100 * (prep.z[i] + prep.z[j]) / 2)
    conflict = (dz > pair_tol) & ~explained
    report["conflict_count"] = int(conflict.sum())
    if conflict.any():
        ci, cj, cdz = i[conflict], j[conflict], dz[conflict]
        h = np.hypot(*(prep.xy[ci] - prep.xy[cj]).T)
        report["conflict_median_dz_m"] = round(float(np.median(cdz)), 2)
        report["conflict_median_h_m"] = round(float(np.median(h)), 2)
        for k in np.argsort(-cdz)[:MAX_QC_ITEMS]:
            report["conflicts"].append({
                "a": _qc_item(prep, files, ci[k]),
                "b": _qc_item(prep, files, cj[k]),
                "h_m": round(float(h[k]), 2),
                "dz_m": round(float(cdz[k]), 2),
                "slope_pct": round(float(cdz[k] / max(h[k], 0.05) * 100)),
            })

    report["spike_count"] = int(spikes.sum())
    for k in np.flatnonzero(spikes)[np.argsort(-np.abs(deviation[spikes]))][:MAX_QC_ITEMS]:
        item = _qc_item(prep, files, k)
        item["neighbour_median_m"] = round(float(median[k]), 2)
        item["deviation_m"] = round(float(deviation[k]), 2)
        report["spikes"].append(item)

    # 5. GPS altitude should be steady when it is the vertical reference.
    if settings.vertical_reference == "gps":
        for idx, meta in enumerate(files):
            alt = prep.rows[prep.rows[:, FILE] == idx, ALT]
            alt = alt[np.isfinite(alt)]
            if alt.size < 10:
                continue
            spread = float(np.percentile(alt, 95) - np.percentile(alt, 5))
            if spread > 0.5:
                report["altitude_warnings"].append({
                    "file": meta["name"],
                    "spread_m": round(spread, 2),
                    "std_m": round(float(np.std(alt)), 2),
                })
    return report, spikes


# ---------------------------------------------------------------------------
# WGS 84 <-> UTM (Krueger series, accurate to well under a millimetre in-zone)
# ---------------------------------------------------------------------------

_A = 6378137.0
_F = 1 / 298.257223563
_N = _F / (2 - _F)
_RECT_A = _A / (1 + _N) * (1 + _N ** 2 / 4 + _N ** 4 / 64)
_K0 = 0.9996
_ALPHA = (
    _N / 2 - 2 * _N ** 2 / 3 + 5 * _N ** 3 / 16,
    13 * _N ** 2 / 48 - 3 * _N ** 3 / 5,
    61 * _N ** 3 / 240,
)
_BETA = (
    _N / 2 - 2 * _N ** 2 / 3 + 37 * _N ** 3 / 96,
    _N ** 2 / 48 + _N ** 3 / 15,
    17 * _N ** 3 / 480,
)
_DELTA = (
    2 * _N - 2 * _N ** 2 / 3 - 2 * _N ** 3,
    7 * _N ** 2 / 3 - 8 * _N ** 3 / 5,
    56 * _N ** 3 / 15,
)


def utm_epsg_for(lat, lon):
    zone = int(math.floor((lon + 180) / 6)) % 60 + 1
    return (32600 if lat >= 0 else 32700) + zone


def _zone_params(epsg):
    zone = epsg % 100
    lon0 = math.radians(-183 + 6 * zone)
    n0 = 10_000_000.0 if epsg >= 32700 else 0.0
    return lon0, n0


def to_utm(lat, lon, epsg):
    """Vectorised WGS 84 lat/lon (degrees) -> UTM easting/northing (m)."""
    lon0, n0 = _zone_params(epsg)
    phi = np.radians(np.asarray(lat, dtype=np.float64))
    dlam = np.radians(np.asarray(lon, dtype=np.float64)) - lon0
    c = 2 * math.sqrt(_N) / (1 + _N)
    t = np.sinh(np.arctanh(np.sin(phi)) - c * np.arctanh(c * np.sin(phi)))
    xi_p = np.arctan2(t, np.cos(dlam))
    eta_p = np.arctanh(np.sin(dlam) / np.sqrt(1 + t * t))
    xi = xi_p.copy()
    eta = eta_p.copy()
    for j, a in enumerate(_ALPHA, start=1):
        xi += a * np.sin(2 * j * xi_p) * np.cosh(2 * j * eta_p)
        eta += a * np.cos(2 * j * xi_p) * np.sinh(2 * j * eta_p)
    e = 500_000.0 + _K0 * _RECT_A * eta
    n = n0 + _K0 * _RECT_A * xi
    return e, n


def from_utm(e, n, epsg):
    """Vectorised UTM easting/northing (m) -> WGS 84 lat/lon (degrees)."""
    lon0, n0 = _zone_params(epsg)
    xi = (np.asarray(n, dtype=np.float64) - n0) / (_K0 * _RECT_A)
    eta = (np.asarray(e, dtype=np.float64) - 500_000.0) / (_K0 * _RECT_A)
    xi_p = xi.copy()
    eta_p = eta.copy()
    for j, b in enumerate(_BETA, start=1):
        xi_p -= b * np.sin(2 * j * xi) * np.cosh(2 * j * eta)
        eta_p -= b * np.cos(2 * j * xi) * np.sinh(2 * j * eta)
    chi = np.arcsin(np.sin(xi_p) / np.cosh(eta_p))
    phi = chi.copy()
    for j, d in enumerate(_DELTA, start=1):
        phi += d * np.sin(2 * j * chi)
    lam = lon0 + np.arctan2(np.sinh(eta_p), np.cos(xi_p))
    return np.degrees(phi), np.degrees(lam)


# ---------------------------------------------------------------------------
# Surface (TIN mesh -> grid)
# ---------------------------------------------------------------------------

def _points_in_polygon(x, y, poly):
    """Even-odd ray casting; x, y are arrays, poly is an (m, 2) array."""
    inside = np.zeros(x.shape, dtype=bool)
    px, py = poly[:, 0], poly[:, 1]
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi, xj, yj = px[i], py[i], px[j], py[j]
        crosses = (yi > y) != (yj > y)
        with np.errstate(divide="ignore", invalid="ignore"):
            x_int = (xj - xi) * (y - yi) / (yj - yi) + xi
        inside ^= crosses & (x < x_int)
        j = i
    return inside


def _dedupe(xy, z, tol_m=0.05):
    """Average soundings that share a position (e.g. while the boat is stationary)."""
    keys = np.round(xy / tol_m).astype(np.int64)
    _, inv, counts = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
    inv = inv.reshape(-1)
    xy_m = np.zeros((counts.size, 2))
    z_m = np.zeros(counts.size)
    np.add.at(xy_m, inv, xy)
    np.add.at(z_m, inv, z)
    return xy_m / counts[:, None], z_m / counts


def _masked_gaussian(z, sigma_cells):
    """Gaussian smooth that ignores NaN cells and leaves them NaN."""
    from scipy import ndimage

    if sigma_cells <= 0:
        return z
    valid = np.isfinite(z)
    filled = np.where(valid, z, 0.0)
    num = ndimage.gaussian_filter(filled, sigma_cells, mode="constant")
    den = ndimage.gaussian_filter(valid.astype(np.float64), sigma_cells, mode="constant")
    with np.errstate(invalid="ignore", divide="ignore"):
        out = num / den
    out[~valid] = np.nan
    return out


@dataclass
class Surface:
    epsg: int
    x0: float          # UTM easting of grid column 0 centre
    y0: float          # UTM northing of grid row 0 centre
    cell: float
    depth: np.ndarray  # (ny, nx), NaN where no data

    def x_centers(self):
        return self.x0 + self.cell * np.arange(self.depth.shape[1])

    def y_centers(self):
        return self.y0 + self.cell * np.arange(self.depth.shape[0])


def build_surface(prep, settings, keep=None, max_grid_cells=4_000_000):
    """Triangulate the prepared soundings and interpolate a depth grid inside the area."""
    from scipy.interpolate import LinearNDInterpolator
    from scipy.spatial import Delaunay, QhullError

    xy, z = prep.xy, prep.z
    if keep is not None:
        xy, z = xy[keep], z[keep]
    xy, z = _dedupe(xy, z)
    if xy.shape[0] < 3:
        raise ProcessingError("Fewer than 3 soundings remain after exclusions")

    try:
        tri = Delaunay(xy)
    except QhullError:
        raise ProcessingError("Soundings are collinear; survey more than a single straight line")

    # Drop long, skinny triangles that would bridge unsurveyed gaps.
    corners = xy[tri.simplices]
    edges = np.stack([
        np.hypot(*(corners[:, 0] - corners[:, 1]).T),
        np.hypot(*(corners[:, 1] - corners[:, 2]).T),
        np.hypot(*(corners[:, 2] - corners[:, 0]).T),
    ], axis=1)
    good_simplex = edges.max(axis=1) <= settings.max_edge_m
    if not good_simplex.any():
        raise ProcessingError(
            f"No mesh triangles are shorter than {settings.max_edge_m:g} m; "
            "increase the max triangle edge"
        )

    # Grid over the extent of the usable triangles.
    used = xy[np.unique(tri.simplices[good_simplex])]
    xmin, ymin = used.min(axis=0)
    xmax, ymax = used.max(axis=0)
    cell = 0.5
    while ((xmax - xmin) / cell + 1) * ((ymax - ymin) / cell + 1) > max_grid_cells:
        cell *= 1.5
    xs = np.arange(math.floor(xmin / cell) * cell, xmax + cell, cell)
    ys = np.arange(math.floor(ymin / cell) * cell, ymax + cell, cell)
    gx, gy = np.meshgrid(xs, ys)
    q = np.column_stack([gx.ravel(), gy.ravel()])

    simplex = tri.find_simplex(q)
    in_mesh = simplex >= 0
    in_mesh[in_mesh] = good_simplex[simplex[in_mesh]]
    if prep.poly_utm is not None:
        in_mesh &= _points_in_polygon(q[:, 0], q[:, 1], prep.poly_utm)

    depth = np.full(q.shape[0], np.nan)
    interp = LinearNDInterpolator(tri, z)
    depth[in_mesh] = interp(q[in_mesh])
    depth = depth.reshape(gx.shape)

    sigma_cells = SMOOTHING_SIGMA_M[settings.smoothing] / cell
    depth = _masked_gaussian(depth, sigma_cells)

    if not np.isfinite(depth).any():
        raise ProcessingError("The mesh does not cover any of the processing area")
    return Surface(epsg=prep.epsg, x0=float(xs[0]), y0=float(ys[0]), cell=cell, depth=depth), int(xy.shape[0])


# ---------------------------------------------------------------------------
# Contours
# ---------------------------------------------------------------------------

@dataclass
class ContourLine:
    level: float        # depth in m (positive down)
    is_major: bool
    xy: np.ndarray      # (n, 2) UTM


def _rdp_simplify(xy, tol):
    """Iterative Douglas-Peucker; preserves endpoints (and thus closed rings)."""
    n = xy.shape[0]
    if n <= 2 or tol <= 0:
        return xy
    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    tol_sq = tol * tol
    while stack:
        i0, i1 = stack.pop()
        if i1 - i0 < 2:
            continue
        p0, p1 = xy[i0], xy[i1]
        seg = p1 - p0
        seg_len_sq = float(seg @ seg)
        v = xy[i0 + 1:i1] - p0
        if seg_len_sq < 1e-12:
            dist_sq = (v ** 2).sum(axis=1)
        else:
            t = (v @ seg) / seg_len_sq
            d = v - np.outer(t, seg)
            dist_sq = (d ** 2).sum(axis=1)
        k_local = int(np.argmax(dist_sq))
        if float(dist_sq[k_local]) <= tol_sq:
            continue
        k = i0 + 1 + k_local
        keep[k] = True
        stack.append((i0, k))
        stack.append((k, i1))
    return xy[keep]


def _polyline_length(xy):
    if xy.shape[0] < 2:
        return 0.0
    d = np.diff(xy, axis=0)
    return float(np.hypot(d[:, 0], d[:, 1]).sum())


def make_contours(surface, settings, min_length_m=3.0):
    import contourpy

    z = surface.depth
    finite = np.isfinite(z)
    zmin, zmax = float(np.nanmin(z)), float(np.nanmax(z))
    ratio = int(round(settings.major_m / settings.minor_m))
    k_lo = int(math.ceil(zmin / settings.minor_m - 1e-9))
    k_hi = int(math.floor(zmax / settings.minor_m + 1e-9))

    cg = contourpy.contour_generator(
        x=surface.x_centers(), y=surface.y_centers(),
        z=np.where(finite, z, np.nan),
        line_type=contourpy.LineType.Separate,
    )
    tol = surface.cell * 0.3
    lines = []
    for k in range(k_lo, k_hi + 1):
        level = round(k * settings.minor_m, 6)
        is_major = k % ratio == 0
        for xy in cg.lines(level) or []:
            xy = _rdp_simplify(np.asarray(xy, dtype=np.float64), tol)
            if _polyline_length(xy) >= min_length_m:
                lines.append(ContourLine(level=level, is_major=is_major, xy=xy))
    return lines, (zmin, zmax)


# ---------------------------------------------------------------------------
# Rendering (ported from point-cloud-trimmer trimmer/contours.py write_geotiff)
# ---------------------------------------------------------------------------

def _rgb_from_hex(hex_str):
    s = str(hex_str).lstrip("#")
    if len(s) not in (6, 8) or any(c not in "0123456789abcdefABCDEF" for c in s):
        raise ValueError(f"Invalid colour: {hex_str}")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def _rgba(hex_str, opacity):
    return _rgb_from_hex(hex_str) + (int(round(255 * min(1.0, max(0.0, opacity)))),)


def _load_font(size_px):
    from PIL import ImageFont
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/System/Library/Fonts/Supplemental/Arial Bold.ttf"):
        try:
            return ImageFont.truetype(p, size=size_px)
        except OSError:
            continue
    return ImageFont.load_default(size=size_px)


def _text_size(font, text):
    l, t, r, b = font.getbbox(text)
    return int(r - l), int(b - t)


def _draw_rotated_rect(draw, cx, cy, w, h, angle_deg, fill=255):
    a = np.deg2rad(-angle_deg)  # image y is inverted vs. math convention
    ca, sa = np.cos(a), np.sin(a)
    corners = np.array([[-w / 2, -h / 2], [w / 2, -h / 2], [w / 2, h / 2], [-w / 2, h / 2]])
    rot = corners @ np.array([[ca, sa], [-sa, ca]])
    draw.polygon([(cx + p[0], cy + p[1]) for p in rot], fill=fill)


def _paste_rotated_text(canvas, cx, cy, text, font, color, angle_deg, halo_color=None, halo_px=0):
    from PIL import Image, ImageDraw
    tw, th = _text_size(font, text)
    l, t, _, _ = font.getbbox(text)
    pad = max(2, int(round(font.size * 0.35))) + max(0, halo_px)
    tile = Image.new("RGBA", (tw + 2 * pad, th + 2 * pad), (0, 0, 0, 0))
    d = ImageDraw.Draw(tile)
    origin = (pad - l, pad - t)
    if halo_color is not None and halo_px > 0:
        d.text(origin, text, font=font, fill=halo_color, stroke_width=halo_px, stroke_fill=halo_color)
    d.text(origin, text, font=font, fill=color)
    tile_rot = tile.rotate(angle_deg, expand=True, resample=Image.BICUBIC)
    rw, rh = tile_rot.size
    canvas.alpha_composite(tile_rot, (int(round(cx - rw / 2)), int(round(cy - rh / 2))))


def _save_geotiff(img, out_path, ulx, uly, pixel_size, epsg):
    from PIL import Image
    from PIL.TiffImagePlugin import ImageFileDirectory_v2

    ifd = ImageFileDirectory_v2()
    ifd.tagtype[33550] = 12   # DOUBLE  ModelPixelScaleTag
    ifd.tagtype[33922] = 12   # DOUBLE  ModelTiepointTag
    ifd.tagtype[34735] = 3    # SHORT   GeoKeyDirectoryTag
    ifd.tagtype[34737] = 2    # ASCII   GeoAsciiParamsTag
    ifd[33550] = (float(pixel_size), float(pixel_size), 0.0)
    ifd[33922] = (0.0, 0.0, 0.0, float(ulx), float(uly), 0.0)
    ifd[34735] = (
        1, 1, 0, 3,
        1024, 0, 1, 1,          # GTModelTypeGeoKey = projected
        1025, 0, 1, 1,          # GTRasterTypeGeoKey = PixelIsArea
        3072, 0, 1, int(epsg),  # ProjectedCSTypeGeoKey
    )
    ifd[34737] = f"WGS 84 / UTM (EPSG:{int(epsg)})|"
    Image.MAX_IMAGE_PIXELS = None
    img.save(out_path, format="TIFF", tiffinfo=ifd, compression="tiff_lzw")


def _label_text(level):
    return f"{round(level, 2):g}"


def render(lines, surface, settings, max_side_px=6000, max_canvas_px=60_000_000):
    """Render contour lines + labels onto a transparent RGBA image.

    Returns (image, ulx, uly, pixel_size).
    """
    from PIL import Image, ImageDraw

    pts_all = np.vstack([l.xy for l in lines])
    margin = settings.label_size_px * settings.pixel_size_m  # room for edge labels
    ux_min, uy_min = pts_all.min(axis=0) - margin
    ux_max, uy_max = pts_all.max(axis=0) + margin

    px_size = settings.pixel_size_m
    while max(ux_max - ux_min, uy_max - uy_min) / px_size > max_side_px:
        px_size *= 1.25
    w = max(1, int(math.ceil((ux_max - ux_min) / px_size)))
    h = max(1, int(math.ceil((uy_max - uy_min) / px_size)))
    ux_max = ux_min + w * px_size
    uy_max = uy_min + h * px_size

    ss = 2
    while ss > 1 and w * h * ss * ss > max_canvas_px:
        ss -= 1
    W, H = w * ss, h * ss
    scale = ss / px_size

    def to_px(xy):
        return np.column_stack([(xy[:, 0] - ux_min) * scale, (uy_max - xy[:, 1]) * scale])

    line_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    line_draw = ImageDraw.Draw(line_layer)
    minor_color = _rgba(settings.minor_color, settings.minor_opacity)
    major_color = _rgba(settings.major_color, settings.major_opacity)
    label_color = _rgba(settings.label_color, settings.label_opacity)
    halo_color = _rgba(settings.label_halo_color, settings.label_opacity * 0.94) if settings.label_halo else None
    minor_w = max(1, int(round(settings.minor_width_px * ss)))
    major_w = max(1, int(round(settings.major_width_px * ss)))
    label_size = max(6, int(round(settings.label_size_px * ss)))
    halo_px = max(1, int(round(label_size / 8))) if halo_color else 0
    spacing_px = max(50.0, settings.label_spacing_m * scale)
    font = _load_font(label_size)

    placements = []
    # Minor first so majors draw on top.
    for major_pass in (False, True):
        for L in lines:
            if L.is_major != major_pass:
                continue
            pts = to_px(L.xy)
            if pts.shape[0] < 2:
                continue
            line_draw.line([tuple(p) for p in pts],
                           fill=major_color if L.is_major else minor_color,
                           width=major_w if L.is_major else minor_w, joint="curve")
            if not (settings.label_major if L.is_major else settings.label_minor):
                continue
            text = _label_text(L.level)
            seg = np.diff(pts, axis=0)
            seg_len = np.hypot(seg[:, 0], seg[:, 1])
            total = float(seg_len.sum())
            tw, th = _text_size(font, text)
            if total < tw * 1.5:
                continue
            if total < spacing_px * 3.0:
                positions = [total * 0.5]
            else:
                positions = list(np.arange(spacing_px * 0.5, total, spacing_px))
            cum = np.concatenate(([0.0], np.cumsum(seg_len)))
            for pos in positions:
                k = int(np.clip(np.searchsorted(cum, pos) - 1, 0, seg.shape[0] - 1))
                t = (pos - cum[k]) / max(1e-9, float(seg_len[k]))
                cx = float(pts[k, 0] + t * seg[k, 0])
                cy = float(pts[k, 1] + t * seg[k, 1])
                angle = float(np.degrees(np.arctan2(-seg[k, 1], seg[k, 0])))
                if angle > 90:
                    angle -= 180
                elif angle < -90:
                    angle += 180
                # Skip labels that would collide with one already placed.
                if any(math.hypot(cx - px, cy - py) < (tw + ptw) / 2 + th
                       for px, py, _a, _t, ptw, _h in placements):
                    continue
                placements.append((cx, cy, angle, text, tw, th))

    if placements:
        # Cut gaps in the lines under each label so text stays readable.
        mask = Image.new("L", (W, H), 0)
        mask_draw = ImageDraw.Draw(mask)
        pad = max(3, int(round(label_size * 0.3))) + halo_px
        for cx, cy, angle, _t, tw, th in placements:
            _draw_rotated_rect(mask_draw, cx, cy, tw + 2 * pad, th + 2 * pad, angle)
        arr = np.array(line_layer)
        arr[np.array(mask) > 0, 3] = 0
        line_layer = Image.fromarray(arr, mode="RGBA")

    img = line_layer
    for cx, cy, angle, text, _tw, _th in placements:
        _paste_rotated_text(img, cx, cy, text, font, label_color, angle, halo_color, halo_px)

    if ss > 1:
        img = img.resize((w, h), resample=Image.LANCZOS)
    return img, float(ux_min), float(uy_max), float(px_size)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Auto-tuning
# ---------------------------------------------------------------------------

_NICE_INTERVALS = (0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0)
_MAJOR_FOR_MINOR = {0.1: 0.5, 0.25: 1.0, 0.5: 2.0, 1.0: 5.0, 2.0: 10.0, 5.0: 25.0,
                    10.0: 50.0, 20.0: 100.0, 50.0: 250.0}
TARGET_MAX_CONTOURS = 40


def suggest_settings(prep):
    """Settings derived from the soundings that will be meshed.

    Returns (values, notes): values is a dict of ContourSettings fields, notes a
    list of short human-readable reasons shown in the UI.
    """
    from scipy.spatial import cKDTree

    z, xy, t = prep.z, prep.xy, prep.rows[:, TIME]
    lo, hi = (float(v) for v in np.percentile(z, [2, 98]))
    depth_range = max(hi - lo, 0.01)
    median_depth = float(np.median(z))
    footprint = beam_footprint_m(max(median_depth, 0.5))
    extent = float(np.ptp(xy, axis=0).max()) if z.size > 1 else 1.0
    tree = cKDTree(xy)

    # Distance from each sounding to the nearest sounding of another pass ~ line spacing.
    k = min(40, z.size)
    dist, idx = tree.query(xy, k=k)
    other = np.abs(t[idx] - t[:, None]) > PASS_GAP_S
    nearest_other = np.where(other, dist, np.inf).min(axis=1)
    nearest_other = nearest_other[np.isfinite(nearest_other)]
    spacing = float(np.percentile(nearest_other, 95)) if nearest_other.size else 10.0

    # Disagreement between passes, relative to depth (robust).
    radius = float(np.clip(round(0.25 * footprint * 2) / 2, 1.0, 5.0))
    pairs = tree.query_pairs(radius, output_type="ndarray")
    rel_scale = None
    if pairs.size:
        a, b = pairs[:, 0], pairs[:, 1]
        sel = np.abs(t[a] - t[b]) > PASS_GAP_S
        if sel.sum() >= 50:
            a, b = a[sel], b[sel]
            rel = np.abs(z[a] - z[b]) / np.maximum((z[a] + z[b]) / 2, 0.5)
            rel_scale = 1.4826 * float(np.median(rel))
    cross_sigma_m = (rel_scale or 0.01) * median_depth / math.sqrt(2)

    # Contour interval: at most ~40 levels, and no finer than twice the pass-to-pass noise.
    wanted = max(depth_range / TARGET_MAX_CONTOURS, 2 * cross_sigma_m)
    minor = next((v for v in _NICE_INTERVALS if v >= wanted), _NICE_INTERVALS[-1])
    major = _MAJOR_FOR_MINOR[minor]

    # Line and label sizes grow gently with survey extent (the image is shown scaled down).
    f = float(np.clip((extent / 100.0) ** 0.3, 1.0, 3.0))
    pct = round(8 * rel_scale * 100, 1) if rel_scale is not None else 5.0
    values = {
        "minor_m": minor,
        "major_m": major,
        "max_edge_m": float(np.clip(round(4 * spacing), 10, 150)),
        "label_size_px": int(round(22 * f)),
        "major_width_px": round(2.5 * f * 2) / 2,
        "minor_width_px": round(1.0 * f * 2) / 2,
        "label_spacing_m": float(round(40 * f * f / 5) * 5),
        "qc_radius_m": radius,
        "qc_depth_tol_m": 0.3,
        "qc_depth_tol_pct": pct,
        "qc_spike_tol_m": 0.3,
        "qc_spike_k": 5.0,
        "overlap_buffer_m": float(np.clip(round(footprint), 3, 30)),
    }
    n_levels = int(depth_range // minor) + 1
    notes = [
        f"Contours every {minor:g} m, majors every {major:g} m: about {n_levels} levels over "
        f"{lo:.1f}–{hi:.1f} m, and no finer than the {cross_sigma_m:.2f} m scatter between passes.",
        f"Max mesh triangle edge {values['max_edge_m']:g} m: survey lines are up to about {spacing:.0f} m apart.",
        f"Line widths and labels sized for a {extent:.0f} m survey.",
        f"Overlap check within {radius:g} m (a quarter of the {footprint:.1f} m beam footprint at the "
        f"median depth of {median_depth:.1f} m), tolerance {pct:g}% of depth (at least 0.3 m).",
        "Spikes: more than 5× the local scatter of neighbouring soundings (and at least 0.3 m).",
    ]
    return values, notes


def _prepare_tuned(points, files, polygon_latlon, settings, tides, exclude, exclude_overlaps, tide_series=None):
    """prepare(), then apply auto-tuned settings when enabled."""
    from dataclasses import replace

    prep = prepare(points, files, polygon_latlon, settings, tides, exclude, exclude_overlaps, tide_series)
    if not settings.auto_tune:
        return prep, settings, None
    values, notes = suggest_settings(prep)
    tuned = replace(settings, **values)
    if exclude_overlaps and tuned.overlap_buffer_m != settings.overlap_buffer_m:
        prep = prepare(points, files, polygon_latlon, tuned, tides, exclude, exclude_overlaps, tide_series)
    return prep, tuned, {"values": values, "notes": notes}


def analyse(csv_paths, polygon_latlon, settings, tides=None, exclude=None, exclude_overlaps=None,
            tide_series=None):
    """Data-quality report for exactly the soundings that would be meshed."""
    points, files = load_survey(csv_paths)
    if points.shape[0] == 0:
        raise ProcessingError("The selected files contain no soundings with a GPS fix")
    prep, settings, tuned = _prepare_tuned(points, files, polygon_latlon, settings, tides, exclude,
                                           exclude_overlaps, tide_series)
    report, _ = quality_check(prep, files, settings)
    report["tuned"] = tuned
    return report


def process(csv_paths, polygon_latlon, settings, out_tif, out_preview, tides=None, exclude=None,
            exclude_overlaps=None, tide_series=None):
    """Run the full pipeline and write the GeoTIFF + PNG preview.

    Returns a JSON-serialisable summary including WGS 84 bounds of the preview
    and the data-quality report for the soundings that were meshed.
    """
    points, files = load_survey(csv_paths)
    if points.shape[0] == 0:
        raise ProcessingError("The selected files contain no soundings with a GPS fix")
    prep, settings, tuned = _prepare_tuned(points, files, polygon_latlon, settings, tides, exclude,
                                           exclude_overlaps, tide_series)
    report, spikes = quality_check(prep, files, settings)
    keep = ~spikes if settings.exclude_spikes else None
    surface, n_used = build_surface(prep, settings, keep)
    lines, (zmin, zmax) = make_contours(surface, settings)
    if not lines:
        raise ProcessingError(
            f"No contours: depth range {zmin:.2f}-{zmax:.2f} m does not cross a "
            f"{settings.minor_m:g} m interval"
        )
    img, ulx, uly, px = render(lines, surface, settings)
    _save_geotiff(img, out_tif, ulx, uly, px, surface.epsg)
    img.save(out_preview, format="PNG", optimize=True)

    w, h = img.size
    ce = np.array([ulx, ulx + w * px, ulx + w * px, ulx])
    cn = np.array([uly, uly, uly - h * px, uly - h * px])
    lat, lon = from_utm(ce, cn, surface.epsg)
    return {
        "epsg": surface.epsg,
        "bounds": [[float(lat.min()), float(lon.min())], [float(lat.max()), float(lon.max())]],
        "width_px": w,
        "height_px": h,
        "pixel_size_m": round(px, 4),
        "points_total": int(points.shape[0]),
        "points_accepted": int(accepted_mask(points, settings.min_conf).sum()),
        "points_used": n_used,
        "points_excluded": prep.n_excluded,
        "overlap_removed": prep.overlap_removed,
        "spikes_excluded": int(spikes.sum()) if settings.exclude_spikes else 0,
        "depth_min_m": round(zmin, 2),
        "depth_max_m": round(zmax, 2),
        "minor_lines": sum(1 for l in lines if not l.is_major),
        "major_lines": sum(1 for l in lines if l.is_major),
        "settings": settings.__dict__,   # effective (auto-tuned) settings
        "tuned_notes": tuned["notes"] if tuned else [],
        "quality": {
            "file_offsets": report["file_offsets"],
            "altitude_warnings": report["altitude_warnings"],
            "conflict_count": report["conflict_count"],
            "spike_count": report["spike_count"],
        },
    }
