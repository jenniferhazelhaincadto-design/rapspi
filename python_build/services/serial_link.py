import time
import json
from typing import Optional

try:
    import serial
except Exception:  # pragma: no cover
    serial = None


class SerialLink:
    def __init__(self, port: str, baud: int, simulate: bool = False) -> None:
        self.simulate = simulate
        self.port = port
        self.baud = baud
        self._ser = None

    def open(self) -> None:
        if self.simulate:
            return
        if self._ser:
            return
        if serial is None:
            raise RuntimeError("pyserial is not installed")
        self._ser = serial.Serial(self.port, baudrate=self.baud, timeout=0.2)
        # Many Arduino boards reset when the port is opened; give firmware time to boot.
        time.sleep(2.5)
        try:
            self._ser.reset_input_buffer()
            self._ser.reset_output_buffer()
        except Exception:
            pass

    def close(self) -> None:
        if self._ser:
            self._ser.close()
            self._ser = None

    def send(self, payload: str) -> None:
        if self.simulate:
            return
        if not self._ser:
            self.open()
        self._ser.write(payload.encode())

    def read_line(self) -> Optional[str]:
        if self.simulate:
            return None
        if not self._ser:
            return None
        line = self._ser.readline().decode("utf-8", errors="ignore").strip()
        return line or None

    def send_and_wait_done(self, payload: str, timeout: float = 30.0) -> str:
        self.send(payload)
        if self.simulate:
            time.sleep(1)
            return "STATUS:OK"
        start = time.time()
        while time.time() - start < timeout:
            line = self.read_line()
            if not line:
                continue
            if line.startswith("STATUS:"):
                return line
        return "STATUS:TIMEOUT"

    def send_and_wait_json(self, payload: str, timeout: float = 2.0):
        self.send(payload)
        if self.simulate:
            return {
                "type": "levels",
                "dry": [
                    {"id": 1, "g": 250},
                    {"id": 2, "g": 180},
                    {"id": 3, "g": 90},
                    {"id": 4, "g": 120},
                    {"id": 5, "g": 60},
                    {"id": 6, "g": 200},
                ],
            }
        start = time.time()
        while time.time() - start < timeout:
            line = self.read_line()
            if not line:
                continue
            if line.startswith("STATUS:"):
                continue
            if line.startswith("{"):
                try:
                    data = json.loads(line)
                except Exception:
                    data = None
                if isinstance(data, dict) and data.get("type") == "levels":
                    return data
            if line.startswith("LEVELS,"):
                items = []
                parts = line[len("LEVELS,"):].split(";")
                for part in parts:
                    fields = [f.strip() for f in part.split(",") if f.strip()]
                    if len(fields) < 3:
                        continue
                    if fields[0].upper() != "D":
                        continue
                    try:
                        cid = int(fields[1])
                        grams = int(float(fields[2]))
                    except ValueError:
                        continue
                    items.append({"id": cid, "g": grams})
                return {"type": "levels", "dry": items}
        return None
