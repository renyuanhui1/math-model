from __future__ import division

import copy
import itertools
import math

import numpy as np
import pandas as pd

from .flight_physics import charge_time_s, relay_flight_metrics
from .q3_communication import (build_transport_trajectory, compress_intervals,
                               evaluate_direct_links, link_available, link_thresholds)
from .q3_relay_candidates import (RelayCoverageConflict, choose_relay_windows,
                                  generate_relay_candidates)
from .q2_candidate_routes import build_route
from .q2_resource_schedule import schedule_routes, schedule_score


class RelayResourceConflict(Exception):
    def __init__(self, task_start, earliest_relay, trip_ids, task_trip_ids=()):
        Exception.__init__(self, "relay resource conflict")
        self.task_start = float(task_start)
        self.earliest_relay = float(earliest_relay)
        self.trip_ids = tuple(trip_ids)
        self.task_trip_ids = tuple(task_trip_ids)


def _shift_transport_schedule(schedule, delta):
    shifted = copy.deepcopy(schedule)
    for item in shifted:
        item["start_s"] += delta
        item["return_s"] += delta
        item["charge_end_s"] += delta
        item["deliveries"] = {box_id: t + delta for box_id, t in item["deliveries"].items()}
    return shifted


def _shift_item(item, delta):
    item["start_s"] += delta
    item["return_s"] += delta
    item["charge_end_s"] += delta
    item["deliveries"] = {box_id: t + delta for box_id, t in item["deliveries"].items()}


def _delay_trip_and_propagate(schedule, trip_id, delta):
    shifted = copy.deepcopy(schedule)
    original = {item["trip_id"]: item["start_s"] for item in shifted}
    target = [item for item in shifted if item["trip_id"] == trip_id]
    if not target:
        raise ValueError("无法定位需要延后的运输架次: " + str(trip_id))
    _shift_item(target[0], delta)
    for _ in range(len(shifted) * 3):
        changed = False
        for resource_key, release_key in (("drone_id", "return_s"), ("battery_id", "charge_end_s")):
            values = sorted(set(item[resource_key] for item in shifted))
            for value in values:
                chain = sorted([item for item in shifted if item[resource_key] == value],
                               key=lambda item: original[item["trip_id"]])
                available = 0.0
                for item in chain:
                    if item["start_s"] < available - 1e-6:
                        _shift_item(item, available - item["start_s"])
                        changed = True
                    available = item[release_key]
        if not changed:
            break
    shifted.sort(key=lambda x: (x["start_s"], x["return_s"], x["trip_id"]))
    return shifted


def _route_key(route):
    return tuple(sorted(str(box_id) for box_id in route["box_ids"]))


