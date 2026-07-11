# docker-korsairr

[![License](https://img.shields.io/badge/license-GPLv3-lightgreen)](https://www.gnu.org/licenses/gpl-3.0.en.html#license-text)
[![Release](https://github.com/tkimball83/docker-korsairr/actions/workflows/release.yml/badge.svg)](https://github.com/tkimball83/docker-korsairr/actions/workflows/release.yml)

Continuously swab the stack

## Configuration

### Global

| Variable                     | Default | Description                         |
|------------------------------|---------|-------------------------------------|
| `KORSAIRR_ENABLE_DISCORD`    | `false` | Enable the discord swabber          |
| `KORSAIRR_ENABLE_FILESYSTEM` | `false` | Enable the filesystem swabber       |
| `KORSAIRR_ENABLE_RADARR`     | `false` | Enable the radarr swabber           |
| `KORSAIRR_ENABLE_SEERR`      | `false` | Enable the seerr swabber            |
| `KORSAIRR_ENABLE_SONARR`     | `false` | Enable the sonarr swabber           |
| `KORSAIRR_INTERVAL`          | `86400` | Seconds between swab passes         |
| `KORSAIRR_TIMEOUT`           | `30`    | Per-request http timeout in seconds |

### Discord

Deletes old messages from every channel and thread in the guild.

| Variable                          | Default | Description                           |
|-----------------------------------|---------|---------------------------------------|
| `KORSAIRR_DISCORD_DELETE_PINNED`  | `false` | Also delete pinned messages           |
| `KORSAIRR_DISCORD_GUILD_ID`       | `null`  | Guild to swab (required when enabled) |
| `KORSAIRR_DISCORD_RETENTION_DAYS` | `7`     | Swab messages after this age          |
| `KORSAIRR_DISCORD_TOKEN`          | `null`  | Bot token (required when enabled)     |

### Filesystem

Deletes each entry under the swab path once everything inside it is old.

| Variable                             | Default | Description                             |
|--------------------------------------|---------|-----------------------------------------|
| `KORSAIRR_FILESYSTEM_DEPTH`          | `1`     | Swab candidates at this directory depth |
| `KORSAIRR_FILESYSTEM_PATH`           | `/swab` | Path to the mounted swab directory      |
| `KORSAIRR_FILESYSTEM_RETENTION_DAYS` | `1`     | Swab entries after this age             |

### Radarr

Deletes old downloaded movies (with exclusion) and expired missing movies.

| Variable                         | Default              | Description                               |
|----------------------------------|----------------------|-------------------------------------------|
| `KORSAIRR_RADARR_CONFIG`         | `/config/radarr.xml` | Path to the mounted radarr config         |
| `KORSAIRR_RADARR_EXPIRY_DAYS`    | `360`                | Swab movies without a file after this age |
| `KORSAIRR_RADARR_RETENTION_DAYS` | `180`                | Swab downloaded files after this age      |
| `KORSAIRR_RADARR_URL`            | `http://radarr:7878` | Base url of the radarr instance           |

### Seerr

Deletes every request, then every media entry, then triggers a plex library scan.

| Variable                | Default              | Description                      |
|-------------------------|----------------------|----------------------------------|
| `KORSAIRR_SEERR_CONFIG` | `/config/seerr.json` | Path to the mounted seerr config |
| `KORSAIRR_SEERR_URL`    | `http://seerr:5055`  | Base url of the seerr instance   |

### Sonarr

Deletes old episode files, unmonitors empty seasons, and deletes ended empty series.

| Variable                         | Default              | Description                                |
|----------------------------------|----------------------|--------------------------------------------|
| `KORSAIRR_SONARR_CONFIG`         | `/config/sonarr.xml` | Path to the mounted sonarr config          |
| `KORSAIRR_SONARR_GRACE_DAYS`     | `7`                  | Skip series added within this grace period |
| `KORSAIRR_SONARR_GRACE_EPISODES` | `8`                  | Minimum episodes to unmonitor a season     |
| `KORSAIRR_SONARR_RETENTION_DAYS` | `180`                | Swab episode files after this age          |
| `KORSAIRR_SONARR_URL`            | `http://sonarr:8989` | Base url of the sonarr instance            |

## Usage

Attach the container to the same user-defined Docker network as the services
it swabs so their names resolve.

```sh
docker run -d --restart unless-stopped \
  --network korsairr-bridge \
  --mount type=bind,source=/containers/radarr/config/config.xml,target=/config/radarr.xml,readonly \
  --mount type=bind,source=/containers/sonarr/config/config.xml,target=/config/sonarr.xml,readonly \
  -e KORSAIRR_ENABLE_RADARR=true \
  -e KORSAIRR_ENABLE_SONARR=true \
  -e KORSAIRR_INTERVAL=86400 \
  -e KORSAIRR_RADARR_URL=http://radarr:7878 \
  -e KORSAIRR_SONARR_URL=http://sonarr:8989 \
  ghcr.io/tkimball83/korsairr:latest
```
