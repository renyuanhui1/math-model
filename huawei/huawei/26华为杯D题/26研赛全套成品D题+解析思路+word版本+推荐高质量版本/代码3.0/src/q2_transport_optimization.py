from __future__ import division

import math
import random

import pandas as pd

from .dem import horizontal_distance_m
from .q1_batching import solve_q1_batching
from .q2_candidate_routes import build_route, can_consider_merge, route_centroid
from .q2_resource_schedule import schedule_routes, schedule_score


MAX_STOPS_PER_ROUTE = 4


def _records(frame):
    return [dict(row) for _, row in frame.iterrows()]


def initial_routes(boxes, models, segments):
    routes = []
    hard = boxes[boxes["hard_deadline_s"].notnull()]
    soft = boxes[boxes["hard_deadline_s"].isnull()]
    for _, frame in hard.groupby("service_id", sort=True):
        route = build_route(_records(frame), models, segments, max_stops=MAX_STOPS_PER_ROUTE)
        if route is None:
            raise ValueError("紧急货箱无法组成单服务区可行架次: " + str(frame.iloc[0]["service_id"]))
        routes.append(route)
    if len(soft):
        _, batches = solve_q1_batching(soft, models, segments, reserve_fraction=0.20)
        for batch in batches:
            route = build_route(batch["boxes"], models, segments, max_stops=MAX_STOPS_PER_ROUTE)
            if route is None:
                raise ValueError("软时限货箱初始架次不可行")
            routes.append(route)
    return routes


def _score_scalar(score):
    """Scalar surrogate used only by the simulated-annealing acceptance rule."""
    return (float(score[0]) * 1.0e12 + float(score[1]) * 1.0e7 +
            float(score[2]) + 0.10 * float(score[3]) +
            30.0 * float(score[4]) + 250.0 * float(score[5]))


def _route_local_value(route):
    best = None
    for metrics in route["options"].values():
        hard_late = 0.0
        weighted_late = 0.0
        for box in route["boxes"]:
            offset = float(metrics["delivery_offsets"][box["box_id"]])
            deadline = box.get("hard_deadline_s")
            if deadline == deadline:
                hard_late += max(0.0, offset - float(deadline))
            weighted_late += float(box["priority"]) * max(0.0, offset - float(box["expected_s"]))
        value = (hard_late * 1.0e8 + weighted_late +
                 0.05 * float(metrics["duration_s"]) +
                 30.0 * float(metrics["energy_kwh"]) + 250.0)
        if best is None or value < best:
            best = value
    return float(best)


def _evaluate(routes, drones, batteries):
    schedule, drone_state, battery_state = schedule_routes(routes, drones, batteries)
    score = schedule_score(schedule)
    return score, _score_scalar(score), schedule, drone_state, battery_state


def _greedy_merge_seed(routes, models, segments, node_lookup, drones, batteries):
    score, scalar, schedule, drone_state, battery_state = _evaluate(routes, drones, batteries)
    while True:
        best_move = None
        for i in range(len(routes)):
            for j in range(i + 1, len(routes)):
                if not can_consider_merge(routes[i], routes[j], node_lookup,
                                          max_stops=MAX_STOPS_PER_ROUTE):
                    continue
                merged = build_route(routes[i]["boxes"] + routes[j]["boxes"], models,
                                     segments, max_stops=MAX_STOPS_PER_ROUTE)
                if merged is None:
                    continue
                proposal = [route for k, route in enumerate(routes) if k not in (i, j)] + [merged]
                evaluated = _evaluate(proposal, drones, batteries)
                if evaluated[0] < score and (best_move is None or evaluated[0] < best_move[0]):
                    best_move = (evaluated[0], proposal) + evaluated[1:]
        if best_move is None:
            return routes, score, scalar, schedule, drone_state, battery_state
        score, routes, scalar, schedule, drone_state, battery_state = best_move


