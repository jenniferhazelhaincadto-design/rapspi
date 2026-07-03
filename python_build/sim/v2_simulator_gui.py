
import argparse
import importlib.util
import json
import os
import queue
import re
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import Tk, Canvas, Entry, Button, PhotoImage, Label, Scrollbar, Frame, StringVar, END, ttk
from typing import Optional

try:
from serial.tools import list_ports
except Exception:  # pragma: no cover
list_ports = None

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

_CONFIG_PATH = ROOT_DIR / "config.py"
if not _CONFIG_PATH.exists():
raise ImportError(f"Local config file not found at {_CONFIG_PATH}")

_config_spec = importlib.util.spec_from_file_location("condimix_local_config", _CONFIG_PATH)
if _config_spec is None or _config_spec.loader is None:
raise ImportError(f"Unable to load config module from {_CONFIG_PATH}")

_config_module = importlib.util.module_from_spec(_config_spec)
_config_spec.loader.exec_module(_config_module)

try:
DB_PATH = _config_module.DB_PATH
VOICE_MODEL = _config_module.VOICE_MODEL
VOICE_COMPUTE_TYPE = _config_module.VOICE_COMPUTE_TYPE
except AttributeError as exc:
raise ImportError(
"Local config.py is missing one of: DB_PATH, VOICE_MODEL, VOICE_COMPUTE_TYPE"
) from exc

from services import db
from services import protocol as protocol
from services.serial_link import SerialLink

build_dispense_payload = protocol.build_dispense_payload
build_clean_payload = protocol.build_clean_payload
build_stop_payload = protocol.build_stop_payload
build_levels_payload = protocol.build_levels_payload


def _build_single_dispense_payload_fallback(
kind: str,
ingredient_id: int,
amount: int,
ms_per_ml: int = 1000,
steps_per_gram: int = 2,
) -> str:
kind_norm = (kind or "").strip().upper()
if kind_norm == "D":
dry = [{"id": int(ingredient_id), "g": int(amount), "steps_per_gram": int(steps_per_gram)}]
wet = []
elif kind_norm == "W":
dry = []
wet = [{"id": int(ingredient_id), "ml": int(amount), "ms_per_ml": int(ms_per_ml)}]
else:
raise ValueError("kind must be 'D' or 'W'")
payload = {
"cmd": "dispense",
"recipe": "single",
"batches": 1,
"dry": dry,
"wet": wet,
}
return json.dumps(payload) + "\n"


build_single_dispense_payload = getattr(
protocol,
"build_single_dispense_payload",
_build_single_dispense_payload_fallback,
)


def _legacy_to_json_payload(payload: str) -> str:
text = (payload or "").strip()
if not text:
return payload
if text.startswith("{"):
return payload if payload.endswith("\n") else payload + "\n"

upper = text.upper()
if upper == "STOP":
return '{"cmd":"stop"}\n'
if upper == "CLEAN":
return '{"cmd":"clean"}\n'
if upper == "LEVELS":
return '{"cmd":"levels"}\n'

parts = [p.strip() for p in text.split(",") if p.strip()]
if len(parts) >= 4 and parts[0].upper() == "DISPENSE":
kind = parts[1].upper()
try:
cid = int(parts[2])
amount = int(float(parts[3]))
except ValueError:
return payload if payload.endswith("\n") else payload + "\n"
return _build_single_dispense_payload_fallback(kind, cid, amount)

if text.upper().startswith("MIX,"):
parts = text.split(",", 3)
if len(parts) < 4:
return payload if payload.endswith("\n") else payload + "\n"

recipe = parts[1].strip() or "mix"
try:
batches = int(parts[2].strip())
except ValueError:
batches = 1

dry = []
wet = []
for chunk in parts[3].split(";"):
fields = [f.strip() for f in chunk.split(",") if f.strip()]
if len(fields) < 3:
continue
kind = fields[0].upper()
try:
cid = int(fields[1])
amount = float(fields[2])
except ValueError:
continue
if kind == "D":
dry.append({"id": cid, "g": int(amount)})
elif kind == "W":
wet.append({"id": cid, "ml": float(amount), "ms_per_ml": 1000})

payload_json = {
"cmd": "dispense",
"recipe": recipe,
"batches": batches,
"dry": dry,
"wet": wet,
}
return json.dumps(payload_json) + "\n"

return payload if payload.endswith("\n") else payload + "\n"

def _resolve_assets_root() -> Path:
env_override = os.getenv("CONDIMIX_ASSETS_ROOT", "").strip()
if env_override:
p = Path(env_override).expanduser().resolve()
if p.exists():
return p

# Prefer local simulator assets if present (common on Raspberry Pi installs).
local_assets = Path(__file__).resolve().parent / "assets"
if local_assets.exists():
return local_assets

# Fallback to repository-level build assets.
build_assets = Path(__file__).resolve().parents[3] / "build2" / "assets"
return build_assets


ASSETS_ROOT = _resolve_assets_root()

VOICE_SAMPLE_RATE = 16000
VOICE_BLOCK_SIZE = 1024
VOICE_MAX_CHUNK_SEC = 6
VOICE_MIN_CHUNK_SEC = 2
VOICE_SILENCE_SEC = 0.7
VOICE_SILENCE_RMS = 0.008
VOICE_OVERLAP_SEC = 0.5
VOICE_FORCED_FLUSH_SEC = 2.5
VOICE_PREFERRED_MIC = "USB PnP Sound Device"


class SimulatorGUI:
_place_hook_installed = False
_orig_place = None

def __init__(self, port: Optional[str], baud: Optional[int]) -> None:
db.init_db(DB_PATH)
self.root = Tk()
self.root.title("CONDIMIX v2 Simulator")
self.root.geometry("480x800+0+0")
self.root.configure(bg="#eef0ed")
self.root.resizable(False, False)
self._kiosk_mode = True
self.root.bind("<Escape>", self._exit_kiosk)
self.root.bind("<F11>", self._toggle_kiosk)
# Apply after window creation so VNC/GNOME honors borderless fullscreen reliably.
self.root.after(50, self._apply_kiosk_mode)

# The UI was designed for 800px height. On GNOME with a top bar, available
# space is smaller, so compress vertical placements to keep bottom controls visible.
self.design_height = 800
self.top_bar_reserved_px = int(os.getenv("CONDIMIX_TOP_BAR_PX", "52"))
self.bottom_reserved_px = int(os.getenv("CONDIMIX_BOTTOM_PX", "10"))
self.screen_height = self.root.winfo_screenheight()
usable_height = max(1, self.screen_height - self.top_bar_reserved_px - self.bottom_reserved_px)
self.ui_scale_y = min(1.0, usable_height / float(self.design_height))
self._install_place_scaling_hook()

self.btn_bg = "#FFFF8F"
self.btn_fg = "#30071F"

saved_port = db.get_setting(DB_PATH, "serial_port", "").strip()
saved_baud_text = db.get_setting(DB_PATH, "serial_baud", "9600").strip()
try:
saved_baud = int(saved_baud_text)
except ValueError:
saved_baud = 9600

self.serial_port = port or saved_port or None
self.serial_baud = baud or saved_baud or 9600
self.serial = SerialLink(self.serial_port, self.serial_baud, simulate=False) if self.serial_port else None
self.serial_debug = os.getenv("CONDIMIX_SERIAL_DEBUG", "1").strip() != "0"

self._debug(
f"Startup serial config: port={self.serial_port or 'None'} baud={self.serial_baud}"
)

self._images = {}
self._bg_image = None

self.active_recipe_id = None
self.batch_count = 1

self._keyboard_frame = None
self._keyboard_var = None
self._keyboard_submit = None

self.voice_running = False
self.voice_state = "idle"
self.voice_queue = queue.Queue()
self.voice_model = None
self.voice_stream = None
self.voice_input_rate = VOICE_SAMPLE_RATE
self.voice_input_device_name = ""
self.voice_buffer = []
self.voice_pending_action = None
self._voice_worker_thread = None
self.voice_status_label = None
self.voice_transcript_label = None
self.voice_anim_label = None
self.voice_debug_label = None

if self.serial is not None:
try:
self.serial.open()
except Exception as exc:
self._debug(f"Startup connect failed: {exc}")
self.serial.close()
self.serial = None

self.show_dashboard()

def _install_place_scaling_hook(self) -> None:
if SimulatorGUI._place_hook_installed:
return

SimulatorGUI._orig_place = tk.Widget.place

def _scaled_place(widget, cnf=None, **kw):
cfg = dict(kw)
root = widget.winfo_toplevel()
scale_y = getattr(root, "_condimix_ui_scale_y", 1.0)
max_h = getattr(root, "_condimix_ui_usable_height", None)

if scale_y < 1.0 and "y" in cfg and "rely" not in cfg:
try:
cfg["y"] = int(float(cfg["y"]) * scale_y)
except Exception:
pass

if scale_y < 1.0 and "height" in cfg and "relheight" not in cfg:
try:
cfg["height"] = max(1, int(float(cfg["height"]) * scale_y))
except Exception:
pass

# Keep controls visible at the bottom edge (GNOME top bar + panel cases).
if max_h and "y" in cfg and "height" in cfg and "rely" not in cfg and "relheight" not in cfg:
try:
y = int(float(cfg["y"]))
h = int(float(cfg["height"]))
if y + h > max_h:
cfg["y"] = max(0, max_h - h)
except Exception:
pass

if cnf is None:
return SimulatorGUI._orig_place(widget, **cfg)
return SimulatorGUI._orig_place(widget, cnf, **cfg)

tk.Widget.place = _scaled_place
SimulatorGUI._place_hook_installed = True
setattr(self.root, "_condimix_ui_scale_y", self.ui_scale_y)
setattr(self.root, "_condimix_ui_usable_height", max(1, self.screen_height - self.top_bar_reserved_px - self.bottom_reserved_px))

def _apply_kiosk_mode(self, _event=None) -> None:
self._kiosk_mode = True
sw = self.root.winfo_screenwidth()
sh = self.root.winfo_screenheight()
self.root.overrideredirect(True)
self.root.geometry(f"{sw}x{sh}+0+0")
self.root.attributes("-topmost", True)
self.root.attributes("-fullscreen", True)
self.root.lift()
self.root.focus_force()

def _exit_kiosk(self, _event=None) -> None:
self._kiosk_mode = False
self.root.attributes("-fullscreen", False)
self.root.overrideredirect(False)
self.root.attributes("-topmost", False)

def _toggle_kiosk(self, _event=None) -> None:
if self._kiosk_mode:
self._exit_kiosk()
else:
self._apply_kiosk_mode()

def _load_image(self, asset_rel: str):
if asset_rel in self._images:
return self._images[asset_rel]
path = ASSETS_ROOT / asset_rel
if not path.exists():
self._debug(f"Missing asset: {path}")
return None
try:
img = PhotoImage(file=str(path))
except Exception:
return None
self._images[asset_rel] = img
return img

def _set_background(self, asset_rel: str):
img = self._load_image(asset_rel)
if not img:
self.root.configure(bg="#eef0ed")
return
self._bg_image = img
bg = Label(self.root, image=img)
bg.image = img
bg.place(x=0, y=0, relwidth=1, relheight=1)

