#!/usr/bin/env python3

import logging
import os
import sys
import time

from swabbers import common, discord, filesystem, radarr, seerr, sonarr

log = logging.getLogger("korsairr")

SWABBERS = (
    ("discord", discord),
    ("filesystem", filesystem),
    ("radarr", radarr),
    ("seerr", seerr),
    ("sonarr", sonarr),
)


def main() -> int:
    log_levels = logging.getLevelNamesMapping()
    log_level = os.environ.get("KORSAIRR_LOG_LEVEL", "info").strip().upper()
    logging.basicConfig(
        level=log_levels.get(log_level, logging.INFO),
        format="[%(name)s] %(message)s",
    )

    if log_level not in log_levels:
        log.error("❌ Invalid KORSAIRR_LOG_LEVEL: %s", log_level)
        return 1

    settings = common.load_settings(common.Settings, "KORSAIRR_", log)

    if settings is None:
        return 1

    failed = False
    crew = []

    for name, module in SWABBERS:
        if not getattr(settings, f"enable_{name}"):
            log.info("💤 %s swabber disabled", name)
            continue

        swabber_settings = common.load_settings(
            module.Settings, f"KORSAIRR_{name.upper()}_", module.log
        )

        if swabber_settings is None:
            failed = True
        else:
            crew.append((name, module, swabber_settings))

    if failed:
        return 1

    if not crew:
        log.error(
            "❌ No swabbers enabled, set at least one KORSAIRR_ENABLE_<SWABBER>=true"
        )
        return 1

    log.info(
        "🏴‍☠️ Swabbing %s every %s",
        ", ".join(name for name, _, _ in crew),
        common.format_duration(settings.interval),
    )
    log.info("   log_level=%s", log_level)
    log.info("   timeout=%gs\n", settings.timeout)

    for _, module, swabber_settings in crew:
        module.banner(swabber_settings)

    while True:
        for _, module, swabber_settings in crew:
            module.swab(swabber_settings, settings)

        log.info(
            "⏰ Swabbing again in about %s . . .\n",
            common.format_duration(settings.interval),
        )
        time.sleep(settings.interval)


if __name__ == "__main__":
    sys.exit(main())
