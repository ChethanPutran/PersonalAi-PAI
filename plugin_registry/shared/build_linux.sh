#!/usr/bin/env bash
set -euo pipefail

SHARED_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REGISTRY_ROOT="$(cd "$SHARED_DIR/.." && pwd)"

echo "REGISTRY_ROOT=$REGISTRY_ROOT"

PLUGINS=(shell files screen desktop camera)

for plugin in "${PLUGINS[@]}"; do
    echo "=== Building $plugin ==="
    PLUGIN_DIR="$REGISTRY_ROOT/$plugin/1.0.0/linux"
    if [ ! -d "$PLUGIN_DIR" ]; then
        echo "  skip: $PLUGIN_DIR not found"
        continue
    fi
    rm -rf "$PLUGIN_DIR/build"
    cmake -S "$PLUGIN_DIR" -B "$PLUGIN_DIR/build" \
        -DCMAKE_BUILD_TYPE=Release
    cmake --build "$PLUGIN_DIR/build" -- -j"$(nproc)"
    cp "$PLUGIN_DIR"/build/libpai_*.so "$PLUGIN_DIR"/
    echo "  → $(ls "$PLUGIN_DIR"/libpai_*.so)"
done

echo
echo "=== All built ==="
ls -la "$REGISTRY_ROOT"/*/*/linux/libpai_*.so 2>/dev/null || true