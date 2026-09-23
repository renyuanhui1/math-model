import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const projectRoot = path.dirname(fileURLToPath(import.meta.url));
const artifactRoot = process.env.CODEX_NODE_MODULES;
if (!artifactRoot) throw new Error("CODEX_NODE_MODULES is required");
const { FileBlob, SpreadsheetFile, Workbook } = await import(
  pathToFileURL(path.join(artifactRoot, "@oai", "artifact-tool", "dist", "artifact_tool.mjs")).href
);

const tableDir = process.env.HUAWEI_D_TABLE_DIR || path.join(projectRoot, "tmp", "tables");
const resultDir = process.env.HUAWEI_D_RESULTS_DIR || path.join(projectRoot, "results");
const previewDir = process.env.HUAWEI_D_PREVIEW_DIR || path.join(projectRoot, "tmp", "workbook_previews");
await fs.mkdir(resultDir, { recursive: true });
await fs.mkdir(previewDir, { recursive: true });

function parseCSV(text) {
  text = text.replace(/^\uFEFF/, "");
  const rows = [];
  let row = [], field = "", quoted = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') { field += '"'; i++; }
      else if (ch === '"') quoted = false;
      else field += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ',') { row.push(field); field = ""; }
    else if (ch === '\n') { row.push(field.replace(/\r$/, "")); rows.push(row); row = []; field = ""; }
    else field += ch;
  }
  if (field.length || row.length) { row.push(field.replace(/\r$/, "")); rows.push(row); }
  return rows.filter(r => r.some(v => v !== ""));
}

function typed(value) {
  if (value === "") return null;
  if (value === "True" || value === "true") return true;
  if (value === "False" || value === "false") return false;
  if (/^-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/.test(value)) {
    const number = Number(value);
    if (Number.isFinite(number)) return number;
  }
  return value;
}

async function csvMatrix(name) {
  const text = await fs.readFile(path.join(tableDir, `${name}.csv`), "utf8");
  const rows = parseCSV(text);
  return rows.map((row, i) => i === 0 ? row : row.map(typed));
}

function colName(index) {
  let out = "", n = index + 1;
  while (n) { n--; out = String.fromCharCode(65 + (n % 26)) + out; n = Math.floor(n / 26); }
  return out;
}

function styleSheet(sheet, matrix) {
  const rows = matrix.length, cols = matrix[0].length;
  const used = sheet.getRange(`A1:${colName(cols - 1)}${rows}`);
  used.format.font = { name: "Microsoft YaHei", size: 10, color: "#1F2937" };
  used.format.verticalAlignment = "center";
  used.format.borders = { preset: "all", style: "thin", color: "#D9E2F3" };
  const header = sheet.getRange(`A1:${colName(cols - 1)}1`);
  header.format = {
    fill: "#1F4E78",
    font: { name: "Microsoft YaHei", size: 10, bold: true, color: "#FFFFFF" },
    wrapText: true,
    verticalAlignment: "center",
  };
  header.format.rowHeight = 30;
  if (rows > 1) sheet.getRange(`A2:${colName(cols - 1)}${rows}`).format.rowHeight = 20;
  sheet.freezePanes.freezeRows(1);
  sheet.showGridLines = false;
  for (let c = 0; c < cols; c++) {
    const values = matrix.slice(0, Math.min(rows, 250)).map(r => String(r[c] ?? ""));
    const width = Math.min(42, Math.max(10, ...values.map(v => Math.min(50, v.length * 1.08))));
    sheet.getRange(`${colName(c)}1:${colName(c)}${rows}`).format.columnWidth = width;
    const title = String(matrix[0][c]);
    if (/(s）|kWh|经度|纬度|高程|能耗|质量|体积|距离|时刻|时间)/.test(title) && rows > 1) {
      sheet.getRange(`${colName(c)}2:${colName(c)}${rows}`).format.numberFormat = "0.000";
    } else if (/(SOC|%|余量|系数)/.test(title) && rows > 1) {
      sheet.getRange(`${colName(c)}2:${colName(c)}${rows}`).format.numberFormat = "0.00";
    }
  }
}

