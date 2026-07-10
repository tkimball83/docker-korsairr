import logging
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from pyarr import Sonarr
from pydantic import (
    Field,
    HttpUrl,
    NonNegativeInt,
    PositiveInt,
    field_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from swabbers import common
from swabbers.common import (
    check_url,
    format_error,
    is_systemic,
    parse_date,
    sort_by_title,
    swab_pyarr,
)

log = logging.getLogger("sonarr")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KORSAIRR_SONARR_", str_strip_whitespace=True
    )

    config: str = Field("/config/sonarr.xml", min_length=1)
    grace_days: NonNegativeInt = 7
    grace_episodes: PositiveInt = 8
    retention_days: PositiveInt = 180
    url: HttpUrl = HttpUrl("http://sonarr:8989")

    validate_url = field_validator("url")(check_url)


class EpisodeFileEntry(NamedTuple):
    title: str
    episode_file: dict


def in_grace_period(series: dict, settings: Settings) -> bool:
    if not settings.grace_days:
        return False

    added = parse_date(series.get("added"))

    if added is None:
        return False

    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.grace_days)
    return added >= cutoff


def get_series(sonarr: Sonarr) -> list[dict]:
    series = sonarr.series.get()

    if not isinstance(series, list):
        raise ValueError("Expected a list response from the 'series' endpoint")

    return series


def get_episode_file_entries(
    sonarr: Sonarr, series: list[dict]
) -> list[EpisodeFileEntry]:
    episode_files: list[EpisodeFileEntry] = []

    for item in sort_by_title(series):
        statistics = item.get("statistics") or {}

        if int(statistics.get("episodeFileCount") or 0) == 0:
            continue

        series_id = item.get("id")

        if series_id is None:
            continue

        title = item.get("title") or f"series {series_id}"
        response = sonarr.episode_file.get(series_id=series_id)

        if not isinstance(response, list):
            raise ValueError(
                "Expected a list response from the "
                f"'episodefile?seriesId={series_id}' endpoint"
            )

        episode_files.extend(
            EpisodeFileEntry(title, episode_file) for episode_file in response
        )

    return episode_files


def try_delete_episode_file(sonarr: Sonarr, episode_file_id: int, title: str) -> bool:
    try:
        sonarr.episode_file.delete(episode_file_id)
    except Exception as exc:
        if is_systemic(exc):
            raise

        log.info(
            "🚫 Failed to delete episode file %s for %s: %s",
            episode_file_id,
            title,
            format_error(exc),
        )
        return False

    return True


def swab_episode_files(
    sonarr: Sonarr, series: list[dict], settings: Settings
) -> tuple[int, int, int]:
    now = datetime.now(timezone.utc)
    retention_cutoff = now - timedelta(days=settings.retention_days)
    deleted = 0
    failed = 0
    skipped = 0

    episode_files = get_episode_file_entries(sonarr, series)

    for title, episode_file in episode_files:
        episode_file_id = episode_file.get("id")

        if episode_file_id is None:
            skipped += 1
            log.info("🚫 Skipping episode file for %s: no id", title)
            continue

        file_added = parse_date(episode_file.get("dateAdded"))

        if file_added is None:
            skipped += 1
            log.info(
                "🚫 Skipping episode file %s for %s: no parsable dateAdded",
                episode_file_id,
                title,
            )
            continue

        if file_added >= retention_cutoff:
            continue

        if not try_delete_episode_file(sonarr, episode_file_id, title):
            failed += 1
            continue

        deleted += 1
        log.info("🗑️ Deleted episode file %s for %s", episode_file_id, title)

    return deleted, failed, skipped


def latest_season(series: dict) -> int:
    return max(
        (season.get("seasonNumber") or 0 for season in series.get("seasons") or []),
        default=0,
    )


def should_unmonitor_season(series: dict, season: dict, settings: Settings) -> bool:
    statistics = season.get("statistics") or {}

    return (
        series.get("status") != "upcoming"
        and not (
            series.get("status") == "continuing"
            and bool(season.get("seasonNumber"))
            and season.get("seasonNumber") == latest_season(series)
        )
        and bool(season.get("monitored"))
        and int(statistics.get("episodeFileCount") or 0) == 0
        and int(statistics.get("totalEpisodeCount") or 0) >= settings.grace_episodes
        and not statistics.get("nextAiring")
    )