def clear(self):
for w in self.root.winfo_children():
w.destroy()

def _format_status(self, status: str) -> str:
pretty = {
"STATUS:OK": "Success",
"STATUS:STOPPED": "Stopped",
"STATUS:EMERGENCY": "Emergency Stop",
"STATUS:ERROR": "Error",
"STATUS:TIMEOUT": "Timeout",
}
return pretty.get(status.strip(), status)

def _debug(self, message: str) -> None:
if not self.serial_debug:
return
print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)

def _serial_read_line_safe(self) -> Optional[str]:
if not self.serial:
return None
try:
return self.serial.read_line()
except Exception as exc:
self._debug(f"Serial read error: {exc}")
self._serial_read_failed = True
try:
self.serial.close()
except Exception:
pass
return None

def _serial_send_wait_status(self, payload: str, timeout: float = 12.0) -> str:
if not self.serial:
return "STATUS:ERROR"
self._serial_read_failed = False
payload = _legacy_to_json_payload(payload)
self._debug(f"TX ({self.serial.port} @ {self.serial.baud}): {payload.strip()}")
try:
self.serial.send(payload)
except Exception as exc:
self._debug(f"Serial send error: {exc}")
return "STATUS:ERROR"

start = time.time()
while time.time() - start < timeout:
line = self._serial_read_line_safe()
if self._serial_read_failed:
return "STATUS:ERROR"
if not line:
continue
self._debug(f"RX: {line}")
if line.startswith("STATUS:"):
return line
self._debug("RX timeout waiting for STATUS line")
return "STATUS:TIMEOUT"

def _serial_send_wait_levels(self, payload: str, timeout: float = 10.0):
if not self.serial:
return None
self._serial_read_failed = False
payload = _legacy_to_json_payload(payload)
self._debug(f"TX ({self.serial.port} @ {self.serial.baud}): {payload.strip()}")
try:
self.serial.send(payload)
except Exception as exc:
self._debug(f"Serial send error: {exc}")
return None

start = time.time()
while time.time() - start < timeout:
line = self._serial_read_line_safe()
if self._serial_read_failed:
return None
if not line:
continue
self._debug(f"RX: {line}")

if line.startswith("STATUS:"):
continue

if line.startswith("{"):
try:
data = json.loads(line)
except Exception:
continue
if isinstance(data, dict) and data.get("type") == "levels":
return data

if line.startswith("LEVELS,"):
items = []
parts = line[len("LEVELS,"):].split(";")
for part in parts:
fields = [f.strip() for f in part.split(",") if f.strip()]
if len(fields) < 3 or fields[0].upper() != "D":
continue
try:
cid = int(fields[1])
grams = int(float(fields[2]))
except ValueError:
continue
items.append({"id": cid, "g": grams})
return {"type": "levels", "dry": items}

self._debug("RX timeout waiting for levels data")
return None

def _serial_send_wait_ir(self, timeout: float = 1.2) -> Optional[bool]:
if not self.serial:
return None
self._serial_read_failed = False
payload = _legacy_to_json_payload('{"cmd":"ir"}\n')
self._debug(f"TX ({self.serial.port} @ {self.serial.baud}): {payload.strip()}")
try:
self.serial.send(payload)
except Exception as exc:
self._debug(f"Serial send error: {exc}")
return None

start = time.time()
while time.time() - start < timeout:
line = self._serial_read_line_safe()
if self._serial_read_failed:
return None
if not line:
continue
self._debug(f"RX: {line}")
if line.startswith("STATUS:"):
continue
if not line.startswith("{"):
continue
try:
data = json.loads(line)
except Exception:
continue
if not isinstance(data, dict) or data.get("type") != "ir":
continue
detected = data.get("detected")
if isinstance(detected, bool):
return detected
raw = data.get("raw")
if raw in (0, 1):
return int(raw) == 0

self._debug("RX timeout waiting for IR data")
return None

def show_container_not_detected(self) -> None:
self.clear()
self._set_background("dashboard/image_1.png")
Label(self.root, text="Container Not Detected", font=("Quicksand", 22, "bold")).place(x=0, y=220, width=480, height=60)
Button(self.root, text="Back", font=("Quicksand", 18, "bold"), command=self.show_dashboard).place(x=140, y=700, width=200, height=70)

def _guard_container_detected(self, action) -> None:
detected = self._serial_send_wait_ir()
if detected is True:
if detected is False:
            self.show_container_not_detected()
            return

        if detected is None:
            self._debug("IR check unavailable; continuing without container guard")

        if detected is True or detected is None:
action()
return
self.show_container_not_detected()

def _serial_send_no_wait(self, payload: str) -> None:
if not self.serial:
return
payload = _legacy_to_json_payload(payload)
self._debug(f"TX ({self.serial.port} @ {self.serial.baud}): {payload.strip()}")
try:
self.serial.send(payload)
except Exception as exc:
self._debug(f"Serial send error: {exc}")
try:
self.serial.close()
except Exception:
pass

def _ensure_serial(self, status_var: Optional[StringVar] = None) -> bool:
if self.serial is not None:
return True
if status_var is not None:
status_var.set("No serial connection. Open Device Setup.")
else:
self.show_device_setup(initial=True)
return False

def _step_int_var(self, var, delta, min_val=0, max_val=9999):
try:
val = int(var.get())
except ValueError:
val = min_val
val = max(min_val, min(max_val, val + delta))
var.set(str(val))

def _open_keyboard(self, title: str, initial: str, on_submit):
if self._keyboard_frame:
self._keyboard_frame.destroy()
self._keyboard_submit = on_submit
self._keyboard_var = StringVar(value=initial.upper())
frame = Frame(self.root, bg="#f2e6d8", highlightbackground="#d9c3a7", highlightthickness=1)
frame.place(x=20, y=140, width=440, height=520)

Label(frame, text=title, font=("Quicksand", 16, "bold"), bg="#f2e6d8", fg="#3d405b").place(x=20, y=20)
display = Label(frame, textvariable=self._keyboard_var, font=("Quicksand", 18, "bold"), bg="white", fg="#3d405b", anchor="w")
display.place(x=20, y=60, width=400, height=40)

keys = [
list("QWERTYUIOP"),
list("ASDFGHJKL"),
list("ZXCVBNM"),
]

y = 120
for row in keys:
x = 20
for ch in row:
Button(frame, text=ch, font=("Quicksand", 14, "bold"), bg="#fff7ee", fg="#3d405b",
command=lambda c=ch: self._keyboard_var.set(self._keyboard_var.get() + c)).place(x=x, y=y, width=36, height=36)
x += 38
y += 46

Button(frame, text="Space", font=("Quicksand", 12, "bold"), bg="#f2cc8f",
command=lambda: self._keyboard_var.set(self._keyboard_var.get() + " ")).place(x=20, y=260, width=200, height=36)
Button(frame, text="Back", font=("Quicksand", 12, "bold"), bg="#f2cc8f",
command=lambda: self._keyboard_var.set(self._keyboard_var.get()[:-1])).place(x=230, y=260, width=90, height=36)
Button(frame, text="Clear", font=("Quicksand", 12, "bold"), bg="#f2cc8f",
command=lambda: self._keyboard_var.set("")).place(x=330, y=260, width=90, height=36)

def done():
value = self._keyboard_var.get().strip().upper()
frame.destroy()
self._keyboard_frame = None
if self._keyboard_submit:
self._keyboard_submit(value)

Button(frame, text="Done", font=("Quicksand", 14, "bold"), bg="#81b29a", fg="white",
command=done).place(x=120, y=320, width=200, height=40)

self._keyboard_frame = frame

def show_dashboard(self):
self.clear()
self._set_background("dashboard/image_1.png")

btn_bg = "#FFFF8F"
fg = "#30071F"

left_x = 40
right_x = 260
top_y = 80
btn_w = 180
btn_h = 120
row_gap = 20

Button(self.root, bg=btn_bg, fg=fg, text="Recipe\nMenu", font=("Quicksand", 18, "bold"),
command=lambda: self._guard_container_detected(self.show_recipe_menu)).place(x=left_x, y=top_y, width=btn_w, height=btn_h)
Button(self.root, bg=btn_bg, fg=fg, text="Dispense\nIngredient", font=("Quicksand", 18, "bold"),
command=lambda: self._guard_container_detected(self.show_single_dispense)).place(x=right_x, y=top_y, width=btn_w, height=btn_h)

row2_y = top_y + btn_h + row_gap
Button(self.root, bg=btn_bg, fg=fg, text="Cleaning\nMode", font=("Quicksand", 18, "bold"),
command=self.show_cleaning).place(x=left_x, y=row2_y, width=btn_w, height=btn_h)
Button(self.root, bg=btn_bg, fg=fg, text="Ingredient\nLevel", font=("Quicksand", 18, "bold"),
command=self.show_levels).place(x=right_x, y=row2_y, width=btn_w, height=btn_h)

row3_y = row2_y + btn_h + row_gap
Button(self.root, bg=btn_bg, fg=fg, text="Dispensing\nLog", font=("Quicksand", 18, "bold"),
command=self.show_logs).place(x=left_x, y=row3_y, width=btn_w, height=btn_h)
Button(self.root, bg=btn_bg, fg=fg, text="Settings", font=("Quicksand", 18, "bold"),
command=self.show_settings).place(x=right_x, y=row3_y, width=btn_w, height=btn_h)

row4_y = row3_y + btn_h + row_gap
Button(self.root, bg=btn_bg, fg=fg, text="Power\nOff", font=("Quicksand", 18, "bold"),
command=self.show_shutdown).place(x=left_x, y=row4_y, width=btn_w, height=btn_h)
Button(self.root, bg=btn_bg, fg=fg, text="Lock", font=("Quicksand", 18, "bold"),
command=self.show_lock).place(x=right_x, y=row4_y, width=btn_w, height=btn_h)

Button(self.root, bg=btn_bg, fg=fg, text="Voice PTT", font=("Quicksand", 18, "bold"),
command=lambda: self._guard_container_detected(self.show_voice)).place(x=150, y=row4_y + btn_h + 20, width=btn_w, height=btn_h)

def show_recipe_menu(self):
self.clear()
self._set_background("menu/button_7.png")
Label(self.root, text="Select Recipe", font=("Quicksand", 22, "bold")).place(x=0, y=20, width=480)
recipes = db.list_recipes(DB_PATH)

wrapper = Frame(self.root)
wrapper.place(x=20, y=80, width=440, height=600)
canvas = Canvas(wrapper)
canvas.pack(side="left", fill="both", expand=True)
scrollbar = Scrollbar(wrapper, orient="vertical", command=canvas.yview)
scrollbar.pack(side="right", fill="y")
canvas.configure(yscrollcommand=scrollbar.set)
frame = Frame(canvas)
canvas.create_window((0, 0), window=frame, anchor="nw")

def on_configure(_event):
canvas.configure(scrollregion=canvas.bbox("all"))

frame.bind("<Configure>", on_configure)

