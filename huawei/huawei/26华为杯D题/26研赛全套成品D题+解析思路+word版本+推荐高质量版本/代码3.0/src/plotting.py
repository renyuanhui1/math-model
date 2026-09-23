from __future__ import division

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .config import FIGURES_DIR


COLORS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
          "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"]


def _style():
    plt.rcParams.update({
        "font.sans-serif": ["Microsoft YaHei", "SimHei", "Arial Unicode MS", "DejaVu Sans"],
        "axes.unicode_minus": False,
        "font.size": 10,
        "axes.titlesize": 13,
        "axes.labelsize": 11,
        "figure.dpi": 120,
    })


def _save(fig, name):
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(str(FIGURES_DIR / (name + ".png")), dpi=300, bbox_inches="tight")
    fig.savefig(str(FIGURES_DIR / (name + ".pdf")), bbox_inches="tight")
    plt.close(fig)


def plot_q1_dem_service_map(dem, nodes, segment_frame):
    _style()
    fig, ax = plt.subplots(figsize=(8.6, 7.0))
    z = dem.dem
    im = ax.imshow(z, extent=[dem.lon_min, dem.lon_max, dem.lat_min, dem.lat_max],
                   origin="upper", cmap="terrain", aspect="auto", alpha=0.88)
    center = nodes[nodes["node_id"] == "O01"].iloc[0]
    services = nodes[nodes["node_id"] != "O01"]
    ax.scatter(services["lon"], services["lat"], c="#d62728", s=34, edgecolor="white", zorder=4,
               label="服务区")
    ax.scatter([center["lon"]], [center["lat"]], marker="*", c="#0068b7", s=180,
               edgecolor="white", zorder=5, label="调度中心O01")
    for _, row in services.iterrows():
        ax.text(row["lon"] + 0.0015, row["lat"] + 0.0010, row["node_id"], fontsize=8)
    ax.set(xlabel="经度（°）", ylabel="纬度（°）", title="镇龙乡地形与服务区分布")
    fig.colorbar(im, ax=ax, shrink=0.82, label="高程（m）")
    ax.legend(loc="best")
    _save(fig, "q1_dem_service_map")


def plot_q1_safe_payload(safe_payload):
    _style()
    pivot = safe_payload.pivot(index="机型编号", columns="服务区编号", values="最大安全载荷（kg）")
    fig, ax = plt.subplots(figsize=(10.6, 3.8))
    im = ax.imshow(pivot.values, cmap="YlGnBu", aspect="auto")
    ax.set_xticks(np.arange(len(pivot.columns)))
    ax.set_xticklabels(pivot.columns, rotation=45, ha="right")
    ax.set_yticks(np.arange(len(pivot.index)))
    ax.set_yticklabels(pivot.index)
    for i in range(pivot.shape[0]):
        for j in range(pivot.shape[1]):
            ax.text(j, i, "%.1f" % pivot.values[i, j], ha="center", va="center", fontsize=7,
                    color="white" if pivot.values[i, j] > np.nanmean(pivot.values) else "black")
    ax.set(title="各机型至各服务区最大安全载荷", xlabel="服务区", ylabel="机型")
    fig.colorbar(im, ax=ax, label="最大安全载荷（kg）")
    _save(fig, "q1_safe_payload_heatmap")


def plot_q1_safety_margin(sensitivity):
    _style()
    fig, ax1 = plt.subplots(figsize=(7.4, 4.8))
    x = sensitivity["返航安全余量"] * 100.0
    ax1.plot(x, sensitivity["最优架次数"], "o-", color=COLORS[0], lw=2, label="最优架次")
    ax1.set(xlabel="返航安全余量（%）", ylabel="最优架次数")
    ax2 = ax1.twinx()
    ax2.plot(x, sensitivity["总能耗（kWh）"], "s--", color=COLORS[1], lw=2, label="总能耗")
    ax2.set_ylabel("总能耗（kWh）")
    lines = ax1.lines + ax2.lines
    ax1.legend(lines, [line.get_label() for line in lines], loc="best")
    ax1.set_title("安全余量对组批结果的敏感性")
    ax1.grid(alpha=0.25)
    _save(fig, "q1_safety_margin")


def _node_lookup(nodes):
    return {row["node_id"]: row for _, row in nodes.iterrows()}


