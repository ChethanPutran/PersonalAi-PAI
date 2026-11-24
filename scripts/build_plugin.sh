#!/usr/bin/env bash
# Build a Kotlin Android plugin into a .dex.
#
# Usage:
#   scripts/build_plugin.sh <plugin-dir> <version>
#
# Example:
#   scripts/build_plugin.sh plugin_registry/camera/1.0.0/android 1.0.0
#
# Output: <plugin-dir>/plugin.dex
set -euo pipefail

# -----------------------------------------------------------------
# 1. Resolve the plugin dir to absolute FIRST — before any cd.
#    Every later path is built from $PLUGIN_DIR so cwd never matters.
# -----------------------------------------------------------------
PLUGIN_DIR_RAW="${1:?plugin dir required}"
VERSION="${2:?version required}"

if [[ ! -d "$PLUGIN_DIR_RAW" ]]; then
    echo "error: plugin dir not found: $PLUGIN_DIR_RAW" >&2
    exit 1
fi
PLUGIN_DIR="$(cd "$PLUGIN_DIR_RAW" && pwd -P)"
echo "PLUGIN_DIR:     $PLUGIN_DIR"

if [[ ! -f "$PLUGIN_DIR/build.gradle.kts" ]]; then
    echo "error: no build.gradle.kts in $PLUGIN_DIR" >&2
    exit 1
fi

# -----------------------------------------------------------------
# 2. Locate the Android SDK
# -----------------------------------------------------------------
SDK="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}"
if [[ -z "$SDK" ]]; then
    echo "error: ANDROID_HOME or ANDROID_SDK_ROOT must be set" >&2
    exit 1
fi
if [[ ! -d "$SDK" ]]; then
    echo "error: SDK dir does not exist: $SDK" >&2
    exit 1
fi

D8=""
if [[ -n "${BUILD_TOOLS_VER:-}" ]]; then
    D8="$SDK/build-tools/$BUILD_TOOLS_VER/d8"
else
    D8=$(ls -1d "$SDK"/build-tools/*/d8 2>/dev/null | sort -V | tail -1 || true)
fi
if [[ -z "$D8" || ! -x "$D8" ]]; then
    echo "error: d8 not found under $SDK/build-tools/*/d8" >&2
    exit 1
fi
BUILD_TOOLS_VER=$(basename "$(dirname "$D8")")

ANDROID_JAR=""
for v in 35 34 33 32 31; do
    if [[ -f "$SDK/platforms/android-$v/android.jar" ]]; then
        ANDROID_JAR="$SDK/platforms/android-$v/android.jar"
        break
    fi
done
if [[ -z "$ANDROID_JAR" ]]; then
    echo "error: no android.jar under $SDK/platforms/" >&2
    exit 1
fi

echo "SDK:            $SDK"
echo "build-tools:    $BUILD_TOOLS_VER"
echo "d8:             $D8"
echo "android.jar:    $ANDROID_JAR"

# -----------------------------------------------------------------
# 3. Locate gradle (wrapper preferred)
# -----------------------------------------------------------------
GRADLE_CMD="$PLUGIN_DIR/gradlew"
if [[ ! -x "$GRADLE_CMD" ]]; then
    if command -v gradle >/dev/null 2>&1; then
        GRADLE_CMD="gradle"
        echo "note: no ./gradlew in $PLUGIN_DIR — using system gradle"
    else
        echo "error: no ./gradlew and no 'gradle' on PATH" >&2
        exit 1
    fi
fi
echo "gradle:         $GRADLE_CMD"

# -----------------------------------------------------------------
# 4. Build the AAR. cd once, stay there.
# -----------------------------------------------------------------
echo
echo "=== assembleRelease ==="
(
    cd "$PLUGIN_DIR"
    "$GRADLE_CMD" assembleRelease
)

# -----------------------------------------------------------------
# 5. Find the AAR. Already in $PLUGIN_DIR? Yes — but use an explicit
#    subshell so this block never leaks cwd.
# -----------------------------------------------------------------
AAR_REL="$(
    cd "$PLUGIN_DIR"
    find build/outputs/aar -maxdepth 1 -name '*-release.aar' -print -quit
)"
if [[ -z "$AAR_REL" ]]; then
    echo "error: no *-release.aar under $PLUGIN_DIR/build/outputs/aar/" >&2
    echo "       contents:" >&2
    ls -la "$PLUGIN_DIR/build/outputs/aar/" 2>/dev/null >&2 || true
    exit 1
fi
AAR="$PLUGIN_DIR/$AAR_REL"
echo "AAR:            $AAR"

# -----------------------------------------------------------------
# 6. Extract classes.jar from the AAR
# -----------------------------------------------------------------
WORK="$PLUGIN_DIR/build/plugin-dist"
rm -rf "$WORK"
mkdir -p "$WORK/extracted" "$WORK/out"

echo
echo "=== extracting classes.jar ==="
unzip -q -o "$AAR" -d "$WORK/extracted" classes.jar

if [[ ! -f "$WORK/extracted/classes.jar" ]]; then
    echo "error: classes.jar not found inside $AAR" >&2
    exit 1
fi

# -----------------------------------------------------------------
# 7. classes.jar → classes.dex via d8
# -----------------------------------------------------------------
echo
echo "=== d8: classes.jar → classes.dex ==="
"$D8" \
    --output "$WORK/out" \
    --min-api 24 \
    --lib "$ANDROID_JAR" \
    "$WORK/extracted/classes.jar"

if [[ ! -f "$WORK/out/classes.dex" ]]; then
    echo "error: d8 produced no classes.dex" >&2
    exit 1
fi

# -----------------------------------------------------------------
# 8. Copy the DEX to where the manifest's `artifact` path points.
#    Manifest declares `android/plugin.dex` relative to the version dir.
#    $PLUGIN_DIR is <version-dir>/android, so the file goes right here.
# -----------------------------------------------------------------
DEX_OUT="$PLUGIN_DIR/plugin.dex"
cp "$WORK/out/classes.dex" "$DEX_OUT"

SIZE=$(stat -c %s "$DEX_OUT" 2>/dev/null || stat -f %z "$DEX_OUT")
SHA=$(sha256sum "$DEX_OUT" | awk '{print $1}')

echo
echo "=== done ==="
echo "artifact:  $DEX_OUT"
echo "size:      $SIZE bytes"
echo "sha256:    $SHA"
echo
echo "verify:"
echo "  curl -s http://127.0.0.1:8000/api/v1/plugins/camera/$VERSION/artifact/android | sha256sum"