# 华为杯 D 题：山区洪涝灾害下无人机运输与通信协同优化

本仓库包含官方输入、用户指定的最新 `代码3.0`、本机重新运行的四问结果、独立核验和匿名论文工作稿。详见 [项目架构](ARCHITECTURE.md) 与 [资料对照](huawei/solution/reports/REFERENCE_AUDIT.md)。

## 复现

在 PowerShell 中进入 `huawei/huawei/26华为杯D题/26研赛全套成品D题+解析思路+word版本+推荐高质量版本/代码3.0/`，安装 `requirements.txt` 后运行：

```powershell
$env:HUAWEI_D_DATA_ROOT = 'C:\Users\PC\Desktop\数模\huawei\huawei\data'
$env:HUAWEI_D_TEMPLATE = 'C:\Users\PC\Desktop\数模\huawei\huawei\结果提交模板.xlsx'
$env:HUAWEI_D_OUTPUT_ROOT = 'C:\Users\PC\Desktop\数模\huawei\solution\runs\reproduced'
python run_all.py
python generate_reports.py
```

路径按实际克隆位置修改。`HUAWEI_D_OUTPUT_ROOT` 可设为任何独立输出目录；配置模块也会向上查找 `data/` 和提交模板。随后在仓库根目录运行 `python huawei/solution/verify_results.py`；如更改输出目录，先在核验脚本中查看其输入路径。论文源文件为 `huawei/solution/paper/main.typ`，用 Typst 编译；论文所引用的图位于 `huawei/solution/runs/reproduced/figures/`。

## 交付

- 匿名论文工作稿：`huawei/solution/output/pdf/D题_匿名工作稿.pdf`
- 官方六表结果：`huawei/solution/runs/reproduced/results/最终结果提交.xlsx`
- 结果、图和运行日志：`huawei/solution/runs/reproduced/`
- 模型、结果与验收报告：`huawei/solution/reports/`

当前论文是匿名工作稿。正式竞赛平台上传前，参赛队需核对并补充本队队号、按平台要求命名并计算最终 PDF 的 MD5；论文中的 AI 工具版本发布日期也需据可核实的公开资料补全。
