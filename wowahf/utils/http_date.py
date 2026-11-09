from datetime import UTC, datetime


def parse_http_date(value: str | None) -> datetime | None:
    """Parse HTTP Last-Modified / Date into timezone-aware UTC datetime."""
    if not value:
        return None
    candidates = [value]
    parts = value.split()
    # Blizzard sometimes emits unpadded day: "Mon, 9 Nov 2026 ..."
    if len(parts) >= 2 and parts[1].isdigit() and len(parts[1]) == 1:
        parts[1] = f"0{parts[1]}"
        candidates.append(" ".join(parts))
    for candidate in candidates:
        for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S GMT"):
            try:
                return datetime.strptime(candidate, fmt).replace(tzinfo=UTC)
            except ValueError:
                continue
    return None


def naive_utc_second(value: datetime) -> datetime:
    """Naive UTC truncated to whole seconds (matches HTTP date precision)."""
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    return value.replace(microsecond=0)