for rec in recipes:
Button(frame, text=rec.name, font=("Quicksand", 19, "bold"), bg="#FFFF8F",
command=lambda rid=rec.rid: self.show_recipe_detail(rid)).pack(pady=10, padx=10, fill="x", ipady=8)

Button(self.root, text="Back", font=("Quicksand", 18, "bold"), command=self.show_dashboard).place(x=140, y=700, width=200, height=70)

def show_recipe_detail(self, recipe_id: int) -> None:
self.clear()
self._set_background("recipe/image_2.png")
recipe, dry, wet = db.get_recipe_detail(DB_PATH, recipe_id)
self.active_recipe_id = recipe_id
Label(self.root, text=recipe[1], font=("Quicksand", 24, "bold")).place(x=0, y=20, width=480)

dry_names = {c.cid: c.name for c in db.get_dry_containers(DB_PATH)}
wet_names = {c.cid: c.name for c in db.get_wet_containers(DB_PATH)}

self.batch_count = 1
batch_label = Label(self.root, text=str(self.batch_count), font=("Quicksand", 28, "bold"))
batch_label.place(x=200, y=110, width=80, height=50)

def change(delta: int):
self.batch_count = max(1, min(10, self.batch_count + delta))
batch_label.config(text=str(self.batch_count))

Button(self.root, text="-", font=("Quicksand", 20, "bold"), command=lambda: change(-1)).place(x=120, y=110, width=60, height=50)
Button(self.root, text="+", font=("Quicksand", 20, "bold"), command=lambda: change(1)).place(x=300, y=110, width=60, height=50)

y = 200
for dry_id, grams in dry:
dry_name = dry_names.get(dry_id, f"Dry {dry_id}")
Label(self.root, text=f"{dry_name}: {grams} g", font=("Quicksand", 14)).place(x=40, y=y, width=400, height=25)
y += 28
for wet_id, ml in wet:
wet_name = wet_names.get(wet_id, f"Wet {wet_id}")
Label(self.root, text=f"{wet_name}: {ml} ml", font=("Quicksand", 14)).place(x=40, y=y, width=400, height=25)
y += 28

Button(self.root, text="Back", font=("Quicksand", 18, "bold"), command=self.show_recipe_menu).place(x=40, y=700, width=180, height=70)
Button(self.root, text="Dispense", font=("Quicksand", 18, "bold"), command=self.start_dispense).place(x=260, y=700, width=180, height=70)

def start_dispense(self) -> None:
if not self._ensure_serial():
return
recipe, dry, wet = db.get_recipe_detail(DB_PATH, self.active_recipe_id)
dry_containers = {c.cid: c.steps_per_gram for c in db.get_dry_containers(DB_PATH)}
dry_list = [{"id": d[0], "g": d[1] * self.batch_count, "steps_per_gram": dry_containers.get(d[0], 2)} for d in dry]
wet_containers = {c.cid: c.ms_per_ml for c in db.get_wet_containers(DB_PATH)}
wet_list = [{"id": w[0], "ml": w[1] * self.batch_count, "ms_per_ml": wet_containers.get(w[0], 100)} for w in wet]

payload = build_dispense_payload(recipe[1], self.batch_count, dry_list, wet_list)
        wet_seconds = sum((float(item.get("ml") or 0.0) * float(item.get("ms_per_ml") or 0.0)) for item in wet_list) / 1000.0
        timeout_s = max(30.0, wet_seconds + (180.0 if dry_list else 15.0))
        status = self._serial_send_wait_status(payload, timeout=timeout_s)
if status == "STATUS:OK":
used_dry = [(item["id"], item["g"]) for item in dry_list]
db.apply_dry_dispense(DB_PATH, used_dry)
used = [(item["id"], item["ml"]) for item in wet_list]
db.apply_wet_dispense(DB_PATH, used)
db.log_dispense(DB_PATH, time.strftime("%Y-%m-%d %H:%M:%S"), recipe[1], self.batch_count, self._format_status(status))
self.show_voice() if self.voice_running else self.show_dashboard()

def show_settings(self) -> None:
self.clear()
self._set_background("dashboard/image_1.png")
Label(self.root, text="Enter PIN", font=("Quicksand", 24, "bold")).place(x=0, y=30, width=480)

pin_var = StringVar(value="")
masked_var = StringVar(value="")
status_var = StringVar(value="")

display = Label(self.root, textvariable=masked_var, font=("Quicksand", 28, "bold"), bg="white")
display.place(x=60, y=100, width=360, height=60)
Label(self.root, textvariable=status_var, font=("Quicksand", 14)).place(x=40, y=170, width=400, height=30)

def update_masked() -> None:
masked_var.set("*" * len(pin_var.get()))

def add_digit(digit: str) -> None:
value = pin_var.get()
if len(value) >= 8:
return
pin_var.set(value + digit)
update_masked()

def backspace() -> None:
pin_var.set(pin_var.get()[:-1])
update_masked()

def clear() -> None:
pin_var.set("")
update_masked()

def submit() -> None:
saved = db.get_setting(DB_PATH, "security_key", "1234")
if pin_var.get() != saved:
status_var.set("Incorrect PIN")
return
self.show_settings_panel()

btn_w = 120
btn_h = 75
start_x = 50
start_y = 220
gap_x = 10
gap_y = 10

digits = ["1", "2", "3", "4", "5", "6", "7", "8", "9"]
idx = 0
for row in range(3):
for col in range(3):
d = digits[idx]
Button(self.root, text=d, font=("Quicksand", 22, "bold"), bg=self.btn_bg, fg=self.btn_fg,
command=lambda v=d: add_digit(v)).place(
x=start_x + col * (btn_w + gap_x),
y=start_y + row * (btn_h + gap_y),
width=btn_w,
height=btn_h,
)
idx += 1

Button(self.root, text="Clear", font=("Quicksand", 18, "bold"), bg=self.btn_bg, fg=self.btn_fg, command=clear).place(
x=start_x, y=start_y + 3 * (btn_h + gap_y), width=btn_w, height=btn_h
)
Button(self.root, text="0", font=("Quicksand", 22, "bold"), bg=self.btn_bg, fg=self.btn_fg,
command=lambda: add_digit("0")).place(
x=start_x + (btn_w + gap_x), y=start_y + 3 * (btn_h + gap_y), width=btn_w, height=btn_h
)
Button(self.root, text="Back", font=("Quicksand", 18, "bold"), bg=self.btn_bg, fg=self.btn_fg,
command=backspace).place(
x=start_x + 2 * (btn_w + gap_x), y=start_y + 3 * (btn_h + gap_y), width=btn_w, height=btn_h
)

Button(self.root, text="Enter", font=("Quicksand", 18, "bold"), bg=self.btn_bg, fg=self.btn_fg,
command=submit).place(
x=60, y=560, width=360, height=70
)
Button(self.root, text="Back", font=("Quicksand", 18, "bold"), bg=self.btn_bg, fg=self.btn_fg,
command=self.show_dashboard).place(
x=140, y=700, width=200, height=70
)

def show_settings_panel(self) -> None:
self.clear()
self._set_background("dashboard/image_1.png")
Label(self.root, text="Settings", font=("Quicksand", 24, "bold")).place(x=0, y=20, width=480)
btn_w = 190
btn_h = 110
row_gap = 20
left_x = 30
right_x = 260
top_y = 110

Button(self.root, text="Dry\nContainers", font=("Quicksand", 18, "bold"), bg=self.btn_bg, fg=self.btn_fg,
command=self.show_dry_settings).place(x=left_x, y=top_y, width=btn_w, height=btn_h)
Button(self.root, text="Wet\nCondiments", font=("Quicksand", 18, "bold"), bg=self.btn_bg, fg=self.btn_fg,
command=self.show_wet_settings).place(x=right_x, y=top_y, width=btn_w, height=btn_h)

row2_y = top_y + btn_h + row_gap
Button(self.root, text="Add\nRecipe", font=("Quicksand", 18, "bold"), bg=self.btn_bg, fg=self.btn_fg,
command=self.show_add_recipe).place(x=left_x, y=row2_y, width=btn_w, height=btn_h)
Button(self.root, text="Edit\nRecipe", font=("Quicksand", 18, "bold"), bg=self.btn_bg, fg=self.btn_fg,
command=self.show_edit_recipe).place(x=right_x, y=row2_y, width=btn_w, height=btn_h)

row3_y = row2_y + btn_h + row_gap
Button(self.root, text="Update\nPIN", font=("Quicksand", 18, "bold"), bg=self.btn_bg, fg=self.btn_fg,
command=self.show_update_pin).place(x=left_x, y=row3_y, width=btn_w, height=btn_h)
Button(self.root, text="Factory\nReset", font=("Quicksand", 18, "bold"), bg=self.btn_bg, fg=self.btn_fg,
command=self.show_factory_reset).place(x=right_x, y=row3_y, width=btn_w, height=btn_h)

Button(self.root, text="Device Setup", font=("Quicksand", 18, "bold"), bg=self.btn_bg, fg=self.btn_fg,
command=self.show_device_setup).place(x=40, y=520, width=400, height=70)
Button(self.root, text="Ingredient\nLevel", font=("Quicksand", 18, "bold"), bg=self.btn_bg, fg=self.btn_fg,
command=self.show_ingredient_levels).place(x=40, y=600, width=400, height=70)
Button(self.root, text="Back", font=("Quicksand", 18, "bold"), bg=self.btn_bg, fg=self.btn_fg,
command=self.show_dashboard).place(x=140, y=700, width=200, height=70)

def show_ingredient_levels(self) -> None:
self.clear()
self._set_background("dashboard/image_1.png")
Label(self.root, text="Ingredient Levels", font=("Quicksand", 22, "bold")).place(x=0, y=20, width=480)

dry_containers = db.get_dry_containers(DB_PATH)
wet_containers = db.get_wet_containers(DB_PATH)

dry_entries = []
wet_entries = []

Label(self.root, text="Dry (g)", font=("Quicksand", 16, "bold"), anchor="w").place(x=20, y=70, width=440, height=24)
Label(self.root, text="Name", font=("Quicksand", 12, "bold"), anchor="w").place(x=70, y=92, width=190, height=24)
Label(self.root, text="Left", font=("Quicksand", 12, "bold"), anchor="center").place(x=270, y=92, width=176, height=24)

row_h = 46
row_step = 50
y = 122
for cont in dry_containers:
Label(self.root, text=f"{cont.cid}", font=("Quicksand", 16, "bold")).place(x=20, y=y, width=40, height=row_h)
Label(self.root, text=cont.name, font=("Quicksand", 14, "bold"), anchor="w").place(x=70, y=y, width=190, height=row_h)

rem_var = StringVar(value=str(cont.remaining_g))
Button(self.root, text="-", font=("Quicksand", 17, "bold"),
command=lambda v=rem_var: self._step_int_var(v, -10, 0, 9999)).place(x=270, y=y, width=48, height=row_h)
Label(self.root, textvariable=rem_var, font=("Quicksand", 15, "bold"), anchor="center").place(x=322, y=y, width=72, height=row_h)
Button(self.root, text="+", font=("Quicksand", 17, "bold"),
command=lambda v=rem_var: self._step_int_var(v, 10, 0, 9999)).place(x=398, y=y, width=48, height=row_h)

