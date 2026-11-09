import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from wowahf.utils.config import get_config_entry
from wowahf.blizzard import BLIZZARD_API_OBJ

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

    def get(self, path: str, namespace: str, *, trailing_slash: bool = False) -> Any:
        path = path if path.startswith("/") else f"/{path}"
        if trailing_slash and not path.endswith("/"):
            path = f"{path}/"
        query = urllib.parse.urlencode(
            {
                "namespace": namespace,
                "locale": self.locale,
            }
        )
        url = f"{self.api_host}{path}?{query}"
        request = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {self.access_token}"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")
            raise RuntimeError(
                f"Blizzard API {exc.code} for {url}: {body}"
            ) from exc

    def forever_namespace(self, kind: str) -> str:
        return f"{kind}-forever-{self.region}"


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
) -> dict[str, Any]:
    api = api or get_blizzard_api_obj()
    ruleset_id = get_config_entry("RULESET_ID")
    return api.get(
        f"/data/wow/ruleset/{ruleset_id}/auctions/{house_id}",
        api.forever_namespace("dynamic"),
    )


def get_house_commodities(
    house_id: int | str, api: BlizzardApiClient | None = None
) -> dict[str, Any]:
    api = api or get_blizzard_api_obj()
    ruleset_id = get_config_entry("RULESET_ID")
    try:
        return api.get(
            f"/data/wow/ruleset/{ruleset_id}/auctions/{house_id}/commodities",
            api.forever_namespace("dynamic"),
        )
    except RuntimeError as exc:
        if " 404 " in str(exc):
            return {"auctions": []}
        raise


def get_ah_data(*, include_listings: bool = True) -> dict[str, Any]:
    api = get_blizzard_api_obj()
    print(f"blizzard: forever {api.region} ruleset {get_config_entry('RULESET_ID')}")

    ruleset = get_ruleset(api)
    print(f"blizzard: ruleset {ruleset.get('id')} ({ruleset.get('name')})")

    houses_index = get_auction_houses(api)
    houses = houses_index.get("auctions") or []
    print(f"blizzard: {len(houses)} auction houses")

    result: dict[str, Any] = {
        "ruleset": ruleset,
        "houses": [],
    }

    for house in houses:
        house_id = house["id"]
        name = house.get("name", house_id)
        entry: dict[str, Any] = {"id": house_id, "name": name}
        if include_listings:
            print(f"blizzard: fetching house {house_id} ({name})")
            auctions_doc = get_house_auctions(house_id, api)
            commodities_doc = get_house_commodities(house_id, api)
            auctions = auctions_doc.get("auctions") or []
            commodities = commodities_doc.get("auctions") or []
            print(
                f"blizzard: house {house_id}: "
                f"{len(auctions)} auctions, {len(commodities)} commodities"
            )
            entry["auctions"] = auctions
            entry["commodities"] = commodities
        result["houses"].append(entry)

    return result

