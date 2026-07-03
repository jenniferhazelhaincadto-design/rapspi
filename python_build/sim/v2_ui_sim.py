import argparse
import json
import sys
from pathlib import Path
from tkinter import Tk, Canvas, Frame, Label, Button, Scrollbar, StringVar, Entry, PhotoImage

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

try:
    from services import db
    from services.protocol import build_dispense_payload, build_clean_payload, build_stop_payload, build_levels_payload
    from services.serial_link import SerialLink
    from config import DB_PATH
except ModuleNotFoundError:
    sys.path.insert(0, str(ROOT_DIR.parent))
    from services import db
    from services.protocol import build_dispense_payload, build_clean_payload, build_stop_payload, build_levels_payload
    from services.serial_link import SerialLink
    from config import DB_PATH


ASSETS_ROOT = Path(__file__).resolve().parents[2] / "simulator_gui" / "assets"


class V2SimApp:
    def __init__(self, port: str, baud: int) -> None:
        db.init_db(DB_PATH)
        self.serial = SerialLink(port, baud, simulate=False)

        self.root = Tk()
        self.root.title("CONDIMIX v2 Simulator")
        self.root.geometry("520x820+40+20")
        self.root.configure(bg="#f7f1e6")
        self.root.resizable(False, False)

        self.font_title = ("Quicksand", 22, "bold")
        self.font_sub = ("Quicksand", 14)
        self.font_btn = ("Quicksand", 16, "bold")

        self._images = {}
        self._bg_image = None
        self._active_recipe_id = None
        self._batch_count = 1

        self.show_dashboard()

    def _load_image(self, key: str, path: Path):
        if key in self._images:
            return self._images[key]
        if not path.exists():
            return None
        try:
            img = PhotoImage(file=str(path))
            self._images[key] = img
            return img
        except Exception:
            return None

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

    def _set_background(self, asset_rel: str):
        img = self._load_image(asset_rel, ASSETS_ROOT / asset_rel)
        if not img:
            self.root.configure(bg="#f7f1e6")
            return
        self._bg_image = img
        bg = Label(self.root, image=img)
        bg.image = img
        bg.place(x=0, y=0, relwidth=1, relheight=1)

    def _header(self, title: str):
        banner = Canvas(self.root, width=520, height=110, highlightthickness=0, bg="#f7f1e6")
        banner.place(x=0, y=0)
        banner.create_rectangle(0, 0, 520, 110, fill="#ffe8d6", outline="")
        banner.create_oval(-80, -60, 120, 110, fill="#f2cc8f", outline="")
        banner.create_oval(420, -40, 640, 120, fill="#e07a5f", outline="")
        Label(self.root, text=title, font=self.font_title, bg="#ffe8d6", fg="#3d405b").place(x=20, y=30)

    def show_dashboard(self):
        self.clear()
        self._set_background("dashboard/image_1.png")
        self._header("CONDIMIX v2")

        card_bg = "#fff7ee"
        accent = "#81b29a"
        text = "#3d405b"

        def card(x, y, label, action):
            frame = Frame(self.root, bg=card_bg, highlightbackground="#eadbc8", highlightthickness=1)
            frame.place(x=x, y=y, width=200, height=120)
            Label(frame, text=label, font=self.font_btn, bg=card_bg, fg=text).place(x=10, y=20)
            Button(frame, text="Open", font=("Quicksand", 12, "bold"), bg=accent, fg="white",
                   command=action, relief="flat").place(x=10, y=70, width=80, height=30)

        card(40, 160, "Recipe Menu", self.show_recipe_menu)
        card(280, 160, "Levels", self.show_levels)
        card(40, 310, "Cleaning", self.show_cleaning)
        card(280, 310, "Logs", self.show_logs)

        Button(self.root, text="Exit", font=("Quicksand", 14, "bold"), bg="#e07a5f", fg="white",
               command=self.root.destroy, relief="flat").place(x=200, y=720, width=120, height=40)

    def show_recipe_menu(self):
        self.clear()
        self._set_background("menu/button_7.png")
        self._header("Select Recipe")

        wrapper = Frame(self.root, bg="#f7f1e6")
        wrapper.place(x=40, y=140, width=440, height=560)
        canvas = Canvas(wrapper, bg="#f7f1e6", highlightthickness=0)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar = Scrollbar(wrapper, orient="vertical", command=canvas.yview)
        scrollbar.pack(side="right", fill="y")
        canvas.configure(yscrollcommand=scrollbar.set)
        frame = Frame(canvas, bg="#f7f1e6")
        canvas.create_window((0, 0), window=frame, anchor="nw")

        def on_configure(_event):
            canvas.configure(scrollregion=canvas.bbox("all"))

        frame.bind("<Configure>", on_configure)

        for rec in db.list_recipes(DB_PATH):
            Button(frame, text=rec.name, font=self.font_btn, bg="#fff7ee", fg="#3d405b",
                   relief="flat", command=lambda rid=rec.rid: self.show_recipe_detail(rid)).pack(pady=8, fill="x")

        Button(self.root, text="Back", font=self.font_btn, bg="#e07a5f", fg="white",
               command=self.show_dashboard, relief="flat").place(x=40, y=720, width=120, height=40)

    def show_recipe_detail(self, recipe_id: int):
        self.clear()
        recipe, dry, wet = db.get_recipe_detail(DB_PATH, recipe_id)
        self._active_recipe_id = recipe_id
        self._set_background("recipe/image_2.png")
        self._header(recipe[1])
        Label(self.root, text="Batch", font=self.font_sub, bg="#f7f1e6", fg="#3d405b").place(x=40, y=140)

        count_label = Label(self.root, text=str(self._batch_count), font=("Quicksand", 20, "bold"), bg="#f7f1e6")
        count_label.place(x=120, y=130, width=60, height=40)

        def change(delta: int):
            self._batch_count = max(1, min(10, self._batch_count + delta))
            count_label.config(text=str(self._batch_count))

        Button(self.root, text="-", font=self.font_btn, command=lambda: change(-1), bg="#f2cc8f", relief="flat").place(x=40, y=130, width=40, height=40)
        Button(self.root, text="+", font=self.font_btn, command=lambda: change(1), bg="#f2cc8f", relief="flat").place(x=190, y=130, width=40, height=40)

        y = 200
        Label(self.root, text="Dry", font=("Quicksand", 14, "bold"), bg="#f7f1e6").place(x=40, y=y)
        y += 30
        for dry_id, grams in dry:
            Label(self.root, text=f"Dry {dry_id}: {grams} g", font=self.font_sub, bg="#f7f1e6").place(x=60, y=y)
            y += 26

        y += 10
        Label(self.root, text="Wet", font=("Quicksand", 14, "bold"), bg="#f7f1e6").place(x=40, y=y)
        y += 30
        for wet_id, ml in wet:
            Label(self.root, text=f"Wet {wet_id}: {ml} ml", font=self.font_sub, bg="#f7f1e6").place(x=60, y=y)
            y += 26

        status_var = StringVar(value="Idle")
        Label(self.root, textvariable=status_var, font=self.font_sub, bg="#f7f1e6", fg="#3d405b").place(x=40, y=620)

        def dispense():
            recipe_row, dry_rows, wet_rows = db.get_recipe_detail(DB_PATH, self._active_recipe_id)
            dry_list = [{"id": d[0], "g": d[1] * self._batch_count} for d in dry_rows]
            wet_containers = {c.cid: c.ms_per_ml for c in db.get_wet_containers(DB_PATH)}
            wet_list = [
                {"id": w[0], "ml": w[1] * self._batch_count, "ms_per_ml": wet_containers.get(w[0], 100)}
                for w in wet_rows
            ]
            payload = build_dispense_payload(dry_list, wet_list)
            status = self.serial.send_and_wait_done(payload)
            if status == "STATUS:OK":
                used = [(item["id"], item["ml"]) for item in wet_list]
                db.apply_wet_dispense(DB_PATH, used)
            db.log_dispense(DB_PATH, time.strftime("%Y-%m-%d %H:%M:%S"), recipe_row[1], self._batch_count, self._format_status(status))
            status_var.set(self._format_status(status))

        Button(self.root, text="Dispense", font=self.font_btn, bg="#81b29a", fg="white",
               command=dispense, relief="flat").place(x=320, y=720, width=140, height=40)
        Button(self.root, text="Back", font=self.font_btn, bg="#e07a5f", fg="white",
               command=self.show_recipe_menu, relief="flat").place(x=40, y=720, width=120, height=40)

    def show_levels(self):
        self.clear()
        self._set_background("dispensing/image_1.png")
        self._header("Ingredient Levels")
        status_var = StringVar(value="Requesting levels...")
        Label(self.root, textvariable=status_var, font=self.font_sub, bg="#f7f1e6", fg="#3d405b").place(x=40, y=140)

        response = self.serial.send_and_wait_json(build_levels_payload(), timeout=2.0)
        if not response or response.get("type") != "levels":
            status_var.set("No level data")
        else:
            status_var.set("Dry container levels (g)")
            y = 190
            for item in response.get("dry", []):
                cid = item.get("id")
                grams = item.get("g")
                Label(self.root, text=f"Dry {cid}: {grams} g", font=self.font_sub, bg="#f7f1e6").place(x=40, y=y)
                y += 26
            y += 10
            Label(self.root, text="Wet (estimated ml)", font=("Quicksand", 14, "bold"), bg="#f7f1e6").place(x=40, y=y)
            y += 30
            for cont in db.get_wet_containers(DB_PATH):
                Label(self.root, text=f"Wet {cont.cid}: {cont.remaining_ml}/{cont.capacity_ml} ml", font=self.font_sub, bg="#f7f1e6").place(x=40, y=y)
                y += 26

        Button(self.root, text="Back", font=self.font_btn, bg="#e07a5f", fg="white",
               command=self.show_dashboard, relief="flat").place(x=40, y=720, width=120, height=40)

    def show_cleaning(self):
        self.clear()
        self._set_background("dispensing/image_1.png")
        self._header("Cleaning")
        status_var = StringVar(value="Idle")
        Label(self.root, textvariable=status_var, font=self.font_sub, bg="#f7f1e6", fg="#3d405b").place(x=40, y=140)

        def start_clean():
            status_var.set("Cleaning...")
            status = self.serial.send_and_wait_done(build_clean_payload(), timeout=60)
            status_var.set(self._format_status(status))

        def stop_clean():
            self.serial.send(build_stop_payload())
            status_var.set("Stop sent")

        Button(self.root, text="Start Clean", font=self.font_btn, bg="#81b29a", fg="white",
               command=start_clean, relief="flat").place(x=40, y=220, width=160, height=40)
        Button(self.root, text="Stop", font=self.font_btn, bg="#e07a5f", fg="white",
               command=stop_clean, relief="flat").place(x=220, y=220, width=120, height=40)
        Button(self.root, text="Back", font=self.font_btn, bg="#e07a5f", fg="white",
               command=self.show_dashboard, relief="flat").place(x=40, y=720, width=120, height=40)

    def show_logs(self):
        self.clear()
        self._set_background("menu/button_7.png")
        self._header("Logs")
        rows = db.get_logs(DB_PATH)
        y = 140
        for ts, recipe, batches, status in rows[:12]:
            Label(self.root, text=f"{ts} | {recipe} x{batches} | {status}", font=("Quicksand", 11), bg="#f7f1e6").place(x=40, y=y)
            y += 24
        Button(self.root, text="Back", font=self.font_btn, bg="#e07a5f", fg="white",
               command=self.show_dashboard, relief="flat").place(x=40, y=720, width=120, height=40)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True, help="Serial port (pty or USB)")
    parser.add_argument("--baud", type=int, default=9600)
    args = parser.parse_args()
    app = V2SimApp(args.port, args.baud)
    app.root.mainloop()


if __name__ == "__main__":
    main()