dry_entries.append((cont, rem_var))
y += row_step

Label(self.root, text="Wet (ml)", font=("Quicksand", 16, "bold"), anchor="w").place(x=20, y=y + 10, width=440, height=24)
y += 32
Label(self.root, text="Name", font=("Quicksand", 12, "bold"), anchor="w").place(x=70, y=y, width=190, height=24)
Label(self.root, text="Left", font=("Quicksand", 12, "bold"), anchor="center").place(x=270, y=y, width=176, height=24)
y += 30

for cont in wet_containers:
Label(self.root, text=f"{cont.cid}", font=("Quicksand", 16, "bold")).place(x=20, y=y, width=40, height=row_h)
Label(self.root, text=cont.name, font=("Quicksand", 14, "bold"), anchor="w").place(x=70, y=y, width=190, height=row_h)

rem_var = StringVar(value=str(cont.remaining_ml))
Button(self.root, text="-", font=("Quicksand", 17, "bold"),
command=lambda v=rem_var: self._step_int_var(v, -10, 0, 9999)).place(x=270, y=y, width=48, height=row_h)
Label(self.root, textvariable=rem_var, font=("Quicksand", 15, "bold"), anchor="center").place(x=322, y=y, width=72, height=row_h)
Button(self.root, text="+", font=("Quicksand", 17, "bold"),
command=lambda v=rem_var: self._step_int_var(v, 10, 0, 9999)).place(x=398, y=y, width=48, height=row_h)

wet_entries.append((cont, rem_var))
y += row_step

def _save_levels(reset_full: bool = False) -> None:
dry_items = []
for cont, rem_var in dry_entries:
cap = int(cont.capacity_g or 1000)
if reset_full:
rem = cap
rem_var.set(str(rem))
else:
try:
rem = int(rem_var.get())
except ValueError:
rem = int(cont.remaining_g or cap)
rem = max(0, min(cap, rem))
rem_var.set(str(rem))
dry_items.append((cont.name, int(cont.steps_per_gram or 2), cap, rem))
db.set_dry_containers(DB_PATH, dry_items)

wet_items = []
for cont, rem_var in wet_entries:
cap = int(cont.capacity_ml or 1000)
if reset_full:
rem = cap
rem_var.set(str(rem))
else:
try:
rem = int(rem_var.get())
except ValueError:
rem = int(cont.remaining_ml or cap)
rem = max(0, min(cap, rem))
rem_var.set(str(rem))
wet_items.append((cont.name, int(cont.ms_per_ml or 1000), cap, rem))
db.set_wet_containers(DB_PATH, wet_items)

def save():
_save_levels(reset_full=False)
self.show_settings_panel()

def reset_levels():
_save_levels(reset_full=True)

Button(self.root, text="Back", font=("Quicksand", 18, "bold"), command=self.show_settings_panel).place(x=20, y=700, width=140, height=70)
Button(self.root, text="Reset", font=("Quicksand", 18, "bold"), command=reset_levels).place(x=170, y=700, width=140, height=70)
Button(self.root, text="Save", font=("Quicksand", 18, "bold"), command=save).place(x=320, y=700, width=140, height=70)

def show_dry_settings(self) -> None:
self.clear()
self._set_background("dashboard/image_1.png")
Label(self.root, text="Dry Containers", font=("Quicksand", 22, "bold")).place(x=0, y=20, width=480)
entries = []
containers = db.get_dry_containers(DB_PATH)

# Headers
Label(self.root, text="Name", font=("Quicksand", 14, "bold")).place(x=70, y=70, width=190, height=28)
Label(self.root, text="Steps/g", font=("Quicksand", 14, "bold")).place(x=280, y=70, width=160, height=28)

y = 110
for cont in containers:
Label(self.root, text=f"{cont.cid}", font=("Quicksand", 16, "bold")).place(x=20, y=y, width=40, height=46)

name_var = StringVar(value=cont.name)
steps_var = StringVar(value=str(cont.steps_per_gram))

# Name Button
Button(self.root, textvariable=name_var, font=("Quicksand", 14, "bold"), bg="#fff7ee",
command=lambda v=name_var: self._open_keyboard("Rename container", v.get(), v.set)).place(x=70, y=y, width=190, height=46)

def step_var(var, delta, floor=0):
try:
val = int(var.get())
except ValueError:
val = 0
val = max(floor, val + delta)
var.set(str(val))

# Steps/g Controls
Button(self.root, text="-", font=("Quicksand", 17, "bold"), command=lambda v=steps_var: step_var(v, -1)).place(x=270, y=y, width=48, height=46)
Label(self.root, textvariable=steps_var, font=("Quicksand", 15, "bold")).place(x=322, y=y, width=72, height=46)
Button(self.root, text="+", font=("Quicksand", 17, "bold"), command=lambda v=steps_var: step_var(v, 1)).place(x=398, y=y, width=48, height=46)

entries.append((name_var, steps_var, cont.capacity_g, cont.remaining_g))
y += 56

def save():
items = []
for name_var, steps_var, capacity_g, remaining_g in entries:
try:
steps = int(steps_var.get())
except ValueError:
steps = 2
items.append((name_var.get(), steps, capacity_g, remaining_g))
db.set_dry_containers(DB_PATH, items)
self.show_settings()

Button(self.root, text="Save", font=("Quicksand", 18, "bold"), command=save).place(x=260, y=700, width=180, height=70)
Button(self.root, text="Back", font=("Quicksand", 18, "bold"), command=self.show_settings_panel).place(x=40, y=700, width=180, height=70)

def show_wet_settings(self) -> None:
self.clear()
self._set_background("dashboard/image_1.png")
Label(self.root, text="Wet Condiments", font=("Quicksand", 22, "bold")).place(x=0, y=20, width=480)
entries = []
containers = db.get_wet_containers(DB_PATH)

# Cleaned up headers
Label(self.root, text="Name", font=("Quicksand", 14, "bold")).place(x=70, y=70, width=190, height=28)
Label(self.root, text="ms/ml", font=("Quicksand", 14, "bold")).place(x=280, y=70, width=160, height=28)

y = 110
for cont in containers:
Label(self.root, text=f"{cont.cid}", font=("Quicksand", 16, "bold")).place(x=20, y=y, width=40, height=46)

name_var = StringVar(value=cont.name)
ms_var = StringVar(value=str(cont.ms_per_ml))

# Name Button
Button(self.root, textvariable=name_var, font=("Quicksand", 14, "bold"), bg="#fff7ee",
command=lambda v=name_var: self._open_keyboard("Rename wet", v.get(), v.set)).place(x=70, y=y, width=190, height=46)

def step_var(var, delta, floor=0):
try:
val = int(var.get())
except ValueError:
val = 0
val = max(floor, val + delta)
var.set(str(val))

# ms/ml Controls (Moved slightly to the right to fill the space)
Button(self.root, text="-", font=("Quicksand", 17, "bold"), command=lambda v=ms_var: step_var(v, -100)).place(x=270, y=y, width=48, height=46)
Label(self.root, textvariable=ms_var, font=("Quicksand", 15, "bold")).place(x=322, y=y, width=72, height=46)
Button(self.root, text="+", font=("Quicksand", 17, "bold"), command=lambda v=ms_var: step_var(v, 100)).place(x=398, y=y, width=48, height=46)

# We store the original capacity and remaining levels in the background so the DB doesn't crash
entries.append((name_var, ms_var, cont.capacity_ml, cont.remaining_ml))
y += 56

def save():
items = []
for name_var, ms_var, cap, rem in entries:
try:
ms = int(ms_var.get())
except ValueError:
ms = 1000

# Silently pass the existing cap and rem back to the database
items.append((name_var.get(), ms, cap, rem))

db.set_wet_containers(DB_PATH, items)
self.show_settings()

Button(self.root, text="Save", font=("Quicksand", 18, "bold"), command=save).place(x=260, y=700, width=180, height=70)
Button(self.root, text="Back", font=("Quicksand", 18, "bold"), command=self.show_settings_panel).place(x=40, y=700, width=180, height=70)
def show_add_recipe(self) -> None:
self.clear()
self._set_background("menu/button_7.png")
Label(self.root, text="Add Recipe", font=("Quicksand", 22, "bold")).place(x=0, y=20, width=480)
name_var = StringVar(value="")
Button(self.root, textvariable=name_var, font=("Quicksand", 16, "bold"), bg="#fff7ee",
command=lambda: self._open_keyboard("Recipe name", name_var.get(), name_var.set)).place(x=40, y=70, width=400, height=50)

dry_vars = []
wet_vars = []
y = 140
for cont in db.get_dry_containers(DB_PATH):
Label(self.root, text=cont.name, font=("Quicksand", 14, "bold"), anchor="w").place(x=20, y=y, width=220, height=42)
var = StringVar(value="0")
Button(self.root, text="-", font=("Quicksand", 17, "bold"), command=lambda v=var: self._step_int_var(v, -1)).place(x=250, y=y, width=48, height=42)
Label(self.root, textvariable=var, font=("Quicksand", 16, "bold")).place(x=304, y=y, width=72, height=42)
Button(self.root, text="+", font=("Quicksand", 17, "bold"), command=lambda v=var: self._step_int_var(v, 1)).place(x=382, y=y, width=48, height=42)
dry_vars.append(var)
y += 48

for cont in db.get_wet_containers(DB_PATH):
Label(self.root, text=cont.name, font=("Quicksand", 14, "bold"), anchor="w").place(x=20, y=y, width=220, height=42)
var = StringVar(value="0")
Button(self.root, text="-", font=("Quicksand", 17, "bold"), command=lambda v=var: self._step_int_var(v, -1)).place(x=250, y=y, width=48, height=42)
Label(self.root, textvariable=var, font=("Quicksand", 16, "bold")).place(x=304, y=y, width=72, height=42)
Button(self.root, text="+", font=("Quicksand", 17, "bold"), command=lambda v=var: self._step_int_var(v, 1)).place(x=382, y=y, width=48, height=42)
wet_vars.append(var)
y += 48

def save():
dry = [int(v.get() or 0) for v in dry_vars]
wet = [int(v.get() or 0) for v in wet_vars]
db.save_recipe(DB_PATH, name_var.get(), dry, wet)
self.show_settings()

Button(self.root, text="Save", font=("Quicksand", 18, "bold"), command=save).place(x=260, y=700, width=180, height=70)
Button(self.root, text="Back", font=("Quicksand", 18, "bold"), command=self.show_settings_panel).place(x=40, y=700, width=180, height=70)

def show_update_pin(self) -> None:
self.clear()
self._set_background("dashboard/image_1.png")
Label(self.root, text="Update PIN", font=("Quicksand", 22, "bold")).place(x=0, y=20, width=480)
status = StringVar(value="Enter current PIN")
Label(self.root, textvariable=status, font=("Quicksand", 14)).place(x=40, y=80, width=400, height=40)

