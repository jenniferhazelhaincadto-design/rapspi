import json
from typing import List, Dict


def build_dispense_payload(recipe: str, batches: int, dry: List[Dict], wet: List[Dict]) -> str:
    payload = {
        "cmd": "dispense",
        "recipe": str(recipe),
        "batches": int(batches),
        "dry": [
            {
                "id": int(item.get("id", 0)),
                "g": int(item.get("g", 0)),
                "steps_per_gram": int(item.get("steps_per_gram", 2)),
            }
            for item in dry
        ],
        "wet": [
            {
                "id": int(item.get("id", 0)),
                "ml": float(item.get("ml", 0)),
                "ms_per_ml": int(item.get("ms_per_ml", 1000)),
            }
            for item in wet
        ],
    }
    return json.dumps(payload) + "\n"


def build_single_dispense_payload(kind: str, cid: int, amount: float) -> str:
    tag = "D" if str(kind).upper().startswith("D") else "W"
    if tag == "D":
        payload = {
            "cmd": "dispense",
            "recipe": "single",
            "batches": 1,
            "dry": [{"id": int(cid), "g": int(amount), "steps_per_gram": 2}],
            "wet": [],
        }
    else:
        payload = {
            "cmd": "dispense",
            "recipe": "single",
            "batches": 1,
            "dry": [],
            "wet": [{"id": int(cid), "ml": float(amount), "ms_per_ml": 1000}],
        }
    return json.dumps(payload) + "\n"


def build_stop_payload() -> str:
    return '{"cmd":"stop"}\n'


def build_clean_payload() -> str:
    return '{"cmd":"clean"}\n'


def build_levels_payload() -> str:
    return '{"cmd":"levels"}\n'
