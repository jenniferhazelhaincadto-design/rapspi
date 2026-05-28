# CONDIMIX v2 Python Build

This folder contains the Tkinter UI for the CONDIMIX v2 system. It manages recipes, schedules, logs, and voice control, then sends JSON commands to the Arduino Mega over serial.

## What the UI Does

- Dashboard: entry point for all screens.
- Recipe Menu: select a recipe and set batch count.
- Dispense: sends a JSON dispense command with dry (grams) and wet (ml) data.
- Settings:
  - Dry Containers: rename container labels.
  - Wet Condiments: rename wet containers, set ms_per_ml, and track estimated remaining ml.
  - Add Recipe: create dry + wet recipe values.
- Cleaning Mode: sends a clean command, shows status.
- Logs: shows recent dispense activity and status results.
- Voice Control: listens for "<number> <recipe name>" and confirms with yes/no.

## Serial Protocol (Python -> Mega)

All commands are single-line JSON with a trailing newline:

1) Dispense
{
  "cmd": "dispense",
  "recipe": "Recipe Name",
  "batches": 2,
  "dry": [
    {"id": 1, "g": 50},
    {"id": 2, "g": 10}
  ],
  "wet": [
    {"id": 1, "ml": 15, "ms_per_ml": 120},
    {"id": 2, "ml": 5, "ms_per_ml": 100}
  ]
}

2) Clean
{"cmd": "clean"}

3) Stop
{"cmd": "stop"}

4) Levels (request)
{"cmd": "levels"}

Notes:
- id values are 1-based (container 1..6 for dry, 1..4 for wet).
- ms_per_ml sets wet pump timing (milliseconds per ml).
- Wet remaining ml is estimated and reduced after successful dispense.
- A newline is appended after each JSON object.

## Serial Responses (Mega -> Python)

The Mega replies with one of these status lines:
- STATUS:OK
- STATUS:STOPPED
- STATUS:EMERGENCY
- STATUS:ERROR

Levels response (Mega -> Python):
{"type":"levels","dry":[{"id":1,"g":245},{"id":2,"g":180}]}

Python treats a missing response as STATUS:TIMEOUT.

## Run the UI

From this folder:

1) Create and activate a venv (recommended)
python3 -m venv .venv
source .venv/bin/activate

2) Install dependencies
pip install -r requirements.txt

3) Start the UI
python app.py

## Simulation (Mega + CLI)

1) Start the Mega simulator
python sim/mega_sim.py

It will print a pseudo-tty path, for example:
/dev/pts/7

2) Start the CLI client in a new terminal
python sim/v2_cli_client.py --port /dev/pts/7

Or run the UI simulator:
python sim/v2_ui_sim.py --port /dev/pts/7

Or run the full simulator GUI (legacy backgrounds + v2 logic):
python sim/v2_simulator_gui.py --port /dev/pts/7

## Config

Edit config.py to change:
- DB path
- Serial port and baud
- Simulation mode
- Voice model settings