def _reschedule_routes(schedule, release_times, drones, battery_inventory):
    """Reassign UAVs and batteries after a Q3 departure-time perturbation."""
    routes = [copy.deepcopy(item["route"]) for item in schedule]
    trip_ids = {_route_key(item["route"]): item["trip_id"] for item in schedule}
    drone_state = {row["drone_id"]: {"model": row["model"], "available": 0.0}
                   for _, row in drones.iterrows()}
    battery_state = {}
    for _, row in battery_inventory.iterrows():
        for idx in range(1, int(row["count"]) + 1):
            battery_state["%s-BAT-%02d" % (row["model"], idx)] = {
                "model": row["model"], "available": 0.0,
                "full_charge_s": float(row["full_charge_s"]),
            }
    pending = sorted(routes, key=lambda route: (
        route["hard_deadline"], route["expected"], -route["priority_sum"], -len(route["boxes"])))
    result = []
    for route in pending:
        stable_id = trip_ids[_route_key(route)]
        release = float(release_times.get(stable_id, 0.0))
        best = None
        for model, metrics in route["options"].items():
            drone_choices = [(state["available"], drone_id) for drone_id, state in drone_state.items()
                             if state["model"] == model]
            battery_choices = [(state["available"], battery_id) for battery_id, state in battery_state.items()
                               if state["model"] == model]
            if not drone_choices or not battery_choices:
                continue
            drone_available, drone_id = min(drone_choices)
            battery_available, battery_id = min(battery_choices)
            start = max(release, drone_available, battery_available)
            deliveries = {box_id: start + offset for box_id, offset in metrics["delivery_offsets"].items()}
            hard_late = []
            weighted_late = 0.0
            for box in route["boxes"]:
                delivery = deliveries[box["box_id"]]
                deadline = float(box["hard_deadline_s"])
                if math.isfinite(deadline):
                    hard_late.append(max(0.0, delivery - deadline))
                weighted_late += float(box["priority"]) * max(0.0, delivery - float(box["expected_s"]))
            key = (sum(x > 1e-6 for x in hard_late), sum(hard_late), weighted_late,
                   start + metrics["duration_s"], metrics["energy_kwh"], model)
            if best is None or key < best[0]:
                best = (key, model, metrics, drone_id, battery_id, start, deliveries)
        if best is None:
            raise ValueError("Q3运输路线无法重新分配无人机或电池")
        _, model, metrics, drone_id, battery_id, start, deliveries = best
        end = start + metrics["duration_s"]
        recharge = charge_time_s(metrics["return_soc"], battery_state[battery_id]["full_charge_s"])
        drone_state[drone_id]["available"] = end
        battery_state[battery_id]["available"] = end + recharge
        result.append({
            "route": route, "model": model, "metrics": metrics,
            "drone_id": drone_id, "battery_id": battery_id,
            "start_s": start, "return_s": end, "deliveries": deliveries,
            "charge_end_s": end + recharge, "charge_time_s": recharge,
            "trip_id": stable_id,
        })
    result.sort(key=lambda item: (item["start_s"], item["return_s"], item["trip_id"]))
    return result


