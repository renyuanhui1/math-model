from __future__ import division

import itertools
import math

from .dem import horizontal_distance_m
from .flight_physics import route_metrics


def build_route(box_records, models, segments, max_stops=4):
    boxes_by_stop = {}
    for box in box_records:
        boxes_by_stop.setdefault(box["service_id"], []).append(dict(box))
    stops = sorted(boxes_by_stop)
    if not stops or len(stops) > int(max_stops):
        return None
    options = {}
    for _, model in models.iterrows():
        best = None
        for order in itertools.permutations(stops):
            metrics = route_metrics(model, order, boxes_by_stop, segments)
            if metrics is None:
                continue
            weighted_offset = 0.0
            hard_risk = 0.0
            for stop_boxes in boxes_by_stop.values():
                for box in stop_boxes:
                    offset = metrics["delivery_offsets"][box["box_id"]]
                    weighted_offset += float(box["priority"]) * max(0.0, offset - float(box["expected_s"]))
                    if box.get("hard_deadline_s") == box.get("hard_deadline_s"):
                        hard_risk += max(0.0, offset - float(box["hard_deadline_s"]))
            key = (hard_risk, weighted_offset, metrics["duration_s"], metrics["energy_kwh"], order)
            if best is None or key < best[0]:
                best = (key, metrics)
        if best is not None:
            options[model["model"]] = best[1]
    if not options:
        return None
    hard_values = [float(b["hard_deadline_s"]) for b in box_records
                   if b.get("hard_deadline_s") == b.get("hard_deadline_s")]
    expected = [float(b["expected_s"]) for b in box_records]
    return {
        "boxes": [dict(b) for b in box_records],
        "box_ids": tuple(sorted(b["box_id"] for b in box_records)),
        "stops": tuple(stops),
        "options": options,
        "hard_deadline": min(hard_values) if hard_values else float("inf"),
        "expected": min(expected) if expected else float("inf"),
        "priority_sum": sum(float(b["priority"]) for b in box_records),
    }


def route_centroid(route, node_lookup):
    lons = [float(node_lookup[s]["lon"]) for s in route["stops"]]
    lats = [float(node_lookup[s]["lat"]) for s in route["stops"]]
    return sum(lons) / len(lons), sum(lats) / len(lats)


def can_consider_merge(route_a, route_b, node_lookup, max_centroid_distance_m=7000.0,
                       max_stops=4):
    stops = set(route_a["stops"]) | set(route_b["stops"])
    if len(stops) > int(max_stops):
        return False
    lon1, lat1 = route_centroid(route_a, node_lookup)
    lon2, lat2 = route_centroid(route_b, node_lookup)
    return horizontal_distance_m(lon1, lat1, lon2, lat2) <= max_centroid_distance_m


def generate_candidate_manifest(routes):
    records = []
    for idx, route in enumerate(routes, 1):
        for model, metrics in sorted(route["options"].items()):
            records.append({
                "candidate_id": "CAND-%04d-%s" % (idx, model),
                "box_count": len(route["boxes"]),
                "box_ids": ",".join(route["box_ids"]),
                "stops": "->".join(metrics["stop_order"]),
                "model": model,
                "mass_kg": metrics["total_mass_kg"],
                "volume_m3": metrics["total_volume_m3"],
                "duration_s": metrics["duration_s"],
                "energy_kwh": metrics["energy_kwh"],
            })
    return records
