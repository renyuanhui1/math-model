from __future__ import division

# AI-assisted review and documentation: OpenAI Codex (GPT-6, OpenAI),
# used 2026-09-23. Model release date needs verification by the team.

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from src.config import (DATA_ROOT, DEM_MAT, LOGS_DIR, PROJECT_ROOT, RESULTS_DIR,
                        TABLE_DIR, TEMPLATE_XLSX, TMP_DIR, ensure_directories)
from src.data_loader import build_inventory, load_all, validate_inventory
from src.dem import DEMGrid, build_segment_database
from src.plotting import generate_all_figures
from src.q1_batching import solve_margin_sensitivity, solve_q1_batching
from src.q1_safe_payload import solve_safe_payloads
from src.q2_resource_schedule import schedule_score, schedule_tables
from src.q2_transport_optimization import alns_optimize, validate_q2
from src.q3_joint_optimization import relay_sortie_table, solve_q3, validate_q3
from src.q4_partition import (RESOURCE_KEYS, build_hypergraph_blocks,
                              centralized_resource, solve_partition)


def _write_table(name, frame):
    path = TABLE_DIR / (name + ".csv")
    frame.to_csv(str(path), index=False, encoding="utf-8-sig")
    return path


def _q2_summary(schedule, history):
    score = schedule_score(schedule)
    return pd.DataFrame([{
        "运输架次数": len(schedule),
        "硬时限违约货箱数": score[0],
        "硬时限总超时（s）": score[1],
        "加权延期（优先系数·s）": score[2],
        "任务完成时刻（s）": score[3],
        "总能耗（kWh）": score[4],
        "初始架次数": int(history.iloc[0]["route_count"]),
        "改进后架次数": int(history.iloc[-1]["route_count"]),
    }])


def _q3_resource_table(transport_resource, relay_sorties, relay_model):
    rows = transport_resource.to_dict("records")
    turnaround = float(relay_model.iloc[0]["turnaround_s"])
    for item in relay_sorties:
        rows.append({"资源类型": "中继无人机", "资源编号": item["relay_id"],
                     "机型": "R", "阶段": "飞行与中继", "关联架次": item["relay_trip_id"],
                     "开始时刻（s）": item["start_s"],
                     "结束时刻（s）": item["return_s"] + turnaround})
        rows.append({"资源类型": "中继能源组件", "资源编号": item["component_id"],
                     "机型": "R", "阶段": "架次占用", "关联架次": item["relay_trip_id"],
                     "开始时刻（s）": item["start_s"],
                     "结束时刻（s）": item["return_s"]})
        rows.append({"资源类型": "中继能源组件", "资源编号": item["component_id"],
                     "机型": "R", "阶段": "充电", "关联架次": item["relay_trip_id"],
                     "开始时刻（s）": item["return_s"],
                     "结束时刻（s）": item["charge_end_s"]})
    return pd.DataFrame(rows)


def _q3_summary(schedule, direct, relay_sorties, coverage, shift):
    hard_slacks = []
    for item in schedule:
        for box in item["route"]["boxes"]:
            deadline = box["hard_deadline_s"]
            if deadline == deadline:
                hard_slacks.append(float(deadline) - item["deliveries"][box["box_id"]])
    transport_energy = sum(float(item["metrics"]["energy_kwh"]) for item in schedule)
    relay_energy = sum(float(item["energy_kwh"]) for item in relay_sorties)
    weighted_tardiness = schedule_score(schedule)[2]
    transport_finish = max(x["return_s"] for x in schedule)
    relay_finish = max([x["return_s"] for x in relay_sorties] or [0.0])
    return pd.DataFrame([{
        "运输架次数": len(schedule),
        "中继架次数": len(relay_sorties),
        "中继悬停点数": len(set((round(x["site"]["lon"], 8), round(x["site"]["lat"], 8),
                                                    round(x["site"]["alt_m"], 3)) for x in relay_sorties)),
        "全航迹采样点数": len(coverage),
        "直连覆盖点数": int((coverage["coverage_state"] == "DIRECT").sum()),
        "中继覆盖点数": int((coverage["coverage_state"] == "RELAY").sum()),
        "通信中断点数": int((coverage["coverage_state"] == "OUTAGE").sum()),
        "复核最大时间间隔（s）": 1.0,
        "最小硬时限余量（s）": min(hard_slacks),
        "通信前置时移（s）": shift,
        "运输加权延期（优先系数·s）": weighted_tardiness,
        "运输总能耗（kWh）": transport_energy,
        "中继总能耗（kWh）": relay_energy,
        "联合总能耗（kWh）": transport_energy + relay_energy,
        "联合任务完成时刻（s）": max(transport_finish, relay_finish),
    }])