def _roulette(weights, rng):
    total = sum(max(0.01, value) for value in weights.values())
    target = rng.random() * total
    cumulative = 0.0
    for name in sorted(weights):
        cumulative += max(0.01, weights[name])
        if cumulative >= target:
            return name
    return sorted(weights)[-1]


def _destroy(routes, count, operator, node_lookup, rng):
    count = min(max(1, int(count)), max(1, len(routes) - 1))
    if operator == "random_routes":
        indices = set(rng.sample(range(len(routes)), count))
    elif operator == "worst_routes":
        ranked = sorted(range(len(routes)),
                        key=lambda i: _route_local_value(routes[i]) /
                        max(1, len(routes[i]["boxes"])), reverse=True)
        pool = ranked[:min(len(ranked), count + 2)]
        indices = set(rng.sample(pool, count))
    elif operator == "related_routes":
        seed = rng.randrange(len(routes))
        seed_lon, seed_lat = route_centroid(routes[seed], node_lookup)
        seed_deadline = float(routes[seed]["hard_deadline"])

        def relatedness(index):
            lon, lat = route_centroid(routes[index], node_lookup)
            distance = horizontal_distance_m(seed_lon, seed_lat, lon, lat)
            deadline = float(routes[index]["hard_deadline"])
            time_gap = (0.0 if not (math.isfinite(seed_deadline) and math.isfinite(deadline))
                        else abs(seed_deadline - deadline))
            return distance + 0.25 * time_gap

        indices = set(sorted(range(len(routes)), key=relatedness)[:count])
    else:
        raise ValueError("未知ALNS破坏算子: " + str(operator))
    kept = [route for i, route in enumerate(routes) if i not in indices]
    removed_boxes = [dict(box) for i in sorted(indices) for box in routes[i]["boxes"]]
    return kept, removed_boxes


def _insertion_actions(routes, box, models, segments):
    actions = []
    for index, route in enumerate(routes):
        candidate = build_route(route["boxes"] + [box], models, segments,
                                max_stops=MAX_STOPS_PER_ROUTE)
        if candidate is None:
            continue
        delta = _route_local_value(candidate) - _route_local_value(route)
        actions.append((delta, index, candidate))
    new_route = build_route([box], models, segments, max_stops=MAX_STOPS_PER_ROUTE)
    if new_route is not None:
        actions.append((_route_local_value(new_route), None, new_route))
    actions.sort(key=lambda item: (item[0], len(item[2]["stops"]), item[2]["box_ids"]))
    return actions


def _apply_action(routes, action):
    _, index, candidate = action
    updated = list(routes)
    if index is None:
        updated.append(candidate)
    else:
        updated[index] = candidate
    return updated


def _repair_greedy(routes, removed_boxes, models, segments, rng):
    pending = sorted(removed_boxes, key=lambda box: (
        float(box["hard_deadline_s"]) if box["hard_deadline_s"] == box["hard_deadline_s"] else float("inf"),
        float(box["expected_s"]), -float(box["priority"]), str(box["box_id"])))
    repaired = list(routes)
    for box in pending:
        actions = _insertion_actions(repaired, box, models, segments)
        if not actions:
            raise ValueError("ALNS无法重新插入货箱: " + str(box["box_id"]))
        near_best = [action for action in actions if action[0] <= actions[0][0] + 1e-8]
        repaired = _apply_action(repaired, rng.choice(near_best))
    return repaired


def _repair_regret2(routes, removed_boxes, models, segments, rng):
    del rng
    pending = [dict(box) for box in removed_boxes]
    repaired = list(routes)
    while pending:
        choice = None
        for position, box in enumerate(pending):
            actions = _insertion_actions(repaired, box, models, segments)
            if not actions:
                continue
            regret = ((actions[1][0] - actions[0][0]) if len(actions) > 1 else 1.0e9)
            urgency = (float(box["hard_deadline_s"])
                       if box["hard_deadline_s"] == box["hard_deadline_s"] else float("inf"))
            key = (-regret, urgency, float(box["expected_s"]), str(box["box_id"]))
            if choice is None or key < choice[0]:
                choice = (key, position, actions[0])
        if choice is None:
            raise ValueError("ALNS遗憾修复无法插入剩余货箱")
        repaired = _apply_action(repaired, choice[2])
        pending.pop(choice[1])
    return repaired


