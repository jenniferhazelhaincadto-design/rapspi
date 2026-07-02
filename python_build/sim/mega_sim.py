import json
import os
import pty
import select
import time


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


def _write_json(fd, obj):
    text = json.dumps(obj)
    os.write(fd, (text + "\n").encode("utf-8"))
    _log(f"TX <- {text}")


def _poll_for_stop(fd, buffer, stop_flag):
    """Non-blocking check for an incoming {"cmd":"stop"} line while busy.
    Mirrors the firmware's pollStop()/checkUserStop() behavior during
    long-running operations."""
    rlist, _, _ = select.select([fd], [], [], 0)
    if fd in rlist:
        buffer = _read_available(fd, buffer)
        lines, buffer = _extract_lines(buffer)
        for line in lines:
            _log(f"RX -> {line}")
            try:
                doc = json.loads(line)
            except (ValueError, TypeError):
                continue
            if doc.get("cmd") == "stop":
                stop_flag[0] = True
    return buffer


def _sleep_with_stop_check(fd, buffer, duration_s, stop_flag):
    """Sleep in small slices, polling for a stop command the whole time,
    same idea as the firmware's delay(5) loops in runPump()/dispenseDry()."""
    end_time = time.monotonic() + duration_s
    slice_s = 0.02
    while time.monotonic() < end_time:
        if stop_flag[0]:
            break
        buffer = _poll_for_stop(fd, buffer, stop_flag)
        if stop_flag[0]:
            break
        time.sleep(slice_s)
    return buffer


def _handle_levels(fd, dry_levels):
    doc = {
        "type": "levels",
        "dry": [{"id": i + 1, "g": int(g)} for i, g in enumerate(dry_levels)],
    }
    _write_json(fd, doc)


def _handle_clean(fd, buffer, stop_flag):
    _log("CLEAN: start")
    for _ in range(4):
        if stop_flag[0]:
            break
        buffer = _sleep_with_stop_check(fd, buffer, 0.3, stop_flag)
    if stop_flag[0]:
        _write_line(fd, "STATUS:STOPPED")
    else:
        _write_line(fd, "STATUS:OK")
    return buffer


def _handle_dispense(fd, buffer, doc, dry_levels, stop_flag):
    dry = doc.get("dry") or []
    wet = doc.get("wet") or []

    for item in dry:
        if stop_flag[0]:
            break
        cid = int(item.get("id", 0) or 0)
        grams = int(item.get("g", 0) or 0)
        if grams <= 0 or not (1 <= cid <= len(dry_levels)):
            continue
        _log(f"DRY {cid}: target {grams} g")
        buffer = _sleep_with_stop_check(fd, buffer, 0.2, stop_flag)
        if stop_flag[0]:
            break
        dry_levels[cid - 1] = max(0, dry_levels[cid - 1] - grams)

    for item in wet:
        if stop_flag[0]:
            break
        cid = int(item.get("id", 0) or 0)
        ml = float(item.get("ml", 0.0) or 0.0)
        if ml <= 0 or cid <= 0:
            continue
        _log(f"WET {cid}: {ml} ml")
        buffer = _sleep_with_stop_check(fd, buffer, 0.2, stop_flag)

    if stop_flag[0]:
        _write_line(fd, "STATUS:STOPPED")
    else:
        _write_line(fd, "STATUS:OK")
    return buffer


def main():
    master_fd, slave_fd = pty.openpty()
    slave_name = os.ttyname(slave_fd)
    _log(f"Mega simulator ready at {slave_name}")
    _log('Send JSON lines, e.g. {"cmd":"dispense","dry":[{"id":1,"g":50}],"wet":[{"id":1,"ml":30}]}')

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

                    try:
                        doc = json.loads(line)
                    except (ValueError, TypeError):
                        _write_line(master_fd, "STATUS:ERROR")
                        continue

                    if not isinstance(doc, dict):
                        _write_line(master_fd, "STATUS:ERROR")
                        continue

                    cmd = doc.get("cmd", "")

                    if cmd == "stop":
                        stop_flag[0] = True
                        _write_line(master_fd, "STATUS:STOPPED")
                        continue

                    if cmd == "clean":
                        stop_flag[0] = False
                        buffer = _handle_clean(master_fd, buffer, stop_flag)
                        continue

                    if cmd == "levels":
                        _handle_levels(master_fd, dry_levels)
                        continue

                    if cmd == "dispense":
                        stop_flag[0] = False
                        buffer = _handle_dispense(master_fd, buffer, doc, dry_levels, stop_flag)
                        continue

                    _write_line(master_fd, "STATUS:ERROR")
    finally:
        os.close(master_fd)
        os.close(slave_fd)


if __name__ == "__main__":
    main()
