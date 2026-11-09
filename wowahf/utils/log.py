from datetime import UTC, datetime
from string.templatelib import Interpolation, Template


def _render(template: Template) -> str:
    """Join a 3.14 t-string Template into a plain str for printing."""
    parts: list[str] = []
    for part in template:
        if isinstance(part, Interpolation):
            parts.append(str(part.value))
        else:
            parts.append(part)
    return "".join(parts)


def log(message: str) -> None:
    ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(_render(t"[{ts}] {message}"), flush=True)