async function writeMatrixWorkbook(fileName, sheets) {
  const workbook = Workbook.create();
  for (const item of sheets) {
    const sheet = workbook.worksheets.add(item.sheetName);
    sheet.getRange("A1").write(item.matrix);
    styleSheet(sheet, item.matrix);
  }
  workbook.recalculate();
  const errors = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
    options: { useRegex: true, maxResults: 100 }, summary: "final formula error scan",
  });
  if (errors.ndjson && /#REF!|#DIV\/0!|#VALUE!|#NAME\?|#N\/A|#NUM!|#NULL!|#SPILL!|#CALC!/.test(errors.ndjson)) {
    throw new Error(`formula error in ${fileName}: ${errors.ndjson}`);
  }
  for (const item of sheets) {
    const preview = await workbook.render({ sheetName: item.sheetName, autoCrop: "all", scale: 1, format: "png" });
    await fs.writeFile(path.join(previewDir, `${path.parse(fileName).name}_${item.sheetName}.png`),
                       new Uint8Array(await preview.arrayBuffer()));
  }
  const output = await SpreadsheetFile.exportXlsx(workbook);
  await output.save(path.join(resultDir, fileName));
}

const specs = [
  ["data_inventory.xlsx", [["data_inventory", "数据清单"], ["data_validation", "完整性检查"]]],
  ["segment_database.xlsx", [["segment_database", "航段数据库"]]],
  ["q1_max_safe_payload.xlsx", [["q1_max_safe_payload", "最大安全载荷"]]],
  ["q1_batching.xlsx", [["q1_batching", "单点组批"]]],
  ["q1_safety_margin.xlsx", [["q1_safety_margin", "安全余量敏感性"]]],
  ["q2_transport_sorties.xlsx", [["q2_transport_sorties", "运输架次"]]],
  ["q2_box_delivery.xlsx", [["q2_box_delivery", "逐箱交付"]]],
  ["q2_resource_schedule.xlsx", [["q2_resource_schedule", "资源时序"]]],
  ["q2_summary.xlsx", [["q2_summary", "总结"], ["q2_optimization_history", "优化过程"]]],
  ["q2_feasibility_check.xlsx", [["q2_feasibility_check", "可行性检查"]]],
  ["q3_direct_link_intervals.xlsx", [["q3_direct_link_intervals", "直连区间"]]],
  ["q3_transport_schedule.xlsx", [["q3_transport_schedule", "运输架次"], ["q3_box_delivery", "逐箱交付"], ["q3_resource_schedule", "联合资源时序"]]],
  ["q3_relay_sorties.xlsx", [["q3_relay_sorties", "中继架次"]]],
  ["q3_communication_coverage.xlsx", [["q3_communication_coverage", "通信保障"]]],
  ["q3_joint_summary.xlsx", [["q3_joint_summary", "联合优化总结"], ["q3_optimization_history", "联合搜索过程"], ["q3_feasibility_check", "可行性检查"]]],
  ["q4_partition_K2.xlsx", [["q4_partition_K2", "K2分区"]]],
  ["q4_partition_K3.xlsx", [["q4_partition_K3", "K3分区"]]],
  ["q4_resource_comparison.xlsx", [["q4_resource_comparison", "资源对比"], ["q4_summary", "分区摘要"]]],
];

for (const [fileName, sheetSpecs] of specs) {
  const sheets = [];
  for (const [csvName, sheetName] of sheetSpecs) sheets.push({ sheetName, matrix: await csvMatrix(csvName) });
  await writeMatrixWorkbook(fileName, sheets);
}

