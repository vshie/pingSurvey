"""Online tide heights for post-mission processing.

Providers:
- NOAA CO-OPS (free, no key; US waters). The nearest prediction station within
  range is used. Reference stations give 6-minute measured water level (preferred
  for past surveys: it includes surge/weather) or predictions; subordinate stations
  give high/low predictions, joined with the standard cosine interpolation.
- WorldTides v3 (global, needs the user's API key), used when no NOAA station is
  in range.

Every series is returned as water level in metres above mean sea level (MSL),
sampled in UTC, together with the height of MSL above each supported chart datum
(LAT, MLLW, MSL) so depths can be reduced to the datum the user picks. Results are
cached per log so reprocessing works offline. Only `requests` is required.
"""

import json
import math
import os
import time
from datetime import datetime, timezone

import requests

NOAA_API = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
NOAA_MDAPI = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi"
WORLDTIDES_API = "https://www.worldtides.info/api/v3"
NOAA_ATTRIBUTION = "Tide data: NOAA CO-OPS (tidesandcurrents.noaa.gov)"

TIMEOUT_S = 15
STATION_LIST_MAX_AGE_S = 30 * 24 * 3600
NOAA_CHUNK_DAYS = 30          # 6-minute products are limited to about a month per request
MARGIN_S = 15 * 60            # fetch a little beyond the log so every sounding is bracketed
HILO_MARGIN_S = 24 * 3600     # high/low extremes must bracket the log
DATUMS = ("lat", "mllw", "msl")


class TideError(Exception):
    """User-facing problem fetching tides (no provider, offline, bad key, ...)."""