pin_var = StringVar(value="")
masked_var = StringVar(value="")
display = Label(self.root, textvariable=masked_var, font=("Quicksand", 28, "bold"), bg="white")
display.place(x=60, y=120, width=360, height=60)

def update_masked() -> None:
masked_var.set("*" * len(pin_var.get()))

def add_digit(digit: str) -> None:
value = pin_var.get()
if len(value) >= 8:
return
pin_var.set(value + digit)
update_masked()

def backspace() -> None:
pin_var.set(pin_var.get()[:-1])
update_masked()

def clear() -> None:
pin_var.set("")
update_masked()

def ask_new_pin() -> None:
pin_var.set("")
update_masked()
status.set("Enter new PIN")

def submit_new() -> None:
if not pin_var.get():
status.set("PIN is required")
return
db.set_setting(DB_PATH, "security_key", pin_var.get())
status.set("PIN updated")
pin_var.set("")
update_masked()

enter_btn.configure(command=submit_new)

def submit_current() -> None:
saved = db.get_setting(DB_PATH, "security_key", "1234")
if pin_var.get() != saved:
status.set("Incorrect PIN")
return
ask_new_pin()

btn_w = 120
btn_h = 75
start_x = 50
start_y = 220
gap_x = 10
gap_y = 10

digits = ["1", "2", "3", "4", "5", "6", "7", "8", "9"]
idx = 0
for row in range(3):
for col in range(3):
d = digits[idx]
Button(self.root, text=d, font=("Quicksand", 22, "bold"), bg=self.btn_bg, fg=self.btn_fg,
command=lambda v=d: add_digit(v)).place(
x=start_x + col * (btn_w + gap_x),
y=start_y + row * (btn_h + gap_y),
width=btn_w,
height=btn_h,
)
idx += 1

Button(self.root, text="Clear", font=("Quicksand", 18, "bold"), bg=self.btn_bg, fg=self.btn_fg,
command=clear).place(
x=start_x, y=start_y + 3 * (btn_h + gap_y), width=btn_w, height=btn_h
)
Button(self.root, text="0", font=("Quicksand", 22, "bold"), bg=self.btn_bg, fg=self.btn_fg,
command=lambda: add_digit("0")).place(
x=start_x + (btn_w + gap_x), y=start_y + 3 * (btn_h + gap_y), width=btn_w, height=btn_h
)
Button(self.root, text="Back", font=("Quicksand", 18, "bold"), bg=self.btn_bg, fg=self.btn_fg,
command=backspace).place(
x=start_x + 2 * (btn_w + gap_x), y=start_y + 3 * (btn_h + gap_y), width=btn_w, height=btn_h
)

enter_btn = Button(self.root, text="Enter", font=("Quicksand", 18, "bold"), bg=self.btn_bg, fg=self.btn_fg,
command=submit_current)
enter_btn.place(x=60, y=560, width=360, height=70)
Button(self.root, text="Back", font=("Quicksand", 18, "bold"), bg=self.btn_bg, fg=self.btn_fg,
command=self.show_settings_panel).place(x=140, y=700, width=200, height=70)

def show_factory_reset(self) -> None:
self.clear()
self._set_background("dashboard/image_1.png")
Label(self.root, text="Factory Reset", font=("Quicksand", 22, "bold")).place(x=0, y=20, width=480)
message = "This will delete all recipes, logs, and reset containers.\nEnter PIN to continue."
Label(self.root, text=message, font=("Quicksand", 14), wraplength=420, justify="center").place(x=30, y=90, width=420, height=100)
status = StringVar(value="")
Label(self.root, textvariable=status, font=("Quicksand", 14)).place(x=40, y=200, width=400, height=30)

pin_var = StringVar(value="")
masked_var = StringVar(value="")
display = Label(self.root, textvariable=masked_var, font=("Quicksand", 28, "bold"), bg="white")
display.place(x=60, y=240, width=360, height=60)

def update_masked() -> None:
masked_var.set("*" * len(pin_var.get()))

def add_digit(digit: str) -> None:
value = pin_var.get()
if len(value) >= 8:
return
pin_var.set(value + digit)
update_masked()

def backspace() -> None:
pin_var.set(pin_var.get()[:-1])
update_masked()

def clear() -> None:
pin_var.set("")
update_masked()

def confirm_reset():
self.clear()
self._set_background("dashboard/image_1.png")
Label(self.root, text="Confirm Factory Reset", font=("Quicksand", 22, "bold")).place(x=0, y=120, width=480)
Label(self.root, text="Are you sure?", font=("Quicksand", 18, "bold")).place(x=0, y=200, width=480, height=40)

def do_reset():
db.reset_factory(DB_PATH)
self.show_settings()

Button(self.root, text="No", font=("Quicksand", 18, "bold"),
command=self.show_settings).place(x=60, y=320, width=160, height=70)
Button(self.root, text="Yes", font=("Quicksand", 18, "bold"),
command=do_reset).place(x=260, y=320, width=160, height=70)

def check_pin():
saved = db.get_setting(DB_PATH, "security_key", "1234")
if pin_var.get() != saved:
status.set("Incorrect PIN")
return
confirm_reset()

btn_w = 120
btn_h = 75
start_x = 50
start_y = 320
gap_x = 10
gap_y = 10

digits = ["1", "2", "3", "4", "5", "6", "7", "8", "9"]
idx = 0
for row in range(3):
for col in range(3):
d = digits[idx]
Button(self.root, text=d, font=("Quicksand", 22, "bold"), bg=self.btn_bg, fg=self.btn_fg,
command=lambda v=d: add_digit(v)).place(
x=start_x + col * (btn_w + gap_x),
y=start_y + row * (btn_h + gap_y),
width=btn_w,
height=btn_h,
)
idx += 1

Button(self.root, text="Clear", font=("Quicksand", 18, "bold"), bg=self.btn_bg, fg=self.btn_fg,
command=clear).place(
x=start_x, y=start_y + 3 * (btn_h + gap_y), width=btn_w, height=btn_h
)
Button(self.root, text="0", font=("Quicksand", 22, "bold"), bg=self.btn_bg, fg=self.btn_fg,
command=lambda: add_digit("0")).place(
x=start_x + (btn_w + gap_x), y=start_y + 3 * (btn_h + gap_y), width=btn_w, height=btn_h
)
Button(self.root, text="Back", font=("Quicksand", 18, "bold"), bg=self.btn_bg, fg=self.btn_fg,
command=backspace).place(
x=start_x + 2 * (btn_w + gap_x), y=start_y + 3 * (btn_h + gap_y), width=btn_w, height=btn_h
)

Button(self.root, text="Enter PIN", font=("Quicksand", 18, "bold"), bg=self.btn_bg, fg=self.btn_fg,
command=check_pin).place(x=60, y=630, width=360, height=60)
Button(self.root, text="Back", font=("Quicksand", 18, "bold"),
command=self.show_settings_panel).place(x=140, y=700, width=200, height=70)

def show_logs(self) -> None:
self.clear()
self._set_background("dashboard/image_1.png")
Label(self.root, text="Inventory Logs", font=("Quicksand", 24, "bold")).place(x=0, y=20, width=480)

rows = db.get_logs(DB_PATH)
if not rows:
Label(self.root, text="No logs yet", font=("Quicksand", 16, "bold")).place(x=0, y=120, width=480, height=40)
Button(self.root, text="Back", font=("Quicksand", 16, "bold"), command=self.show_dashboard).place(x=180, y=700, width=120, height=50)
return

dates = sorted({ts.split(" ")[0] for ts, _recipe, _batches, _status in rows}, reverse=True)
date_var = StringVar(value=dates[0])

date_combo = ttk.Combobox(
self.root,
state="readonly",
values=dates,
textvariable=date_var,
font=("Quicksand", 16, "bold"),
justify="center",
)
date_combo.place(x=115, y=70, width=250, height=42)

table_frame = Frame(self.root)
table_frame.place(x=15, y=130, width=450, height=520)

header_bg = "#6F5C57"
header_fg = "#D4D5CF"
Label(table_frame, text="TIME", font=("Quicksand", 12, "bold"), bg=header_bg, fg=header_fg).place(x=0, y=0, width=90, height=32)
Label(table_frame, text="RECIPE", font=("Quicksand", 12, "bold"), bg=header_bg, fg=header_fg).place(x=90, y=0, width=190, height=32)
Label(table_frame, text="QTY", font=("Quicksand", 12, "bold"), bg=header_bg, fg=header_fg).place(x=280, y=0, width=60, height=32)
Label(table_frame, text="STATUS", font=("Quicksand", 12, "bold"), bg=header_bg, fg=header_fg).place(x=340, y=0, width=110, height=32)

wrapper = Frame(table_frame)
wrapper.place(x=0, y=34, width=450, height=486)
canvas = Canvas(wrapper)
canvas.pack(side="left", fill="both", expand=True)
scrollbar = Scrollbar(wrapper, orient="vertical", command=canvas.yview)
scrollbar.pack(side="right", fill="y")
canvas.configure(yscrollcommand=scrollbar.set)
body = Frame(canvas)
canvas.create_window((0, 0), window=body, anchor="nw")

def on_configure(_event):
bbox = canvas.bbox("all")
if bbox:
canvas.configure(scrollregion=bbox)
canvas.yview_moveto(0)

body.bind("<Configure>", on_configure)

def render_rows(selected_date: str) -> None:
for child in body.winfo_children():
child.destroy()
filtered = [(ts, recipe, batches, status) for ts, recipe, batches, status in rows if ts.startswith(selected_date)]
if not filtered:
Label(body, text="No logs for this date", font=("Quicksand", 14, "bold")).pack(pady=20)
canvas.update_idletasks()
canvas.configure(scrollregion=canvas.bbox("all"))
canvas.yview_moveto(0)
if scrollbar.winfo_ismapped():
scrollbar.pack_forget()
return
col_time = 90
col_recipe = 190
col_qty = 60
col_status = 110
for ts, recipe, batches, status in filtered:
time_text = ts.split(" ")[1] if " " in ts else ts
display_status = self._format_status(status)
row = Frame(body, width=450, height=34)
row.pack(fill="x", pady=2)
row.pack_propagate(False)

Label(row, text=time_text, font=("Quicksand", 12, "bold"), anchor="center").place(x=0, y=0, width=col_time, height=34)
Label(row, text=recipe, font=("Quicksand", 12, "bold"), anchor="center").place(x=col_time, y=0, width=col_recipe, height=34)
Label(row, text=str(batches), font=("Quicksand", 12, "bold"), anchor="center").place(x=col_time + col_recipe, y=0, width=col_qty, height=34)
Label(row, text=display_status, font=("Quicksand", 12, "bold"), anchor="center").place(x=col_time + col_recipe + col_qty, y=0, width=col_status, height=34)

canvas.update_idletasks()
bbox = canvas.bbox("all")
if bbox:
canvas.configure(scrollregion=bbox)
visible_height = 486
content_height = (bbox[3] - bbox[1]) if bbox else 0
if content_height <= visible_height:
canvas.yview_moveto(0)
if scrollbar.winfo_ismapped():
scrollbar.pack_forget()
else:
if not scrollbar.winfo_ismapped():
scrollbar.pack(side="right", fill="y")

