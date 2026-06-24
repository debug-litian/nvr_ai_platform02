from utils.logger import get_logger
import json
from config import settings

logger = get_logger("export_report")

def export(index_path=None, out_path="report.json"):
    # simple report of mapping
    mp = settings.MAPPING_FILE
    try:
        with open(mp, 'r', encoding='utf-8') as f:
            data = json.load(f)
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info("Report exported to %s", out_path)
    except Exception:
        logger.exception("Export failed")
