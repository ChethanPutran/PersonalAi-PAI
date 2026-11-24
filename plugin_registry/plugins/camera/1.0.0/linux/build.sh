#!/usr/bin/env bash
# Build the Linux camera plugin. Prefer plugin_registry/shared/build_linux.sh (builds in a temp
# dir and refuses to change a released file); this is the manual equivalent.
# Build output belongs in artifact/ - the only folder the server packages.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build
mkdir -p artifact
cp build/libpai_camera.so artifact/libpai_camera.so
