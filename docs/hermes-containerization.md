# Hermes Containerization

Hermes should run as managed Docker containers on the NAS, not as loose host
processes. This keeps restart policy, service ownership, and DSM visibility in
one place.

## Services

`deploy/hermes/docker-compose.yml` defines four host-network services:

- `hermes`: Hermes gateway and cron scheduler.
- `hermes-dashboard`: web dashboard on port `9119`.
- `paotuan-webhook`: GitHub webhook receiver on port `8766`.
- `hermes-coder-bridge`: AstrBot `/coder` bridge and notify API on port `8767`.

The compose file currently uses the existing `node:22-bookworm` runtime image and
mounts the existing Hermes installation into the containers. This avoids building
the large Hermes image directly on the NAS. It deliberately mounts the existing
host paths at the same paths inside the containers:

- `/volume1/docker/hermes`
- `/volume1/docker/astrbot`

Keeping paths stable avoids rewriting the deployment scripts, log review state,
secrets paths, AstrBot plugin paths, and OpenAPI notification config.

The Docker socket is not mounted. Hermes deploys the plugin by copying files into
the AstrBot data volume and asking the AstrBot Dashboard API to hot reload the
plugin, so it does not need blanket Docker control.

Containers run as the NAS deployment user UID/GID (`1026:100` on the current
host, or the caller's `id -u` / `id -g` through `hermes-container-migrate.sh`) so
Hermes does not create root-owned state files in mounted volumes.

## Migration

The migration script is intentionally conservative:

```sh
/volume1/docker/hermes/paotuan/bin/hermes-container-migrate.sh preflight
/volume1/docker/hermes/paotuan/bin/hermes-container-migrate.sh up
```

`preflight` refuses to continue if `/volume1` has less than 5 GiB free and checks
that the existing Hermes virtualenv plus `node:22-bookworm` image are present.

`up` performs the same preflight, stops the old host Hermes processes, starts the
compose project, and verifies:

- `http://127.0.0.1:8767/health`
- `http://127.0.0.1:8766/health`
- `http://127.0.0.1:9119/`

## Rollback

If the containerized services fail health checks:

```sh
/volume1/docker/hermes/paotuan/bin/hermes-container-migrate.sh down
/volume1/docker/hermes/paotuan/bin/start_services.sh
```

This returns to the previous host-process mode without touching AstrBot or
Docker containers unrelated to Hermes.

Do not keep the old host-process watchdog enabled after the container migration.
The compose services use Docker restart policies instead.

This is an intermediate containerization step: Hermes becomes Docker-managed and
DSM-visible, but the Hermes Python virtualenv still lives on the mounted NAS
volume. A later cleanup can replace this with a fully self-contained prebuilt
Hermes image produced off the NAS.