def _communication_aware_merge(q2_schedule, models, segments, transport_drones, transport_batteries):
    """Rebuild Q3 routes by feasibility-driven merging, without service-name rules."""
    all_boxes = [copy.deepcopy(box) for item in q2_schedule for box in item["route"]["boxes"]]
    urgent_boxes = [box for box in all_boxes
                    if math.isfinite(float(box["hard_deadline_s"]))
                    and float(box["hard_deadline_s"]) <= 3600.0 + 1e-6]
    all_hard_boxes = [box for box in all_boxes if math.isfinite(float(box["hard_deadline_s"]))]
    urgent_services = sorted(set(box["service_id"] for box in urgent_boxes))
    candidate_routes = {}
    for count in range(1, min(4, len(urgent_services)) + 1):
        for subset in itertools.combinations(urgent_services, count):
            route = build_route([box for box in all_hard_boxes if box["service_id"] in subset],
                                models, segments, max_stops=4)
            if route is not None:
                candidate_routes[frozenset(subset)] = route

    partitions = []
    def enumerate_partitions(remaining, chosen):
        if not remaining:
            partitions.append(list(chosen))
            return
        first = min(remaining)
        for subset, route in candidate_routes.items():
            if first in subset and subset.issubset(remaining):
                enumerate_partitions(remaining - subset, chosen + [route])
    enumerate_partitions(frozenset(urgent_services), [])
    best_partition = None
    for proposal in partitions:
        trial, _, _ = schedule_routes(proposal, transport_drones, transport_batteries)
        score = schedule_score(trial)
        if score[0] > 0:
            continue
        min_slack = min(float(box["hard_deadline_s"]) - item["deliveries"][box["box_id"]]
                        for item in trial for box in item["route"]["boxes"])
        # A 1200 s lead-time reserve lets relays leave O01 and establish their links before
        # the first blind segment.  Among qualifying partitions, use the fewest sorties.
        key = (min_slack < 1200.0, len(proposal), -min_slack, score[3], score[4])
        if best_partition is None or key < best_partition[0]:
            best_partition = (key, proposal)
    if best_partition is None:
        raise ValueError("Q3紧急货箱集合划分不存在满足硬时限的路线组合")
    routes = list(best_partition[1])
    urgent_ids = set(box["box_id"] for route in routes for box in route["boxes"])
    urgent_route_count = len(routes)

    # Preserve Q2's associations for all remaining boxes.  Only the first-hour subset is
    # repartitioned exhaustively because it determines whether relays can be pre-positioned.
    for item in q2_schedule:
        selected_ids = set(item["route"]["box_ids"])
        selected = [box for box in all_boxes
                    if box["box_id"] not in urgent_ids and box["box_id"] in selected_ids]
        if not selected:
            continue
        route = build_route(selected, models, segments, max_stops=4)
        if route is not None:
            routes.append(route)
            continue
        for service_id in sorted(set(box["service_id"] for box in selected)):
            single = build_route([box for box in selected if box["service_id"] == service_id],
                                 models, segments, max_stops=4)
            if single is None:
                raise ValueError("Q3非硬时限货箱路线重建失败: " + service_id)
            routes.append(single)

    # Merge compatible later-deadline routes by exhaustive pair evaluation.  The first-hour
    # partition stays fixed; all later combinations compete on deadline slack and timeliness.
    while True:
        best_merge = None
        for i in range(urgent_route_count, len(routes)):
            for j in range(i + 1, len(routes)):
                if not (math.isfinite(float(routes[i]["hard_deadline"])) and
                        math.isfinite(float(routes[j]["hard_deadline"]))):
                    continue
                if len(set(routes[i]["stops"]) | set(routes[j]["stops"])) > 4:
                    continue
                merged = build_route(routes[i]["boxes"] + routes[j]["boxes"],
                                     models, segments, max_stops=4)
                if merged is None:
                    continue
                proposal = [route for idx, route in enumerate(routes) if idx not in (i, j)] + [merged]
                trial, _, _ = schedule_routes(proposal, transport_drones, transport_batteries)
                score = schedule_score(trial)
                if score[0] > 0:
                    continue
                min_slack = min(float(box["hard_deadline_s"]) - item["deliveries"][box["box_id"]]
                                for item in trial for box in item["route"]["boxes"]
                                if math.isfinite(float(box["hard_deadline_s"])))
                key = (-min_slack, score[2], score[3], score[4], tuple(merged["stops"]))
                if best_merge is None or key < best_merge[0]:
                    best_merge = (key, proposal)
        if best_merge is None:
            break
        routes = best_merge[1]

    schedule, _, _ = schedule_routes(routes, transport_drones, transport_batteries)
    release_times = {}
    for item in schedule:
        desired_starts = []
        latest_starts = []
        for box in item["route"]["boxes"]:
            offset = float(item["deliveries"][box["box_id"]]) - float(item["start_s"])
            desired_starts.append(float(box["expected_s"]) - offset)
            deadline = float(box["hard_deadline_s"])
            if math.isfinite(deadline):
                latest_starts.append(deadline - offset)
        desired = max(0.0, min(desired_starts) if desired_starts else 0.0)
        latest = min(latest_starts) if latest_starts else float("inf")
        # Place each hard route from its own latest-feasible start, while retaining a
        # 2400 s relay/conflict buffer.  This creates data-derived waves without deadline bands.
        release_target = max(0.0, latest - 2400.0) if math.isfinite(latest) else desired
        release = math.floor(release_target / 300.0) * 300.0
        release_times[item["trip_id"]] = release
    proposal = _reschedule_routes(schedule, release_times, transport_drones, transport_batteries)
    return proposal if schedule_score(proposal)[0] == 0 else schedule


