import os 
import pty
import select
import time
import json


def _now():
    return time.strftime("%H:%M:%S")


def _log(msg):
    print(f"[{_now()}] {msg}", flush=True)


def _read_available(fd, buffer):
    try:
        data = os.read(fd, 1024)
    except OSError:
        return buffer
    if not data:
        return buffer
    buffer += data
    return buffer


def _extract_lines(buffer):
    lines = []
    while b"\n" in buffer:
        line, buffer = buffer.split(b"\n", 1)
        text = line.decode("utf-8", errors="ignore").strip()
        if text:
            lines.append(text)
    return lines, buffer


def _write_line(fd, text):
    os.write(fd, (text + "\n").encode("utf-8"))
    _log(f"TX <- {text}")


def _handle_levels(fd, dry_levels):
    items = [f"D,{i + 1},{int(g)}" for i, g in enumerate(dry_levels)]
    _write_line(fd, "LEVELS," + ";".join(items))


def _handle_clean(fd):
    _log("CLEAN: start")
    time.sleep(0.5)
    _write_line(fd, "STATUS:OK")  # Ensure using STATUS:OK only


def _handle_dispense_items(fd, items, dry_levels, stop_flag):
    for kind, cid, amount in items:
        if stop_flag[0]:
            break
        if kind == "D":
            grams = int(amount)
            _log(f"DRY {cid}: target {grams} g")
            time.sleep(0.2)
            if 1 <= cid <= len(dry_levels):
                dry_levels[cid - 1] = max(0, dry_levels[cid - 1] - grams)
        else:
            ml = float(amount)
            _log(f"WET {cid}: {ml} ml")
            time.sleep(0.2)

    if stop_flag[0]:
        _write_line(fd, "STATUS:STOPPED")
    else:
        _write_line(fd, "STATUS:OK")


def _parse_items(text):
    items = []
    for part in text.split(";"):
        fields = [f.strip() for f in part.split(",") if f.strip()]
        if len(fields) < 3:
            continue
        kind = fields[0].upper()
        if kind not in ("D", "W"):
            continue
        try:
            cid = int(fields[1])
            amount = float(fields[2])
        except ValueError:
            continue
        items.append((kind, cid, amount))
    return items


def _parse_json_command(line):
    try:
        payload = json.loads(line)
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None

    cmd = str(payload.get("cmd", "")).strip().lower()
    if cmd in ("stop", "clean", "levels"):
        return (cmd, None)

    if cmd == "dispense":
        items = []
        for item in payload.get("dry", []) or []:
            try:
                cid = int(item.get("id", 0))
                grams = float(item.get("g", 0))
            except Exception:
                continue
            if cid > 0 and grams > 0:
                items.append(("D", cid, grams))

        for item in payload.get("wet", []) or []:
            try:
                cid = int(item.get("id", 0))
                ml = float(item.get("ml", 0))
            except Exception:
                continue
            if cid > 0 and ml > 0:
                items.append(("W", cid, ml))

        return ("dispense", items)

    return None


def main():
    master_fd, slave_fd = pty.openpty()
    slave_name = os.ttyname(slave_fd)
    _log(f"Mega simulator ready at {slave_name}")

    dry_levels = [500, 500, 500, 500, 500, 500]
    stop_flag = [False]
    buffer = b""

    try:
        while True:
            rlist, _, _ = select.select([master_fd], [], [], 0.1)
            if master_fd in rlist:
                buffer = _read_available(master_fd, buffer)
                lines, buffer = _extract_lines(buffer)
                for line in lines:
                    _log(f"RX -> {line}")
                    line = line.strip()
                    if not line:
                        continue

                    parsed = _parse_json_command(line)
                    if parsed:
                        cmd, items = parsed
                        if cmd == "stop":
                            stop_flag[0] = True
                            _write_line(master_fd, "STATUS:STOPPED")
                            continue
                        if cmd == "clean":
                            stop_flag[0] = False
                            _handle_clean(master_fd)
                            continue
                        if cmd == "levels":
                            _handle_levels(master_fd, dry_levels)
                            continue
                        if cmd == "dispense":
                            stop_flag[0] = False
                            _handle_dispense_items(master_fd, items or [], dry_levels, stop_flag)
                            continue

                    upper = line.upper()
                    if upper == "STOP":
                        stop_flag[0] = True
                        _write_line(master_fd, "STATUS:STOPPED")
                        continue
                    if upper == "CLEAN":
                        stop_flag[0] = False
                        _handle_clean(master_fd)
                        continue
                    if upper == "LEVELS":
                        _handle_levels(master_fd, dry_levels)
                        continue

                    if upper.startswith("DISPENSE,"):
                        stop_flag[0] = False
                        parts = [p.strip() for p in line.split(",") if p.strip()]
                        if len(parts) >= 4:
                            kind = parts[1].upper()
                            try:
                                cid = int(parts[2])
                                amount = float(parts[3])
                            except ValueError:
                                _write_line(master_fd, "STATUS:ERROR")
                                continue
                            _handle_dispense_items(master_fd, [(kind, cid, amount)], dry_levels, stop_flag)
                        else:
                            _write_line(master_fd, "STATUS:ERROR")
                        continue

                    if upper.startswith("MIX,"):
                        stop_flag[0] = False
                        parts = line.split(",", 3)
                        if len(parts) < 4:
                            _write_line(master_fd, "STATUS:ERROR")
                            continue
                        recipe = parts[1].strip()
                        batches = parts[2].strip()
                        _log(f"MIX: recipe={recipe} batches={batches}")
                        items = _parse_items(parts[3])
                        _handle_dispense_items(master_fd, items, dry_levels, stop_flag)
                        continue

                    _write_line(master_fd, "STATUS:ERROR")
    finally:
        os.close(master_fd)
        os.close(slave_fd)


if __name__ == "__main__":
    main()
