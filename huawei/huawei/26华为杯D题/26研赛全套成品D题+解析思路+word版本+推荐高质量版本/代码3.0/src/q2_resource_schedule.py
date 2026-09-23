from __future__ import division

import math

import pandas as pd

from .flight_physics import charge_time_s


def _build_resources(drones, battery_inventory):
    drone_state = {}
    for _, row in drones.iterrows():
        drone_state[row["drone_id"]] = {"model": row["model"], "available": 0.0, "tasks": []}
    battery_state = {}
    for _, row in battery_inventory.iterrows():
        model = row["model"]
        for i in range(1, int(row["count"]) + 1):
            battery_id = "%s-BAT-%02d" % (model, i)
            battery_state[battery_id] = {"model": model, "available": 0.0,
                                         "full_charge_s": float(row["full_charge_s"]), "tasks": []}
    return drone_state, battery_state


def schedule_routes(routes, drones, battery_inventory):
    drone_state, battery_state = _build_resources(drones, battery_inventory)
    pending = sorted(list(routes), key=lambda r: (r["hard_deadline"], r["expected"], -r["priority_sum"], -len(r["boxes"])))
    scheduled = []
    for route in pending:
        best = None
        for model, metrics in route["options"].items():
            model_drones = [(state["available"], drone_id) for drone_id, state in drone_state.items()
                            if state["model"] == model]
            model_batteries = [(state["available"], battery_id) for battery_id, state in battery_state.items()
                               if state["model"] == model]
            if not model_drones or not model_batteries:
                continue
            drone_available, drone_id = min(model_drones)
            battery_available, battery_id = min(model_batteries)
            start = max(drone_available, battery_available)
            deliveries = {box_id: start + offset for box_id, offset in metrics["delivery_offsets"].items()}
            hard_late = []
            weighted_tardiness = 0.0
            for box in route["boxes"]:
                delivery = deliveries[box["box_id"]]
                deadline = box.get("hard_deadline_s")
                if deadline == deadline:
                    hard_late.append(max(0.0, delivery - float(deadline)))
                weighted_tardiness += float(box["priority"]) * max(0.0, delivery - float(box["expected_s"]))
            hard_count = sum(1 for x in hard_late if x > 1e-6)
            key = (hard_count, sum(hard_late), weighted_tardiness,
                   start + metrics["duration_s"], metrics["energy_kwh"], model)
            if best is None or key < best[0]:
                best = (key, model, metrics, drone_id, battery_id, start, deliveries)
        if best is None:
            raise ValueError("路线无法分配到任何实体无人机或电池")
        _, model, metrics, drone_id, battery_id, start, deliveries = best
        end = start + metrics["duration_s"]
        soc = metrics["return_soc"]
        recharge = charge_time_s(soc, battery_state[battery_id]["full_charge_s"])
        drone_state[drone_id]["available"] = end
        drone_state[drone_id]["tasks"].append((start, end))
        battery_state[battery_id]["available"] = end + recharge
        battery_state[battery_id]["tasks"].append((start, end, end + recharge))
        scheduled.append({
            "route": route, "model": model, "metrics": metrics, "drone_id": drone_id,
            "battery_id": battery_id, "start_s": start, "return_s": end,
            "deliveries": deliveries, "charge_end_s": end + recharge,
            "charge_time_s": recharge,
        })
    scheduled.sort(key=lambda x: (x["start_s"], x["return_s"], x["drone_id"]))
    for i, item in enumerate(scheduled, 1):
        item["trip_id"] = "Q2-%03d" % i
    return scheduled, drone_state, battery_state


def schedule_score(schedule):
    hard_count = 0
    hard_lateness = 0.0
    weighted_tardiness = 0.0
    total_energy = 0.0
    makespan = 0.0
    for item in schedule:
        makespan = max(makespan, item["return_s"])
        total_energy += item["metrics"]["energy_kwh"]
        for box in item["route"]["boxes"]:
            delivery = item["deliveries"][box["box_id"]]
            deadline = box.get("hard_deadline_s")
            if deadline == deadline and delivery > float(deadline) + 1e-6:
                hard_count += 1
                hard_lateness += delivery - float(deadline)
            weighted_tardiness += float(box["priority"]) * max(0.0, delivery - float(box["expected_s"]))
    return (hard_count, hard_lateness, weighted_tardiness, makespan, total_energy, len(schedule))


def schedule_tables(schedule, battery_state):
    trip_rows = []
    box_rows = []
    resource_rows = []
    for item in schedule:
        metrics = item["metrics"]
        trip_rows.append({
            "架次编号": item["trip_id"],
            "无人机编号": item["drone_id"],
            "机型编号": item["model"],
            "电池编号": item["battery_id"],
            "开始时刻（s）": item["start_s"],
            "访问服务区顺序": "->".join(metrics["stop_order"]),
            "返回O01时刻（s）": item["return_s"],
            "架次能耗（kWh）": metrics["energy_kwh"],
            "返航SOC（%）": metrics["return_soc"] * 100.0,
            "货箱数量": len(item["route"]["boxes"]),
            "货箱编号列表": ",".join(item["route"]["box_ids"]),
        })
        resource_rows.append({"资源类型": "运输无人机", "资源编号": item["drone_id"],
                              "机型": item["model"], "阶段": "执行架次", "关联架次": item["trip_id"],
                              "开始时刻（s）": item["start_s"], "结束时刻（s）": item["return_s"]})
        resource_rows.append({"资源类型": "共享电池", "资源编号": item["battery_id"],
                              "机型": item["model"], "阶段": "随架次占用", "关联架次": item["trip_id"],
                              "开始时刻（s）": item["start_s"], "结束时刻（s）": item["return_s"]})
        if item["charge_time_s"] > 1e-6:
            resource_rows.append({"资源类型": "共享电池", "资源编号": item["battery_id"],
                                  "机型": item["model"], "阶段": "两阶段充电", "关联架次": item["trip_id"],
                                  "开始时刻（s）": item["return_s"], "结束时刻（s）": item["charge_end_s"]})
        for box in item["route"]["boxes"]:
            box_rows.append({
                "货箱编号": box["box_id"], "架次编号": item["trip_id"],
                "服务区编号": box["service_id"], "物资类型": box["material"],
                "是否首批保障": "是" if box["is_first"] else "否",
                "硬截止时刻（s）": box["hard_deadline_s"], "期望送达时间（s）": box["expected_s"],
                "交付完成时刻（s）": item["deliveries"][box["box_id"]],
                "加权延期（优先系数·s）": float(box["priority"]) * max(0.0, item["deliveries"][box["box_id"]] - float(box["expected_s"])),
            })
    return pd.DataFrame(trip_rows), pd.DataFrame(box_rows), pd.DataFrame(resource_rows)

