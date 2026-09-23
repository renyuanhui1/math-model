# D 题项目架构

## 数据流

官方题面与 `huawei/huawei/data/`、`结果提交模板.xlsx` 是只读输入。用户指定的 `huawei/huawei/26华为杯D题/26研赛全套成品D题+解析思路+word版本+推荐高质量版本/代码3.0/` 为计算入口。`run_all.py` 依次执行数据读取与校验、DEM 航段构建、四问求解、可行性检查、Excel 导出和数据图生成。隔离输出位于 `huawei/solution/runs/reproduced/`。论文 Typst 源文件位于 `huawei/solution/paper/`，编译为 `huawei/solution/output/pdf/D题_匿名工作稿.pdf`。

```text
官方数据和提交模板
       ↓
代码3.0/src/config.py、data_loader.py、dem.py、flight_physics.py
       ↓
Q1 安全载荷与精确组批 → Q2 候选路线、ALNS 和共享电池排程
       ↓
Q3 地形链路、运输与中继联合搜索 → Q4 固定任务块的 K=2/3 分区
       ↓
results/*.xlsx、figures/*.pdf、summary.md、logs/run_all.log
       ↓
solution/verify_results.py 独立读取官方输入及导出结果
       ↓
solution/paper/main.typ → output/pdf/D题_匿名工作稿.pdf
```

## 模块职责

| 模块 | 职责 |
| --- | --- |
| `config.py`、`data_loader.py` | 定位官方数据与模板、读取输入、检查编号和字段 |
| `dem.py`、`flight_physics.py` | DEM 插值、航段与载荷相关的时间和能耗 |
| `q1_safe_payload.py`、`q1_batching.py` | 返航安全载荷与单点不可拆货箱组批 |
| `q2_candidate_routes.py`、`q2_transport_optimization.py`、`q2_resource_schedule.py` | 多点候选路线、ALNS、实体无人机与共享电池时序 |
| `q3_communication.py`、`q3_relay_candidates.py`、`q3_joint_optimization.py` | 双向链路、候选中继、运输与通信联合排程 |
| `q4_partition.py` | 固定运输路线的不可拆块、分区和独立库存配置 |
| `plotting.py`、`export_workbooks.py`、`generate_reports.py` | 数据图、结果工作簿与动态摘要 |

## 验证边界

`verify_results.py` 不调用求解器，核对 80 箱唯一交付、官方硬时限、质量体积和能量、机体及电池时序、中继窗口、分区和六表模板。Q1 的精确性限于定义的单点组批模型；Q4 的穷举限于固定 Q3 任务块。Q2/Q3 是固定候选与迭代预算下经核验的可行启发式解。Q3 零通信中断限于 DEM 与题设链路参数下的 1 秒轨迹采样网格，没有连续时间证明。

## 文档与来源

`huawei/solution/reports/REFERENCE_AUDIT.md` 记录移植资料及 GitHub skills 对照；`ANALYSIS_MODELING_REPORT.md`、`RESULTS_REPORT.md`、`VERIFY_REPORT.md` 分别记录模型、数值和终验。旧版参考论文和日志仅用于比对；正式数值以本次重跑结果为准。
