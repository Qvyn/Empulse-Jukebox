from __future__ import annotations


def normalize_entry(value) -> dict:
    """Migrate v0.1 string entries and sanitize current entries."""
    if isinstance(value, str):
        return {"path": value, "start_ms": 0, "end_ms": 0}
    if isinstance(value, dict):
        return {
            "path": str(value.get("path", "")),
            "start_ms": max(0, int(value.get("start_ms", 0) or 0)),
            "end_ms": max(0, int(value.get("end_ms", 0) or 0)),
        }
    return {"path": "", "start_ms": 0, "end_ms": 0}


def format_time(milliseconds: int, blank_zero: bool = False) -> str:
    milliseconds = max(0, int(milliseconds or 0))
    if blank_zero and milliseconds == 0:
        return ""
    minutes, remainder = divmod(milliseconds, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{minutes}:{seconds:02d}.{millis:03d}"


def parse_time(value: str, allow_blank: bool = False) -> int:
    value = value.strip()
    if not value and allow_blank:
        return 0
    if not value:
        return 0
    try:
        parts = value.split(":")
        if len(parts) == 1:
            seconds = float(parts[0])
        elif len(parts) == 2:
            seconds = int(parts[0]) * 60 + float(parts[1])
        elif len(parts) == 3:
            seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        else:
            raise ValueError
    except ValueError as exc:
        raise ValueError("Use M:SS, M:SS.mmm, or H:MM:SS") from exc
    if seconds < 0:
        raise ValueError("Time cannot be negative")
    return round(seconds * 1000)

