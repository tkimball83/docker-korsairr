import logging
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from pydantic import Field, PositiveInt, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from swabbers import common
from swabbers.common import format_error

log = logging.getLogger("filesystem")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KORSAIRR_FILESYSTEM_", str_strip_whitespace=True
    )

    depth: int = Field(1, ge=1, le=64)
    path: str = Field("/swab", min_length=1)
    retention_days: PositiveInt = 1

    @field_validator("path")
    @classmethod
    def check_path(cls, value: str) -> str:
        path = Path(os.path.normpath(value))

        if not path.is_absolute():
            raise ValueError("must be an absolute path")

        if path == Path(path.anchor):
            raise ValueError("must not be the filesystem root")

        return str(path)


def is_real_dir(path: Path) -> bool:
    return path.is_dir() and not path.is_symlink()


def newest_mtime(path: Path) -> datetime:
    newest = path.lstat().st_mtime

    if is_real_dir(path):
        for dirpath, dirnames, filenames in os.walk(path):
            for name in dirnames + filenames:
                try:
                    mtime = os.lstat(os.path.join(dirpath, name)).st_mtime
                except OSError:
                    continue

                newest = max(newest, mtime)

    return datetime.fromtimestamp(newest, tz=timezone.utc)


def iter_candidates(root: Path, depth: int) -> Iterator[Path]:
    for entry in sorted(root.iterdir()):
        if depth > 1 and is_real_dir(entry):
            try:
                yield from iter_candidates(entry, depth - 1)
            except OSError as exc:
                log.info("🚫 Failed to list %s: %s", entry, format_error(exc))
        else:
            yield entry


def try_delete(entry: Path) -> bool:
    try:
        if is_real_dir(entry):
            shutil.rmtree(entry)
        else:
            entry.unlink()
    except OSError as exc:
        log.info("🚫 Failed to delete %s: %s", entry, format_error(exc))
        return False

    return True


def swab_empty_dirs(root: Path, depth: int, cutoff: datetime) -> int:
    removed = 0

    for entry in sorted(root.iterdir()):
        if not is_real_dir(entry):
            continue

        if depth > 1:
            try:
                removed += swab_empty_dirs(entry, depth - 1, cutoff)
            except OSError as exc:
                log.info("🚫 Failed to list %s: %s", entry, format_error(exc))
                continue

        try:
            modified = datetime.fromtimestamp(entry.lstat().st_mtime, tz=timezone.utc)
        except OSError as exc:
            log.info("🚫 Failed to stat %s: %s", entry, format_error(exc))
            continue

        if modified >= cutoff:
            continue

        try:
            entry.rmdir()
        except OSError:
            continue

        removed += 1
        log.info("🗑️ Deleted empty directory %s", entry)

    return removed


def swab_once(settings: Settings) -> None:
    root = Path(settings.path)

    if not is_real_dir(root):
        log.info("❌ Not a directory: %s", root)
        return

    resolved = root.resolve()

    if resolved == Path(resolved.anchor):
        log.info("❌ Refusing to swab the filesystem root: %s", root)
        return

    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.retention_days)
    directories = 0
    files = 0
    failed = 0

    try:
        candidates = list(iter_candidates(root, settings.depth))
    except OSError as exc:
        log.info("❌ Failed to list %s: %s", root, format_error(exc))
        return

    for entry in candidates:
        try:
            modified = newest_mtime(entry)
        except OSError as exc:
            failed += 1
            log.info("🚫 Failed to stat %s: %s", entry, format_error(exc))
            continue

        if modified >= cutoff:
            continue

        is_directory = is_real_dir(entry)

        if not try_delete(entry):
            failed += 1
            continue

        if is_directory:
            directories += 1
        else:
            files += 1

        log.info("🗑️ Deleted %s (modified %s)", entry, modified.isoformat())

    empty = 0

    if settings.depth > 1:
        empty = swab_empty_dirs(root, settings.depth - 1, cutoff)

    if files or directories or empty:
        log.info(
            "✅ Swabbed %d file(s) and %d director(y/ies)", files, directories + empty
        )

    if failed:
        log.info("⚠️ %d entr(y/ies) failed to delete", failed)

    if not (files or directories or empty or failed):
        log.info("🤷 No entries matched the swab policy")


def banner(settings: Settings) -> None:
    log.info("🚀 Swabbing filesystem at %s", settings.path)
    log.info("   depth=%d", settings.depth)
    log.info("   retention=%dd", settings.retention_days)


def swab(settings: Settings, korsairr: common.Settings) -> None:
    try:
        swab_once(settings)
    except Exception:
        log.info("❌ Swab pass failed", exc_info=True)