def plot_q2_routes(schedule, nodes, dem):
    _style()
    lookup = _node_lookup(nodes)
    fig, ax = plt.subplots(figsize=(8.6, 7.0))
    ax.imshow(dem.dem, extent=[dem.lon_min, dem.lon_max, dem.lat_min, dem.lat_max],
              origin="upper", cmap="Greys", aspect="auto", alpha=0.42)
    for idx, item in enumerate(schedule):
        seq = ["O01"] + list(item["metrics"]["stop_order"]) + ["O01"]
        ax.plot([lookup[s]["lon"] for s in seq], [lookup[s]["lat"] for s in seq],
                color=COLORS[idx % len(COLORS)], lw=1.3, alpha=0.74)
    ax.scatter(nodes["lon"], nodes["lat"], c=np.where(nodes["node_id"].eq("O01"), "#0068b7", "#d62728"),
               s=np.where(nodes["node_id"].eq("O01"), 90, 28), edgecolor="white", zorder=4)
    for _, row in nodes.iterrows():
        ax.text(row["lon"] + 0.0015, row["lat"] + 0.001, row["node_id"], fontsize=8)
    ax.set(xlabel="经度（°）", ylabel="纬度（°）", title="问题二优化运输航线")
    _save(fig, "q2_routes")


def _gantt(frame, resource_col, start_col, end_col, label_col, title, name, color_col=None):
    _style()
    resources = sorted(frame[resource_col].dropna().unique())
    fig, ax = plt.subplots(figsize=(10.4, max(4.6, 0.48 * len(resources) + 1.6)))
    ymap = {r: i for i, r in enumerate(resources)}
    color_map = {}
    if color_col:
        for i, value in enumerate(sorted(frame[color_col].dropna().unique())):
            color_map[value] = COLORS[i % len(COLORS)]
    for _, row in frame.iterrows():
        y = ymap[row[resource_col]]
        color = color_map.get(row[color_col], COLORS[0]) if color_col else COLORS[0]
        width = float(row[end_col]) - float(row[start_col])
        ax.barh(y, width, left=float(row[start_col]), height=0.6, color=color, alpha=0.82,
                edgecolor="white")
        if width > 350:
            ax.text(float(row[start_col]) + width / 2.0, y, str(row[label_col]), ha="center", va="center", fontsize=7)
    ax.set_yticks(range(len(resources)))
    ax.set_yticklabels(resources)
    ax.set(xlabel="时刻（s）", ylabel="资源", title=title)
    ax.grid(axis="x", alpha=0.22)
    _save(fig, name)


def plot_q2_gantts(trip_table, resource_table):
    _gantt(trip_table, "无人机编号", "开始时刻（s）", "返回O01时刻（s）", "架次编号",
           "运输无人机任务甘特图", "q2_uav_gantt", "机型编号")
    battery = resource_table[resource_table["资源类型"] == "共享电池"].copy()
    _gantt(battery, "资源编号", "开始时刻（s）", "结束时刻（s）", "阶段",
           "共享电池占用与充电甘特图", "q2_battery_gantt", "阶段")


def plot_q2_delivery_timeline(box_table):
    _style()
    frame = box_table.sort_values("交付完成时刻（s）").reset_index(drop=True)
    fig, ax = plt.subplots(figsize=(10.4, 5.2))
    colors = np.where(frame["硬截止时刻（s）"].notnull(), "#d62728", "#1f77b4")
    ax.scatter(frame["交付完成时刻（s）"] / 3600.0, np.arange(len(frame)), c=colors, s=18)
    ax.set(xlabel="交付完成时间（h）", ylabel="按交付时间排序的货箱", title="80个货箱交付时间线")
    ax.grid(alpha=0.22)
    ax.text(0.99, 0.02, "红：硬时限货箱    蓝：软时限货箱", transform=ax.transAxes,
            ha="right", va="bottom", fontsize=9)
    _save(fig, "q2_delivery_timeline")


