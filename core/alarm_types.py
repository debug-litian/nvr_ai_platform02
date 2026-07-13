"""
alarm_types.py — Reolink 报警类型 → YOLO COCO 类别映射

本模块是 FTP 报警核验的单一数据源（Single Source of Truth），
所有其他模块都通过此模块查询报警类型映射。

映射关系：
- Reolink "人形"    → COCO person (0)
- Reolink "机动车"  → COCO car(2), motorcycle(3), bus(5), truck(7)
- Reolink "宠物"    → COCO cat(15), dog(16)
- Reolink "画面变动" → 无 YOLO 检测（纯 NVR 触发，不可 AI 核验）
"""

from typing import List, Dict, Optional

# ── 报警类型完整定义 ──────────────────────────────────
# key:   内部标识符
# value: { dirs, coco_ids, label_cn }
# dirs:  可能的 FTP 子目录名列表（支持多种命名约定）

ALARM_TYPE_MAP: Dict[str, dict] = {
    "human": {
        "dirs": ["human", "person"],       # Reolink 默认 "person"，也可以改成 "human"
        "coco_ids": [0],                     # person
        "label_cn": "人形",
    },
    "vehicle": {
        "dirs": ["vehicle", "car"],
        "coco_ids": [2, 3, 5, 7],           # car, motorcycle, bus, truck
        "label_cn": "机动车",
    },
    "pet": {
        "dirs": ["pet", "animal"],
        "coco_ids": [15, 16],                # cat, dog
        "label_cn": "宠物",
    },
    "motion": {
        "dirs": ["motion", "move"],
        "coco_ids": [],                      # 画面变动 → 无 YOLO 核验
        "label_cn": "画面变动",
    },
}

# ── COCO class ID → 中文名 ───────────────────────────
COCO_CN_NAMES: Dict[int, str] = {
    0:  "人",
    1:  "自行车",
    2:  "汽车",
    3:  "摩托车",
    5:  "公交车",
    7:  "卡车",
    14: "鸟",
    15: "猫",
    16: "狗",
    25: "雨伞",
    39: "瓶子",
    41: "杯子",
    56: "椅子",
    63: "笔记本",
    64: "鼠标",
    67: "手机",
}

# ── COCO class ID → 英文名 ───────────────────────────
COCO_EN_NAMES: Dict[int, str] = {
    0: "person", 1: "bicycle", 2: "car", 3: "motorcycle",
    5: "bus", 7: "truck", 14: "bird", 15: "cat", 16: "dog",
    25: "umbrella", 39: "bottle", 41: "cup",
    56: "chair", 63: "laptop", 64: "mouse", 67: "cell phone",
}


def get_alarm_types() -> List[str]:
    """返回所有报警类型标识符列表"""
    return list(ALARM_TYPE_MAP.keys())


def get_friendly_name(alarm_type: str) -> str:
    """返回报警类型的中文名"""
    entry = ALARM_TYPE_MAP.get(alarm_type)
    return entry["label_cn"] if entry else alarm_type


def get_expected_coco_ids(alarm_type: str) -> List[int]:
    """
    返回某报警类型期望检测到的 COCO class ID 列表。

    例: get_expected_coco_ids("human") → [0]
         get_expected_coco_ids("motion") → []  (不可核验)
    """
    entry = ALARM_TYPE_MAP.get(alarm_type)
    return entry["coco_ids"].copy() if entry else []


def is_yolo_verifiable(alarm_type: str) -> bool:
    """该报警类型能否由 YOLO 核验？画面变动不可核验。"""
    return len(get_expected_coco_ids(alarm_type)) > 0


def get_coco_cn_name(class_id: int) -> str:
    """COCO class ID → 中文名"""
    return COCO_CN_NAMES.get(class_id, f"类别{class_id}")


def get_coco_en_name(class_id: int) -> str:
    """COCO class ID → 英文名"""
    return COCO_EN_NAMES.get(class_id, f"cls_{class_id}")


def get_alarm_type_by_dir(subdir_name: str) -> Optional[str]:
    """从 FTP 子目录名反查报警类型标识符（支持多种命名约定）"""
    for key, entry in ALARM_TYPE_MAP.items():
        if subdir_name in entry["dirs"]:
            return key
    # 日期目录名（如 2026-07-13）不匹配任何报警类型
    return None