def on_date_change(_event=None):
render_rows(date_var.get())

date_combo.bind("<<ComboboxSelected>>", on_date_change)
render_rows(date_var.get())

Button(self.root, text="Back", font=("Quicksand", 18, "bold"), command=self.show_dashboard).place(x=140, y=690, width=200, height=70)

def show_single_dispense(self) -> None:
self.clear()
self._set_background("dispensing/image_1.png")
Label(self.root, text="Dispense Ingredient", font=("Quicksand", 22, "bold")).place(x=0, y=20, width=480)

status_var = StringVar(value="Select an ingredient")
amount_var = StringVar(value="10")
unit_var = StringVar(value="g")
selection = {"type": None, "id": None, "name": None, "ms_per_ml": 1000, "steps_per_gram": 2}

Label(self.root, textvariable=status_var, font=("Quicksand", 12)).place(x=40, y=70, width=400, height=30)

wrapper = Frame(self.root)
wrapper.place(x=20, y=110, width=440, height=420)
canvas = Canvas(wrapper)
canvas.pack(side="left", fill="both", expand=True)
scrollbar = Scrollbar(wrapper, orient="vertical", command=canvas.yview)
scrollbar.pack(side="right", fill="y")
canvas.configure(yscrollcommand=scrollbar.set)
frame = Frame(canvas)
canvas.create_window((0, 0), window=frame, anchor="nw")

def on_configure(_event):
canvas.configure(scrollregion=canvas.bbox("all"))

frame.bind("<Configure>", on_configure)

def select_dry(cont):
selection.update({"type": "dry", "id": cont.cid, "name": cont.name, "steps_per_gram": cont.steps_per_gram})
unit_var.set("g")
status_var.set(f"Selected dry: {cont.name}")

def select_wet(cont):
selection.update({"type": "wet", "id": cont.cid, "name": cont.name, "ms_per_ml": cont.ms_per_ml})
unit_var.set("ml")
status_var.set(f"Selected wet: {cont.name}")

Label(frame, text="Dry", font=("Quicksand", 14, "bold"), anchor="w").pack(fill="x", pady=(0, 6))
for cont in db.get_dry_containers(DB_PATH):
Button(frame, text=cont.name, font=("Quicksand", 14), bg="#FFFF8F",
command=lambda c=cont: select_dry(c)).pack(pady=4, fill="x")

Label(frame, text="Wet", font=("Quicksand", 14, "bold"), anchor="w").pack(fill="x", pady=(12, 6))
for cont in db.get_wet_containers(DB_PATH):
Button(frame, text=cont.name, font=("Quicksand", 14), bg="#FFFF8F",
command=lambda c=cont: select_wet(c)).pack(pady=4, fill="x")

Label(self.root, text="Amount", font=("Quicksand", 14, "bold")).place(x=40, y=550, width=100, height=30)
Button(self.root, text="-", font=("Quicksand", 14, "bold"),
command=lambda: self._step_int_var(amount_var, -1, 1, 9999)).place(x=150, y=545, width=40, height=40)
Label(self.root, textvariable=amount_var, font=("Quicksand", 16, "bold")).place(x=200, y=545, width=80, height=40)
Label(self.root, textvariable=unit_var, font=("Quicksand", 14)).place(x=285, y=550, width=40, height=30)
Button(self.root, text="+", font=("Quicksand", 14, "bold"),
command=lambda: self._step_int_var(amount_var, 1, 1, 9999)).place(x=330, y=545, width=40, height=40)

def dispense():
if not self._ensure_serial(status_var):
return
if not selection["type"]:
status_var.set("Select an ingredient")
return
try:
amount = int(amount_var.get())
except ValueError:
amount = 0
if amount <= 0:
status_var.set("Amount must be > 0")
return

if selection["type"] == "dry":
payload = _build_single_dispense_payload_fallback(
"D",
selection["id"],
amount,
steps_per_gram=selection["steps_per_gram"],
)
else:
payload = _build_single_dispense_payload_fallback(
"W",
selection["id"],
amount,
ms_per_ml=selection["ms_per_ml"],
)
name = f"Single: {selection['name']}"
status_var.set("Sending command...")
self.root.update_idletasks()
            if selection["type"] == "wet":
                wet_seconds = (float(amount) * float(selection.get("ms_per_ml") or 0.0)) / 1000.0
                timeout_s = max(30.0, wet_seconds + 15.0)
            else:
                timeout_s = 180.0
            status = self._serial_send_wait_status(payload, timeout=timeout_s)
if status == "STATUS:OK" and selection["type"] == "dry":
db.apply_dry_dispense(DB_PATH, [(selection["id"], amount)])
if status == "STATUS:OK" and selection["type"] == "wet":
db.apply_wet_dispense(DB_PATH, [(selection["id"], amount)])
db.log_dispense(DB_PATH, time.strftime("%Y-%m-%d %H:%M:%S"), name, 1, self._format_status(status))
status_var.set(self._format_status(status))

Button(self.root, text="Dispense", font=("Quicksand", 16, "bold"), command=dispense).place(x=320, y=700, width=140, height=50)
Button(self.root, text="Back", font=("Quicksand", 16, "bold"), command=self.show_dashboard).place(x=20, y=700, width=120, height=50)

def show_cleaning(self) -> None:
self.clear()
self._set_background("dispensing/image_1.png")
Label(self.root, text="Cleaning Mode", font=("Quicksand", 22, "bold")).place(x=0, y=20, width=480)
status_var = StringVar(value="Idle")
Label(self.root, textvariable=status_var, font=("Quicksand", 12)).place(x=40, y=80, width=400, height=40)

def start_clean():
if not self._ensure_serial(status_var):
return
status_var.set("Cleaning...")
self.root.update_idletasks()
result = self._serial_send_wait_status(build_clean_payload(), timeout=15.0)
status_var.set(self._format_status(result))

def stop_clean():
if not self._ensure_serial(status_var):
return
self._serial_send_no_wait(build_stop_payload())
status_var.set("Stop sent")

Button(self.root, text="Start Clean", font=("Quicksand", 16, "bold"), command=start_clean).place(x=80, y=160, width=320, height=60)
Button(self.root, text="Stop", font=("Quicksand", 16, "bold"), command=stop_clean).place(x=80, y=240, width=320, height=60)
Button(self.root, text="Back", font=("Quicksand", 16, "bold"), command=self.show_dashboard).place(x=180, y=700, width=120, height=50)

def show_levels(self) -> None:
self.clear()
self._set_background("dispensing/image_1.png")
Label(self.root, text="Ingredient Levels", font=("Quicksand", 22, "bold")).place(x=0, y=20, width=480)
status_var = StringVar(value="Dry container levels")
Label(self.root, textvariable=status_var, font=("Quicksand", 12)).place(x=40, y=80, width=400, height=40)

y = 140
for cont in db.get_dry_containers(DB_PATH):
Label(
self.root,
text=f"Dry {cont.cid}: {cont.remaining_g}/{cont.capacity_g} g",
font=("Quicksand", 12),
).place(x=40, y=y, width=400, height=24)
y += 28
y += 10
Label(self.root, text="Wet (ml)", font=("Quicksand", 12, "bold")).place(x=40, y=y, width=400, height=24)
y += 28
for cont in db.get_wet_containers(DB_PATH):
Label(
self.root,
text=f"Wet {cont.cid}: {cont.remaining_ml}/{cont.capacity_ml} ml",
font=("Quicksand", 12),
).place(x=40, y=y, width=400, height=24)
y += 28
Button(self.root, text="Back", font=("Quicksand", 16, "bold"), command=self.show_dashboard).place(x=180, y=700, width=120, height=50)

def show_shutdown(self) -> None:
self.clear()
self._set_background("dashboard/image_1.png")
Label(self.root, text="Power Off?", font=("Quicksand", 22, "bold")).place(x=0, y=200, width=480)
Button(self.root, text="No", font=("Quicksand", 16, "bold"), command=self.show_dashboard).place(x=80, y=500, width=120, height=50)
Button(self.root, text="Yes", font=("Quicksand", 16, "bold"), command=self.root.destroy).place(x=280, y=500, width=120, height=50)

def show_lock(self) -> None:
self.show_standby()

def show_standby(self) -> None:
self.clear()
self._set_background("standby/image_1.png")
time_label = Label(self.root, text="00:00", bg="#FFFFFF", fg="#30071F", font=("Quicksand", 48, "bold"))
date_label = Label(self.root, text="", bg="#FFFFFF", fg="#30071F", font=("Quicksand", 16))
time_label.place(x=0, y=220, width=480, height=70)
date_label.place(x=0, y=300, width=480, height=30)

def update_clock():
now = datetime.now()
time_label.config(text=now.strftime("%H:%M:%S"))
date_label.config(text=now.strftime("%B %d, %Y"))
time_label.after(1000, update_clock)

update_clock()

Button(self.root, text="Unlock", font=("Quicksand", 16, "bold"), command=self.show_dashboard).place(x=170, y=700, width=140, height=50)

def show_device_setup(self, initial: bool = False) -> None:
self.clear()
self._set_background("dashboard/image_1.png")
Label(self.root, text="Device Setup", font=("Quicksand", 22, "bold")).place(x=0, y=20, width=480)

ports = [p.device for p in list_ports.comports()] if list_ports else []
port_var = StringVar(value=self.serial_port or (ports[0] if ports else ""))
baud_options = ["9600", "19200", "38400", "57600", "115200"]
baud_var = StringVar(value=str(self.serial_baud or 9600))
status_var = StringVar(value="Select a port and baud")

Label(self.root, text="Port", font=("Quicksand", 16, "bold")).place(x=40, y=120, width=100, height=50)
port_combo = ttk.Combobox(self.root, textvariable=port_var, values=ports, state="normal", font=("Quicksand", 16, "bold"))
port_combo.place(x=140, y=120, width=300, height=50)
Label(self.root, text="Baud", font=("Quicksand", 16, "bold")).place(x=40, y=190, width=100, height=50)
baud_combo = ttk.Combobox(self.root, textvariable=baud_var, values=baud_options, state="readonly", font=("Quicksand", 16, "bold"))
baud_combo.place(x=140, y=190, width=220, height=50)

def refresh_ports():
if not list_ports:
status_var.set("pyserial not installed")
return
found = [p.device for p in list_ports.comports()]
if found:
port_combo["values"] = found
status_var.set(f"Found {len(found)} ports")
else:
status_var.set("No ports found")

def connect():
port = port_var.get().strip()
try:
baud = int(baud_var.get().strip())
except ValueError:
status_var.set("Invalid baud")
return
if not port:
status_var.set("Port is required")
return
if self.serial:
try:
self.serial.close()
except Exception:
pass
self.serial_port = port
self.serial_baud = baud
db.set_setting(DB_PATH, "serial_port", port)
db.set_setting(DB_PATH, "serial_baud", str(baud))
self.serial = SerialLink(port, baud, simulate=False)
try:
self.serial.open()
except Exception as exc:
status_var.set(f"Connect failed: {exc}")
self._debug(f"Connect error: {exc}")
return
self._debug(f"Connected config set: port={port} baud={baud}")
status_var.set(f"Connected to {port}")
if initial:
self.show_dashboard()

