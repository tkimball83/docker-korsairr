import json
import logging
from collections.abc import Callable

import urllib3
from pydantic import Field, HttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

import seerr
from seerr.exceptions import ApiException

from swabbers import common

log = logging.getLogger("korsairr.seerr")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KORSAIRR_SEERR_", str_strip_whitespace=True
    )

    config: str = Field("/config/seerr.json", min_length=1)
    url: HttpUrl = HttpUrl("http://seerr:5055")

    @field_validator("url")
    @classmethod
    def check_url(cls, value: HttpUrl) -> HttpUrl:
        if value.query or value.fragment:
            raise ValueError("must not contain a query or fragment")

        if "api" in (value.path or "").lower().split("/"):
            raise ValueError("must be the base URL without an /api path")

        return value


def base_exception(exc: BaseException) -> BaseException:
    while exc.__cause__ is not None:
        exc = exc.__cause__
    return exc


def is_systemic(exc: ApiException) -> bool:
    status = exc.status or 0
    return status in (0, 401, 403, 429) or status >= 500


def format_api_error(exc: ApiException) -> str:
    parts = [f"status={exc.status}"]

    if exc.reason:
        parts.append(f"reason={exc.reason}")

    if exc.body:
        body = " ".join(exc.body.split())
        parts.append(f"body={body[:200]}")

    return " ".join(parts)


def load_api_key(config_path: str) -> str | None:
    try:
        with open(config_path, encoding="utf-8") as handle:
            config = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        log.error("❌ Failed to read config %s: %s", config_path, exc)
        return None

    try:
        return config["main"]["apiKey"].strip()
    except (KeyError, TypeError, AttributeError):
        log.error("❌ No main.apiKey found in %s", config_path)
        return None


def sorted_ids(results) -> list[int]:
    ids: list[int] = []

    for result in results or []:
        if result.id is None:
            continue

        try:
            ids.append(int(result.id))
        except (TypeError, ValueError):
            log.warning("🚫 Skipping result %r: no parsable id", result.id)

    return sorted(ids)


def swab_items(ids: list[int], delete: Callable[[str], None], label: str) -> int:
    deleted = 0

    for item_id in ids:
        try:
            delete(str(item_id))
        except ApiException as exc:
            if is_systemic(exc):
                raise

            log.warning(
                "🚫 Failed to delete %s %d: %s", label, item_id, format_api_error(exc)
            )
            continue

        deleted += 1
        log.info("🗑️ Deleted %s %d", label, item_id)

    return deleted


def swab_requests(request_api: seerr.RequestApi, timeout: float) -> int:
    response = request_api.get_request(
        take=1024, filter="all", _request_timeout=timeout
    )

    return swab_items(
        sorted_ids(response.results),
        lambda item_id: request_api.delete_request(
            request_id=item_id, _request_timeout=timeout
        ),
        "request",
    )


def swab_media(media_api: seerr.MediaApi, timeout: float) -> int:
    response = media_api.get_media(take=1024, filter="all", _request_timeout=timeout)

    return swab_items(
        sorted_ids(response.results),
        lambda item_id: media_api.delete_media(
            media_id=item_id, _request_timeout=timeout
        ),
        "media",
    )


def library_scan(settings_api: seerr.SettingsApi, timeout: float) -> None:
    request = seerr.CreateJellyfinSyncRequest(start=True)

    settings_api.create_plex_sync(
        create_jellyfin_sync_request=request, _request_timeout=timeout
    )

    log.info("📚 Triggered plex library scan")


def swab_once(
    configuration: seerr.Configuration, settings: Settings, timeout: float
) -> None:
    with seerr.ApiClient(configuration) as client:
        requests_deleted = swab_requests(seerr.RequestApi(client), timeout)

        if requests_deleted:
            log.info("✅ Swabbed %d request(s)", requests_deleted)

        media_deleted = 0

        if requests_deleted:
            media_deleted = swab_media(seerr.MediaApi(client), timeout)

        if media_deleted:
            log.info("✅ Swabbed %d media item(s)", media_deleted)
            library_scan(seerr.SettingsApi(client), timeout)

        if not (requests_deleted or media_deleted):
            log.info("🤷 No requests or media matched the swab policy")


def banner(settings: Settings) -> None:
    log.info("🚀 Swabbing seerr at %s", str(settings.url).rstrip("/"))
    log.info("   config=%s\n", settings.config)


def swab(settings: Settings, korsairr: common.Settings) -> None:
    url = str(settings.url).rstrip("/")
    api_key = load_api_key(settings.config)

    if not api_key:
        log.error("❌ Unable to determine API key")
        return

    configuration = seerr.Configuration(
        host=f"{url}/api/v1",
        api_key={"apiKey": api_key},
        retries=0,
    )

    try:
        swab_once(configuration, settings, korsairr.timeout)
    except ApiException as exc:
        log.error("❌ Swab pass failed: %s", format_api_error(exc))
    except urllib3.exceptions.HTTPError as exc:
        log.error("❌ Cannot reach %s: %s", url, base_exception(exc))
    except Exception:
        log.exception("❌ Swab pass failed")
