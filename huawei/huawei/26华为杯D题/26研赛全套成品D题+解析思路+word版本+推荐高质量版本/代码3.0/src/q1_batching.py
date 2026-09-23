from __future__ import division

import math

import pandas as pd

from .flight_physics import segment_energy_kwh, segment_time_s


def _box_records(frame):
    return [dict(row) for _, row in frame.iterrows()]


def _candidate_batches(service_boxes, models, segments, reserve_fraction):
    boxes = _box_records(service_boxes)
    n = len(boxes)
    service_id = boxes[0]["service_id"]
    mass = [0.0] * (1 << n)
    volume = [0.0] * (1 << n)
    count = [0] * (1 << n)
    for mask in range(1, 1 << n):
        bit = mask & -mask
        idx = int(math.log(bit, 2))
        prev = mask ^ bit
        mass[mask] = mass[prev] + float(boxes[idx]["mass_kg"])
        volume[mask] = volume[prev] + float(boxes[idx]["volume_m3"])
        count[mask] = count[prev] + 1
    model_records = [dict(row) for _, row in models.iterrows()]
    outbound = segments[("O01", service_id)]
    inbound = segments[(service_id, "O01")]
    candidates = []
    for mask in range(1, 1 << n):
        best = None
        for original in model_records:
            model = dict(original)
            model["reserve_fraction"] = reserve_fraction
            if mass[mask] > float(model["max_payload_kg"]) + 1e-9:
                continue
            if volume[mask] > float(model["max_volume_m3"]) + 1e-12:
                continue
            energy = (segment_energy_kwh(model, outbound, mass[mask]) +
                      segment_energy_kwh(model, inbound, 0.0))
            if energy > (1.0 - reserve_fraction) * float(model["usable_energy_kwh"]) + 1e-9:
                continue
            duration = (float(model["prep_fixed_s"]) + float(model["load_per_box_s"]) * count[mask] +
                        segment_time_s(model, outbound) + segment_time_s(model, inbound) +
                        float(model["handoff_base_s"]) + float(model["handoff_per_box_s"]) * count[mask])
            metrics = {"total_mass_kg": mass[mask], "total_volume_m3": volume[mask],
                       "duration_s": duration, "energy_kwh": energy,
                       "return_soc": 1.0 - energy / float(model["usable_energy_kwh"])}
            key = (energy, duration, model["model"])
            if best is None or key < best[0]:
                best = (key, dict(model), metrics)
        if best is not None:
            candidates.append({"mask": mask, "count": count[mask], "model": best[1], "metrics": best[2]})
    return candidates


def _exact_cover(service_boxes, candidates):
    n = len(service_boxes)
    full = (1 << n) - 1
    by_mask = {candidate["mask"]: candidate for candidate in candidates}
    by_bit = [[] for _ in range(n)]
    for candidate in candidates:
        for bit in range(n):
            if candidate["mask"] & (1 << bit):
                by_bit[bit].append(candidate)
    for bit in range(n):
        by_bit[bit].sort(key=lambda c: (-c["count"], c["metrics"]["energy_kwh"], c["metrics"]["duration_s"]))
    memo = {}

    def solve_exact(remaining, trips_left):
        key_memo = (remaining, trips_left)
        if key_memo in memo:
            return memo[key_memo]
        if trips_left == 0:
            return ((0.0, 0.0), []) if remaining == 0 else None
        if remaining == 0:
            return None
        if trips_left == 1:
            candidate = by_mask.get(remaining)
            if candidate is None:
                return None
            result = ((candidate["metrics"]["energy_kwh"], candidate["metrics"]["duration_s"]), [candidate])
            memo[key_memo] = result
            return result
        first_bit = int(math.log(remaining & -remaining, 2))
        best = None
        for candidate in by_bit[first_bit]:
            mask = candidate["mask"]
            if mask & remaining != mask:
                continue
            next_remaining = remaining ^ mask
            if bin(next_remaining).count("1") < trips_left - 1:
                continue
            sub = solve_exact(next_remaining, trips_left - 1)
            if sub is None:
                continue
            key = (sub[0][0] + candidate["metrics"]["energy_kwh"],
                   sub[0][1] + candidate["metrics"]["duration_s"])
            if best is None or key < best[0]:
                best = (key, sub[1] + [candidate])
        memo[key_memo] = best
        return best
    max_boxes = max(c["count"] for c in candidates)
    lower = int(math.ceil(n / float(max_boxes)))
    for trip_count in range(lower, n + 1):
        result = solve_exact(full, trip_count)
        if result is not None:
            return ((trip_count, result[0][0], result[0][1]), result[1])
    return None


def solve_q1_batching(boxes, models, segments, reserve_fraction=0.20):
    rows = []
    selected_internal = []
    counter = 1
    for service_id, frame in boxes.groupby("service_id", sort=True):
        frame = frame.reset_index(drop=True)
        candidates = _candidate_batches(frame, models, segments, reserve_fraction)
        solution = _exact_cover(frame, candidates)
        if solution is None:
            raise ValueError("问题一无可行组批: " + str(service_id))
        chosen = solution[1]
        for candidate in chosen:
            candidate["boxes"] = [_box_records(frame)[i] for i in range(len(frame)) if candidate["mask"] & (1 << i)]
        chosen.sort(key=lambda c: (-len(c["boxes"]), c["metrics"]["energy_kwh"]))
        for candidate in chosen:
            metrics = candidate["metrics"]
            trip_id = "Q1-%03d" % counter
            counter += 1
            box_ids = [b["box_id"] for b in candidate["boxes"]]
            rows.append({
                "架次编号": trip_id,
                "服务区编号": service_id,
                "机型编号": candidate["model"]["model"],
                "货箱编号列表": ",".join(box_ids),
                "总质量（kg）": metrics["total_mass_kg"],
                "总体积（m³）": metrics["total_volume_m3"],
                "往返时间（s）": metrics["duration_s"],
                "架次能耗（kWh）": metrics["energy_kwh"],
                "返航SOC（%）": metrics["return_soc"] * 100.0,
            })
            selected_internal.append({"trip_id": trip_id, "service_id": service_id,
                                      "boxes": candidate["boxes"], "model": candidate["model"],
                                      "metrics": metrics})
    return pd.DataFrame(rows), selected_internal


def solve_margin_sensitivity(boxes, models, segments, margins):
    summary = []
    all_batches = {}
    for margin in margins:
        batch_table, batches = solve_q1_batching(boxes, models, segments, reserve_fraction=margin)
        all_batches[margin] = batches
        summary.append({
            "返航安全余量": margin,
            "最优架次数": len(batch_table),
            "总能耗（kWh）": float(batch_table["架次能耗（kWh）"].sum()),
            "累计作业时间（s）": float(batch_table["往返时间（s）"].sum()),
            "最低返航SOC（%）": float(batch_table["返航SOC（%）"].min()),
        })
    return pd.DataFrame(summary), all_batches
