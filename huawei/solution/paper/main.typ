// Adapted from the installed MathModelAgent huaweibei Typst template.
// The competition's official format starts at the anonymous abstract page.
#set document(title: "山区洪涝灾害下异构无人机运输与通信协同优化", author: ())
#set page(
  paper: "a4",
  margin: (top: 25mm, bottom: 25mm, left: 22.5mm, right: 22.5mm),
  numbering: "1",
  number-align: center,
  header: none,
)
#set text(font: ("SimSun", "Times New Roman"), size: 12pt, lang: "zh")
#set par(first-line-indent: 2em, justify: true, leading: 0.45em, spacing: 0.55em)
#show heading.where(level: 1): set align(center)
#show heading.where(level: 1): set text(font: "SimHei", size: 14pt, weight: "bold")
#show heading.where(level: 2): set text(font: "SimHei", size: 12pt, weight: "bold")
#show figure.caption: set text(size: 10.5pt)

#align(center)[#text(font: "SimHei", size: 16pt, weight: "bold")[山区洪涝灾害下异构无人机运输与通信协同优化]]
#v(0.85cm)
#align(center)[#text(font: "SimHei", size: 14pt, weight: "bold")[摘　要]]
#v(0.45cm)

针对山区洪涝灾害中道路与地面通信同时受损的标准场景，本文把 15 个服务区、80 个不可拆货箱、异构运输机、共享电池、中继机与 30 m 数字高程模型纳入统一的航段—时间—能源计算框架。对问题一，沿 DEM 航段确定巡航海拔，以载荷相关能耗反求三型机最大安全载荷，并用精确集合划分完成单点货箱组批：20% 返航安全余量下共需 18 架次，运输能耗 59.232 kWh；余量从 20% 增至 30% 时，最少架次升至 20。对问题二，将多服务区访问顺序与实体无人机、同型号共享电池的时序解码结合，用自适应大邻域搜索得到 21 架次、全部 80 箱唯一交付且硬时限零违约的可行计划，任务最晚返航时刻为 11420.942 s。

对问题三，依据 DEM 和双向链路预算识别直连盲区，联合调整运输路线、出发时刻与中继窗口。得到 21 次运输、10 次中继、4 个悬停点；34919 个 1 秒轨迹采样点中直连 19865 点、中继 15054 点，未检出通信中断，联合最晚返航时刻 16947.580 s。对问题四，固定问题三任务结构，以多点架次构造不可拆超图任务块，枚举 2 组和 3 组独立分区。六个任务块下，K=2 的资源缺口为 2、相对集中调度冗余为 3，均低于 K=3 的 5 和 9；集中调度在现有库存内可执行。Q1 和 Q4 在各自定义的离散搜索域内精确求解，Q2 和 Q3 为经约束复核的启发式方案。通信零中断结论限于 1 秒网格及题设确定性链路模型。

#v(0.3cm)
#par(first-line-indent: 0pt)[#text(font: "SimHei", weight: "bold")[关键词：] 洪涝救援；异构无人机；共享电池；通信中继；任务分区]

#pagebreak()

#include("sections/1_restatement.typ")
#include("sections/2_analysis.typ")
#include("sections/3_assumptions.typ")
#include("sections/4_symbols.typ")
#include("sections/5_problem1.typ")
#include("sections/6_problem2.typ")
#include("sections/7_problem3.typ")
#include("sections/8_problem4.typ")
#include("sections/9_evaluation.typ")
#include("references.typ")
#include("sections/A_code.typ")
