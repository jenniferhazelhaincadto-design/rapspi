import threading
import time
from datetime import datetime
from pathlib import Path
from tkinter import Tk, Button, Label, Entry, StringVar, Frame, Scrollbar, Canvas, END

import schedule

from config import DB_PATH, SERIAL_PORT, SERIAL_BAUD, SERIAL_SIMULATE, VOICE_MODEL, VOICE_COMPUTE_TYPE
from services import db
from services.protocol import build_dispense_payload, build_clean_payload, build_stop_payload, build_levels_payload
from services.serial_link import SerialLink


VOICE_SAMPLE_RATE = 16000
VOICE_BLOCK_SIZE = 1024
VOICE_MAX_CHUNK_SEC = 6
VOICE_MIN_CHUNK_SEC = 2
VOICE_SILENCE_SEC = 0.7
VOICE_SILENCE_RMS = 0.008
VOICE_OVERLAP_SEC = 0.5


class App:
    def __init__(self) -> None:
        db.init_db(DB_PATH)
        self.root = Tk()
        self.root.title("CONDIMIX v2")
        self.root.geometry("480x800+0+0")
        self.root.configure(bg="#eef0ed")
        self.root.resizable(False, False)

        self.serial = SerialLink(SERIAL_PORT, SERIAL_BAUD, simulate=SERIAL_SIMULATE)

        self.active_recipe_id = None
        self.batch_count = 1

        # voice
        self.voice_running = False
        self.voice_state = "idle"  # listen_command | confirm
        self.voice_queue = []
        self.voice_model = None
        self.voice_stream = None
        self.voice_pending_recipe = None
        self.voice_pending_count = 1
        self.voice_status_label = None
        self.voice_transcript_label = None
        self.voice_anim_label = None

        self._start_scheduler()
        self.show_dashboard()

    def clear(self) -> None:
        for w in self.root.winfo_children():
            w.destroy()

    def _format_status(self, status: str) -> str:
        normalized = status.strip()
        pretty = {
            "STATUS:OK": "Success",
            "STATUS:STOPPED": "Stopped",
            "STATUS:EMERGENCY": "Emergency Stop",
            "STATUS:ERROR": "Error",
            "STATUS:TIMEOUT": "Timeout",
        }
        return pretty.get(normalized, normalized)

    def show_dashboard(self) -> None:
        self.clear()
        bg = "#eef0ed"
        btn_bg = "#FFFF8F"
        fg = "#30071F"

        left_x = 40
        right_x = 260
        top_y = 80
        btn_w = 180
        btn_h = 120
        row_gap = 20

        Button(self.root, bg=btn_bg, fg=fg, text="Recipe\nMenu", font=("Arial", 18, "bold"),
               command=self.show_recipe_menu).place(x=left_x, y=top_y, width=btn_w, height=btn_h)
        Button(self.root, bg=btn_bg, fg=fg, text="Schedule\nDispense", font=("Arial", 18, "bold"),
               command=self.show_schedule).place(x=right_x, y=top_y, width=btn_w, height=btn_h)

        row2_y = top_y + btn_h + row_gap
        Button(self.root, bg=btn_bg, fg=fg, text="Cleaning\nMode", font=("Arial", 18, "bold"),
               command=self.show_cleaning).place(x=left_x, y=row2_y, width=btn_w, height=btn_h)
        Button(self.root, bg=btn_bg, fg=fg, text="Ingredient\nLevel", font=("Arial", 18, "bold"),
               command=self.show_levels).place(x=right_x, y=row2_y, width=btn_w, height=btn_h)

        row3_y = row2_y + btn_h + row_gap
        Button(self.root, bg=btn_bg, fg=fg, text="Dispensing\nLog", font=("Arial", 18, "bold"),
               command=self.show_logs).place(x=left_x, y=row3_y, width=btn_w, height=btn_h)
        Button(self.root, bg=btn_bg, fg=fg, text="Settings", font=("Arial", 18, "bold"),
               command=self.show_settings).place(x=right_x, y=row3_y, width=btn_w, height=btn_h)

        row4_y = row3_y + btn_h + row_gap
        Button(self.root, bg=btn_bg, fg=fg, text="Power\nOff", font=("Arial", 18, "bold"),
               command=self.show_shutdown).place(x=left_x, y=row4_y, width=btn_w, height=btn_h)
        Button(self.root, bg=btn_bg, fg=fg, text="Lock", font=("Arial", 18, "bold"),
               command=self.show_lock).place(x=right_x, y=row4_y, width=btn_w, height=btn_h)

        Button(self.root, bg=btn_bg, fg=fg, text="Voice PTT", font=("Arial", 18, "bold"),
               command=self.show_voice).place(x=150, y=row4_y + btn_h + 20, width=btn_w, height=btn_h)

    def show_recipe_menu(self) -> None:
        self.clear()
        Label(self.root, text="Select Recipe", font=("Arial", 22, "bold")).place(x=0, y=20, width=480)
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
            Button(frame, text=rec.name, font=("Arial", 18, "bold"), bg="#FFFF8F",
                   command=lambda rid=rec.rid: self.show_recipe_detail(rid)).pack(pady=8, fill="x")

        Button(self.root, text="Back", font=("Arial", 16, "bold"), command=self.show_dashboard).place(x=20, y=700, width=120, height=50)

    def show_recipe_detail(self, recipe_id: int) -> None:
        self.clear()
        recipe, dry, wet = db.get_recipe_detail(DB_PATH, recipe_id)
        self.active_recipe_id = recipe_id
        Label(self.root, text=recipe[1], font=("Arial", 24, "bold")).place(x=0, y=20, width=480)

        self.batch_count = 1
        batch_label = Label(self.root, text=str(self.batch_count), font=("Arial", 28, "bold"))
        batch_label.place(x=200, y=110, width=80, height=50)

        def change(delta: int):
            self.batch_count = max(1, min(10, self.batch_count + delta))
            batch_label.config(text=str(self.batch_count))

        Button(self.root, text="-", font=("Arial", 20, "bold"), command=lambda: change(-1)).place(x=120, y=110, width=60, height=50)
        Button(self.root, text="+", font=("Arial", 20, "bold"), command=lambda: change(1)).place(x=300, y=110, width=60, height=50)

        y = 200
        for dry_id, grams in dry:
            Label(self.root, text=f"Dry {dry_id}: {grams} g", font=("Arial", 14)).place(x=40, y=y, width=400, height=25)
            y += 28
        for wet_id, ml in wet:
            Label(self.root, text=f"Wet {wet_id}: {ml} ml", font=("Arial", 14)).place(x=40, y=y, width=400, height=25)
            y += 28

        Button(self.root, text="Back", font=("Arial", 16, "bold"), command=self.show_recipe_menu).place(x=20, y=700, width=120, height=50)
        Button(self.root, text="Dispense", font=("Arial", 16, "bold"), command=self.start_dispense).place(x=320, y=700, width=140, height=50)

    def start_dispense(self) -> None:
        recipe, dry, wet = db.get_recipe_detail(DB_PATH, self.active_recipe_id)
        dry_list = [{"id": d[0], "g": d[1] * self.batch_count} for d in dry]
        wet_containers = {c.cid: c.ms_per_ml for c in db.get_wet_containers(DB_PATH)}
        wet_list = [{"id": w[0], "ml": w[1] * self.batch_count, "ms_per_ml": wet_containers.get(w[0], 100)} for w in wet]

        payload = build_dispense_payload(dry_list, wet_list)
        status = self.serial.send_and_wait_done(payload)
        if status == "STATUS:OK":
            used = [(item["id"], item["ml"]) for item in wet_list]
            db.apply_wet_dispense(DB_PATH, used)
        db.log_dispense(DB_PATH, datetime.now().isoformat(timespec="seconds"), recipe[1], self.batch_count, self._format_status(status))
        self.show_voice() if self.voice_running else self.show_dashboard()

    def show_settings(self) -> None:
        self.clear()
        Label(self.root, text="Settings", font=("Arial", 24, "bold")).place(x=0, y=20, width=480)
        Button(self.root, text="Dry Containers", font=("Arial", 16, "bold"), command=self.show_dry_settings).place(x=80, y=120, width=320, height=60)
        Button(self.root, text="Wet Condiments", font=("Arial", 16, "bold"), command=self.show_wet_settings).place(x=80, y=200, width=320, height=60)
        Button(self.root, text="Add Recipe", font=("Arial", 16, "bold"), command=self.show_add_recipe).place(x=80, y=280, width=320, height=60)
        Button(self.root, text="Back", font=("Arial", 16, "bold"), command=self.show_dashboard).place(x=180, y=700, width=120, height=50)

    def show_dry_settings(self) -> None:
        self.clear()
        Label(self.root, text="Dry Containers", font=("Arial", 22, "bold")).place(x=0, y=20, width=480)
        entries = []
        containers = db.get_dry_containers(DB_PATH)
        y = 100
        for cont in containers:
            Label(self.root, text=f"{cont.cid}", font=("Arial", 14)).place(x=40, y=y, width=30, height=30)
            var = StringVar(value=cont.name)
            Entry(self.root, textvariable=var).place(x=80, y=y, width=300, height=30)
            entries.append(var)
            y += 40

        def save():
            db.set_dry_containers(DB_PATH, [v.get() for v in entries])
            self.show_settings()

        Button(self.root, text="Save", font=("Arial", 16, "bold"), command=save).place(x=320, y=700, width=120, height=50)
        Button(self.root, text="Back", font=("Arial", 16, "bold"), command=self.show_settings).place(x=20, y=700, width=120, height=50)

    def show_wet_settings(self) -> None:
        self.clear()
        Label(self.root, text="Wet Condiments", font=("Arial", 22, "bold")).place(x=0, y=20, width=480)
        entries = []
        containers = db.get_wet_containers(DB_PATH)
        Label(self.root, text="Name", font=("Arial", 10, "bold")).place(x=60, y=70, width=180, height=20)
        Label(self.root, text="ms/ml", font=("Arial", 10, "bold")).place(x=250, y=70, width=60, height=20)
        Label(self.root, text="Cap", font=("Arial", 10, "bold")).place(x=320, y=70, width=60, height=20)
        Label(self.root, text="Left", font=("Arial", 10, "bold")).place(x=390, y=70, width=60, height=20)
        y = 100
        for cont in containers:
            Label(self.root, text=f"{cont.cid}", font=("Arial", 14)).place(x=20, y=y, width=30, height=30)
            name_var = StringVar(value=cont.name)
            ms_var = StringVar(value=str(cont.ms_per_ml))
            cap_var = StringVar(value=str(cont.capacity_ml))
            rem_var = StringVar(value=str(cont.remaining_ml))
            Entry(self.root, textvariable=name_var).place(x=60, y=y, width=180, height=30)
            Entry(self.root, textvariable=ms_var).place(x=250, y=y, width=60, height=30)
            Entry(self.root, textvariable=cap_var).place(x=320, y=y, width=60, height=30)
            Entry(self.root, textvariable=rem_var).place(x=390, y=y, width=60, height=30)
            entries.append((name_var, ms_var, cap_var, rem_var))
            y += 40

        def save():
            items = []
            for name_var, ms_var, cap_var, rem_var in entries:
                try:
                    ms = int(ms_var.get())
                except ValueError:
                    ms = 100
                try:
                    cap = int(cap_var.get())
                except ValueError:
                    cap = 1000
                try:
                    rem = int(rem_var.get())
                except ValueError:
                    rem = cap
                rem = max(0, min(rem, cap))
                items.append((name_var.get(), ms, cap, rem))
            db.set_wet_containers(DB_PATH, items)
            self.show_settings()

        Button(self.root, text="Save", font=("Arial", 16, "bold"), command=save).place(x=320, y=700, width=120, height=50)
        Button(self.root, text="Back", font=("Arial", 16, "bold"), command=self.show_settings).place(x=20, y=700, width=120, height=50)

    def show_add_recipe(self) -> None:
        self.clear()
        Label(self.root, text="Add Recipe", font=("Arial", 22, "bold")).place(x=0, y=20, width=480)
        name_var = StringVar()
        Entry(self.root, textvariable=name_var).place(x=60, y=70, width=360, height=30)

        dry_vars = []
        wet_vars = []
        y = 130
        for cont in db.get_dry_containers(DB_PATH):
            Label(self.root, text=cont.name, font=("Arial", 12)).place(x=20, y=y, width=200, height=25)
            var = StringVar(value="0")
            Entry(self.root, textvariable=var).place(x=240, y=y, width=80, height=25)
            dry_vars.append(var)
            y += 30

        for cont in db.get_wet_containers(DB_PATH):
            Label(self.root, text=cont.name, font=("Arial", 12)).place(x=20, y=y, width=200, height=25)
            var = StringVar(value="0")
            Entry(self.root, textvariable=var).place(x=240, y=y, width=80, height=25)
            wet_vars.append(var)
            y += 30

        def save():
            dry = [int(v.get() or 0) for v in dry_vars]
            wet = [int(v.get() or 0) for v in wet_vars]
            db.save_recipe(DB_PATH, name_var.get(), dry, wet)
            self.show_settings()

        Button(self.root, text="Save", font=("Arial", 16, "bold"), command=save).place(x=320, y=700, width=120, height=50)
        Button(self.root, text="Back", font=("Arial", 16, "bold"), command=self.show_settings).place(x=20, y=700, width=120, height=50)

    def show_logs(self) -> None:
        self.clear()
        Label(self.root, text="Logs", font=("Arial", 22, "bold")).place(x=0, y=20, width=480)
        rows = db.get_logs(DB_PATH)
        y = 80
        for ts, recipe, batches, status in rows[:15]:
            display_status = self._format_status(status)
            Label(self.root, text=f"{ts} | {recipe} x{batches} | {display_status}", font=("Arial", 11)).place(x=20, y=y, width=440, height=20)
            y += 22
        Button(self.root, text="Back", font=("Arial", 16, "bold"), command=self.show_dashboard).place(x=180, y=700, width=120, height=50)

    def show_schedule(self) -> None:
        self.clear()
        Label(self.root, text="Schedule", font=("Arial", 22, "bold")).place(x=0, y=20, width=480)
        Label(self.root, text="Time (HH:MM)", font=("Arial", 12)).place(x=40, y=100, width=150, height=30)
        time_var = StringVar(value="12:00")
        Entry(self.root, textvariable=time_var).place(x=200, y=100, width=120, height=30)

        recipes = db.list_recipes(DB_PATH)
        if not recipes:
            Label(self.root, text="No recipes available", font=("Arial", 12)).place(x=40, y=150, width=300, height=30)
            Button(self.root, text="Back", font=("Arial", 16, "bold"), command=self.show_dashboard).place(x=180, y=700, width=120, height=50)
            return

        selected = StringVar(value=recipes[0].name)
        y = 170
        for rec in recipes[:6]:
            Button(self.root, text=rec.name, font=("Arial", 12),
                   command=lambda r=rec.name: selected.set(r)).place(x=40, y=y, width=200, height=30)
            y += 35

        batches = StringVar(value="1")
        Entry(self.root, textvariable=batches).place(x=200, y=380, width=80, height=30)

        def schedule_it():
            t = time_var.get()
            recipe = selected.get()
            try:
                count = int(batches.get())
            except ValueError:
                count = 1
            schedule.every().day.at(t).do(lambda: self._scheduled_dispense(recipe, count))
            self.show_dashboard()

        Button(self.root, text="Set", font=("Arial", 16, "bold"), command=schedule_it).place(x=320, y=700, width=120, height=50)
        Button(self.root, text="Back", font=("Arial", 16, "bold"), command=self.show_dashboard).place(x=20, y=700, width=120, height=50)

    def _scheduled_dispense(self, recipe_name: str, batches: int) -> None:
        recipes = db.list_recipes(DB_PATH)
        match = [r for r in recipes if r.name == recipe_name]
        if not match:
            return
        self.active_recipe_id = match[0].rid
        self.batch_count = batches
        self.start_dispense()

    def show_cleaning(self) -> None:
        self.clear()
        Label(self.root, text="Cleaning Mode", font=("Arial", 22, "bold")).place(x=0, y=20, width=480)
        status_var = StringVar(value="Idle")
        Label(self.root, textvariable=status_var, font=("Arial", 12)).place(x=40, y=80, width=400, height=40)

        def start_clean():
            status_var.set("Cleaning...")
            result = self.serial.send_and_wait_done(build_clean_payload(), timeout=60)
            status_var.set(self._format_status(result))

        def stop_clean():
            self.serial.send(build_stop_payload())
            status_var.set("Stop sent")

        Button(self.root, text="Start Clean", font=("Arial", 16, "bold"), command=start_clean).place(x=80, y=160, width=320, height=60)
        Button(self.root, text="Stop", font=("Arial", 16, "bold"), command=stop_clean).place(x=80, y=240, width=320, height=60)
        Button(self.root, text="Back", font=("Arial", 16, "bold"), command=self.show_dashboard).place(x=180, y=700, width=120, height=50)

    def show_levels(self) -> None:
        self.clear()
        Label(self.root, text="Ingredient Levels", font=("Arial", 22, "bold")).place(x=0, y=20, width=480)
        status_var = StringVar(value="Requesting levels...")
        Label(self.root, textvariable=status_var, font=("Arial", 12)).place(x=40, y=80, width=400, height=40)

        response = self.serial.send_and_wait_json(build_levels_payload(), timeout=2.0)
        if not response or response.get("type") != "levels":
            status_var.set("No level data")
        else:
            status_var.set("Dry container levels (g)")
            y = 140
            for item in response.get("dry", []):
                cid = item.get("id")
                grams = item.get("g")
                Label(self.root, text=f"Dry {cid}: {grams} g", font=("Arial", 12)).place(x=40, y=y, width=400, height=24)
                y += 28
            y += 10
            Label(self.root, text="Wet (estimated ml)", font=("Arial", 12, "bold")).place(x=40, y=y, width=400, height=24)
            y += 28
            for cont in db.get_wet_containers(DB_PATH):
                Label(
                    self.root,
                    text=f"Wet {cont.cid}: {cont.remaining_ml}/{cont.capacity_ml} ml",
                    font=("Arial", 12),
                ).place(x=40, y=y, width=400, height=24)
                y += 28
        Button(self.root, text="Back", font=("Arial", 16, "bold"), command=self.show_dashboard).place(x=180, y=700, width=120, height=50)

    def show_shutdown(self) -> None:
        self.clear()
        Label(self.root, text="Power Off?", font=("Arial", 22, "bold")).place(x=0, y=200, width=480)
        Button(self.root, text="No", font=("Arial", 16, "bold"), command=self.show_dashboard).place(x=80, y=500, width=120, height=50)
        Button(self.root, text="Yes", font=("Arial", 16, "bold"), command=self.show_dashboard).place(x=280, y=500, width=120, height=50)

    def show_lock(self) -> None:
        self.clear()
        Label(self.root, text="Locked", font=("Arial", 22, "bold")).place(x=0, y=200, width=480)
        Button(self.root, text="Unlock", font=("Arial", 16, "bold"), command=self.show_dashboard).place(x=180, y=500, width=120, height=50)

    def show_voice(self) -> None:
        self.clear()
        self.voice_running = True
        self.voice_state = "listen_command"
        self.voice_queue = []
        self.voice_pending_recipe = None
        self.voice_pending_count = 1
        self.voice_model = None
        self._voice_ensure_model()

        Label(self.root, text="Voice Control", font=("Arial", 24, "bold")).place(x=0, y=30, width=480)
        self.voice_anim_label = Label(self.root, text="Listening...", font=("Arial", 18, "bold"))
        self.voice_anim_label.place(x=0, y=100, width=480, height=40)
        self.voice_status_label = Label(self.root, text="Say: '<number> <recipe name>'", font=("Arial", 14), wraplength=440)
        self.voice_status_label.place(x=20, y=160, width=440, height=60)
        self.voice_transcript_label = Label(self.root, text="Heard: ...", font=("Arial", 12), wraplength=440, justify="left")
        self.voice_transcript_label.place(x=20, y=240, width=440, height=80)

        Button(self.root, text="Exit", font=("Arial", 16, "bold"), command=self._voice_exit).place(x=180, y=700, width=120, height=50)

        self._voice_start_stream()
        self._voice_anim_tick()
        threading.Thread(target=self._voice_worker, daemon=True).start()

    def _voice_exit(self) -> None:
        self.voice_running = False
        self.voice_state = "idle"
        self._voice_stop_stream()
        self.show_dashboard()

    def _voice_ensure_model(self) -> None:
        if self.voice_model is None:
            from faster_whisper import WhisperModel
            self.voice_model = WhisperModel(VOICE_MODEL, device="cpu", compute_type=VOICE_COMPUTE_TYPE)

    def _voice_start_stream(self) -> None:
        import sounddevice as sd
        self.voice_stream = sd.InputStream(
            samplerate=VOICE_SAMPLE_RATE,
            channels=1,
            blocksize=VOICE_BLOCK_SIZE,
            callback=self._voice_audio_callback,
        )
        self.voice_stream.start()

    def _voice_stop_stream(self) -> None:
        if self.voice_stream:
            self.voice_stream.stop()
            self.voice_stream.close()
            self.voice_stream = None

    def _voice_audio_callback(self, indata, _frames, _time, _status) -> None:
        if self.voice_running:
            self.voice_queue.append(indata.copy())

    def _voice_anim_tick(self, step=0) -> None:
        if not self.voice_running or not self.voice_anim_label:
            return
        dots = "." * ((step % 3) + 1)
        self.voice_anim_label.config(text=f"Listening{dots}")
        self.root.after(400, self._voice_anim_tick, step + 1)

    def _voice_worker(self) -> None:
        import numpy as np
        recipes = [r.name for r in db.list_recipes(DB_PATH)]

        max_chunk = VOICE_SAMPLE_RATE * VOICE_MAX_CHUNK_SEC
        min_chunk = VOICE_SAMPLE_RATE * VOICE_MIN_CHUNK_SEC
        silence_len = int(VOICE_SAMPLE_RATE * VOICE_SILENCE_SEC)
        overlap_len = int(VOICE_SAMPLE_RATE * VOICE_OVERLAP_SEC)

        while self.voice_running:
            if not self.voice_queue:
                time.sleep(0.1)
                continue
            chunk = self.voice_queue.pop(0)
            self.voice_queue.append(chunk)
            samples = np.concatenate(self.voice_queue, axis=0) if self.voice_queue else np.zeros((0, 1), dtype=np.float32)
            if len(samples) < min_chunk:
                continue

            recent = samples[-silence_len:] if len(samples) >= silence_len else samples
            rms = float(np.sqrt(np.mean(np.square(recent)))) if recent.size else 0.0
            should_flush = rms < VOICE_SILENCE_RMS or len(samples) >= max_chunk
            if not should_flush:
                continue

            audio = samples.flatten()
            keep = samples[-overlap_len:] if overlap_len > 0 else np.zeros((0, 1), dtype=np.float32)
            self.voice_queue = [keep]

            try:
                segments, _ = self.voice_model.transcribe(audio, language="en")
                text = " ".join(seg.text for seg in segments).strip()
            except Exception:
                text = ""

            if not text:
                continue

            if self.voice_transcript_label:
                self.voice_transcript_label.config(text=f"Heard: {text}")

            if self.voice_state == "listen_command":
                recipe, count = self._voice_parse_recipe(text, recipes)
                if recipe:
                    self.voice_pending_recipe = recipe
                    self.voice_pending_count = count
                    self.voice_state = "confirm"
                    if self.voice_status_label:
                        self.voice_status_label.config(text=f"Detected {recipe} x{count}. Say yes or no.")
                else:
                    if self.voice_status_label:
                        self.voice_status_label.config(text="Say: '<number> <recipe name>'")
            elif self.voice_state == "confirm":
                decision = self._voice_parse_yes_no(text)
                if decision == "yes":
                    self._voice_execute_recipe()
                elif decision == "no":
                    self.voice_state = "listen_command"
                    if self.voice_status_label:
                        self.voice_status_label.config(text="Okay. Say: '<number> <recipe name>'")

    def _voice_parse_recipe(self, text: str, recipes):
        text = text.strip().lower()
        tokens = text.split()
        if not tokens or not tokens[0].isdigit():
            return None, None
        count = int(tokens[0])
        remainder = " ".join(tokens[1:]).strip()
        if not remainder:
            return None, None
        recipe = None
        for name in recipes:
            if name and name.lower() in remainder:
                recipe = name
                break
        return recipe, count

    def _voice_parse_yes_no(self, text: str):
        text = text.strip().lower()
        if "yes" in text:
            return "yes"
        if "no" in text:
            return "no"
        return None

    def _voice_execute_recipe(self) -> None:
        self.voice_state = "idle"
        self._voice_stop_stream()
        recipes = db.list_recipes(DB_PATH)
        match = [r for r in recipes if r.name == self.voice_pending_recipe]
        if not match:
            if self.voice_status_label:
                self.voice_status_label.config(text="Recipe not found.")
            self.voice_state = "listen_command"
            self._voice_start_stream()
            return
        self.active_recipe_id = match[0].rid
        self.batch_count = self.voice_pending_count
        self.start_dispense()

    def _start_scheduler(self) -> None:
        def run_scheduler():
            while True:
                schedule.run_pending()
                time.sleep(1)
        threading.Thread(target=run_scheduler, daemon=True).start()

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    App().run()
