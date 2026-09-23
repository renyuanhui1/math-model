from __future__ import division

import math

import numpy as np
from scipy.io import loadmat


EARTH_RADIUS_M = 6371008.8


class DEMGrid(object):
    def __init__(self, path):
        raw = loadmat(str(path))
        self.dem = np.asarray(raw["dem"], dtype=float)
        self.longitude = np.asarray(raw["longitude"], dtype=float).ravel()
        self.latitude = np.asarray(raw["latitude"], dtype=float).ravel()
        self.nodata = float(np.asarray(raw["nodata"]).ravel()[0])
        self.epsg = int(np.asarray(raw["epsg_code"]).ravel()[0])
        self.dem[self.dem == self.nodata] = np.nan
        self.lon_min = float(self.longitude.min())
        self.lon_max = float(self.longitude.max())
        self.lat_min = float(self.latitude.min())
        self.lat_max = float(self.latitude.max())

    def contains(self, lon, lat):
        return self.lon_min <= lon <= self.lon_max and self.lat_min <= lat <= self.lat_max

    def elevation(self, lon, lat):
        if not self.contains(lon, lat):
            return np.nan
        ix = int(np.clip(np.searchsorted(self.longitude, lon), 1, len(self.longitude) - 1))
        if abs(self.longitude[ix - 1] - lon) < abs(self.longitude[ix] - lon):
            ix -= 1
        lat_asc = self.latitude[::-1]
        iy_asc = int(np.clip(np.searchsorted(lat_asc, lat), 1, len(lat_asc) - 1))
        if abs(lat_asc[iy_asc - 1] - lat) < abs(lat_asc[iy_asc] - lat):
            iy_asc -= 1
        iy = len(self.latitude) - 1 - iy_asc
        return float(self.dem[iy, ix])

    def elevations(self, lons, lats):
        lons = np.asarray(lons, dtype=float)
        lats = np.asarray(lats, dtype=float)
        lon_step = float(self.longitude[1] - self.longitude[0])
        lat_step = float(self.latitude[0] - self.latitude[1])
        ix = np.rint((lons - self.longitude[0]) / lon_step).astype(int)
        iy = np.rint((self.latitude[0] - lats) / lat_step).astype(int)
        valid = ((ix >= 0) & (ix < len(self.longitude)) & (iy >= 0) & (iy < len(self.latitude)))
        out = np.full(lons.shape, np.nan, dtype=float)
        out[valid] = self.dem[iy[valid], ix[valid]]
        return out

    def sample_line(self, lon1, lat1, lon2, lat2, spacing_m=15.0):
        distance = horizontal_distance_m(lon1, lat1, lon2, lat2)
        count = max(2, int(math.ceil(distance / spacing_m)) + 1)
        f = np.linspace(0.0, 1.0, count)
        lons = lon1 + (lon2 - lon1) * f
        lats = lat1 + (lat2 - lat1) * f
        values = np.array([self.elevation(float(x), float(y)) for x, y in zip(lons, lats)], dtype=float)
        return f, lons, lats, values

    def max_elevation_on_line(self, lon1, lat1, lon2, lat2, spacing_m=15.0):
        _, _, _, z = self.sample_line(lon1, lat1, lon2, lat2, spacing_m=spacing_m)
        if np.all(np.isnan(z)):
            raise ValueError("航段完全落在DEM有效范围之外")
        return float(np.nanmax(z))

    def local_high_points(self, lon, lat, radius_m=1500.0, count=6, min_separation_m=300.0):
        lat_pad = radius_m / 111320.0
        lon_pad = radius_m / (111320.0 * math.cos(math.radians(lat)))
        x_idx = np.where((self.longitude >= lon - lon_pad) & (self.longitude <= lon + lon_pad))[0]
        y_idx = np.where((self.latitude >= lat - lat_pad) & (self.latitude <= lat + lat_pad))[0]
        candidates = []
        for iy in y_idx:
            for ix in x_idx:
                x = float(self.longitude[ix])
                y = float(self.latitude[iy])
                if horizontal_distance_m(lon, lat, x, y) <= radius_m:
                    z = float(self.dem[iy, ix])
                    if np.isfinite(z):
                        candidates.append((z, x, y))
        candidates.sort(reverse=True)
        selected = []
        for z, x, y in candidates:
            if all(horizontal_distance_m(x, y, q[1], q[2]) >= min_separation_m for q in selected):
                selected.append((z, x, y))
                if len(selected) >= count:
                    break
        return selected


def horizontal_distance_m(lon1, lat1, lon2, lat2):
    lat0 = math.radians((lat1 + lat2) / 2.0)
    dx = math.radians(lon2 - lon1) * EARTH_RADIUS_M * math.cos(lat0)
    dy = math.radians(lat2 - lat1) * EARTH_RADIUS_M
    return math.hypot(dx, dy)


def metric_xy(lon, lat, lon0, lat0):
    x = math.radians(lon - lon0) * EARTH_RADIUS_M * math.cos(math.radians(lat0))
    y = math.radians(lat - lat0) * EARTH_RADIUS_M
    return x, y


def lonlat_from_xy(x, y, lon0, lat0):
    lon = lon0 + math.degrees(x / (EARTH_RADIUS_M * math.cos(math.radians(lat0))))
    lat = lat0 + math.degrees(y / EARTH_RADIUS_M)
    return lon, lat


def build_segment_database(nodes, dem):
    records = []
    rows = {r["node_id"]: r for _, r in nodes.iterrows()}
    for origin_id, origin in rows.items():
        for destination_id, destination in rows.items():
            if origin_id == destination_id:
                continue
            distance = horizontal_distance_m(origin["lon"], origin["lat"], destination["lon"], destination["lat"])
            max_dem = dem.max_elevation_on_line(origin["lon"], origin["lat"], destination["lon"], destination["lat"])
            cruise = max(max_dem + 50.0, float(origin["work_alt_m"]), float(destination["work_alt_m"]))
            records.append({
                "origin": origin_id,
                "destination": destination_id,
                "distance_m": distance,
                "max_dem_m": max_dem,
                "cruise_alt_m": cruise,
                "origin_work_alt_m": float(origin["work_alt_m"]),
                "destination_work_alt_m": float(destination["work_alt_m"]),
                "climb_m": max(0.0, cruise - float(origin["work_alt_m"])),
                "descent_m": max(0.0, cruise - float(destination["work_alt_m"])),
            })
    import pandas as pd
    return pd.DataFrame(records)
