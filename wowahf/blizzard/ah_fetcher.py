import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any

from wowahf.blizzard import BLIZZARD_API_OBJ
from wowahf.utils.config import get_config_entry
from wowahf.utils.http_date import naive_utc_second, parse_http_date

OAUTH_TOKEN_URL = "https://oauth.battle.net/token"


class BlizzardApiClient:
    """Minimal Battle.net client-credentials client for WoW: Forever."""

    def __init__(self, client_id: str, client_secret: str, region: str, locale: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.region = region
        self.locale = locale
        self._access_token: str | None = None
        self.api_host = f"https://{region}.api.blizzard.com"

    def _fetch_token(self) -> str:
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
        return token

    @property
    def access_token(self) -> str:
        if not self._access_token:
            return self._fetch_token()
        return self._access_token

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

    def request(
        self,
        path: str,
        namespace: str,
        *,
        method: str = "GET",
        trailing_slash: bool = False,
        read_body: bool = True,
    ) -> tuple[int, dict[str, str], bytes]:
        """Low-level request. Returns (status, headers_lower, body)."""
        url = self._url(path, namespace, trailing_slash=trailing_slash)
        request = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {self.access_token}"},
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                headers = {k.lower(): v for k, v in response.headers.items()}
                body = response.read() if read_body else b""
                return int(response.status), headers, body
        except urllib.error.HTTPError as exc:
            body = exc.read() if read_body else b""
            headers = {k.lower(): v for k, v in exc.headers.items()} if exc.headers else {}
            if not read_body:
                try:
                    exc.read()
                except Exception:
                    pass
            raise RuntimeError(
                f"Blizzard API {exc.code} for {url}: "
                f"{body.decode(errors='replace') if body else ''}"
            ) from exc

    def get(self, path: str, namespace: str, *, trailing_slash: bool = False) -> Any:
        doc, _lm = self.get_with_last_modified(
            path, namespace, trailing_slash=trailing_slash
        )
        return doc

    def get_with_last_modified(
        self, path: str, namespace: str, *, trailing_slash: bool = False
    ) -> tuple[Any, datetime | None]:
        """GET JSON body and parsed Last-Modified (UTC) from response headers."""
        _status, headers, body = self.request(
            path, namespace, method="GET", trailing_slash=trailing_slash, read_body=True
        )
        return json.loads(body.decode()), parse_http_date(headers.get("last-modified"))

    def forever_namespace(self, kind: str) -> str:
        return f"{kind}-forever-{self.region}"

    def get_item(self, item_id: int) -> dict[str, Any] | None:
        """Fetch static item data. Returns None if the id is unknown (404)."""
        try:
            return self.get(
                f"/data/wow/item/{item_id}",
                self.forever_namespace("static"),
            )
        except RuntimeError as exc:
            if " 404 " in str(exc):
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


def get_auction_houses(api: BlizzardApiClient | None = None) -> dict[str, Any]:
    """Forever auction-house index. Trailing slash is required (as of Nov 2026)."""
    api = api or get_blizzard_api_obj()
    ruleset_id = get_config_entry("RULESET_ID")
    return api.get(
        f"/data/wow/ruleset/{ruleset_id}/auctions",
        api.forever_namespace("dynamic"),
        trailing_slash=True,
    )


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
    try:
        return api.get_with_last_modified(
            f"/data/wow/ruleset/{ruleset_id}/auctions/{house_id}/commodities",
            api.forever_namespace("dynamic"),
        )
    except RuntimeError as exc:
        if " 404 " in str(exc):
            return {"auctions": []}, None
        raise


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
    Newest Last-Modified across houses without keeping listing bodies.
    Used to skip unchanged snapshots before allocating a parse row.
    """
    api = api or get_blizzard_api_obj()
    houses = (get_auction_houses(api).get("auctions") or [])
    newest: datetime | None = None
    for house in houses:
        house_id = house["id"]
        auctions_doc, auc_lm = get_house_auctions(house_id, api)
        commodities_doc, com_lm = get_house_commodities(house_id, api)
        del auctions_doc, commodities_doc
        newest = _max_lm(newest, auc_lm, com_lm)
    return newest


def iter_ah_houses(api: BlizzardApiClient | None = None):
    """
    Yield one house at a time with listings so peak RSS stays near one AH
    dump (loading every house at once OOMs the 1.5–2Gi container).
    """
    api = api or get_blizzard_api_obj()
    houses = (get_auction_houses(api).get("auctions") or [])
    for house in houses:
        house_id = house["id"]
        name = house.get("name", house_id)
        auctions_doc, auc_lm = get_house_auctions(house_id, api)
        commodities_doc, com_lm = get_house_commodities(house_id, api)
        yield {
            "id": house_id,
            "name": name,
            "auctions": auctions_doc.get("auctions") or [],
            "commodities": commodities_doc.get("auctions") or [],
            "last_modified": _max_lm(None, auc_lm, com_lm),
        }


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
        for house in (get_auction_houses(api).get("auctions") or []):
            result["houses"].append(
                {"id": house["id"], "name": house.get("name", house["id"])}
            )
    result["last_modified"] = newest
    return result