def plot_q3(direct, relay_sorties, coverage_intervals, q3_trip_table, q3_resource_table, nodes, dem):
    _style()
    fig, ax = plt.subplots(figsize=(8.6, 7.0))
    ax.imshow(dem.dem, extent=[dem.lon_min, dem.lon_max, dem.lat_min, dem.lat_max],
              origin="upper", cmap="Greys", aspect="auto", alpha=0.4)
    for state, color, label in [(True, "#2ca02c", "直连可用"), (False, "#d62728", "直连盲区")]:
        f = direct[direct["direct_available"] == state]
        ax.scatter(f["lon"], f["lat"], s=5, c=color, alpha=0.5, label=label)
    ax.scatter(nodes["lon"], nodes["lat"], c="#111111", s=22, zorder=4)
    ax.set(xlabel="经度（°）", ylabel="纬度（°）", title="运输航迹的直连通信覆盖")
    ax.legend(loc="best")
    _save(fig, "q3_direct_coverage_map")

    fig, ax = plt.subplots(figsize=(8.6, 7.0))
    ax.imshow(dem.dem, extent=[dem.lon_min, dem.lon_max, dem.lat_min, dem.lat_max],
              origin="upper", cmap="terrain", aspect="auto", alpha=0.72)
    ax.scatter(nodes["lon"], nodes["lat"], c="#252525", s=24, label="地面节点")
    if relay_sorties:
        sites = pd.DataFrame([x["site"] for x in relay_sorties]).drop_duplicates(["lon", "lat", "alt_m"])
        ax.scatter(sites["lon"], sites["lat"], marker="^", s=105, c="#ff7f0e", edgecolor="white", label="中继悬停点")
        for idx, row in sites.reset_index(drop=True).iterrows():
            ax.text(row["lon"] + 0.001, row["lat"] + 0.001, "R%d" % (idx + 1), fontsize=9)
    ax.set(xlabel="经度（°）", ylabel="纬度（°）", title="中继悬停点布局")
    ax.legend(loc="best")
    _save(fig, "q3_relay_layout")

    _gantt(coverage_intervals, "运输架次编号", "开始时刻（s）", "结束时刻（s）", "保障方式",
           "通信保障时间线（无中断）", "q3_communication_timeline", "保障方式")
    combined = q3_resource_table.copy()
    _gantt(combined, "资源编号", "开始时刻（s）", "结束时刻（s）", "关联架次",
           "运输—中继联合资源甘特图", "q3_joint_gantt", "资源类型")


def _plot_partition(frame, nodes, dem, k):
    _style()
    lookup = _node_lookup(nodes)
    fig, ax = plt.subplots(figsize=(8.6, 7.0))
    ax.imshow(dem.dem, extent=[dem.lon_min, dem.lon_max, dem.lat_min, dem.lat_max],
              origin="upper", cmap="Greys", aspect="auto", alpha=0.35)
    for idx, row in frame.iterrows():
        services = str(row["服务区列表"]).split(",")
        values = [lookup[s] for s in services]
        ax.scatter([v["lon"] for v in values], [v["lat"] for v in values], s=60,
                   color=COLORS[idx % len(COLORS)], edgecolor="white", label=row["任务组编号"])
        for v in values:
            ax.text(v["lon"] + 0.001, v["lat"] + 0.001, v["node_id"], fontsize=8)
    ax.set(xlabel="经度（°）", ylabel="纬度（°）", title="K=%d独立任务组分区" % k)
    ax.legend(loc="best")
    _save(fig, "q4_partition_K%d" % k)


def plot_q4(k2, k3, resource_comparison, nodes, dem):
    _plot_partition(k2, nodes, dem, 2)
    _plot_partition(k3, nodes, dem, 3)
    _style()
    fields = [c for c in resource_comparison.columns if c not in ("方案", "总资源缺口", "总资源冗余")]
    x = np.arange(len(fields))
    fig, ax = plt.subplots(figsize=(10.6, 5.2))
    width = 0.24
    for idx, (_, row) in enumerate(resource_comparison.iterrows()):
        ax.bar(x + (idx - 1) * width, [row[c] for c in fields], width=width,
               color=COLORS[idx], label=row["方案"])
    ax.set_xticks(x)
    ax.set_xticklabels(fields, rotation=35, ha="right")
    ax.set(ylabel="资源数量", title="集中调度与K=2/K=3分区资源对比")
    ax.legend(loc="best")
    ax.grid(axis="y", alpha=0.22)
    _save(fig, "q4_resource_comparison")


def generate_all_figures(data):
    plot_q1_dem_service_map(data["dem"], data["nodes"], data["segment_frame"])
    plot_q1_safe_payload(data["q1_safe_payload"])
    plot_q1_safety_margin(data["q1_sensitivity"])
    plot_q2_routes(data["q2_schedule"], data["nodes"], data["dem"])
    plot_q2_gantts(data["q2_trip_table"], data["q2_resource_table"])
    plot_q2_delivery_timeline(data["q2_box_table"])
    plot_q3(data["q3_direct"], data["q3_relay_sorties"], data["q3_communication_intervals"],
            data["q3_trip_table"], data["q3_resource_table"], data["nodes"], data["dem"])
    plot_q4(data["q4_k2"], data["q4_k3"], data["q4_resource_comparison"], data["nodes"], data["dem"])