def _resource_comparison(central, summaries):
    names = [("集中调度", central, 0, 0)]
    for label, summary in summaries:
        values = {key: summary[key + "_总需求"] for key in RESOURCE_KEYS}
        names.append((label, values, summary["总资源缺口"], summary["总资源冗余"]))
    labels = {"UAV_A": "A型无人机", "UAV_B": "B型无人机", "UAV_C": "C型无人机",
              "BAT_A": "A型电池", "BAT_B": "B型电池", "BAT_C": "C型电池",
              "RELAY": "中继无人机", "RELAY_ENERGY": "中继能源组件"}
    rows = []
    for name, values, gap, redundancy in names:
        row = {"方案": name, "总资源缺口": gap, "总资源冗余": redundancy}
        for key in RESOURCE_KEYS:
            row[labels[key]] = values[key]
        rows.append(row)
    return pd.DataFrame(rows)


def main():
    ensure_directories()
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    handler = logging.FileHandler(str(LOGS_DIR / "run_all.log"), mode="w", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root_logger.addHandler(handler)
    log = logging.getLogger("d_problem")
    log.info("开始加载官方数据：%s", DATA_ROOT)
    log.info("结果提交模板：%s", TEMPLATE_XLSX)
    data = load_all()
    inventory = build_inventory()
    data_checks = validate_inventory(data)
    dem = DEMGrid(DEM_MAT)
    segment_frame = build_segment_database(data["nodes"], dem)
    segments = {(row["origin"], row["destination"]): dict(row)
                for _, row in segment_frame.iterrows()}

    service_ids = list(data["nodes"][data["nodes"]["node_type"] == "service"]["node_id"])
    q1_safe = solve_safe_payloads(data["transport_models"], service_ids, segments)
    q1_batch, _ = solve_q1_batching(data["boxes"], data["transport_models"], segments, reserve_fraction=0.20)
    q1_sensitivity, _ = solve_margin_sensitivity(
        data["boxes"], data["transport_models"], segments, [0.15, 0.20, 0.25, 0.30])
    log.info("问题一完成：%s架次", len(q1_batch))

    _, q2_schedule, _, q2_battery_state, q2_history = alns_optimize(
        data["boxes"], data["transport_models"], segments, data["nodes"],
        data["transport_drones"], data["transport_batteries"])
    q2_validation = validate_q2(q2_schedule, data["boxes"], data["transport_models"])
    q2_trip, q2_box, q2_resource = schedule_tables(q2_schedule, q2_battery_state)
    q2_summary = _q2_summary(q2_schedule, q2_history)
    log.info("问题二完成：%s架次", len(q2_schedule))

    q3 = solve_q3(q2_schedule, data["nodes"], data["transport_models"], segments, dem,
                  data["link_parameters"], data["relay_model"], data["relay_drones"],
                  data["relay_components"], data["transport_drones"], data["transport_batteries"])
    (q3_schedule, q3_direct, q3_direct_intervals, q3_relays, q3_coverage,
     q3_comm_intervals, q3_sites, q3_shift, q3_params, q3_history) = q3
    q3_trip, q3_box, q3_transport_resource = schedule_tables(q3_schedule, {})
    q3_relay_table = relay_sortie_table(q3_relays)
    q3_resource = _q3_resource_table(q3_transport_resource, q3_relays, data["relay_model"])
    q3_summary = _q3_summary(q3_schedule, q3_direct, q3_relays, q3_coverage, q3_shift)
    q3_validation = validate_q3(q3_schedule, q3_relays, q3_coverage, data["relay_model"])
    if int(q3_summary.iloc[0]["通信中断点数"]) != 0:
        raise ValueError("问题三最终通信覆盖检查失败")
    validate_q2(q3_schedule, data["boxes"], data["transport_models"])
    log.info("问题三完成：%s个采样点全覆盖", len(q3_coverage))

    service_ids = sorted(data["nodes"][data["nodes"]["node_type"] == "service"]["node_id"])
    blocks = build_hypergraph_blocks(q3_schedule, service_ids)
    central = centralized_resource(q3_schedule, q3_relays)
    physical = {"UAV_A": 4, "UAV_B": 2, "UAV_C": 2, "BAT_A": 6, "BAT_B": 4,
                "BAT_C": 4, "RELAY": 2, "RELAY_ENERGY": 6}
    q4_k2, q4_s2 = solve_partition(2, blocks, q3_schedule, q3_relays,
                                   q3_comm_intervals, physical, central)
    q4_k3, q4_s3 = solve_partition(3, blocks, q3_schedule, q3_relays,
                                   q3_comm_intervals, physical, central)
    q4_resource = _resource_comparison(central, [("K=2分区", q4_s2), ("K=3分区", q4_s3)])
    q4_summary = pd.DataFrame([q4_s2, q4_s3])
    log.info("问题四完成：%s个不可分任务块", len(blocks))

    tables = {
        "data_inventory": inventory,
        "data_validation": data_checks,
        "segment_database": segment_frame,
        "q1_max_safe_payload": q1_safe,
        "q1_batching": q1_batch,
        "q1_safety_margin": q1_sensitivity,
        "q2_transport_sorties": q2_trip,
        "q2_box_delivery": q2_box,
        "q2_resource_schedule": q2_resource,
        "q2_summary": q2_summary,
        "q2_feasibility_check": q2_validation,
        "q3_direct_link_intervals": q3_direct_intervals,
        "q3_transport_schedule": q3_trip,
        "q3_box_delivery": q3_box,
        "q3_relay_sorties": q3_relay_table,
        "q3_communication_coverage": q3_comm_intervals,
        "q3_resource_schedule": q3_resource,
        "q3_joint_summary": q3_summary,
        "q3_optimization_history": q3_history,
        "q3_feasibility_check": q3_validation,
        "q4_partition_K2": q4_k2,
        "q4_partition_K3": q4_k3,
        "q4_resource_comparison": q4_resource,
        "q4_summary": q4_summary,
        "q2_optimization_history": q2_history,
    }
    for name, frame in tables.items():
        _write_table(name, frame)

    figure_data = {
        "dem": dem, "nodes": data["nodes"], "segment_frame": segment_frame,
        "q1_safe_payload": q1_safe, "q1_sensitivity": q1_sensitivity,
        "q2_schedule": q2_schedule, "q2_trip_table": q2_trip,
        "q2_box_table": q2_box, "q2_resource_table": q2_resource,
        "q3_direct": q3_direct, "q3_relay_sorties": q3_relays,
        "q3_communication_intervals": q3_comm_intervals,
        "q3_trip_table": q3_trip, "q3_resource_table": q3_resource,
        "q4_k2": q4_k2, "q4_k3": q4_k3,
        "q4_resource_comparison": q4_resource,
    }
    generate_all_figures(figure_data)

    metrics = {
        "q1": {"sorties": int(len(q1_batch)), "energy_kwh": float(q1_batch["架次能耗（kWh）"].sum()),
               "min_return_soc_pct": float(q1_batch["返航SOC（%）"].min())},
        "q2": q2_summary.iloc[0].to_dict(),
        "q3": q3_summary.iloc[0].to_dict(),
        "q4": {"blocks": len(blocks), "k2": q4_s2, "k3": q4_s3},
    }
    with (TABLE_DIR / "metrics.json").open("w", encoding="utf-8") as stream:
        json.dump(metrics, stream, ensure_ascii=False, indent=2)
    node_modules = os.environ.get("CODEX_NODE_MODULES")
    node_exe = shutil.which("node")
    if not node_modules:
        bundled = (Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" /
                   "dependencies" / "node")
        if (bundled / "node_modules" / "@oai" / "artifact-tool").exists():
            node_modules = str(bundled / "node_modules")
            if node_exe is None and (bundled / "bin" / "node.exe").exists():
                node_exe = str(bundled / "bin" / "node.exe")
    if node_modules and node_exe:
        environment = dict(os.environ)
        environment["CODEX_NODE_MODULES"] = node_modules
        environment["HUAWEI_D_TEMPLATE"] = str(TEMPLATE_XLSX)
        environment["HUAWEI_D_TABLE_DIR"] = str(TABLE_DIR)
        environment["HUAWEI_D_RESULTS_DIR"] = str(RESULTS_DIR)
        environment["HUAWEI_D_PREVIEW_DIR"] = str(TMP_DIR / "workbook_previews")
        subprocess.check_call([node_exe, str(PROJECT_ROOT / "export_workbooks.mjs")],
                              cwd=str(PROJECT_ROOT), env=environment)
    else:
        log.info("未找到artifact-tool，改用openpyxl兼容导出器")
        subprocess.check_call([sys.executable, str(PROJECT_ROOT / "export_workbooks.py")],
                              cwd=str(PROJECT_ROOT))
    subprocess.check_call([sys.executable, str(PROJECT_ROOT / "generate_reports.py")],
                          cwd=str(PROJECT_ROOT))
    log.info("全部计算和绘图完成")


if __name__ == "__main__":
    main()
