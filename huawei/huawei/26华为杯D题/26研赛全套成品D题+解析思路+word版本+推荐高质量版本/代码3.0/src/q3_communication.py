from __future__ import division

import math

import numpy as np
import pandas as pd

from .dem import horizontal_distance_m


def link_thresholds(link_parameters):
    def value(category, name):
        row = link_parameters[(link_parameters["category"] == category) &
                              (link_parameters["name"] == name)]
        return float(row.iloc[0]["value"])
    psens = value("接收参数", "接收灵敏度（dBm）")
    margin = value("接收参数", "衰落裕量（dB）")
    pth = psens + margin
    lsys = value("传播参数", "系统损耗（dB）")
    endpoints = {
        "transport": (value("运输无人机", "发射功率（dBm）"), value("运输无人机", "天线增益（dBi）")),
        "relay_access": (value("中继接入端", "发射功率（dBm）"), value("中继接入端", "天线增益（dBi）")),
        "relay_backhaul": (value("中继回传端", "发射功率（dBm）"), value("中继回传端", "天线增益（dBi）")),
        "gateway": (value("固定网关 G01", "发射功率（dBm）"), value("固定网关 G01", "天线增益（dBi）")),
    }

    def bidirectional(a, b):
        a_to_b = endpoints[a][0] + endpoints[a][1] + endpoints[b][1] - lsys - pth
        b_to_a = endpoints[b][0] + endpoints[b][1] + endpoints[a][1] - lsys - pth
        return min(a_to_b, b_to_a)
    return {
        "frequency_mhz": value("传播参数", "载波频率（MHz）"),
        "obstacle_loss_db": value("传播参数", "地形遮挡附加损耗（dB）"),
        "gateway_height_m": value("固定网关 G01", "天线离地高度（m）"),
        "direct_max_loss_db": bidirectional("transport", "gateway"),
        "access_max_loss_db": bidirectional("transport", "relay_access"),
        "backhaul_max_loss_db": bidirectional("relay_backhaul", "gateway"),
    }


def terrain_obstructed(dem, a, b):
    horizontal = horizontal_distance_m(a["lon"], a["lat"], b["lon"], b["lat"])
    if horizontal < 1.0:
        return False
    spacing = 30.0
    count = max(3, int(math.ceil(horizontal / spacing)) + 1)
    f = np.linspace(0.0, 1.0, count)[1:-1]
    lons = a["lon"] + (b["lon"] - a["lon"]) * f
    lats = a["lat"] + (b["lat"] - a["lat"]) * f
    terrain = dem.elevations(lons, lats)
    line_alt = a["alt_m"] + (b["alt_m"] - a["alt_m"]) * f
    return bool(np.any(np.isfinite(terrain) & (terrain >= line_alt - 0.5)))


def link_available(dem, a, b, max_loss_db, params):
    horizontal = horizontal_distance_m(a["lon"], a["lat"], b["lon"], b["lat"])
    vertical = float(b["alt_m"]) - float(a["alt_m"])
    distance_km = max(math.hypot(horizontal, vertical) / 1000.0, 1e-6)
    fspl = 32.45 + 20.0 * math.log10(params["frequency_mhz"]) + 20.0 * math.log10(distance_km)
    obstructed = terrain_obstructed(dem, a, b)
    path_loss = fspl + (params["obstacle_loss_db"] if obstructed else 0.0)
    return path_loss <= max_loss_db + 1e-9, path_loss, obstructed, distance_km


def _append_phase(samples, trip_id, phase, t0, duration, p0, p1, step_s):
    duration = max(float(duration), 0.0)
    count = max(1, int(math.ceil(duration / step_s)))
    for k in range(count + 1):
        f = min(1.0, k / float(count))
        samples.append({
            "trip_id": trip_id, "phase": phase, "time_s": t0 + duration * f,
            "lon": p0["lon"] + (p1["lon"] - p0["lon"]) * f,
            "lat": p0["lat"] + (p1["lat"] - p0["lat"]) * f,
            "alt_m": p0["alt_m"] + (p1["alt_m"] - p0["alt_m"]) * f,
        })
    return t0 + duration


