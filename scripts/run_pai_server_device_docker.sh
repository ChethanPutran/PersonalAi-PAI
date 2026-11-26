#!/usr/bin/env bash
# Run the PAI device agent in Docker, connected to a backend on THIS machine.
#
#   PAI_SERVER_EMAIL=you@example.com PAI_SERVER_PASSWORD=... ./scripts/run_pai_server_device_docker.sh
#
#   --network host  so "localhost:8000" reaches the backend (inside a container, localhost is the container)
# A previous run with an older image may have left a root-owned volume: remove it once with
#   docker volume rm pai-device
# If the container cannot resolve names (plugin installs need PyPI), your network may block the DNS servers
# Docker uses: pass your resolver, e.g.  PAI_DOCKER_DNS=10.16.25.15 ./scripts/run_pai_server_device_docker.sh
set -euo pipefail
docker run --rm --network host ${PAI_DOCKER_DNS:+--dns "$PAI_DOCKER_DNS"} \
  -e PAI_BACKEND_URL="${PAI_BACKEND_URL:-http://localhost:8000}" \
  -e PAI_SERVER_EMAIL="${PAI_SERVER_EMAIL:?set PAI_SERVER_EMAIL}" \
  -e PAI_SERVER_PASSWORD="${PAI_SERVER_PASSWORD:?set PAI_SERVER_PASSWORD}" \
  -v pai-device:/home/pai/.pai \
  pai-device
