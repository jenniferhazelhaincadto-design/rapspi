from typing import List, Dict


def build_dispense_payload(recipe: str, batches: int, dry: List[Dict], wet: List[Dict]) -> str:
    items = []
    for item in dry:
        items.append(f"D,{int(item.get('id', 0))},{int(item.get('g', 0))}")
    for item in wet:
        ml = item.get("ml", 0)
        items.append(f"W,{int(item.get('id', 0))},{float(ml)}")
    if items:
        return f"MIX,{recipe},{int(batches)},{';'.join(items)}\n"
    return f"MIX,{recipe},{int(batches)}\n"


def build_single_dispense_payload(kind: str, cid: int, amount: float) -> str:
    tag = "D" if str(kind).upper().startswith("D") else "W"
    if tag == "D":
        return f"DISPENSE,D,{int(cid)},{int(amount)}\n"
    return f"DISPENSE,W,{int(cid)},{float(amount)}\n"


def build_stop_payload() -> str:
    return "STOP\n"


def build_clean_payload() -> str:
    return "CLEAN\n"


def build_levels_payload() -> str:
    return "LEVELS\n"
