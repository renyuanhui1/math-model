# 2026年中国研究生数学建模竞赛D题计算工程

本工程完成安全载荷与组批、多无人机/共享电池调度、运输—通信联合优化以及K=2/K=3分区独立保障。原始附件只读，正式输出位于 `results/` 和 `figures/`。

## 运行

```powershell
python run_all.py
```

程序会从项目当前目录及各级父目录中自动寻找 `data/`、`数据/` 或旧版
`赛题/D题/数据/`。也可以显式指定：

```powershell
$env:HUAWEI_D_DATA_ROOT = "D:\huawei\data"
$env:HUAWEI_D_TEMPLATE = "D:\huawei\结果提交模板.xlsx"
python run_all.py
```

如果默认 `results/` 中的工作簿正在被Excel占用，可把本次运行输出到独立目录：

```powershell
$env:HUAWEI_D_OUTPUT_ROOT = ".\alns_output"
python run_all.py
```

首次在新电脑运行时，可使用一键脚本创建隔离环境并安装依赖：

```powershell
.\run_reproducible.ps1 -Python "python" -DataRoot "D:\huawei\data" -Template "D:\huawei\结果提交模板.xlsx"
```

推荐 Python 3.10–3.13。正式Excel优先由 artifact-tool 导出；普通电脑未安装该工具时，
程序会自动使用 openpyxl 兼容导出器，不影响结果表和官方提交模板的生成。

`run_all.py` 依次执行数据检查、四问求解、可行性复核和图表生成。问题二采用固定随机种子2026的自适应大邻域搜索（ALNS），包含随机路线移除、最差路线移除、相关路线移除、贪心插入、二阶遗憾插入、自适应算子权重和模拟退火接受准则；运行过程保存在 `q2_summary.xlsx` 的“优化过程”工作表。问题三不再使用手工服务区分组，而是穷举首小时任务的可行集合划分、搜索后续路线合并、联合调整运输出发时刻和中继时窗，并在DEM上按1秒航迹网格复核连续通信；搜索与验证记录保存在 `q3_joint_summary.xlsx`。每次运行都会在日志开头记录实际使用的数据目录和模板路径，便于复核。

## 目录

- `src/`：数据读取、飞行物理、运输优化、通信链路、中继时序、分区与绘图。
- `results/`：19个正式Excel结果，含官方提交模板。
- `figures/`：14张论文图，每张同时提供300 dpi PNG和PDF矢量版。
- `logs/`：一键运行日志。
- `summary.md`：从正式Excel自动读取数字生成的结果摘要。
- `paper_mapping.md`：论文表图与结果文件对应关系。

## 主要验证

工程自动检查80个货箱恰好交付一次、硬时限、载重/体积/能量上限、无人机/电池/中继能源时序冲突以及通信中断。数学模型和关键单位见各源文件中的简明注释。
