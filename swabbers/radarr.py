import logging
from datetime import datetime, timedelta, timezone

from pyarr import Radarr
from pydantic import Field, HttpUrl, PositiveInt, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from swabbers import common
from swabbers.common import (
    format_error,
    is_systemic,
    parse_date,
    run_pyarr,
    sort_by_title,
)

log = logging.getLogger("korsairr.radarr")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KORSAIRR_RADARR_", str_strip_whitespace=True
    )

    config: str = Field("/config/radarr.xml", min_length=1)
    expiry_days: PositiveInt = 360
    retention_days: PositiveInt = 180
    url: HttpUrl = HttpUrl("http://radarr:7878")

    @field_validator("url")
    @classmethod
    def check_url(cls, value: HttpUrl) -> HttpUrl:
        if value.query or value.fragment:
            raise ValueError("must not contain a query or fragment")

        if "api" in (value.path or "").lower().split("/"):
            raise ValueError("must be the base URL without an /api path")

        return value


def try_delete_movie(
    radarr: Radarr,
    movie_id: int,
    title: str,
    delete_files: bool,
    add_exclusion: bool,
) -> bool:
    try:
        radarr.movie.delete(
            movie_id, delete_files=delete_files, add_exclusion=add_exclusion
        )
    except Exception as exc:
        if is_systemic(exc):
            raise

        log.warning("🚫 Failed to delete %s: %s", title, format_error(exc))
        return False

    return True


def swab_once(radarr: Radarr, settings: Settings) -> None:
    now = datetime.now(timezone.utc)
    retention_cutoff = now - timedelta(days=settings.retention_days)
    expiry_cutoff = now - timedelta(days=settings.expiry_days)

    movies = radarr.movie.get()
    downloaded = 0
    missing = 0
    failed = 0
    skipped = 0

    for movie in sort_by_title(movies):
        movie_id = movie.get("id")
        title = movie.get("title") or f"movie {movie_id}"

        if movie_id is None:
            continue

        movie_file = movie.get("movieFile") or {}

        if movie_file or movie.get("hasFile"):
            file_added = parse_date(movie_file.get("dateAdded"))

            if file_added is None:
                skipped += 1
                log.warning("🚫 Skipping %s: no parsable movieFile.dateAdded", title)
                continue

            if file_added >= retention_cutoff:
                continue

            if not try_delete_movie(
                radarr, movie_id, title, delete_files=True, add_exclusion=True
            ):
                failed += 1
                continue

            downloaded += 1
            log.info("🗑️ Deleted %s (file added %s)", title, file_added.isoformat())
        else:
            added = parse_date(movie.get("added"))

            if added is None:
                skipped += 1
                log.warning("🚫 Skipping %s: no parsable added date", title)
                continue

            if added >= expiry_cutoff:
                continue

            if not try_delete_movie(
                radarr,
                movie_id,
                title,
                delete_files=False,
                add_exclusion=False,
            ):
                failed += 1
                continue

            missing += 1
            log.info("🗑️ Deleted %s (added %s, no file)", title, added.isoformat())

    if downloaded or missing:
        log.info(
            "✅ Swabbed %d downloaded and %d missing movie(s)", downloaded, missing
        )

    if skipped:
        log.warning("⚠️ %d movie(s) skipped due to unparsable dates", skipped)

    if failed:
        log.warning("⚠️ %d movie(s) failed to delete", failed)

    if not (downloaded or missing or failed or skipped):
        log.info("🤷 No movies matched the swab policy")


def run(settings: Settings, korsairr: common.Settings) -> None:
    log.info("🚀 Swabbing radarr at %s", str(settings.url).rstrip("/"))
    log.info("   config=%s", settings.config)
    log.info("   expiry=%dd", settings.expiry_days)
    log.info("   retention=%dd\n", settings.retention_days)

    run_pyarr(log, Radarr, settings, korsairr, swab_once)
