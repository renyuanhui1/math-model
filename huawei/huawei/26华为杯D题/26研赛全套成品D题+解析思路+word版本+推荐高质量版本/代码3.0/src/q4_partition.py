from __future__ import division

import math

import numpy as np
import pandas as pd


RESOURCE_KEYS = ["UAV_A", "UAV_B", "UAV_C", "BAT_A", "BAT_B", "BAT_C", "RELAY", "RELAY_ENERGY"]


class UnionFind(object):
    def __init__(self, items):
        self.parent = {item: item for item in items}

    def find(self, item):
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def build_hypergraph_blocks(schedule, service_ids):
    uf = UnionFind(service_ids)
    for item in schedule:
        stops = list(item["metrics"]["stop_order"])
        for stop in stops[1:]:
            uf.union(stops[0], stop)
    groups = {}
    for service in service_ids:
        groups.setdefault(uf.find(service), []).append(service)
    return [tuple(sorted(v)) for v in sorted(groups.values(), key=lambda x: x[0])]


def _max_overlap(intervals):
    events = []
    for start, end in intervals:
        events.append((float(start), 1))
        events.append((float(end), -1))
    active = 0
    best = 0
    for _, delta in sorted(events, key=lambda x: (x[0], x[1])):
        active += delta
        best = max(best, active)
    return best


def _resource_for_group(services, schedule, relay_sorties, communication_intervals):
    service_set = set(services)
    transport = [item for item in schedule if set(item["metrics"]["stop_order"]).issubset(service_set)]
    resource = {}
    for model in ("A", "B", "C"):
        tasks = [(x["start_s"], x["return_s"]) for x in transport if x["model"] == model]
        battery = [(x["start_s"], x["charge_end_s"]) for x in transport if x["model"] == model]
        resource["UAV_" + model] = _max_overlap(tasks)
        resource["BAT_" + model] = _max_overlap(battery)
    trip_ids = set(x["trip_id"] for x in transport)
    relay_ids = set(communication_intervals[
        communication_intervals["运输架次编号"].isin(trip_ids) &
        (communication_intervals["保障方式"] == "RELAY")]["中继架次编号"].dropna().astype(str))
    used_relays = [x for x in relay_sorties if x["relay_trip_id"] in relay_ids]
    resource["RELAY"] = _max_overlap([(x["start_s"], x["return_s"] + 300.0) for x in used_relays])
    resource["RELAY_ENERGY"] = _max_overlap([(x["start_s"], x["charge_end_s"]) for x in used_relays])
    workload = sum(x["return_s"] - x["start_s"] for x in transport)
    workload += sum(x["return_s"] - x["start_s"] for x in used_relays)
    return resource, workload, transport, used_relays


def _canonical_assignments(n, k):
    assignment = [0] * n

    def rec(pos, max_label):
        if pos == n:
            if max_label == k - 1:
                yield tuple(assignment)
            return
        upper = min(max_label + 1, k - 1)
        for label in range(upper + 1):
            assignment[pos] = label
            for value in rec(pos + 1, max(max_label, label)):
                yield value
    for value in rec(1, 0):
        yield value


def solve_partition(k, blocks, schedule, relay_sorties, communication_intervals, inventory, central_resource):
    cache = {}

    def group_result(services):
        key = tuple(sorted(services))
        if key not in cache:
            cache[key] = _resource_for_group(key, schedule, relay_sorties, communication_intervals)
        return cache[key]

    best = None
    evaluated = 0
    for assignment in _canonical_assignments(len(blocks), k):
        service_groups = []
        for group_id in range(k):
            services = sorted([s for idx, block in enumerate(blocks) if assignment[idx] == group_id for s in block])
            service_groups.append(services)
        results = [group_result(group) for group in service_groups]
        totals = {key: sum(result[0][key] for result in results) for key in RESOURCE_KEYS}
        gaps = {key: max(0, totals[key] - inventory[key]) for key in RESOURCE_KEYS}
        gap_sum = sum(gaps.values())
        redundancy = sum(max(0, totals[key] - central_resource[key]) for key in RESOURCE_KEYS)
        workloads = np.asarray([result[1] for result in results], dtype=float)
        cv = float(workloads.std() / workloads.mean()) if workloads.mean() > 0 else 0.0
        key = (gap_sum, redundancy, cv, float(workloads.max() - workloads.min()))
        evaluated += 1
        if best is None or key < best[0]:
            best = (key, service_groups, results, totals, gaps, evaluated)
    if best is None:
        raise ValueError("问题四无有效分区")
    key, groups, results, totals, gaps, _ = best
    rows = []
    for idx, (services, result) in enumerate(zip(groups, results), 1):
        resource, workload, transport, used_relays = result
        rows.append({
            "K（2或3）": k, "任务组编号": "G%d-%d" % (k, idx), "服务区列表": ",".join(services),
            "A型运输无人机数": resource["UAV_A"], "B型运输无人机数": resource["UAV_B"],
            "C型运输无人机数": resource["UAV_C"], "A型电池组数": resource["BAT_A"],
            "B型电池组数": resource["BAT_B"], "C型电池组数": resource["BAT_C"],
            "中继无人机数": resource["RELAY"], "中继能源组件数": resource["RELAY_ENERGY"],
            "运输架次数": len(transport), "复制中继架次数": len(used_relays), "工作量（资源占用秒）": workload,
        })
    summary = {
        "K": k, "任务块数量": len(blocks), "枚举分区数量": evaluated,
        "总资源缺口": key[0], "总资源冗余": key[1], "工作量变异系数": key[2],
    }
    for resource_key in RESOURCE_KEYS:
        summary[resource_key + "_总需求"] = totals[resource_key]
        summary[resource_key + "_缺口"] = gaps[resource_key]
    return pd.DataFrame(rows), summary


def centralized_resource(schedule, relay_sorties):
    out = {}
    for model in ("A", "B", "C"):
        out["UAV_" + model] = _max_overlap([(x["start_s"], x["return_s"]) for x in schedule if x["model"] == model])
        out["BAT_" + model] = _max_overlap([(x["start_s"], x["charge_end_s"]) for x in schedule if x["model"] == model])
    out["RELAY"] = _max_overlap([(x["start_s"], x["return_s"] + 300.0) for x in relay_sorties])
    out["RELAY_ENERGY"] = _max_overlap([(x["start_s"], x["charge_end_s"]) for x in relay_sorties])
    return out

