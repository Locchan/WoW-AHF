"""HTTP client for led SourceHTTP (`POST /event` with `{"message": ...}`)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from wowahf.utils.log import log

DEFAULT_TIMEOUT_S = 5.0


def send_event(
    message: str,
    *,
    base_url: str,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> None:
    """
    Deliver one event to a led SourceHTTP instance.

    ``base_url`` is the daemon root (e.g. ``http://host.docker.internal:55501``);
    the client appends ``/event``.
    """
    root = base_url.rstrip("/")
    url = f"{root}/event"
    body = json.dumps({"message": message}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                raise RuntimeError(f"led HTTP {resp.status}")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"led HTTP {exc.code}") from exc


def _ensure_config_loaded() -> dict:
    from wowahf import utils

    if utils.CONFIG_OBJ:
        return utils.CONFIG_OBJ
    import os
    from pathlib import Path

    path = Path(utils.CONFIG_PATH)
    if path.is_file():
        with path.open() as f:
            utils.CONFIG_OBJ = json.load(f)
    else:
        utils.CONFIG_OBJ = dict(os.environ)
    return utils.CONFIG_OBJ


def _led_base_url() -> str | None:
    cfg = _ensure_config_loaded()
    url = cfg.get("LED_HTTP_URL")
    if not url or not isinstance(url, str):
        return None
    return url.strip() or None


def alert(message: str, *, timeout: float = DEFAULT_TIMEOUT_S) -> None:
    """
    Best-effort led alert using ``LED_HTTP_URL`` from config.

    Missing config or send failures are logged and swallowed so callers
    (job failure paths) never fail because of alerting.
    """
    base_url = _led_base_url()
    if base_url is None:
        log("led: skip alert (LED_HTTP_URL not configured)")
        return
    try:
        send_event(message, base_url=base_url, timeout=timeout)
        log(f"led: alert sent ({base_url})")
    except Exception as exc:
        log(f"led: alert failed: {exc.__class__.__name__}: {exc}")
