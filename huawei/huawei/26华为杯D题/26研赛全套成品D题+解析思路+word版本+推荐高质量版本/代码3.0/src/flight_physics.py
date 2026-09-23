from __future__ import division

import math


GRAVITY = 9.80665


def equivalent_range_m(model, payload_kg):
    qmax = float(model["max_payload_kg"])
    q = min(max(float(payload_kg), 0.0), qmax)
    return float(model["range_empty_m"]) - (
        float(model["range_empty_m"]) - float(model["range_full_m"])) * (q / qmax) ** 1.5


def segment_time_s(model, segment):
    return (float(segment["climb_m"]) / float(model["climb_speed_mps"]) +
            float(segment["distance_m"]) / float(model["cruise_speed_mps"]) +
            float(segment["descent_m"]) / float(model["descent_speed_mps"]))


def segment_energy_kwh(model, segment, payload_kg):
    horizontal = float(model["usable_energy_kwh"]) * float(segment["distance_m"]) / equivalent_range_m(model, payload_kg)
    climb = ((float(model["empty_mass_kg"]) + float(payload_kg)) * GRAVITY *
             float(segment["climb_m"]) /
             (3.6e6 * float(model["climb_efficiency"])))
    descent = 0.0
    if float(model.get("descent_efficiency", 0.0)) > 0:
        descent = ((float(model["empty_mass_kg"]) + float(payload_kg)) * GRAVITY *
                   float(segment["descent_m"]) * float(model["descent_efficiency"]) / 3.6e6)
    return horizontal + climb + descent


def charge_time_s(soc_fraction, full_charge_s):
    s = min(max(float(soc_fraction), 0.0), 1.0)
    full = float(full_charge_s)
    if s < 0.90:
        return full * (0.65 * (0.90 - s) / 0.90 + 0.35)
    return full * 0.35 * (1.0 - s) / 0.10


def route_metrics(model, stop_order, boxes_by_stop, segments, include_service=True):
    total_mass = sum(sum(float(b["mass_kg"]) for b in boxes_by_stop[s]) for s in stop_order)
    total_volume = sum(sum(float(b["volume_m3"]) for b in boxes_by_stop[s]) for s in stop_order)
    if total_mass > float(model["max_payload_kg"]) + 1e-9:
        return None
    if total_volume > float(model["max_volume_m3"]) + 1e-12:
        return None
    current_payload = total_mass
    energy = 0.0
    flight_time = 0.0
    elapsed = float(model["prep_fixed_s"]) + float(model["load_per_box_s"]) * sum(len(boxes_by_stop[s]) for s in stop_order)
    delivery_offsets = {}
    legs = []
    prev = "O01"
    for stop in list(stop_order) + ["O01"]:
        seg = segments[(prev, stop)]
        dt = segment_time_s(model, seg)
        de = segment_energy_kwh(model, seg, current_payload)
        elapsed += dt
        flight_time += dt
        energy += de
        legs.append({"origin": prev, "destination": stop, "payload_kg": current_payload,
                     "time_s": dt, "energy_kwh": de})
        if stop != "O01" and include_service:
            handoff = float(model["handoff_base_s"]) + float(model["handoff_per_box_s"]) * len(boxes_by_stop[stop])
            elapsed += handoff
            for box in boxes_by_stop[stop]:
                delivery_offsets[box["box_id"]] = elapsed
            current_payload -= sum(float(b["mass_kg"]) for b in boxes_by_stop[stop])
        prev = stop
    limit = (1.0 - float(model["reserve_fraction"])) * float(model["usable_energy_kwh"])
    if energy > limit + 1e-9:
        return None
    return {
        "model": model["model"],
        "stop_order": list(stop_order),
        "total_mass_kg": total_mass,
        "total_volume_m3": total_volume,
        "duration_s": elapsed,
        "flight_time_s": flight_time,
        "energy_kwh": energy,
        "return_soc": 1.0 - energy / float(model["usable_energy_kwh"]),
        "delivery_offsets": delivery_offsets,
        "legs": legs,
    }


def relay_flight_metrics(relay_model, origin, hover, dem):
    from .dem import horizontal_distance_m
    distance = horizontal_distance_m(origin["lon"], origin["lat"], hover["lon"], hover["lat"])
    max_dem = dem.max_elevation_on_line(origin["lon"], origin["lat"], hover["lon"], hover["lat"])
    cruise_alt = max(max_dem + 50.0, float(origin["work_alt_m"]), float(hover["alt_m"]))
    climb_out = max(0.0, cruise_alt - float(origin["work_alt_m"]))
    descent_out = max(0.0, cruise_alt - float(hover["alt_m"]))
    climb_back = max(0.0, cruise_alt - float(hover["alt_m"]))
    descent_back = max(0.0, cruise_alt - float(origin["work_alt_m"]))
    cruise_time = distance / float(relay_model["cruise_speed_mps"])
    out_time = climb_out / float(relay_model["climb_speed_mps"]) + cruise_time + descent_out / float(relay_model["descent_speed_mps"])
    back_time = climb_back / float(relay_model["climb_speed_mps"]) + cruise_time + descent_back / float(relay_model["descent_speed_mps"])
    horizontal_energy = 2.0 * float(relay_model["cruise_power_kw"]) * cruise_time / 3600.0
    climb_energy = (float(relay_model["takeoff_mass_kg"]) * GRAVITY * (climb_out + climb_back) /
                    (3.6e6 * float(relay_model["climb_efficiency"])))
    return {"distance_m": distance, "cruise_alt_m": cruise_alt, "out_time_s": out_time,
            "back_time_s": back_time, "flight_energy_kwh": horizontal_energy + climb_energy}

