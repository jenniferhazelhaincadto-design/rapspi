import json
from typing import List, Dict


def build_dispense_payload(dry: List[Dict], wet: List[Dict]) -> str:
    """Build JSON dispense command payload."""
    payload = {
        "cmd": "dispense",
        "dry": dry,
        "wet": wet
    }
    return json.dumps(payload) + "\n"


def build_single_dispense_payload(kind: str, cid: int, amount: float) -> str:
    """Build JSON single dispense command payload."""
    is_dry = str(kind).upper().startswith("D")
    if is_dry:
        payload = {
            "cmd": "dispense",
            "dry": [{"id": int(cid), "g": int(amount), "steps_per_gram": 2}],
            "wet": []
        }
    else:
        payload = {
            "cmd": "dispense",
            "dry": [],
            "wet": [{"id": int(cid), "ml": float(amount), "ms_per_ml": 100}]
        }
    return json.dumps(payload) + "\n"


def build_stop_payload() -> str:
    """Build JSON stop command payload."""
    payload = {"cmd": "stop"}
    return json.dumps(payload) + "\n"


def build_clean_payload() -> str:
    """Build JSON clean command payload."""
    payload = {"cmd": "clean"}
    return json.dumps(payload) + "\n"


def build_levels_payload() -> str:
    """Build JSON levels command payload."""
    payload = {"cmd": "levels"}
    return json.dumps(payload) + "\n"