function records(matrix) {
  const headers = matrix[0];
  return matrix.slice(1).map(row => Object.fromEntries(headers.map((h, i) => [h, row[i] ?? null])));
}
function selectRows(matrix, columns) {
  return [columns, ...records(matrix).map(obj => columns.map(c => obj[c] ?? null))];
}

const officialTemplate = process.env.HUAWEI_D_TEMPLATE
  ? path.resolve(process.env.HUAWEI_D_TEMPLATE)
  : path.resolve(projectRoot, "..", "..", "..", "赛题", "D题", "结果提交模板.xlsx");
const templateBlob = await FileBlob.load(officialTemplate);
const finalBook = await SpreadsheetFile.importXlsx(templateBlob);
const finalMappings = [
  ["Q1_单点组批", await csvMatrix("q1_batching"), ["架次编号", "服务区编号", "机型编号", "货箱编号列表", "总质量（kg）", "总体积（m³）", "往返时间（s）", "架次能耗（kWh）", "返航SOC（%）"]],
  ["Q2_运输架次", await csvMatrix("q2_transport_sorties"), ["架次编号", "无人机编号", "机型编号", "电池编号", "开始时刻（s）", "访问服务区顺序", "返回O01时刻（s）", "架次能耗（kWh）"]],
  ["Q2_逐箱交付", await csvMatrix("q2_box_delivery"), ["货箱编号", "架次编号", "服务区编号", "交付完成时刻（s）"]],
  ["Q3_中继架次", await csvMatrix("q3_relay_sorties"), ["中继架次编号", "中继无人机编号", "能源组件编号", "开始时刻（s）", "悬停经度（°）", "悬停纬度（°）", "悬停海拔（m）", "建链完成时刻（s）", "服务结束时刻（s）", "返回O01时刻（s）", "架次能耗（kWh）"]],
  ["Q3_通信保障", await csvMatrix("q3_communication_coverage"), ["运输架次编号", "通信阶段", "开始时刻（s）", "结束时刻（s）", "保障方式", "中继架次编号"]],
];
const k2 = await csvMatrix("q4_partition_K2"), k3 = await csvMatrix("q4_partition_K3");
const q4cols = ["K（2或3）", "任务组编号", "服务区列表", "A型运输无人机数", "B型运输无人机数", "C型运输无人机数", "A型电池组数", "B型电池组数", "C型电池组数", "中继无人机数", "中继能源组件数"];
const q4matrix = [q4cols, ...selectRows(k2, q4cols).slice(1), ...selectRows(k3, q4cols).slice(1)];
finalMappings.push(["Q4_分区配置", q4matrix, q4cols]);

for (const [sheetName, source, columns] of finalMappings) {
  const matrix = source === q4matrix ? q4matrix : selectRows(source, columns);
  const sheet = finalBook.worksheets.getItem(sheetName);
  const existing = sheet.getUsedRange();
  if (existing) existing.clear({ applyTo: "contents" });
  sheet.getRange("A1").write(matrix);
  styleSheet(sheet, matrix);
}
finalBook.recalculate();
const finalErrors = await finalBook.inspect({ kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 100 }, summary: "final submission formula error scan" });
if (finalErrors.ndjson && /#REF!|#DIV\/0!|#VALUE!|#NAME\?|#N\/A|#NUM!|#NULL!|#SPILL!|#CALC!/.test(finalErrors.ndjson)) {
  throw new Error(finalErrors.ndjson);
}
for (const [sheetName] of finalMappings) {
  const preview = await finalBook.render({ sheetName, autoCrop: "all", scale: 1, format: "png" });
  await fs.writeFile(path.join(previewDir, `最终结果提交_${sheetName}.png`),
                     new Uint8Array(await preview.arrayBuffer()));
}
const finalOutput = await SpreadsheetFile.exportXlsx(finalBook);
await finalOutput.save(path.join(resultDir, "最终结果提交.xlsx"));

console.log(`exported ${specs.length + 1} workbooks`);