Label(self.root, textvariable=status_var, font=("Quicksand", 12)).place(x=40, y=250, width=400, height=40)
Button(self.root, text="Refresh", font=("Quicksand", 18, "bold"), command=refresh_ports).place(x=40, y=320, width=180, height=70)
Button(self.root, text="Connect", font=("Quicksand", 18, "bold"), command=connect).place(x=240, y=320, width=180, height=70)

back_target = self.show_dashboard if initial else self.show_settings_panel
Button(self.root, text="Back", font=("Quicksand", 18, "bold"), command=back_target).place(x=140, y=700, width=200, height=70)

def show_edit_recipe(self) -> None:
self.clear()
self._set_background("menu/button_7.png")
Label(self.root, text="Edit Recipe", font=("Quicksand", 22, "bold")).place(x=0, y=20, width=480)
recipes = db.list_recipes(DB_PATH)
status = StringVar(value="Select a recipe")
Label(self.root, textvariable=status, font=("Quicksand", 12)).place(x=20, y=70, width=440, height=30)

wrapper = Frame(self.root)
wrapper.place(x=20, y=110, width=440, height=520)
canvas = Canvas(wrapper)
canvas.pack(side="left", fill="both", expand=True)
scrollbar = Scrollbar(wrapper, orient="vertical", command=canvas.yview)
scrollbar.pack(side="right", fill="y")
canvas.configure(yscrollcommand=scrollbar.set)
frame = Frame(canvas)
canvas.create_window((0, 0), window=frame, anchor="nw")

def on_configure(_event):
canvas.configure(scrollregion=canvas.bbox("all"))

frame.bind("<Configure>", on_configure)

def open_editor(rid):
self._show_edit_recipe_detail(rid)

for rec in recipes:
Button(frame, text=rec.name, font=("Quicksand", 18, "bold"), bg="#fff7ee",
command=lambda r=rec: open_editor(r.rid)).pack(pady=8, padx=10, fill="x", ipady=8)

Button(self.root, text="Back", font=("Quicksand", 18, "bold"), command=self.show_settings_panel).place(x=140, y=700, width=200, height=70)

def _show_edit_recipe_detail(self, recipe_id: int) -> None:
self.clear()
self._set_background("menu/button_7.png")

recipe, dry, wet = db.get_recipe_detail(DB_PATH, recipe_id)
if not recipe:
self.show_edit_recipe()
return

Label(self.root, text="Edit Recipe", font=("Quicksand", 22, "bold")).place(x=0, y=20, width=480)

name_var = StringVar(value=recipe[1])
Button(self.root, textvariable=name_var, font=("Quicksand", 16, "bold"), bg="#fff7ee",
command=lambda: self._open_keyboard("Recipe name", name_var.get(), name_var.set)).place(x=40, y=70, width=400, height=50)

dry_map = {did: grams for did, grams in dry}
wet_map = {wid: ml for wid, ml in wet}
dry_vars = []
wet_vars = []

y = 140
for cont in db.get_dry_containers(DB_PATH):
Label(self.root, text=cont.name, font=("Quicksand", 14, "bold"), anchor="w").place(x=20, y=y, width=220, height=42)
var = StringVar(value=str(dry_map.get(cont.cid, 0)))
Button(self.root, text="-", font=("Quicksand", 17, "bold"), command=lambda v=var: self._step_int_var(v, -1)).place(x=250, y=y, width=48, height=42)
Label(self.root, textvariable=var, font=("Quicksand", 16, "bold")).place(x=304, y=y, width=72, height=42)
Button(self.root, text="+", font=("Quicksand", 17, "bold"), command=lambda v=var: self._step_int_var(v, 1)).place(x=382, y=y, width=48, height=42)
dry_vars.append(var)
y += 48

for cont in db.get_wet_containers(DB_PATH):
Label(self.root, text=cont.name, font=("Quicksand", 14, "bold"), anchor="w").place(x=20, y=y, width=220, height=42)
var = StringVar(value=str(wet_map.get(cont.cid, 0)))
Button(self.root, text="-", font=("Quicksand", 17, "bold"), command=lambda v=var: self._step_int_var(v, -1)).place(x=250, y=y, width=48, height=42)
Label(self.root, textvariable=var, font=("Quicksand", 16, "bold")).place(x=304, y=y, width=72, height=42)
Button(self.root, text="+", font=("Quicksand", 17, "bold"), command=lambda v=var: self._step_int_var(v, 1)).place(x=382, y=y, width=48, height=42)
wet_vars.append(var)
y += 48

status_var = StringVar(value="")
Label(self.root, textvariable=status_var, font=("Quicksand", 12)).place(x=20, y=650, width=440, height=24)

def save():
new_name = name_var.get().strip()
if not new_name:
status_var.set("Recipe name is required")
return

dry_values = [int(v.get() or 0) for v in dry_vars]
wet_values = [int(v.get() or 0) for v in wet_vars]

try:
db.update_recipe(DB_PATH, recipe_id, new_name, dry_values, wet_values)
except Exception as exc:
status_var.set(f"Save failed: {exc}")
return

self.show_edit_recipe()

def confirm_delete():
self.clear()
self._set_background("menu/button_7.png")
Label(self.root, text="Delete Recipe", font=("Quicksand", 22, "bold")).place(x=0, y=180, width=480)
Label(self.root, text=name_var.get(), font=("Quicksand", 18, "bold")).place(x=0, y=240, width=480, height=40)
Label(self.root, text="Are you sure?", font=("Quicksand", 16)).place(x=0, y=290, width=480, height=30)

def do_delete():
db.delete_recipe(DB_PATH, recipe_id)
self.show_edit_recipe()

Button(self.root, text="No", font=("Quicksand", 18, "bold"),
command=lambda: self._show_edit_recipe_detail(recipe_id)).place(x=60, y=360, width=160, height=70)
Button(self.root, text="Yes", font=("Quicksand", 18, "bold"),
command=do_delete).place(x=260, y=360, width=160, height=70)

Button(self.root, text="Save", font=("Quicksand", 18, "bold"), command=save).place(x=260, y=700, width=180, height=70)
Button(self.root, text="Delete", font=("Quicksand", 18, "bold"), command=confirm_delete).place(x=260, y=620, width=180, height=70)
Button(self.root, text="Back", font=("Quicksand", 18, "bold"), command=self.show_edit_recipe).place(x=40, y=700, width=180, height=70)

def show_voice(self) -> None:
self.clear()
self._set_background("dashboard/image_1.png")
self.voice_running = True
self.voice_state = "listen_command"
self._voice_stop_stream()
self._voice_clear_queue()
self.voice_buffer = []
self.voice_pending_action = None

Label(self.root, text="Voice Control", font=("Quicksand", 24, "bold")).place(x=0, y=30, width=480)
self.voice_anim_label = Label(self.root, text="Listening...", font=("Quicksand", 18, "bold"))
self.voice_anim_label.place(x=0, y=100, width=480, height=40)
self.voice_status_label = Label(self.root, text="Preparing voice engine...", font=("Quicksand", 14), wraplength=440)
self.voice_status_label.place(x=20, y=160, width=440, height=60)
self.voice_transcript_label = Label(self.root, text="Heard: ...", font=("Quicksand", 12), wraplength=440, justify="left")
self.voice_transcript_label.place(x=20, y=240, width=440, height=80)
self.voice_debug_label = Label(self.root, text="Mic debug: initializing...", font=("Quicksand", 10), wraplength=440, justify="left")
self.voice_debug_label.place(x=20, y=330, width=440, height=60)

Button(self.root, text="Exit", font=("Quicksand", 16, "bold"), command=self._voice_exit).place(x=180, y=700, width=120, height=50)

self._voice_anim_tick()
threading.Thread(target=self._voice_bootstrap, daemon=True).start()

def _voice_exit(self) -> None:
self.voice_running = False
self.voice_state = "idle"
self._voice_stop_stream()
self.show_dashboard()

def _voice_set_status(self, text: str) -> None:
if self.voice_status_label:
try:
self.voice_status_label.config(text=text)
except tk.TclError:
pass

def _voice_set_transcript(self, text: str) -> None:
if self.voice_transcript_label:
try:
self.voice_transcript_label.config(text=f"Heard: {text}")
except tk.TclError:
pass

def _voice_set_debug(self, text: str) -> None:
if self.voice_debug_label:
try:
self.voice_debug_label.config(text=text)
except tk.TclError:
pass

def _voice_ensure_model(self) -> None:
if self.voice_model is None:
from faster_whisper import WhisperModel
self.voice_model = WhisperModel(VOICE_MODEL, device="cpu", compute_type=VOICE_COMPUTE_TYPE)

def _voice_start_stream(self) -> bool:
import sounddevice as sd
input_device = None
preferred = os.getenv("VOICE_INPUT_DEVICE", VOICE_PREFERRED_MIC).strip().lower()

for idx, dev in enumerate(sd.query_devices()):
if dev.get("max_input_channels", 0) <= 0:
continue
name = str(dev.get("name", "")).lower()
if preferred and preferred in name:
input_device = idx
break
if input_device is None:
input_device = idx

if input_device is None:
raise RuntimeError("No input-capable microphone device found")

device_info = sd.query_devices(input_device, "input")
self.voice_input_device_name = str(device_info.get("name", "Unknown"))
default_rate = int(device_info.get("default_samplerate") or 0)
candidate_rates = [default_rate, 48000, 44100, 32000, VOICE_SAMPLE_RATE]
tried_rates = []

for rate in candidate_rates:
if rate <= 0 or rate in tried_rates:
continue
tried_rates.append(rate)
try:
sd.check_input_settings(device=input_device, channels=1, samplerate=rate)
self.voice_stream = sd.InputStream(
samplerate=rate,
channels=1,
blocksize=VOICE_BLOCK_SIZE,
device=input_device,
callback=self._voice_audio_callback,
)
self.voice_stream.start()
self.voice_input_rate = rate
self.root.after(0, self._voice_set_status, f"Mic ready ({rate} Hz)")
self.root.after(0, self._voice_set_debug, f"Mic: {self.voice_input_device_name} | Rate: {rate} Hz")
return True
except Exception:
if self.voice_stream:
try:
self.voice_stream.close()
except Exception:
pass
self.voice_stream = None

raise RuntimeError(f"Unsupported sample rates: {tried_rates}")

def _voice_stop_stream(self) -> None:
if self.voice_stream:
self.voice_stream.stop()
self.voice_stream.close()
self.voice_stream = None

def _voice_audio_callback(self, indata, _frames, _time, _status) -> None:
if self.voice_running:
self.voice_queue.put(indata.copy())

def _voice_clear_queue(self) -> None:
while not self.voice_queue.empty():
try:
self.voice_queue.get_nowait()
except queue.Empty:
break

