import base64
import json
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import Any

from wowahf.blizzard import BLIZZARD_API_OBJ
from wowahf.utils.config import get_config_entry
from wowahf.utils.http_date import naive_utc_second, parse_http_date
from wowahf.utils.log import log

OAUTH_TOKEN_URL = "https://oauth.battle.net/token"
TOKEN_REFRESH_MIN_INTERVAL = timedelta(minutes=10)

# Backoff for resilient AH downloads (never give up on transient failures).
_RETRY_BASE_S = 1.0
_RETRY_CAP_S = 60.0
_EMPTY_INDEX_GIVE_UP_S = 900.0  # ~15m of empty index → treat as no data

_RETRYABLE_HTTP = frozenset({408, 425, 429, 500, 502, 503, 504})


class BlizzardHttpError(RuntimeError):
    """HTTP failure from the Blizzard API (may be retryable)."""

    def __init__(self, code: int, url: str, body: str = ""):
        self.code = int(code)
        self.url = url
        self.body = body
        super().__init__(f"Blizzard API {code} for {url}: {body}")


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _backoff_sleep(attempt: int, *, label: str) -> None:
    """Exponential backoff with jitter; attempt is 0-based."""
    delay = min(_RETRY_CAP_S, _RETRY_BASE_S * (2 ** min(attempt, 8)))
    delay *= 0.5 + random.random()  # 50–150% jitter
    log(f"blizzard: {label}; retry in {delay:.1f}s (attempt {attempt + 1})")
    time.sleep(delay)