def build_transport_trajectory(schedule, nodes, models, segments, step_s=10.0):
    node_lookup = {row["node_id"]: row for _, row in nodes.iterrows()}
    model_lookup = {row["model"]: row for _, row in models.iterrows()}
    all_samples = []
    for item in schedule:
        model = model_lookup[item["model"]]
        t = item["start_s"] + float(model["prep_fixed_s"]) + float(model["load_per_box_s"]) * len(item["route"]["boxes"])
        prev = "O01"
        for leg in item["metrics"]["legs"]:
            destination = leg["destination"]
            seg = segments[(prev, destination)]
            origin = node_lookup[prev]
            dest = node_lookup[destination]
            cruise = float(seg["cruise_alt_m"])
            p_start = {"lon": float(origin["lon"]), "lat": float(origin["lat"]), "alt_m": float(origin["work_alt_m"])}
            p_cruise_origin = {"lon": p_start["lon"], "lat": p_start["lat"], "alt_m": cruise}
            t = _append_phase(all_samples, item["trip_id"], "爬升:%s" % prev, t,
                              float(seg["climb_m"]) / float(model["climb_speed_mps"]),
                              p_start, p_cruise_origin, step_s)
            p_cruise_dest = {"lon": float(dest["lon"]), "lat": float(dest["lat"]), "alt_m": cruise}
            t = _append_phase(all_samples, item["trip_id"], "巡航:%s-%s" % (prev, destination), t,
                              float(seg["distance_m"]) / float(model["cruise_speed_mps"]),
                              p_cruise_origin, p_cruise_dest, step_s)
            p_end = {"lon": float(dest["lon"]), "lat": float(dest["lat"]), "alt_m": float(dest["work_alt_m"])}
            t = _append_phase(all_samples, item["trip_id"], "下降:%s" % destination, t,
                              float(seg["descent_m"]) / float(model["descent_speed_mps"]),
                              p_cruise_dest, p_end, step_s)
            if destination != "O01":
                count_boxes = sum(1 for b in item["route"]["boxes"] if b["service_id"] == destination)
                handoff = float(model["handoff_base_s"]) + float(model["handoff_per_box_s"]) * count_boxes
                t = _append_phase(all_samples, item["trip_id"], "交接:%s" % destination, t, handoff,
                                  p_end, p_end, step_s)
            prev = destination
    frame = pd.DataFrame(all_samples)
    frame = frame.sort_values(["trip_id", "time_s", "phase"]).drop_duplicates(["trip_id", "time_s"], keep="last")
    return frame.reset_index(drop=True)


def evaluate_direct_links(trajectory, nodes, dem, params):
    gateway_row = nodes[nodes["node_id"] == "O01"].iloc[0]
    gateway = {"lon": float(gateway_row["lon"]), "lat": float(gateway_row["lat"]),
               "alt_m": float(gateway_row["ground_alt_m"]) + params["gateway_height_m"]}
    rows = []
    for _, sample in trajectory.iterrows():
        point = {"lon": float(sample["lon"]), "lat": float(sample["lat"]), "alt_m": float(sample["alt_m"])}
        available, loss, obstructed, distance = link_available(
            dem, point, gateway, params["direct_max_loss_db"], params)
        row = dict(sample)
        row.update({"direct_available": bool(available), "path_loss_db": loss,
                    "obstructed": bool(obstructed), "distance_km": distance})
        rows.append(row)
    return pd.DataFrame(rows)


def compress_intervals(frame, state_column, relay_column=None):
    rows = []
    for trip_id, group in frame.sort_values(["trip_id", "time_s"]).groupby("trip_id", sort=True):
        current = None
        for _, row in group.iterrows():
            relay = row[relay_column] if relay_column else ""
            key = (row[state_column], relay, row.get("phase", ""))
            if current is None or key != current["key"]:
                if current is not None:
                    rows.append(current["row"])
                current = {"key": key, "row": {"运输架次编号": trip_id,
                                                   "通信阶段": row.get("phase", ""),
                                                   "开始时刻（s）": float(row["time_s"]),
                                                   "结束时刻（s）": float(row["time_s"]),
                                                   "保障方式": row[state_column],
                                                   "中继架次编号": relay if relay == relay else ""}}
            else:
                current["row"]["结束时刻（s）"] = float(row["time_s"])
        if current is not None:
            rows.append(current["row"])
    return pd.DataFrame(rows)
