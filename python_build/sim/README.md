# v2 Serial Simulation

This folder contains two scripts:

- mega_sim.py: simulates the Arduino Mega over a pseudo-tty.
- v2_cli_client.py: simple CLI to send commands to the simulator.
- v2_ui_sim.py: styled Tkinter UI that talks to the simulator.
- v2_simulator_gui.py: full simulator UI using the legacy backgrounds and v2 logic.

## Run

1) Start the Mega simulator:
python mega_sim.py

2) Copy the printed device path (example /dev/pts/7).

3) Start the client:
python v2_cli_client.py --port /dev/pts/7

Or run the UI simulator:
python v2_ui_sim.py --port /dev/pts/7

Run the full simulator UI:
python v2_simulator_gui.py --port /dev/pts/7

The simulator prints all RX/TX lines for debugging.