def _assign_blind_to_sites(blind, sites, dem, params):
    assigned = []
    for _, row in blind.iterrows():
        transport = {"lon": float(row["lon"]), "lat": float(row["lat"]), "alt_m": float(row["alt_m"])}
        choices = []
        for site in sites:
            ok, loss, _, _ = link_available(dem, transport, site, params["access_max_loss_db"], params)
            if ok:
                choices.append((loss, site["site_id"]))
        if not choices:
            raise ValueError("存在未被候选中继点覆盖的直连盲区")
        assigned.append(min(choices)[1])
    out = blind.copy()
    out["site_id"] = assigned
    return out


def _build_relay_sorties(intervals, origin, relay_model, relay_drones, components, dem):
    prelim = []
    for interval in intervals:
        site = interval["site"]
        flight = relay_flight_metrics(relay_model, origin, site, dem)
        active_lag = float(relay_model["prep_fixed_s"]) + flight["out_time_s"] + float(relay_model["link_time_s"])
        prelim.append({"site": site, "flight": flight, "first_need": float(interval["first_need"]),
                       "last_need": float(interval["last_need"]), "active_lag": active_lag,
                       "trip_ids": interval.get("trip_ids", ())})
    shift = max([max(0.0, p["active_lag"] - p["first_need"]) for p in prelim] or [0.0])
    tasks = []
    full_charge = float(components.iloc[0]["full_charge_s"])
    for p in prelim:
        first_need = p["first_need"] + shift
        last_need = p["last_need"] + shift
        limit = (1.0 - float(relay_model["reserve_fraction"])) * float(relay_model["usable_energy_kwh"])
        hover_power = float(relay_model["hover_power_kw"]) + float(relay_model["comm_power_kw"])
        max_service = (limit - p["flight"]["flight_energy_kwh"]) * 3600.0 / hover_power
        if max_service <= 30.0:
            raise ValueError("中继点往返飞行已耗尽安全能量")
        cursor = first_need
        while cursor < last_need - 1e-6:
            service_end = min(last_need, cursor + max_service - 10.0)
            start = max(0.0, cursor - p["active_lag"])
            active = start + p["active_lag"]
            service_duration = max(0.0, service_end - active)
            energy = p["flight"]["flight_energy_kwh"] + hover_power * service_duration / 3600.0
            return_time = service_end + p["flight"]["back_time_s"]
            soc = 1.0 - energy / float(relay_model["usable_energy_kwh"])
            charge_end = return_time + charge_time_s(soc, full_charge)
            tasks.append({"start_s": start, "site": p["site"], "active_s": active,
                          "service_end_s": service_end, "return_s": return_time,
                          "energy_kwh": energy, "return_soc": soc, "charge_end_s": charge_end,
                          "flight": p["flight"], "trip_ids": p["trip_ids"]})
            if service_end >= last_need - 1e-6:
                break
            cursor = service_end - 10.0
    relay_available = {row["relay_id"]: 0.0 for _, row in relay_drones.iterrows()}
    relay_current_trips = {row["relay_id"]: tuple() for _, row in relay_drones.iterrows()}
    component_available = {"R-EC-%02d" % i: 0.0 for i in range(1, int(components.iloc[0]["count"]) + 1)}
    sorties = []
    for idx, task in enumerate(sorted(tasks, key=lambda x: (x["start_s"], x["return_s"])), 1):
        relay_choices = [relay_id for relay_id, available in relay_available.items() if available <= task["start_s"] + 1e-6]
        component_choices = [component_id for component_id, available in component_available.items() if available <= task["start_s"] + 1e-6]
        if not relay_choices or not component_choices:
            if not relay_choices:
                blockers = set(task.get("trip_ids", ()))
                for relay_id, available in relay_available.items():
                    if available > task["start_s"] + 1e-6:
                        blockers.update(relay_current_trips.get(relay_id, ()))
                raise RelayResourceConflict(task["start_s"], min(relay_available.values()),
                                            sorted(blockers), task.get("trip_ids", ()))
            raise ValueError("六组中继能源组件在所需起飞时刻均未充满")
        relay_id = sorted(relay_choices, key=lambda x: relay_available[x])[0]
        component_id = sorted(component_choices, key=lambda x: component_available[x])[0]
        relay_available[relay_id] = task["return_s"] + float(relay_model["turnaround_s"])
        relay_current_trips[relay_id] = tuple(task.get("trip_ids", ()))
        component_available[component_id] = task["charge_end_s"]
        task.update({"relay_trip_id": "Q3-R-%02d" % idx, "relay_id": relay_id,
                     "component_id": component_id})
        sorties.append(task)
    return sorties, shift