def try_unmonitor_season(
    sonarr: Sonarr,
    series_id: int,
    title: str,
    series_monitored: bool,
    season_number: int,
) -> bool:
    try:
        sonarr.http_utils.request(
            "seasonpass",
            method="POST",
            json_data={
                "series": [
                    {
                        "id": series_id,
                        "monitored": series_monitored,
                        "seasons": [
                            {
                                "seasonNumber": season_number,
                                "monitored": False,
                            }
                        ],
                    }
                ]
            },
        )
    except Exception as exc:
        if is_systemic(exc):
            raise

        log.info(
            "🚫 Failed to unmonitor %s season %s: %s",
            title,
            season_number,
            format_error(exc),
        )
        return False

    return True


def unmonitor_empty_seasons(
    sonarr: Sonarr, series: list[dict], settings: Settings
) -> tuple[int, int]:
    unmonitored = 0
    failed = 0

    for item in sort_by_title(series):
        series_id = item.get("id")
        title = item.get("title") or f"series {series_id}"
        series_monitored = bool(item.get("monitored"))

        if series_id is None or in_grace_period(item, settings):
            continue

        for season in item.get("seasons") or []:
            if not should_unmonitor_season(item, season, settings):
                continue

            season_number = season.get("seasonNumber")

            if season_number is None:
                failed += 1
                log.info("🚫 Skipping %s season: no season number", title)
                continue

            if not try_unmonitor_season(
                sonarr, series_id, title, series_monitored, season_number
            ):
                failed += 1
                continue

            unmonitored += 1
            log.info("📺 Unmonitored %s season %s", title, season_number)

    return unmonitored, failed


def try_delete_series(sonarr: Sonarr, series_id: int, title: str) -> bool:
    try:
        sonarr.series.delete(series_id, delete_files=True)
    except Exception as exc:
        if is_systemic(exc):
            raise

        log.info("🚫 Failed to delete %s: %s", title, format_error(exc))
        return False

    return True


def delete_ended_empty_series(
    sonarr: Sonarr, series: list[dict], settings: Settings
) -> tuple[int, int]:
    deleted = 0
    failed = 0

    for item in sort_by_title(series):
        statistics = item.get("statistics") or {}

        if in_grace_period(item, settings):
            continue

        if int(statistics.get("episodeFileCount") or 0) != 0:
            continue

        if item.get("status") != "ended":
            continue

        series_id = item.get("id")
        title = item.get("title") or f"series {series_id}"

        if series_id is None:
            continue

        if not try_delete_series(sonarr, series_id, title):
            failed += 1
            continue

        deleted += 1
        log.info("🗑️ Deleted %s (ended with no episode files)", title)

    return deleted, failed


def swab_once(sonarr: Sonarr, settings: Settings) -> None:
    series = get_series(sonarr)
    deleted_files, file_failures, skipped = swab_episode_files(sonarr, series, settings)
    failed = file_failures

    series = get_series(sonarr)
    unmonitored_seasons, season_failures = unmonitor_empty_seasons(
        sonarr, series, settings
    )
    failed += season_failures

    series = get_series(sonarr)
    deleted_series, series_failures = delete_ended_empty_series(
        sonarr, series, settings
    )
    failed += series_failures

    if deleted_files or unmonitored_seasons or deleted_series:
        log.info(
            "✅ Swabbed %d episode file(s), unmonitored %d season(s), "
            "deleted %d series",
            deleted_files,
            unmonitored_seasons,
            deleted_series,
        )

    if skipped:
        log.info(
            "⚠️ %d episode file(s) skipped due to missing or unparsable data",
            skipped,
        )

    if failed:
        log.info("⚠️ %d operation(s) failed", failed)

    if not (
        deleted_files or unmonitored_seasons or deleted_series or failed or skipped
    ):
        log.info("🤷 No episode files or series matched the swab policy")


def banner(settings: Settings) -> None:
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    log.info(
        "🚀 config=%s grace_days=%dd grace_episodes=%d retention=%dd url=%s",
        settings.config,
        settings.grace_days,
        settings.grace_episodes,
        settings.retention_days,
        str(settings.url).rstrip("/"),
    )


def swab(settings: Settings, korsairr: common.Settings) -> None:
    swab_pyarr(log, Sonarr, settings, korsairr, swab_once)
