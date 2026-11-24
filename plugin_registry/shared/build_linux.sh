#!/usr/bin/env bash
# Build the Linux native plugins in plugin_registry/plugins/<plugin>/<version>/linux.
#
#   build_linux.sh                 build the LATEST version of every plugin
#   build_linux.sh desktop         build the latest desktop
#   build_linux.sh desktop@1.2.0   build one specific version
#   build_linux.sh --list          show what would be built, build nothing
#
# Build output goes to <plugin>/<version>/linux/artifact/ - the ONLY folder the server
# packages (it never compiles), so nothing else in the plugin folder can leak into a
# package. Every library the build produces is copied there, and stale *.so files that
# the build no longer produces are removed. The manifest's `platforms.linux.artifact`
# ("linux/libpai_x.so") is the packaged path of the main library: it must be one of
# the files in artifact/. Builds run in a temporary directory, so no build/ clutter.
#
# A released version must not change under its version number: devices that already have it
# would keep the old binary and never see an update. So the script refuses to overwrite a
# git-tracked artifact with different bytes; bump the plugin version instead (--force to
# override, only for something never released).
#
# After building: restart the server (it packages plugins at startup).
set -uo pipefail

SHARED_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REGISTRY_ROOT="$(cd "$SHARED_DIR/.." && pwd)"
PLUGINS_DIR="$REGISTRY_ROOT/plugins"

FORCE=0
LIST=0
TARGETS=()

usage() { sed -n '2,/^set -uo/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//'; }

for arg in "$@"; do
    case "$arg" in
        -h|--help)  usage; exit 0 ;;
        -l|--list)  LIST=1 ;;
        -f|--force) FORCE=1 ;;
        -*)         echo "unknown option: $arg" >&2; usage >&2; exit 2 ;;
        *)          TARGETS+=("$arg") ;;
    esac
done

# Highest version (sort -V) of a plugin that has a linux/CMakeLists.txt.
latest_version() {
    local plugin="$1" v
    for v in "$PLUGINS_DIR/$plugin"/*/; do
        v="$(basename "$v")"
        [ -f "$PLUGINS_DIR/$plugin/$v/linux/CMakeLists.txt" ] && echo "$v"
    done | sort -V | tail -n 1
}

if [ ${#TARGETS[@]} -eq 0 ]; then
    for d in "$PLUGINS_DIR"/*/; do
        [ -n "$(latest_version "$(basename "$d")")" ] && TARGETS+=("$(basename "$d")")
    done
fi

# Reads manifest.json -> "<version> <artifact path>" (empty artifact if none declared).
manifest_info() {
    python3 -I - "$1" <<'PY'
import json, sys
m = json.load(open(sys.argv[1]))
print(m.get("version", ""), (m.get("platforms", {}).get("linux", {}) or {}).get("artifact", ""))
PY
}

OK=(); FAILED=(); SKIPPED=()

build_one() {
    local target="$1" plugin version dir manifest info mver artifact so tmp log adir lib name old
    plugin="${target%@*}"
    [ "$target" = "$plugin" ] && version="$(latest_version "$plugin")" || version="${target#*@}"

    if [ -z "$version" ] || [ ! -f "$PLUGINS_DIR/$plugin/$version/linux/CMakeLists.txt" ]; then
        echo "  no linux build found for $target"; return 1
    fi
    dir="$PLUGINS_DIR/$plugin/$version/linux"
    manifest="$dir/manifest.json"
    echo "=== $plugin@$version"
    [ -f "$manifest" ] || { echo "  missing $manifest"; return 1; }

    info="$(manifest_info "$manifest")" || { echo "  unreadable manifest"; return 1; }
    mver="${info%% *}"; artifact="${info#* }"
    if [ "$mver" != "$version" ]; then
        echo "  manifest says version $mver but the folder is $version"; return 1
    fi
    if [ -z "$artifact" ]; then echo "  manifest declares no linux artifact"; return 1; fi
    case "$artifact" in
        linux/?*) so="${artifact#linux/}" ;;
        *) echo "  manifest artifact must be 'linux/<file>' (got '$artifact')"; return 1 ;;
    esac
    adir="$dir/artifact"

    # The CMake files must find shared/ (…/plugins/<p>/<v>/linux -> four levels up).
    if [ ! -f "$dir/../../../../shared/pai_system_helpers.cpp" ] || \
       ! grep -q '\.\./\.\./\.\./\.\./shared' "$dir/CMakeLists.txt"; then
        echo "  CMakeLists.txt does not point at plugin_registry/shared."
        echo "  Set: set(SHARED_DIR \"\${CMAKE_CURRENT_SOURCE_DIR}/../../../../shared\")"
        return 1
    fi

    if [ "$LIST" -eq 1 ]; then echo "  would build -> $adir/$so"; return 0; fi

    tmp="$(mktemp -d)"; log="$tmp/build.log"
    if ! { cmake -S "$dir" -B "$tmp/b" -DCMAKE_BUILD_TYPE=Release \
           && cmake --build "$tmp/b" -- -j"$(nproc)"; } >"$log" 2>&1; then
        echo "  BUILD FAILED:"; sed 's/^/    /' "$log" | tail -n 30; rm -rf "$tmp"; return 1
    fi
    if [ ! -f "$tmp/b/$so" ]; then
        echo "  build succeeded but produced no $so (manifest artifact name mismatch?)"
        ls "$tmp/b"/*.so 2>/dev/null | sed 's/^/    found: /'; rm -rf "$tmp"; return 1
    fi

    # Refuse to change a released (git-tracked) file under its version number.
    for lib in "$tmp/b"/*.so; do
        name="$(basename "$lib")"
        if [ -f "$adir/$name" ] && ! cmp -s "$lib" "$adir/$name" && [ "$FORCE" -eq 0 ] && \
           git -C "$REGISTRY_ROOT" ls-files --error-unmatch "$adir/$name" >/dev/null 2>&1; then
            echo "  REFUSING to overwrite released $plugin@$version/artifact/$name with different bytes."
            echo "  Bump the version (new folder) instead, or pass --force if it was never released."
            rm -rf "$tmp"; return 3
        fi
    done

    mkdir -p "$adir"
    for old in "$adir"/*.so; do          # stale libraries from earlier builds must not ship
        [ -e "$old" ] && [ ! -f "$tmp/b/$(basename "$old")" ] && rm -f "$old" \
            && echo "  removed stale $(basename "$old")"
    done
    for lib in "$tmp/b"/*.so; do
        cp "$lib" "$adir/"
        echo "  -> $adir/$(basename "$lib")"
        echo "     sha256 $(sha256sum "$adir/$(basename "$lib")" | cut -d' ' -f1)"
    done
    rm -rf "$tmp"
}

for t in "${TARGETS[@]}"; do
    build_one "$t"; rc=$?
    case $rc in
        0) OK+=("$t") ;;
        3) SKIPPED+=("$t") ;;
        *) FAILED+=("$t") ;;
    esac
done

echo
echo "built: ${#OK[@]}  refused: ${#SKIPPED[@]}  failed: ${#FAILED[@]}"
[ ${#SKIPPED[@]} -gt 0 ] && echo "refused (released, bump version): ${SKIPPED[*]}"
[ ${#FAILED[@]}  -gt 0 ] && echo "failed: ${FAILED[*]}"
[ "$LIST" -eq 0 ] && [ ${#OK[@]} -gt 0 ] && echo "Restart the server so it packages the new build."
[ ${#FAILED[@]} -eq 0 ] && [ ${#SKIPPED[@]} -eq 0 ]