def _update_weights(weights, uses, rewards, reaction=0.20):
    for name in weights:
        if uses[name] > 0:
            observed = rewards[name] / float(uses[name])
            weights[name] = max(0.10, (1.0 - reaction) * weights[name] + reaction * observed)
        uses[name] = 0
        rewards[name] = 0.0


def _history_row(iteration, stage, best_score, best_routes, current_score,
                 destroy_operator="", repair_operator="", accepted=True,
                 temperature=0.0, destroy_weights=None, repair_weights=None):
    return {
        "iteration": iteration,
        "operator": stage,
        "destroy_operator": destroy_operator,
        "repair_operator": repair_operator,
        "accepted": bool(accepted),
        "temperature": float(temperature),
        "route_count": len(best_routes),
        "current_route_count": int(current_score[5]),
        "hard_violations": best_score[0],
        "weighted_tardiness": best_score[2],
        "makespan_s": best_score[3],
        "energy_kwh": best_score[4],
        "current_hard_violations": current_score[0],
        "current_weighted_tardiness": current_score[2],
        "current_makespan_s": current_score[3],
        "current_energy_kwh": current_score[4],
        "destroy_weights": ",".join("%s=%.3f" % item for item in sorted((destroy_weights or {}).items())),
        "repair_weights": ",".join("%s=%.3f" % item for item in sorted((repair_weights or {}).items())),
    }


def alns_optimize(boxes, models, segments, nodes, drones, batteries,
                  max_iterations=120, random_seed=2026):
    rng = random.Random(int(random_seed))
    node_lookup = {row["node_id"]: row for _, row in nodes.iterrows()}
    initial = initial_routes(boxes, models, segments)
    initial_eval = _evaluate(initial, drones, batteries)
    history = [_history_row(0, "initial", initial_eval[0], initial, initial_eval[0])]

    seeded = _greedy_merge_seed(initial, models, segments, node_lookup, drones, batteries)
    current_routes, current_score, current_scalar = seeded[:3]
    current_schedule, current_drone_state, current_battery_state = seeded[3:]
    best_routes = list(current_routes)
    best_score = current_score
    best_scalar = current_scalar
    best_schedule = current_schedule
    best_drone_state = current_drone_state
    best_battery_state = current_battery_state

    destroy_weights = {"random_routes": 1.0, "worst_routes": 1.0, "related_routes": 1.0}
    repair_weights = {"greedy": 1.0, "regret2": 1.0}
    destroy_uses = {name: 0 for name in destroy_weights}
    repair_uses = {name: 0 for name in repair_weights}
    destroy_rewards = {name: 0.0 for name in destroy_weights}
    repair_rewards = {name: 0.0 for name in repair_weights}
    # A relatively warm start allows feasible, temporarily worse solutions to
    # escape the greedy seed; hard-deadline violations remain effectively
    # forbidden through the large penalty in _score_scalar.
    temperature = max(5000.0, abs(current_scalar) * 8.0)
    history.append(_history_row(1, "greedy_merge_seed", best_score, best_routes,
                                current_score, temperature=temperature,
                                destroy_weights=destroy_weights, repair_weights=repair_weights))

    for iteration in range(2, int(max_iterations) + 2):
        destroy_name = _roulette(destroy_weights, rng)
        repair_name = _roulette(repair_weights, rng)
        destroy_uses[destroy_name] += 1
        repair_uses[repair_name] += 1
        upper = min(5, max(2, int(math.ceil(0.25 * len(current_routes)))))
        remove_count = rng.randint(2, upper) if upper >= 2 else 1
        partial, removed = _destroy(current_routes, remove_count, destroy_name, node_lookup, rng)
        if repair_name == "greedy":
            proposal_routes = _repair_greedy(partial, removed, models, segments, rng)
        else:
            proposal_routes = _repair_regret2(partial, removed, models, segments, rng)
        proposal = _evaluate(proposal_routes, drones, batteries)
        proposal_score, proposal_scalar = proposal[:2]
        delta = proposal_scalar - current_scalar
        accepted = delta <= 0.0 or rng.random() < math.exp(-delta / max(temperature, 1e-9))
        reward = 0.0
        if proposal_score < best_score:
            best_routes = list(proposal_routes)
            best_score, best_scalar = proposal_score, proposal_scalar
            best_schedule, best_drone_state, best_battery_state = proposal[2:]
            reward = 6.0
        elif accepted and proposal_score < current_score:
            reward = 3.0
        elif accepted:
            reward = 1.0
        if accepted:
            current_routes = proposal_routes
            current_score, current_scalar = proposal_score, proposal_scalar
            current_schedule, current_drone_state, current_battery_state = proposal[2:]
        destroy_rewards[destroy_name] += reward
        repair_rewards[repair_name] += reward
        if (iteration - 1) % 10 == 0:
            _update_weights(destroy_weights, destroy_uses, destroy_rewards)
            _update_weights(repair_weights, repair_uses, repair_rewards)
        history.append(_history_row(iteration, "alns", best_score, best_routes,
                                    current_score, destroy_name, repair_name,
                                    accepted, temperature, destroy_weights,
                                    repair_weights))
        temperature *= 0.99

    return (best_routes, best_schedule, best_drone_state, best_battery_state,
            pd.DataFrame(history))