def solve_q3(q2_schedule, nodes, models, segments, dem, link_parameters,
             relay_model_frame, relay_drones, relay_components,
             transport_drones=None, transport_batteries=None):
    params = link_thresholds(link_parameters)
    if transport_drones is not None and transport_batteries is not None:
        q2_schedule = _communication_aware_merge(q2_schedule, models, segments,
                                                 transport_drones, transport_batteries)
    working_schedule = copy.deepcopy(q2_schedule)
    release_times = {item["trip_id"]: float(item["start_s"]) for item in working_schedule}
    relay_sorties = None
    sites = []
    shift = 0.0
    cached_candidates = None
    coverage_cache = {}
    conflict_log = []

    def route_slack(item):
        values = [float(box["hard_deadline_s"]) - item["deliveries"][box["box_id"]]
                  for box in item["route"]["boxes"] if box["hard_deadline_s"] == box["hard_deadline_s"]]
        return min(values) if values else float("inf")

    def delay_one(trip_ids, requested_delay):
        nonlocal release_times
        candidates_to_delay = [item for item in working_schedule if item["trip_id"] in trip_ids]
        if not candidates_to_delay:
            raise ValueError("通信冲突无法映射到运输架次")
        candidates_to_delay.sort(key=route_slack, reverse=True)
        feasible = []
        for item in candidates_to_delay:
            if route_slack(item) < requested_delay + 1.0:
                continue
            proposed_releases = dict(release_times)
            proposed_releases[item["trip_id"]] = max(
                proposed_releases.get(item["trip_id"], 0.0), float(item["start_s"]) + requested_delay)
            proposal = _reschedule_routes(working_schedule, proposed_releases,
                                          transport_drones, transport_batteries)
            score = schedule_score(proposal)
            if score[0] == 0:
                feasible.append((score[2], score[3], score[4], item["trip_id"],
                                 proposed_releases, proposal))
        if not feasible:
            details = ",".join("%s:%.1f" % (item["trip_id"], route_slack(item))
                               for item in candidates_to_delay)
            raise ValueError("消除通信冲突需延后%.1f秒，超过相关硬时限余量[%s]" %
                             (requested_delay, details))
        selected = min(feasible, key=lambda row: row[:4])
        release_times = selected[-2]
        return selected[-1]

    def delay_group(trip_ids, requested_delay):
        nonlocal release_times
        selected = [item for item in working_schedule if item["trip_id"] in set(trip_ids)]
        if selected:
            proposed_releases = dict(release_times)
            for item in selected:
                proposed_releases[item["trip_id"]] = max(
                    proposed_releases.get(item["trip_id"], 0.0),
                    float(item["start_s"]) + requested_delay)
            proposal = _reschedule_routes(working_schedule, proposed_releases,
                                          transport_drones, transport_batteries)
            if schedule_score(proposal)[0] == 0:
                release_times = proposed_releases
                return proposal
        return delay_one(trip_ids, requested_delay)

    for attempt in range(80):
        # 规划与独立复核均使用1 s网格，选址时按架次、阶段和连续盲段分层抽样。
        trajectory = build_transport_trajectory(working_schedule, nodes, models, segments, step_s=1.0)
        direct = evaluate_direct_links(trajectory, nodes, dem, params)
        blind = direct[~direct["direct_available"]].copy()
        if blind.empty:
            relay_sorties = []
            break
        relay_model = relay_model_frame.iloc[0]
        if cached_candidates is None:
            candidates, sampled = generate_relay_candidates(blind, nodes, dem, params,
                                                            float(relay_model["max_hover_agl_m"]))
            cached_candidates = candidates
        else:
            candidates = cached_candidates
        try:
            sites, intervals = choose_relay_windows(candidates, blind, dem, params,
                                                    bucket_s=3000.0, coverage_cache=coverage_cache)
        except RelayCoverageConflict as conflict:
            conflict_log.append((attempt + 1, "coverage", 120.0, conflict.trip_ids))
            working_schedule = delay_one(conflict.trip_ids, 120.0)
            continue
        origin = nodes[nodes["node_id"] == "O01"].iloc[0]
        try:
            relay_sorties, shift = _build_relay_sorties(intervals, origin, relay_model, relay_drones,
                                                        relay_components, dem)
            break
        except RelayResourceConflict as conflict:
            delay = max(60.0, min(300.0, conflict.earliest_relay - conflict.task_start + 60.0))
            conflict_log.append((attempt + 1, "resource", delay, conflict.trip_ids))
            working_schedule = delay_group(conflict.task_trip_ids or conflict.trip_ids, delay)
    if relay_sorties is None:
        tail = "; ".join("%d:%s:%.1f:%s" % (idx, kind, delay, ",".join(ids))
                         for idx, kind, delay, ids in conflict_log[-8:])
        raise ValueError("通信感知时序调整达到迭代上限；最近冲突=" + tail)
    q2_schedule = working_schedule
    trajectory = build_transport_trajectory(q2_schedule, nodes, models, segments, step_s=1.0)
    direct = evaluate_direct_links(trajectory, nodes, dem, params)
    blind = direct[~direct["direct_available"]].copy()
    if blind.empty:
        sites = []
        relay_sorties = []
        shifted_schedule = q2_schedule
        shift = 0.0
    else:
        shifted_schedule = _shift_transport_schedule(q2_schedule, shift)
        trajectory = build_transport_trajectory(shifted_schedule, nodes, models, segments, step_s=1.0)
        direct = evaluate_direct_links(trajectory, nodes, dem, params)
    coverage_rows = []
    for _, row in direct.iterrows():
        out = dict(row)
        out["coverage_state"] = "DIRECT" if row["direct_available"] else "OUTAGE"
        out["relay_trip_id"] = ""
        if not row["direct_available"]:
            point = {"lon": float(row["lon"]), "lat": float(row["lat"]), "alt_m": float(row["alt_m"])}
            choices = []
            for sortie in relay_sorties:
                if sortie["active_s"] - 1e-6 <= float(row["time_s"]) <= sortie["service_end_s"] + 1e-6:
                    ok, loss, _, _ = link_available(dem, point, sortie["site"], params["access_max_loss_db"], params)
                    if ok:
                        choices.append((loss, sortie["relay_trip_id"]))
            if choices:
                out["coverage_state"] = "RELAY"
                out["relay_trip_id"] = min(choices)[1]
        coverage_rows.append(out)
    coverage = pd.DataFrame(coverage_rows)
    if (coverage["coverage_state"] == "OUTAGE").any():
        count = int((coverage["coverage_state"] == "OUTAGE").sum())
        examples = coverage.loc[coverage["coverage_state"] == "OUTAGE",
                                ["trip_id", "time_s", "phase"]].head(20).to_dict("records")
        raise ValueError("问题三仍有%d个采样时刻通信中断；示例=%s" % (count, examples))

    direct_intervals = compress_intervals(
        direct.assign(direct_state=np.where(direct["direct_available"], "DIRECT_AVAILABLE", "DIRECT_BLOCKED")),
        "direct_state")
    communication_intervals = compress_intervals(coverage, "coverage_state", "relay_trip_id")
    history_rows = [{
        "迭代": idx, "事件": kind, "时序调整（s）": delay,
        "涉及运输架次": ",".join(ids), "是否得到可行联合方案": False,
    } for idx, kind, delay, ids in conflict_log]
    history_rows.append({
        "迭代": len(conflict_log) + 1, "事件": "feasible", "时序调整（s）": 0.0,
        "涉及运输架次": "", "是否得到可行联合方案": True,
    })
    history = pd.DataFrame(history_rows)
    return (shifted_schedule, direct, direct_intervals, relay_sorties, coverage,
            communication_intervals, sites, shift, params, history)


