import logging
import xml.etree.ElementTree as ElementTree
from datetime import datetime, timezone
from typing import Callable

from pyarr.exceptions import (
    PyarrConnectionError,
    PyarrError,
    PyarrRecordNotFound,
    PyarrResourceNotFound,
)
from pydantic import HttpUrl, PositiveFloat, PositiveInt, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

NON_SYSTEMIC = (
    PyarrRecordNotFound,
    PyarrResourceNotFound,
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KORSAIRR_", str_strip_whitespace=True)

    enable_discord: bool = False
    enable_filesystem: bool = False
    enable_radarr: bool = False
    enable_seerr: bool = False
    enable_sonarr: bool = False
    interval: PositiveInt = 86400
    timeout: PositiveFloat = 30


def bold(value) -> str:
    return f"\033[1m{value}\033[0m"


def check_url(value: HttpUrl) -> HttpUrl:
    if value.query or value.fragment:
        raise ValueError("must not contain a query or fragment")

    if "api" in (value.path or "").lower().split("/"):
        raise ValueError("must be the base URL without an /api path")

    return value


def format_duration(seconds: int) -> str:
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)

    if hours:
        return f"{hours}h{minutes}m{secs}s"

    if minutes:
        return f"{minutes}m{secs}s"

    return f"{secs}s"


def format_error(exc: BaseException) -> str:
    message = " ".join(str(exc).split())

    if message:
        return f"{type(exc).__name__}: {message[:200]}"

    return type(exc).__name__


def is_systemic(exc: Exception) -> bool:
    if isinstance(exc, NON_SYSTEMIC):
        return False

    if isinstance(exc, PyarrError):
        return True

    status = exc.args[0] if exc.args and isinstance(exc.args[0], int) else 0
    return status in (0, 401, 403, 429) or status >= 500


def load_settings(cls, prefix: str, log: logging.Logger):
    try:
        return cls()
    except ValidationError as exc:
        for error in exc.errors():
            name = "_".join(
                str(part) for part in error["loc"] if not isinstance(part, int)
            ).upper()
            log.info("❌ Invalid %s%s: %s", prefix, name, error["msg"])
        return None


def load_xml_api_key(log: logging.Logger, config_path: str) -> str | None:
    try:
        tree = ElementTree.parse(config_path)
    except (OSError, ElementTree.ParseError) as exc:
        log.info("❌ Failed to read config %s: %s", config_path, exc)
        return None

    element = tree.getroot().find("ApiKey")
    api_key = (element.text or "").strip() if element is not None else ""

    if not api_key:
        log.info("❌ No Config/ApiKey found in %s", config_path)
        return None

    return api_key


def parse_date(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed


def swab_pyarr(
    log: logging.Logger,
    client_cls: type,
    settings,
    korsairr: Settings,
    swab_once: Callable,
) -> None:
    url = str(settings.url).rstrip("/")
    api_key = load_xml_api_key(log, settings.config)

    if not api_key:
        log.info("❌ Unable to determine API key")
        return

    try:
        with client_cls(
            url,
            api_key,
            request_timeout=korsairr.timeout,
            api_ver="v3",
        ) as client:
            swab_once(client, settings)
    except PyarrConnectionError as exc:
        log.info("❌ Cannot reach %s: %s", url, format_error(exc))
    except PyarrError as exc:
        log.info("❌ Swab pass failed: %s", format_error(exc))
    except Exception:
        log.info("❌ Swab pass failed", exc_info=True)


def sort_by_title(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda item: str(item.get("title") or "").lower())
