from __future__ import division

import pandas as pd

from .flight_physics import segment_energy_kwh


def direct_energy(model, service_id, payload_kg, segments):
    return (segment_energy_kwh(model, segments[("O01", service_id)], payload_kg) +
            segment_energy_kwh(model, segments[(service_id, "O01")], 0.0))


def maximum_safe_payload(model, service_id, segments, reserve_fraction=None, tolerance_kg=1e-4):
    reserve = float(model["reserve_fraction"] if reserve_fraction is None else reserve_fraction)
    limit = (1.0 - reserve) * float(model["usable_energy_kwh"])
    low, high = 0.0, float(model["max_payload_kg"])
    if direct_energy(model, service_id, high, segments) <= limit:
        best = high
    elif direct_energy(model, service_id, 0.0, segments) > limit:
        best = 0.0
    else:
        while high - low > tolerance_kg:
            mid = (low + high) / 2.0
            if direct_energy(model, service_id, mid, segments) <= limit:
                low = mid
            else:
                high = mid
        best = low
    energy = direct_energy(model, service_id, best, segments)
    return best, energy, 1.0 - energy / float(model["usable_energy_kwh"])


def solve_safe_payloads(models, service_ids, segments, reserve_fraction=None):
    records = []
    for _, model in models.iterrows():
        for service_id in service_ids:
            payload, energy, soc = maximum_safe_payload(
                model, service_id, segments, reserve_fraction=reserve_fraction)
            records.append({
                "服务区编号": service_id,
                "机型编号": model["model"],
                "返航安全余量": float(model["reserve_fraction"] if reserve_fraction is None else reserve_fraction),
                "最大安全载荷（kg）": payload,
                "法定载重上限（kg）": float(model["max_payload_kg"]),
                "边界能耗（kWh）": energy,
                "边界返航SOC": soc,
                "主要限制": "额定载荷" if abs(payload - float(model["max_payload_kg"])) < 1e-3 else "地形与电量",
            })
    return pd.DataFrame(records)