def relay_sortie_table(relay_sorties):
    rows = []
    for item in relay_sorties:
        site = item["site"]
        rows.append({
            "中继架次编号": item["relay_trip_id"], "中继无人机编号": item["relay_id"],
            "能源组件编号": item["component_id"], "开始时刻（s）": item["start_s"],
            "悬停经度（°）": site["lon"], "悬停纬度（°）": site["lat"],
            "悬停海拔（m）": site["alt_m"], "建链完成时刻（s）": item["active_s"],
            "服务结束时刻（s）": item["service_end_s"], "返回O01时刻（s）": item["return_s"],
            "架次能耗（kWh）": item["energy_kwh"], "返航SOC（%）": item["return_soc"] * 100.0,
            "充电完成时刻（s）": item["charge_end_s"],
        })
    return pd.DataFrame(rows)


def validate_q3(schedule, relay_sorties, coverage, relay_model_frame):
    checks = []
    outage_count = int((coverage["coverage_state"] == "OUTAGE").sum())
    checks.append(("communication_outage_count", outage_count == 0, outage_count, 0))
    hard_slacks = [float(box["hard_deadline_s"]) - item["deliveries"][box["box_id"]]
                   for item in schedule for box in item["route"]["boxes"]
                   if math.isfinite(float(box["hard_deadline_s"]))]
    checks.append(("minimum_hard_deadline_slack_s", min(hard_slacks) >= -1e-6,
                   min(hard_slacks), 0.0))
    relay_model = relay_model_frame.iloc[0]
    reserve = float(relay_model["reserve_fraction"])
    for item in relay_sorties:
        checks.append(("relay_soc_" + item["relay_trip_id"], item["return_soc"] >= reserve - 1e-9,
                       item["return_soc"], reserve))
    turnaround = float(relay_model["turnaround_s"])
    for relay_id in sorted(set(item["relay_id"] for item in relay_sorties)):
        tasks = sorted([item for item in relay_sorties if item["relay_id"] == relay_id],
                       key=lambda item: item["start_s"])
        overlap = max([tasks[idx - 1]["return_s"] + turnaround - tasks[idx]["start_s"]
                       for idx in range(1, len(tasks))] or [0.0])
        checks.append(("relay_overlap_" + relay_id, overlap <= 1e-6, max(0.0, overlap), 0.0))
    for component_id in sorted(set(item["component_id"] for item in relay_sorties)):
        tasks = sorted([item for item in relay_sorties if item["component_id"] == component_id],
                       key=lambda item: item["start_s"])
        overlap = max([tasks[idx - 1]["charge_end_s"] - tasks[idx]["start_s"]
                       for idx in range(1, len(tasks))] or [0.0])
        checks.append(("relay_component_overlap_" + component_id,
                       overlap <= 1e-6, max(0.0, overlap), 0.0))
    max_step = 0.0
    for _, group in coverage.groupby(["trip_id", "phase"], sort=False):
        times = np.sort(group["time_s"].astype(float).unique())
        if len(times) > 1:
            max_step = max(max_step, float(np.diff(times).max()))
    checks.append(("trajectory_max_sampling_interval_s", max_step <= 1.01, max_step, 1.0))
    return pd.DataFrame([{
        "检查项": name, "是否通过": bool(ok), "实际值": actual, "允许上限或目标": limit,
    } for name, ok, actual, limit in checks])