class BlizzardApiClient:
    """Minimal Battle.net client-credentials client for WoW: Forever."""

    def __init__(self, client_id: str, client_secret: str, region: str, locale: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.region = region
        self.locale = locale
        self._access_token: str | None = None
        self._token_refreshed_at: datetime | None = None
        self._token_lock = threading.Lock()
        self.api_host = f"https://{region}.api.blizzard.com"

    def _fetch_token(self) -> str:
        """Fetch a new OAuth token. Caller must hold `_token_lock`."""
        credentials = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()
        request = urllib.request.Request(
            OAUTH_TOKEN_URL,
            data=urllib.parse.urlencode({"grant_type": "client_credentials"}).encode(),
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode())
        token = payload.get("access_token")
        if not token:
            raise RuntimeError(f"OAuth token response missing access_token: {payload}")
        self._access_token = token
        self._token_refreshed_at = _utc_now()
        return token

    def _get_access_token(self) -> str:
        with self._token_lock:
            if not self._access_token:
                return self._fetch_token()
            return self._access_token

    def _refresh_after_401(self, stale_token: str) -> None:
        """
        Ensure we have a token newer than ``stale_token``.

        If a refresh just happened, wait out the min interval then refresh again
        (never give up on 401 for the AH download path).
        """
        while True:
            with self._token_lock:
                if self._access_token and self._access_token != stale_token:
                    return
                if (
                    self._token_refreshed_at is not None
                    and _utc_now() - self._token_refreshed_at
                    < TOKEN_REFRESH_MIN_INTERVAL
                ):
                    wait_s = (
                        TOKEN_REFRESH_MIN_INTERVAL
                        - (_utc_now() - self._token_refreshed_at)
                    ).total_seconds()
                    wait_s = max(1.0, wait_s)
                else:
                    wait_s = 0.0
            if wait_s > 0:
                log(
                    f"blizzard: 401 — waiting {wait_s:.0f}s before next token refresh"
                )
                time.sleep(wait_s)
                continue
            with self._token_lock:
                if self._access_token and self._access_token != stale_token:
                    return
                log("blizzard: 401 — refreshing OAuth token")
                try:
                    self._fetch_token()
                    return
                except Exception as exc:
                    log(f"blizzard: OAuth refresh failed ({exc})")
            _backoff_sleep(0, label="OAuth refresh retry")

    @property
    def access_token(self) -> str:
        return self._get_access_token()

    def _url(self, path: str, namespace: str, *, trailing_slash: bool = False) -> str:
        path = path if path.startswith("/") else f"/{path}"
        if trailing_slash and not path.endswith("/"):
            path = f"{path}/"
        query = urllib.parse.urlencode(
            {
                "namespace": namespace,
                "locale": self.locale,
            }
        )
        return f"{self.api_host}{path}?{query}"

    def _http(
        self,
        url: str,
        token: str,
        *,
        method: str,
        read_body: bool,
    ) -> tuple[int, dict[str, str], bytes]:
        request = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {token}"},
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                headers = {k.lower(): v for k, v in response.headers.items()}
                body = response.read() if read_body else b""
                if not read_body:
                    try:
                        response.read()
                    except Exception:
                        pass
                return int(response.status), headers, body
        except urllib.error.HTTPError as exc:
            body = b""
            try:
                body = exc.read() if read_body else b""
                if not read_body:
                    try:
                        exc.read()
                    except Exception:
                        pass
            except Exception:
                pass
            headers = (
                {k.lower(): v for k, v in exc.headers.items()} if exc.headers else {}
            )
            raise BlizzardHttpError(
                int(exc.code),
                url,
                body.decode(errors="replace") if body else "",
            ) from exc

    def request_once(
        self,
        path: str,
        namespace: str,
        *,
        method: str = "GET",
        trailing_slash: bool = False,
        read_body: bool = True,
    ) -> tuple[int, dict[str, str], bytes]:
        """Single attempt (plus 401 refresh). Raises BlizzardHttpError / URLError."""
        url = self._url(path, namespace, trailing_slash=trailing_slash)
        token = self._get_access_token()
        try:
            return self._http(url, token, method=method, read_body=read_body)
        except BlizzardHttpError as exc:
            if exc.code != 401:
                raise
            self._refresh_after_401(token)
            token = self._get_access_token()
            return self._http(url, token, method=method, read_body=read_body)

    def request_resilient(
        self,
        path: str,
        namespace: str,
        *,
        method: str = "GET",
        trailing_slash: bool = False,
        read_body: bool = True,
        allow_404: bool = False,
        label: str | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        """
        HTTP with infinite retry on transient failures.

        404 raises BlizzardHttpError unless allow_404 (returns the 404 response).
        """
        tag = label or f"{method} {path}"
        attempt = 0
        while True:
            try:
                status, headers, body = self.request_once(
                    path,
                    namespace,
                    method=method,
                    trailing_slash=trailing_slash,
                    read_body=read_body,
                )
                return status, headers, body
            except BlizzardHttpError as exc:
                if exc.code == 404 and allow_404:
                    return exc.code, {}, b""
                if exc.code == 404:
                    raise
                if exc.code == 401 or exc.code in _RETRYABLE_HTTP or exc.code >= 500:
                    _backoff_sleep(attempt, label=f"{tag} → HTTP {exc.code}")
                    attempt += 1
                    continue
                # Other 4xx: still retry (Blizzard blips); never abandon AH download.
                _backoff_sleep(attempt, label=f"{tag} → HTTP {exc.code}")
                attempt += 1
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                _backoff_sleep(attempt, label=f"{tag} → {exc.__class__.__name__}: {exc}")
                attempt += 1

    def request(
        self,
        path: str,
        namespace: str,
        *,
        method: str = "GET",
        trailing_slash: bool = False,
        read_body: bool = True,
    ) -> tuple[int, dict[str, str], bytes]:
        """Resilient request (AH / general API path)."""
        return self.request_resilient(
            path,
            namespace,
            method=method,
            trailing_slash=trailing_slash,
            read_body=read_body,
        )

    def get(self, path: str, namespace: str, *, trailing_slash: bool = False) -> Any:
        doc, _lm = self.get_with_last_modified(
            path, namespace, trailing_slash=trailing_slash
        )
        return doc

    def get_with_last_modified(
        self, path: str, namespace: str, *, trailing_slash: bool = False
    ) -> tuple[Any, datetime | None]:
        """GET JSON body and parsed Last-Modified (UTC) from response headers."""
        _status, headers, body = self.request_resilient(
            path,
            namespace,
            method="GET",
            trailing_slash=trailing_slash,
            read_body=True,
            label=f"GET {path}",
        )
        return json.loads(body.decode()), parse_http_date(headers.get("last-modified"))

    def head_last_modified(
        self, path: str, namespace: str, *, trailing_slash: bool = False
    ) -> datetime | None:
        """Last-Modified via HEAD (falls back to GET-discard if HEAD unsupported)."""
        try:
            _status, headers, _body = self.request_resilient(
                path,
                namespace,
                method="HEAD",
                trailing_slash=trailing_slash,
                read_body=False,
                label=f"HEAD {path}",
            )
            return parse_http_date(headers.get("last-modified"))
        except BlizzardHttpError as exc:
            if exc.code not in (405, 501):
                raise
        _status, headers, body = self.request_resilient(
            path,
            namespace,
            method="GET",
            trailing_slash=trailing_slash,
            read_body=True,
            label=f"GET(fallback) {path}",
        )
        del body
        return parse_http_date(headers.get("last-modified"))

    def forever_namespace(self, kind: str) -> str:
        return f"{kind}-forever-{self.region}"

    def get_item(self, item_id: int) -> dict[str, Any] | None:
        """Fetch static item data. Returns None if the id is unknown (404)."""
        try:
            _status, _headers, body = self.request_resilient(
                f"/data/wow/item/{item_id}",
                self.forever_namespace("static"),
                allow_404=True,
                label=f"item {item_id}",
            )
            if _status == 404 or not body:
                return None
            return json.loads(body.decode())
        except BlizzardHttpError as exc:
            if exc.code == 404:
                return None
            raise


def get_blizzard_api_obj() -> BlizzardApiClient:
    global BLIZZARD_API_OBJ
    if BLIZZARD_API_OBJ is None:
        BLIZZARD_API_OBJ = BlizzardApiClient(
            get_config_entry("BLIZZARD_CLIENT_ID"),
            get_config_entry("BLIZZARD_CLIENT_SECRET"),
            get_config_entry("REGION"),
            get_config_entry("LOCALE"),
        )
    return BLIZZARD_API_OBJ


def get_ruleset(api: BlizzardApiClient | None = None) -> dict[str, Any]:
    api = api or get_blizzard_api_obj()
    ruleset_id = get_config_entry("RULESET_ID")
    return api.get(
        f"/data/wow/ruleset/{ruleset_id}",
        api.forever_namespace("dynamic"),
    )


def get_auction_houses(
    api: BlizzardApiClient | None = None,
    *,
    require_nonempty: bool = False,
) -> dict[str, Any]:
    """
    Forever auction-house index. Trailing slash is required (as of Nov 2026).

    When require_nonempty, retry while the index is empty until
    ``_EMPTY_INDEX_GIVE_UP_S`` elapses, then return the empty doc (caller
    treats that as Blizzard no-data).
    """
    api = api or get_blizzard_api_obj()
    ruleset_id = get_config_entry("RULESET_ID")
    path = f"/data/wow/ruleset/{ruleset_id}/auctions"
    ns = api.forever_namespace("dynamic")
    started = time.monotonic()
    attempt = 0
    while True:
        doc = api.get(path, ns, trailing_slash=True)
        houses = doc.get("auctions") or []
        if houses or not require_nonempty:
            return doc
        if time.monotonic() - started >= _EMPTY_INDEX_GIVE_UP_S:
            log("blizzard: no auction houses in index (giving up after sustained empty)")
            return doc
        _backoff_sleep(
            attempt,
            label="auction house index empty",
        )
        attempt += 1


def get_house_auctions(
    house_id: int | str, api: BlizzardApiClient | None = None
) -> tuple[dict[str, Any], datetime | None]:
    api = api or get_blizzard_api_obj()
    ruleset_id = get_config_entry("RULESET_ID")
    return api.get_with_last_modified(
        f"/data/wow/ruleset/{ruleset_id}/auctions/{house_id}",
        api.forever_namespace("dynamic"),
    )


def get_house_commodities(
    house_id: int | str, api: BlizzardApiClient | None = None
) -> tuple[dict[str, Any], datetime | None]:
    api = api or get_blizzard_api_obj()
    ruleset_id = get_config_entry("RULESET_ID")
    path = f"/data/wow/ruleset/{ruleset_id}/auctions/{house_id}/commodities"
    ns = api.forever_namespace("dynamic")
    try:
        return api.get_with_last_modified(path, ns)
    except BlizzardHttpError as exc:
        if exc.code == 404:
            return {"auctions": []}, None
        raise


def _house_head_lms(
    house_id: int | str, api: BlizzardApiClient
) -> tuple[datetime | None, datetime | None]:
    ruleset_id = get_config_entry("RULESET_ID")
    ns = api.forever_namespace("dynamic")
    auc_lm = api.head_last_modified(
        f"/data/wow/ruleset/{ruleset_id}/auctions/{house_id}", ns
    )
    try:
        com_lm = api.head_last_modified(
            f"/data/wow/ruleset/{ruleset_id}/auctions/{house_id}/commodities", ns
        )
    except BlizzardHttpError as exc:
        if exc.code == 404:
            com_lm = None
        else:
            raise
    return auc_lm, com_lm


def _max_lm(
    newest: datetime | None, *candidates: datetime | None
) -> datetime | None:
    for lm in candidates:
        if lm is None:
            continue
        lm_naive = naive_utc_second(lm)
        if newest is None or lm_naive > newest:
            newest = lm_naive
    return newest


def peek_ah_last_modified(api: BlizzardApiClient | None = None) -> datetime | None:
    """
    Newest Last-Modified across houses via HEAD (no listing bodies).

    Returns None when the auctions index is empty after sustained retries
    (Blizzard no-data). Individual house HEADs retry forever.
    """
    api = api or get_blizzard_api_obj()
    houses = get_auction_houses(api, require_nonempty=True).get("auctions") or []
    if not houses:
        return None
    newest: datetime | None = None
    for house in houses:
        house_id = house["id"]
        attempt = 0
        while True:
            try:
                auc_lm, com_lm = _house_head_lms(house_id, api)
                newest = _max_lm(newest, auc_lm, com_lm)
                break
            except BlizzardHttpError as exc:
                if exc.code == 404:
                    # House vanished from live data; skip it for peek.
                    log(f"blizzard: peek house={house_id} → 404, skipping")
                    break
                _backoff_sleep(
                    attempt, label=f"peek house={house_id} → HTTP {exc.code}"
                )
                attempt += 1
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                _backoff_sleep(
                    attempt,
                    label=f"peek house={house_id} → {exc.__class__.__name__}",
                )
                attempt += 1
    return newest


def iter_ah_houses(api: BlizzardApiClient | None = None):
    """
    Yield one house at a time with listings so peak RSS stays near one AH
    dump (loading every house at once OOMs the 1.5–2Gi container).

    Each house is fetched with infinite retry until success. Empty auctions
    index after sustained retries yields nothing (caller must not write).
    """
    api = api or get_blizzard_api_obj()
    houses = get_auction_houses(api, require_nonempty=True).get("auctions") or []
    if not houses:
        return
    for house in houses:
        house_id = house["id"]
        name = house.get("name", house_id)
        attempt = 0
        while True:
            try:
                auctions_doc, auc_lm = get_house_auctions(house_id, api)
                commodities_doc, com_lm = get_house_commodities(house_id, api)
                yield {
                    "id": house_id,
                    "name": name,
                    "auctions": auctions_doc.get("auctions") or [],
                    "commodities": commodities_doc.get("auctions") or [],
                    "last_modified": _max_lm(None, auc_lm, com_lm),
                }
                break
            except BlizzardHttpError as exc:
                _backoff_sleep(
                    attempt,
                    label=f"download house={house_id} → HTTP {exc.code}",
                )
                attempt += 1
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                _backoff_sleep(
                    attempt,
                    label=f"download house={house_id} → {exc.__class__.__name__}: {exc}",
                )
                attempt += 1


def get_ah_data(*, include_listings: bool = True) -> dict[str, Any]:
    """
    Fetch ruleset houses (+ listings). When include_listings, also sets
    result['last_modified'] to the newest Last-Modified seen (naive UTC).

    Prefer iter_ah_houses() in the parse path to bound memory.
    """
    api = get_blizzard_api_obj()
    ruleset = get_ruleset(api)
    result: dict[str, Any] = {
        "ruleset": ruleset,
        "houses": [],
        "last_modified": None,
    }
    newest: datetime | None = None
    if include_listings:
        for entry in iter_ah_houses(api):
            lm = entry.pop("last_modified", None)
            newest = _max_lm(newest, lm)
            result["houses"].append(entry)
    else:
        for house in (
            get_auction_houses(api, require_nonempty=True).get("auctions") or []
        ):
            result["houses"].append(
                {"id": house["id"], "name": house.get("name", house["id"])}
            )
    result["last_modified"] = newest
    return result
