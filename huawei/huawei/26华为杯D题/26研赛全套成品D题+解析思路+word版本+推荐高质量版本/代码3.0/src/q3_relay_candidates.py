from __future__ import division

import itertools
import math

import numpy as np
import pandas as pd

from .dem import horizontal_distance_m
from .q3_communication import link_available


class RelayCoverageConflict(Exception):
    def __init__(self, start, end, trip_ids):
        Exception.__init__(self, "relay coverage conflict")
        self.start = float(start)
        self.end = float(end)
        self.trip_ids = tuple(trip_ids)


def _kmeans_lonlat(points, k, iterations=20):
    data = np.asarray(points, dtype=float)
    if k == 1:
        return [tuple(data.mean(axis=0))]
    order = np.argsort(data[:, 0])
    centers = np.vstack([data[order[0]], data[order[-1]]])
    for _ in range(iterations):
        distances = ((data[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
        labels = distances.argmin(axis=1)
        new_centers = centers.copy()
        for idx in range(k):
            if np.any(labels == idx):
                new_centers[idx] = data[labels == idx].mean(axis=0)
        if np.max(np.abs(new_centers - centers)) < 1e-9:
            break
        centers = new_centers
    return [tuple(row) for row in centers]


def generate_relay_candidates(blind_samples, nodes, dem, params, max_agl_m):
    origin = nodes[nodes["node_id"] == "O01"].iloc[0]
    gateway = {"lon": float(origin["lon"]), "lat": float(origin["lat"]),
               "alt_m": float(origin["ground_alt_m"]) + params["gateway_height_m"]}
    positions = list(zip(blind_samples["lon"].astype(float), blind_samples["lat"].astype(float)))
    seeds = []
    for k in (1, 2, 3, 4):
        seeds.extend(_kmeans_lonlat(positions, k))
    for lon, lat in list(seeds):
        seeds.append(((lon + gateway["lon"]) / 2.0, (lat + gateway["lat"]) / 2.0))
        for z, x, y in dem.local_high_points(lon, lat, radius_m=1800.0, count=4):
            seeds.append((x, y))
    for _, row in blind_samples.iloc[::max(1, len(blind_samples) // 12)].iterrows():
        seeds.append(((float(row["lon"]) + gateway["lon"]) / 2.0,
                      (float(row["lat"]) + gateway["lat"]) / 2.0))
    for _, group in blind_samples.groupby("trip_id"):
        points = [group.iloc[0], group.iloc[-1],
                  group.loc[group["lon"].idxmin()], group.loc[group["lon"].idxmax()],
                  group.loc[group["lat"].idxmin()], group.loc[group["lat"].idxmax()]]
        mean_lon, mean_lat = float(group["lon"].mean()), float(group["lat"].mean())
        seeds.append((mean_lon, mean_lat))
        seeds.append(((mean_lon + gateway["lon"]) / 2.0, (mean_lat + gateway["lat"]) / 2.0))
        for row in points:
            seeds.append((float(row["lon"]), float(row["lat"])))
            seeds.append(((float(row["lon"]) + gateway["lon"]) / 2.0,
                          (float(row["lat"]) + gateway["lat"]) / 2.0))
    unique = []
    for lon, lat in seeds:
        if not dem.contains(lon, lat):
            continue
        if all(horizontal_distance_m(lon, lat, x, y) > 120.0 for x, y in unique):
            unique.append((lon, lat))
    candidates = []
    # 候选点评分保留更密的盲区轨迹，避免细窄山脊附近的候选点被粗采样漏掉。
    sampled = blind_samples.iloc[::max(1, len(blind_samples) // 300)].reset_index(drop=True)
    for lon, lat in unique:
        ground = dem.elevation(lon, lat)
        for agl in (225.0, float(max_agl_m)):
            site = {"lon": lon, "lat": lat, "ground_m": ground, "agl_m": agl, "alt_m": ground + agl}
            backhaul = link_available(dem, site, gateway, params["backhaul_max_loss_db"], params)[0]
            if not backhaul:
                continue
            covered = []
            for idx, row in sampled.iterrows():
                transport = {"lon": float(row["lon"]), "lat": float(row["lat"]), "alt_m": float(row["alt_m"])}
                if link_available(dem, transport, site, params["access_max_loss_db"], params)[0]:
                    covered.append(idx)
            if covered:
                site["covered"] = frozenset(covered)
                candidates.append(site)
    candidates.sort(key=lambda c: (-len(c["covered"]), c["agl_m"],
                                   horizontal_distance_m(c["lon"], c["lat"], gateway["lon"], gateway["lat"])))
    selected = list(candidates[:40])
    if candidates:
        lon_edges = np.linspace(min(c["lon"] for c in candidates), max(c["lon"] for c in candidates), 4)
        lat_edges = np.linspace(min(c["lat"] for c in candidates), max(c["lat"] for c in candidates), 4)
        for i in range(3):
            for j in range(3):
                cell = [c for c in candidates if lon_edges[i] - 1e-12 <= c["lon"] <= lon_edges[i + 1] + 1e-12
                        and lat_edges[j] - 1e-12 <= c["lat"] <= lat_edges[j + 1] + 1e-12]
                if cell and cell[0] not in selected:
                    selected.append(cell[0])
        for trip_id, group in sampled.groupby("trip_id"):
            indices = set(group.index)
            ranked = sorted(candidates, key=lambda c: -len(indices & set(c["covered"])))
            for candidate in ranked[:3]:
                if candidate not in selected:
                    selected.append(candidate)
    return selected, sampled


def choose_relay_sites(candidates, sampled):
    universe = frozenset(range(len(sampled)))
    best = None
    for count in (1, 2):
        for combo in itertools.combinations(candidates, count):
            covered = frozenset().union(*(c["covered"] for c in combo))
            uncovered = len(universe - covered)
            key = (uncovered, count, sum(c["agl_m"] for c in combo),
                   -sum(len(c["covered"]) for c in combo))
            if best is None or key < best[0]:
                best = (key, combo)
            if uncovered == 0 and count == 1:
                break
        if best is not None and best[0][0] == 0:
            break
    if best is None or best[0][0] > 0:
        raise ValueError("两处候选中继悬停点仍无法覆盖全部直连盲区样本")
    result = []
    for idx, site in enumerate(best[1], 1):
        copied = dict(site)
        copied["site_id"] = "SITE-%02d" % idx
        result.append(copied)
    return result


def choose_relay_windows(candidates, blind_samples, dem, params, bucket_s=300.0, coverage_cache=None):
    if blind_samples.empty:
        return [], []
    if coverage_cache is None:
        coverage_cache = {}
    buckets = []
    unique_times = np.sort(blind_samples["time_s"].astype(float).unique())
    positive_steps = np.diff(unique_times)
    positive_steps = positive_steps[positive_steps > 1e-6]
    sample_step = float(np.median(positive_steps)) if len(positive_steps) else 5.0
    # Build windows from the actual blind-demand timeline.  A natural outage-free gap
    # closes a window; long continuous periods are split only at the requested maximum.
    ranges = []
    phase_start = float(unique_times[0])
    previous_time = phase_start
    for current_time in unique_times[1:]:
        current_time = float(current_time)
        if current_time - previous_time > 300.0 or current_time - phase_start >= bucket_s:
            ranges.append((phase_start, previous_time + sample_step))
            phase_start = current_time
        previous_time = current_time
    ranges.append((phase_start, previous_time + sample_step))
    previous = set()
    for start, end in ranges:
        group = blind_samples[(blind_samples["time_s"] >= start) & (blind_samples["time_s"] < end)]
        if group.empty:
            continue
        # Stratify by sortie and flight phase so a two- or three-sample ridge shadow is
        # never discarded by a global stride across a large time window.
        target_per_phase = 80 if bucket_s > 600.0 else 40
        pieces = []
        for _, phase_group in group.groupby(["trip_id", "phase"], sort=False):
            phase_group = phase_group.sort_values("time_s")
            run_id = (phase_group["time_s"].astype(float).diff().fillna(0.0)
                      > max(1.5 * sample_step, sample_step + 0.25)).cumsum()
            for _, run_group in phase_group.groupby(run_id, sort=False):
                stride = max(1, int(math.ceil(len(run_group) / float(target_per_phase))))
                selected = run_group.iloc[::stride]
                if selected.index[-1] != run_group.index[-1]:
                    selected = pd.concat([selected, run_group.iloc[[-1]]])
                pieces.append(selected)
        sample = pd.concat(pieces, ignore_index=True)
        universe = frozenset(range(len(sample)))
        coverage = []
        for idx, candidate in enumerate(candidates):
            covered = []
            for row_idx, row in sample.iterrows():
                cache_key = (idx, str(row["trip_id"]), str(row["phase"]),
                             round(float(row["lon"]), 7), round(float(row["lat"]), 7), round(float(row["alt_m"]), 2))
                if cache_key not in coverage_cache:
                    transport = {"lon": float(row["lon"]), "lat": float(row["lat"]), "alt_m": float(row["alt_m"])}
                    coverage_cache[cache_key] = link_available(
                        dem, transport, candidate, params["access_max_loss_db"], params)[0]
                if coverage_cache[cache_key]:
                    covered.append(row_idx)
            if covered:
                coverage.append((idx, frozenset(covered)))
        best = None
        for count in (1, 2):
            for combo in itertools.combinations(coverage, count):
                covered = frozenset().union(*(x[1] for x in combo))
                if covered != universe:
                    continue
                ids = set(x[0] for x in combo)
                key = (len(ids - previous), len(previous - ids), count,
                       sum(candidates[x]["agl_m"] for x in ids))
                if best is None or key < best[0]:
                    best = (key, ids)
        if best is None:
            raise RelayCoverageConflict(start, end,
                                        sorted(set(group["trip_id"].astype(str))))
        previous = set(best[1])
        group_times = np.sort(group["time_s"].astype(float).unique())
        group_steps = np.diff(group_times)
        group_steps = group_steps[group_steps > 1e-6]
        padding = float(np.median(group_steps)) if len(group_steps) else min(5.0, bucket_s)
        buckets.append({"start": float(group["time_s"].min()), "end": float(group["time_s"].max()) + padding,
                        "candidate_ids": tuple(sorted(best[1])),
                        "trip_ids": tuple(sorted(set(group["trip_id"].astype(str))))})
    intervals = []
    for candidate_id in sorted(set(x for bucket in buckets for x in bucket["candidate_ids"])):
        windows = [(b["start"], b["end"], set(b["trip_ids"])) for b in buckets if candidate_id in b["candidate_ids"]]
        merged = []
        for start, end, trip_ids in windows:
            if merged and start <= merged[-1][1] + bucket_s * 0.25:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end), merged[-1][2] | trip_ids)
            else:
                merged.append((start, end, set(trip_ids)))
        for start, end, trip_ids in merged:
            intervals.append({"site": candidates[candidate_id], "first_need": start, "last_need": end,
                              "trip_ids": tuple(sorted(trip_ids))})
    selected_sites = []
    for interval in intervals:
        site = interval["site"]
        matching = [x for x in selected_sites if abs(x["lon"] - site["lon"]) < 1e-12 and abs(x["lat"] - site["lat"]) < 1e-12 and x["agl_m"] == site["agl_m"]]
        if not matching:
            copied = dict(site)
            copied["site_id"] = "SITE-%02d" % (len(selected_sites) + 1)
            selected_sites.append(copied)
            matching = [copied]
        interval["site"] = matching[0]
    return selected_sites, intervals