def _voice_bootstrap(self) -> None:
try:
self.root.after(0, self._voice_set_status, "Loading voice model...")
self._voice_ensure_model()
if not self.voice_running:
return

self.root.after(0, self._voice_set_status, "Starting microphone...")
if not self._voice_start_stream():
self.voice_running = False
return

if not self.voice_running:
self._voice_stop_stream()
return

self.root.after(0, self._voice_set_status, "Say: '<number> <recipe or ingredient name>'")
if self._voice_worker_thread is None or not self._voice_worker_thread.is_alive():
self._voice_worker_thread = threading.Thread(target=self._voice_worker, daemon=True)
self._voice_worker_thread.start()
except Exception as exc:
self.voice_running = False
self.root.after(0, self._voice_set_status, f"Mic error: {exc}")

def _voice_resample(self, audio, input_rate, target_rate):
if input_rate == target_rate or audio.size == 0:
return audio
import numpy as np
duration = len(audio) / float(input_rate)
target_len = max(1, int(duration * target_rate))
x_old = np.linspace(0.0, 1.0, num=len(audio), endpoint=False)
x_new = np.linspace(0.0, 1.0, num=target_len, endpoint=False)
return np.interp(x_new, x_old, audio).astype(np.float32)

def _voice_anim_tick(self, step=0) -> None:
if not self.voice_running or not self.voice_anim_label:
return
dots = "." * ((step % 3) + 1)
self.voice_anim_label.config(text=f"Listening{dots}")
self.root.after(400, self._voice_anim_tick, step + 1)

def _voice_worker(self) -> None:
import numpy as np
recipes = [r.name for r in db.list_recipes(DB_PATH)]
dry_containers = db.get_dry_containers(DB_PATH)
wet_containers = db.get_wet_containers(DB_PATH)

sample_rate = int(self.voice_input_rate or VOICE_SAMPLE_RATE)
max_chunk = int(sample_rate * VOICE_MAX_CHUNK_SEC)
min_chunk = int(sample_rate * VOICE_MIN_CHUNK_SEC)
silence_len = int(sample_rate * VOICE_SILENCE_SEC)
overlap_len = int(sample_rate * VOICE_OVERLAP_SEC)
last_flush = time.monotonic()

while self.voice_running:
try:
chunk = self.voice_queue.get(timeout=0.5)
except queue.Empty:
continue

self.voice_buffer.append(chunk)
samples = np.concatenate(self.voice_buffer, axis=0) if self.voice_buffer else np.zeros((0, 1), dtype=np.float32)
if len(samples) < min_chunk:
continue

recent = samples[-silence_len:] if len(samples) >= silence_len else samples
rms = float(np.sqrt(np.mean(np.square(recent)))) if recent.size else 0.0
self.root.after(
0,
self._voice_set_debug,
f"Mic: {self.voice_input_device_name} | Rate: {sample_rate} Hz | RMS: {rms:.4f} | Q: {self.voice_queue.qsize()}",
)
elapsed = time.monotonic() - last_flush
should_flush = (
rms < VOICE_SILENCE_RMS
or len(samples) >= max_chunk
or elapsed >= VOICE_FORCED_FLUSH_SEC
)
if not should_flush:
continue

audio = samples.flatten().astype(np.float32)
audio = self._voice_resample(audio, sample_rate, VOICE_SAMPLE_RATE)
keep = samples[-overlap_len:] if overlap_len > 0 else np.zeros((0, 1), dtype=np.float32)
self.voice_buffer = [keep]
last_flush = time.monotonic()

try:
segments, _ = self.voice_model.transcribe(
audio,
language="en",
task="transcribe",
temperature=0.0,
beam_size=1,
best_of=1,
condition_on_previous_text=False,
vad_filter=True,
initial_prompt=(
"Transcribe only spoken commands for a dispenser. "
"Expected words are numbers, recipe names, ingredient names, yes, and no."
),
)
text = " ".join(seg.text for seg in segments).strip()
except Exception as exc:
self.root.after(0, self._voice_set_status, f"STT error: {exc}")
continue

if not text:
continue

self.root.after(0, self._voice_set_transcript, text)

if self.voice_state == "listen_command":
action = self._voice_parse_command(text, recipes, dry_containers, wet_containers)
if action:
self.voice_pending_action = action
self.voice_state = "confirm"
if action.get("kind") == "recipe":
summary = f"{action.get('name')} x{action.get('count')}"
else:
summary = f"{action.get('name')} {action.get('amount')} {action.get('unit')}"
self.root.after(0, self._voice_set_status, f"Detected {summary}. Say yes or no.")
else:
self.root.after(0, self._voice_set_status, "Say: '<number> <recipe or ingredient name>'")
elif self.voice_state == "confirm":
decision = self._voice_parse_yes_no(text)
if decision == "yes":
self.root.after(0, self._voice_execute_recipe)
elif decision == "no":
self.voice_state = "listen_command"
self.root.after(0, self._voice_set_status, "Okay. Say: '<number> <recipe or ingredient name>'")

def _voice_parse_command(self, text: str, recipes, dry_containers, wet_containers):
"""Parse '<number> <name>' where name can be a recipe or a dry/wet container."""
text = text.strip().lower()
if not text:
return None

norm_text = re.sub(r"[^a-z0-9\s]", " ", text)
norm_text = re.sub(r"\s+", " ", norm_text).strip()
if not norm_text:
return None

number_words = {
"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
"six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

def norm_name(name: str) -> str:
name = (name or "").strip().lower()
name = re.sub(r"[^a-z0-9\s]", " ", name)
name = re.sub(r"\s+", " ", name).strip()
return name

def extract_qty(prefix: str):
prefix = (prefix or "").strip()
if not prefix:
return None
digits = re.findall(r"\b\d+\b", prefix)
if digits:
try:
return int(digits[-1])
except ValueError:
return None
tokens = prefix.split()
for token in reversed(tokens):
if token in number_words:
return number_words[token]
return None

candidates = []
# Each entry: (score, priority, -idx, action_dict)
priority = {"recipe": 3, "wet": 2, "dry": 1}

for name in recipes or []:
n = norm_name(name)
if not n:
continue
idx = norm_text.find(n)
if idx < 0:
continue
qty = extract_qty(norm_text[:idx])
if qty is None:
continue
count = max(1, min(10, int(qty)))
action = {"kind": "recipe", "name": name, "count": count}
candidates.append((len(n), priority["recipe"], -idx, action))

for cont in dry_containers or []:
n = norm_name(getattr(cont, "name", ""))
if not n:
continue
idx = norm_text.find(n)
if idx < 0:
continue
qty = extract_qty(norm_text[:idx])
if qty is None:
continue
amount = max(1, min(9999, int(qty)))
action = {
"kind": "dry",
"id": int(getattr(cont, "cid", 0)),
"name": getattr(cont, "name", ""),
"amount": amount,
"unit": "g",
}
candidates.append((len(n), priority["dry"], -idx, action))

for cont in wet_containers or []:
n = norm_name(getattr(cont, "name", ""))
if not n:
continue
idx = norm_text.find(n)
if idx < 0:
continue
qty = extract_qty(norm_text[:idx])
if qty is None:
continue
amount = max(1, min(9999, int(qty)))
action = {
"kind": "wet",
"id": int(getattr(cont, "cid", 0)),
"name": getattr(cont, "name", ""),
"amount": amount,
"unit": "ml",
}
candidates.append((len(n), priority["wet"], -idx, action))

if not candidates:
return None

candidates.sort(reverse=True)
return candidates[0][3]

def _voice_parse_yes_no(self, text: str):
text = text.strip().lower()
if any(word in text for word in ("yes", "yeah", "yep", "confirm", "proceed", "go")):
return "yes"
if any(word in text for word in ("no", "nope", "cancel", "stop")):
return "no"
return None

def _voice_execute_recipe(self) -> None:
action = self.voice_pending_action
self.voice_state = "idle"
self._voice_stop_stream()

if not action or not isinstance(action, dict) or not action.get("kind"):
self.voice_state = "listen_command"
try:
self._voice_start_stream()
except Exception as exc:
self.root.after(0, self._voice_set_status, f"Mic error: {exc}")
return

if self.serial is None:
self.voice_state = "listen_command"
self.root.after(0, self._voice_set_status, "No serial connection. Open Device Setup.")
try:
self._voice_start_stream()
except Exception as exc:
self.root.after(0, self._voice_set_status, f"Mic error: {exc}")
return

kind = action.get("kind")
if kind == "recipe":
recipes = db.list_recipes(DB_PATH)
match = [r for r in recipes if r.name == action.get("name")]
if not match:
self.voice_state = "listen_command"
self.root.after(0, self._voice_set_status, "Recipe not found.")
try:
self._voice_start_stream()
except Exception as exc:
self.root.after(0, self._voice_set_status, f"Mic error: {exc}")
return
self.active_recipe_id = match[0].rid
self.batch_count = int(action.get("count") or 1)
self.start_dispense()
return

cid = int(action.get("id") or 0)
amount = int(action.get("amount") or 0)
if cid <= 0 or amount <= 0:
self.voice_state = "listen_command"
self.root.after(0, self._voice_set_status, "Invalid ingredient command.")
try:
self._voice_start_stream()
except Exception as exc:
self.root.after(0, self._voice_set_status, f"Mic error: {exc}")
return

self.root.after(0, self._voice_set_status, "Sending command...")
if kind == "dry":
steps = 2
for c in db.get_dry_containers(DB_PATH):
if c.cid == cid:
steps = int(c.steps_per_gram or 2)
break
payload = _build_single_dispense_payload_fallback(
"D",
cid,
amount,
steps_per_gram=steps,
)
            status = self._serial_send_wait_status(payload, timeout=180.0)
if status == "STATUS:OK":
db.apply_dry_dispense(DB_PATH, [(cid, amount)])
else:
ms_per_ml = 1000
for c in db.get_wet_containers(DB_PATH):
if c.cid == cid:
ms_per_ml = int(c.ms_per_ml or 1000)
break
payload = _build_single_dispense_payload_fallback(
"W",
cid,
amount,
ms_per_ml=ms_per_ml,
)
            wet_seconds = (float(amount) * float(ms_per_ml)) / 1000.0
            timeout_s = max(30.0, wet_seconds + 15.0)
            status = self._serial_send_wait_status(payload, timeout=timeout_s)
if status == "STATUS:OK":
db.apply_wet_dispense(DB_PATH, [(cid, amount)])

name = f"Single: {action.get('name') or ''}".strip()
db.log_dispense(DB_PATH, time.strftime("%Y-%m-%d %H:%M:%S"), name, 1, self._format_status(status))
if self.voice_running:
self.show_voice()
else:
self.show_dashboard()

def run(self) -> None:
self.root.mainloop()


def main():
parser = argparse.ArgumentParser()
parser.add_argument("--port", help="Serial port (pty or USB)")
parser.add_argument("--baud", type=int)
args = parser.parse_args()

app = SimulatorGUI(args.port, args.baud)
app.run()


if __name__ == "__main__":
main()
