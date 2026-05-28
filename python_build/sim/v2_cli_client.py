import argparse
import json
import sys
import time

try:
    import serial
except Exception:  # pragma: no cover
    serial = None


def _send_and_read(port, payload, timeout=3.0):
    port.write((payload + "\n").encode("utf-8"))
    start = time.time()
    while time.time() - start < timeout:
        line = port.readline().decode("utf-8", errors="ignore").strip()
        if line:
            print(f"RX <- {line}")
            return line
    print("RX <- STATUS:TIMEOUT")
    return "STATUS:TIMEOUT"


def _prompt_int(label, default):
    raw = input(f"{label} [{default}]: ").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def main():
    if serial is None:
        print("pyserial not installed")
        sys.exit(1)

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True, help="Serial port (pty or USB)")
    parser.add_argument("--baud", type=int, default=9600)
    args = parser.parse_args()

    ser = serial.Serial(args.port, baudrate=args.baud, timeout=0.2)
    print(f"Connected to {args.port}")

    while True:
        print("\n1) Dispense  2) Clean  3) Stop  4) Levels  5) Exit")
        choice = input("> ").strip()
        if choice == "1":
            recipe = input("Recipe name: ").strip() or "Demo"
            batches = _prompt_int("Batches", 1)
            dry = []
            for i in range(1, 7):
                grams = _prompt_int(f"Dry {i} grams", 0)
                if grams > 0:
                    dry.append({"id": i, "g": grams})
            wet = []
            for i in range(1, 5):
                ml = _prompt_int(f"Wet {i} ml", 0)
                if ml > 0:
                    ms_per_ml = _prompt_int(f"Wet {i} ms/ml", 100)
                    wet.append({"id": i, "ml": ml, "ms_per_ml": ms_per_ml})
            payload = json.dumps({
                "cmd": "dispense",
                "recipe": recipe,
                "batches": batches,
                "dry": dry,
                "wet": wet,
            })
            _send_and_read(ser, payload, timeout=5.0)
        elif choice == "2":
            payload = json.dumps({"cmd": "clean"})
            _send_and_read(ser, payload, timeout=5.0)
        elif choice == "3":
            payload = json.dumps({"cmd": "stop"})
            _send_and_read(ser, payload, timeout=2.0)
        elif choice == "4":
            payload = json.dumps({"cmd": "levels"})
            _send_and_read(ser, payload, timeout=2.0)
        elif choice == "5":
            break
        else:
            print("Invalid choice")

    ser.close()


if __name__ == "__main__":
    main()
