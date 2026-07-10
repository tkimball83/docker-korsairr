#!/usr/bin/env python3

import logging
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
    logging.basicConfig(
        level=logging.INFO,
        format="[%(name)s] %(message)s",
        stream=sys.stdout,
    )

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
        while True:
            time.sleep(settings.interval)

    log.info(
        "🏴‍☠️ Swabbing %s every %s",
        ", ".join(name for name, _, _ in crew),
        common.format_duration(settings.interval),
    )
    log.info("   timeout=%gs", settings.timeout)

    for _, module, swabber_settings in crew:
        module.banner(swabber_settings)

    while True:
        for _, module, swabber_settings in crew:
            module.swab(swabber_settings, settings)
            sys.stdout.write("\n")

        log.info(
            "⏰ Swabbing again in about %s . . .",
            common.format_duration(settings.interval),
        )
        time.sleep(settings.interval)


if __name__ == "__main__":
    sys.exit(main())
