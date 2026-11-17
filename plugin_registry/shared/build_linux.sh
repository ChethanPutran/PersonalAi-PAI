#!/usr/bin/env bash
set -euo pipefail

REGISTRY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo $REGISTRY_ROOT
SHARED="$REGISTRY_ROOT/shared"

for plugin in shell files screen desktop; do
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
    cp "$PLUGIN_DIR/build/libpai_$plugin.so" "$PLUGIN_DIR/libpai_$plugin.so"
    echo "  → $PLUGIN_DIR/libpai_$plugin.so"
done

echo
echo "=== All built ==="
ls -la "$REGISTRY_ROOT"/*/1.0.0/linux/libpai_*.so 2>/dev/null