def _distance_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def _noaa_time(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y%m%d %H:%M")


def _parse_noaa_time(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp()


def _get_json(url, params, what):
    try:
        r = requests.get(url, params=params, timeout=TIMEOUT_S)
    except requests.RequestException as e:
        raise TideError(f"Internet required: could not reach {what} ({e.__class__.__name__})")
    try:
        return r.json()
    except ValueError:
        raise TideError(f"{what} returned an unexpected response (HTTP {r.status_code})")


def cosine_interpolate(extremes, times):
    """Water level at `times` from a list of (time, height) high/low extremes."""
    out = []
    j = 0
    for t in times:
        while j + 1 < len(extremes) - 1 and extremes[j + 1][0] <= t:
            j += 1
        (t1, h1), (t2, h2) = extremes[j], extremes[j + 1]
        if not t1 <= t <= t2:
            raise TideError("High/low tide predictions don't cover the whole survey")
        f = (t - t1) / (t2 - t1)
        out.append(h1 + (h2 - h1) * (1 - math.cos(math.pi * f)) / 2)
    return out


class TideService:
    def __init__(self, base_dir):
        self.base_dir = base_dir
        self.cache_dir = os.path.join(base_dir, "cache")
        self.config_path = os.path.join(base_dir, "config.json")
        self.stations_path = os.path.join(base_dir, "noaa_stations.json")
        self._stations = None
        self._datums = {}

    # ---------------------------------------------------------------- config
    def _load_config(self):
        try:
            with open(self.config_path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def worldtides_key(self):
        return self._load_config().get("worldtides_key") or ""

    def set_worldtides_key(self, key):
        os.makedirs(self.base_dir, exist_ok=True)
        cfg = self._load_config()
        cfg["worldtides_key"] = (key or "").strip()
        with open(self.config_path, "w") as f:
            json.dump(cfg, f)
        os.chmod(self.config_path, 0o600)

    def public_config(self):
        key = self.worldtides_key()
        return {"worldtides_key_set": bool(key), "worldtides_key_masked": ("•" * 8 + key[-4:]) if key else ""}

    # --------------------------------------------------------------- NOAA
    def noaa_stations(self):
        if self._stations is not None:
            return self._stations
        fresh = os.path.isfile(self.stations_path) and \
            time.time() - os.path.getmtime(self.stations_path) < STATION_LIST_MAX_AGE_S
        if not fresh:
            try:
                data = _get_json(f"{NOAA_MDAPI}/stations.json", {"type": "tidepredictions"}, "NOAA")
                stations = [{"id": s["id"], "name": s["name"], "lat": float(s["lat"]), "lon": float(s["lng"]),
                             "type": s.get("type"), "reference_id": s.get("reference_id")}
                            for s in data["stations"]]
                os.makedirs(self.base_dir, exist_ok=True)
                with open(self.stations_path, "w") as f:
                    json.dump(stations, f)
            except (TideError, KeyError, TypeError):
                if not os.path.isfile(self.stations_path):
                    raise
        with open(self.stations_path) as f:
            self._stations = json.load(f)
        return self._stations

    def nearest_noaa_station(self, lat, lon, max_km):
        ranked = sorted(((_distance_km(lat, lon, s["lat"], s["lon"]), s) for s in self.noaa_stations()),
                        key=lambda x: x[0])
        in_range = [(d, s) for d, s in ranked if d <= max_km]
        if not in_range:
            return None
        best_d, best = in_range[0]
        if best.get("type") != "R":
            # A reference station gives continuous 6-minute data; prefer it when nearly as close.
            ref = next(((d, s) for d, s in in_range if s.get("type") == "R" and d <= max(1.5 * best_d, best_d + 1)), None)
            if ref:
                best_d, best = ref
        return dict(best, distance_km=round(best_d, 2))

    def _station_datums(self, station_id):
        """Height of MSL above each datum (m) from a station's datum table, or None."""
        if station_id in self._datums:
            return self._datums[station_id]
        data = _get_json(f"{NOAA_MDAPI}/stations/{station_id}/datums.json", {"units": "metric"}, "NOAA")
        table = {d["name"]: d["value"] for d in (data.get("datums") or [])}
        result = None
        if "MSL" in table:
            msl = table["MSL"]
            result = {"msl": 0.0}
            if "MLLW" in table:
                result["mllw"] = round(msl - table["MLLW"], 3)
            if data.get("LAT") is not None:
                result["lat"] = round(msl - float(data["LAT"]), 3)
        self._datums[station_id] = result
        return result

    def _subordinate_info(self, station_id):
        data = _get_json(f"{NOAA_MDAPI}/stations/{station_id}.json", {"expand": "tidepredoffsets"}, "NOAA")
        offsets = data["stations"][0].get("tidepredoffsets") or {}
        return offsets

    def _noaa_6min(self, station_id, product, start, end):
        times, heights, quality = [], [], set()
        t0 = start
        while t0 < end:
            t1 = min(end, t0 + NOAA_CHUNK_DAYS * 86400)
            params = {"product": product, "station": station_id, "begin_date": _noaa_time(t0),
                      "end_date": _noaa_time(t1), "datum": "MSL", "units": "metric", "time_zone": "gmt",
                      "format": "json", "application": "pingSurvey"}
            if product == "predictions":
                params["interval"] = "6"
            data = _get_json(NOAA_API, params, "NOAA")
            if "error" in data:
                return None
            rows = data.get("predictions") if product == "predictions" else data.get("data")
            for r in rows or []:
                if r.get("v") in (None, ""):
                    continue
                times.append(_parse_noaa_time(r["t"]))
                heights.append(float(r["v"]))
                if r.get("q"):
                    quality.add(r["q"])
            t0 = t1
        return times, heights, quality

    def _noaa_series(self, station, start, end, prefer_measured):
        s, e = start - MARGIN_S, end + MARGIN_S
        warnings = []
        if station.get("type") == "R":
            datums = self._station_datums(station["id"])
            if prefer_measured and end < time.time():
                got = self._noaa_6min(station["id"], "water_level", s, e)
                if got and got[0] and got[0][0] <= start and got[0][-1] >= end:
                    q = "verified" if got[2] == {"v"} else "preliminary"
                    return got[0], got[1], f"measured water level ({q})", datums, False, warnings
                warnings.append("Measured water level not available for the whole survey; using predictions.")
            got = self._noaa_6min(station["id"], "predictions", s, e)
            if not got or not got[0]:
                raise TideError(f"NOAA has no tide data for station {station['id']} at this time")
            return got[0], got[1], "predicted tide", datums, False, warnings

        # Subordinate station: high/low predictions relative to MLLW, offsets from its reference.
        params = {"product": "predictions", "station": station["id"], "begin_date": _noaa_time(start - HILO_MARGIN_S),
                  "end_date": _noaa_time(end + HILO_MARGIN_S), "datum": "MLLW", "interval": "hilo",
                  "units": "metric", "time_zone": "gmt", "format": "json", "application": "pingSurvey"}
        data = _get_json(NOAA_API, params, "NOAA")
        if "error" in data or not data.get("predictions"):
            raise TideError(f"NOAA has no high/low predictions for station {station['id']} at this time")
        extremes = [(_parse_noaa_time(r["t"]), float(r["v"])) for r in data["predictions"]]
        times = [float(t) for t in range(int(s), int(e) + 1, 360)]
        mllw_heights = cosine_interpolate(extremes, times)

        offsets = self._subordinate_info(station["id"])
        ref_id = offsets.get("refStationId") or station.get("reference_id")
        ref_datums = self._station_datums(ref_id) if ref_id else None
        if not ref_datums or "mllw" not in ref_datums:
            raise TideError(f"Can't relate subordinate station {station['id']} to mean sea level")
        ratio = 1.0
        if offsets.get("heightAdjustedType") == "R":
            ratio = (float(offsets.get("heightOffsetHighTide") or 1) + float(offsets.get("heightOffsetLowTide") or 1)) / 2
        datums = {k: round(v * ratio, 3) for k, v in ref_datums.items()}
        heights = [h - datums["mllw"] for h in mllw_heights]   # MLLW -> MSL
        warnings.append(f"Subordinate station: high/low predictions joined by cosine interpolation; datum offsets "
                        f"scaled from reference station {ref_id} (approximate).")
        return times, heights, "predicted tide (high/low)", datums, True, warnings

    # ---------------------------------------------------------- WorldTides
    def _worldtides(self, lat, lon, start, end, key):
        s = int(start - MARGIN_S)
        params = {"heights": "", "datums": "", "datum": "MSL", "lat": f"{lat:.5f}", "lon": f"{lon:.5f}",
                  "start": s, "length": int(end + MARGIN_S - s), "step": 600, "key": key}
        data = _get_json(WORLDTIDES_API, params, "WorldTides")
        if data.get("status") != 200 or "heights" not in data:
            raise TideError(f"WorldTides: {data.get('error') or 'request failed'}")
        times = [float(h["dt"]) for h in data["heights"]]
        heights = [float(h["height"]) for h in data["heights"]]
        datums = {"msl": 0.0}
        for d in data.get("datums") or []:
            name = str(d.get("name", "")).lower()
            if name in ("lat", "mllw"):
                datums[name] = round(-float(d["height"]), 3)   # datum height relative to MSL -> MSL above datum
        info = {"atlas": data.get("atlas"), "copyright": data.get("copyright"),
                "station": data.get("station"), "call_count": data.get("callCount")}
        return times, heights, datums, info

    def test_worldtides_key(self, key):
        params = {"extremes": "", "lat": "33.7", "lon": "-118.3", "length": 86400, "key": key}
        data = _get_json(WORLDTIDES_API, params, "WorldTides")
        if data.get("status") != 200:
            raise TideError(f"WorldTides: {data.get('error') or 'request failed'}")
        return {"ok": True, "call_count": data.get("callCount")}

    # ------------------------------------------------------------ public
    def _cache_file(self, log_name):
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in log_name)
        return os.path.join(self.cache_dir, safe + ".json")

    def series_for_log(self, log_name, lat, lon, start, end, prefer_measured=True, max_station_km=30.0,
                       refresh=False):
        """Water level (m above MSL) through one log, with datum offsets. Cached per log."""
        key = f"{round(start)}|{round(end)}|{prefer_measured}|{float(max_station_km):g}"
        path = self._cache_file(log_name)
        cache = {}
        if os.path.isfile(path):
            try:
                with open(path) as f:
                    cache = json.load(f)
            except (OSError, ValueError):
                cache = {}
        if not refresh and key in cache:
            return dict(cache[key], cached=True)

        station = self.nearest_noaa_station(lat, lon, max_station_km)
        if station:
            times, heights, product, datums, approximate, warnings = \
                self._noaa_series(station, start, end, prefer_measured)
            result = {"provider": "NOAA CO-OPS", "station": station, "product": product,
                      "attribution": NOAA_ATTRIBUTION}
        else:
            wt_key = self.worldtides_key()
            if not wt_key:
                raise TideError(f"No NOAA tide station within {max_station_km:g} km and no WorldTides API key "
                                f"saved. Add a key, or enter tide heights manually.")
            times, heights, datums, info = self._worldtides(lat, lon, start, end, wt_key)
            approximate, warnings = False, []
            result = {"provider": "WorldTides", "station": info.get("station"), "product": "predicted tide",
                      "attribution": info.get("copyright") or "Tide data: WorldTides (worldtides.info)",
                      "atlas": info.get("atlas")}
        if not times or times[0] > start or times[-1] < end:
            raise TideError("The tide data returned doesn't cover the whole survey")
        result.update({
            "times": times, "heights": [round(h, 4) for h in heights], "datums": datums or {"msl": 0.0},
            "approximate": approximate, "warnings": warnings, "fetched_at": time.time(),
            "log": {"lat": lat, "lon": lon, "start": start, "end": end},
        })
        os.makedirs(self.cache_dir, exist_ok=True)
        cache[key] = result
        with open(path, "w") as f:
            json.dump(cache, f)
        return dict(result, cached=False)