def validate_q2(schedule, boxes, models):
    delivered = []
    checks = []
    hard_late = []
    for item in schedule:
        metric = item["metrics"]
        model = models[models["model"] == item["model"]].iloc[0]
        delivered.extend(item["route"]["box_ids"])
        checks.append(("mass_" + item["trip_id"], metric["total_mass_kg"] <= float(model["max_payload_kg"]) + 1e-8,
                       metric["total_mass_kg"], float(model["max_payload_kg"])))
        checks.append(("volume_" + item["trip_id"], metric["total_volume_m3"] <= float(model["max_volume_m3"]) + 1e-10,
                       metric["total_volume_m3"], float(model["max_volume_m3"])))
        checks.append(("energy_" + item["trip_id"], metric["return_soc"] >= float(model["reserve_fraction"]) - 1e-8,
                       metric["return_soc"], float(model["reserve_fraction"])))
        for box in item["route"]["boxes"]:
            deadline = box["hard_deadline_s"]
            if deadline == deadline:
                hard_late.append(item["deliveries"][box["box_id"]] - float(deadline))
    target = list(boxes["box_id"])
    checks.append(("all_80_boxes_exactly_once", sorted(delivered) == sorted(target) and len(delivered) == len(set(delivered)),
                   len(delivered), len(target)))
    checks.append(("all_hard_deadlines", max(hard_late) <= 1e-6 if hard_late else True,
                   max(hard_late) if hard_late else 0.0, 0.0))
    for resource_key, release_key, prefix in (("drone_id", "return_s", "drone_overlap_"),
                                              ("battery_id", "charge_end_s", "battery_overlap_")):
        for resource_id in sorted(set(item[resource_key] for item in schedule)):
            tasks = sorted([item for item in schedule if item[resource_key] == resource_id],
                           key=lambda item: item["start_s"])
            overlap = max([tasks[i - 1][release_key] - tasks[i]["start_s"]
                           for i in range(1, len(tasks))] or [0.0])
            checks.append((prefix + resource_id, overlap <= 1e-6, max(0.0, overlap), 0.0))
    rows = [{"检查项": name, "是否通过": bool(ok), "实际值": actual, "允许上限或目标": limit}
            for name, ok, actual, limit in checks]
    result = pd.DataFrame(rows)
    if not bool(result["是否通过"].all()):
        failed = result[~result["是否通过"]]
        raise ValueError("问题二可行性检查失败: " + ", ".join(failed["检查项"].astype(str)))
    return result
