import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _normalize_data_root(path):
    """Return the directory that directly contains the two official data folders."""
    path = Path(path).expanduser().resolve()
    required = ("无人机应急物资运输基础数据", "镇龙乡地理空间数据")
    for candidate in (path, path / "data", path / "数据"):
        if all((candidate / name).is_dir() for name in required):
            return candidate
    return None


def _candidate_roots():
    configured = os.environ.get("HUAWEI_D_DATA_ROOT")
    if configured:
        yield Path(configured)
    yield PROJECT_ROOT / "data"
    yield PROJECT_ROOT / "数据"
    for ancestor in PROJECT_ROOT.parents:
        yield ancestor / "data"
        yield ancestor / "数据"
        yield ancestor / "赛题" / "D题" / "数据"


def _find_data_root():
    checked = []
    for candidate in _candidate_roots():
        checked.append(str(candidate))
        resolved = _normalize_data_root(candidate)
        if resolved is not None:
            return resolved
    raise FileNotFoundError(
        "未找到D题数据目录。请将数据放在项目附近的 data/ 或 数据/ 目录，"
        "或设置环境变量 HUAWEI_D_DATA_ROOT。已检查：\n- " + "\n- ".join(checked)
    )


def _find_template(data_root):
    configured = os.environ.get("HUAWEI_D_TEMPLATE")
    candidates = []
    if configured:
        candidates.append(Path(configured))
    candidates.extend([
        data_root.parent / "结果提交模板.xlsx",
        PROJECT_ROOT / "结果提交模板.xlsx",
    ])
    for ancestor in PROJECT_ROOT.parents:
        candidates.extend([
            ancestor / "结果提交模板.xlsx",
            ancestor / "赛题" / "D题" / "结果提交模板.xlsx",
        ])
    for candidate in candidates:
        candidate = candidate.expanduser().resolve()
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "未找到结果提交模板.xlsx。请把模板放在数据目录的上一级，"
        "或设置环境变量 HUAWEI_D_TEMPLATE。"
    )


DATA_ROOT = _find_data_root()
OFFICIAL_ROOT = DATA_ROOT.parent
BASE_DATA_ROOT = DATA_ROOT / "无人机应急物资运输基础数据"
GEO_ROOT = DATA_ROOT / "镇龙乡地理空间数据"
GEO_DATA_ROOT = GEO_ROOT / "镇龙乡及周边地理数据"
DEM_MAT = GEO_DATA_ROOT / "数字高程模型数据（DEM）" / "镇龙乡及周边30米DEM.mat"
DEM_TIF = GEO_DATA_ROOT / "数字高程模型数据（DEM）" / "镇龙乡及周边30米DEM.tif"
TEMPLATE_XLSX = _find_template(DATA_ROOT)

OUTPUT_ROOT = Path(os.environ.get("HUAWEI_D_OUTPUT_ROOT", str(PROJECT_ROOT))).expanduser().resolve()
RESULTS_DIR = OUTPUT_ROOT / "results"
FIGURES_DIR = OUTPUT_ROOT / "figures"
LOGS_DIR = OUTPUT_ROOT / "logs"
TMP_DIR = OUTPUT_ROOT / "tmp"
TABLE_DIR = TMP_DIR / "tables"


def ensure_directories():
    for path in (RESULTS_DIR, FIGURES_DIR, LOGS_DIR, TMP_DIR, TABLE_DIR):
        path.mkdir(parents=True, exist_ok=True)


def resolved_input_paths():
    return {"data_root": str(DATA_ROOT), "template_xlsx": str(TEMPLATE_XLSX)}
