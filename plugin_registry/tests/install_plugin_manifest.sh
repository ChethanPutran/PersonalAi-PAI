#!/usr/bin/env bash
#
# Validate every plugin manifest under a directory.
#
# Usage:
#   ./plugin_registry/tests/install_plugin_manifest.sh [DIR]
#
#   DIR   Directory to scan for `manifest.json` files.
#         Defaults to `plugin_registry/plugins`.
#
# Exit codes:
#   0   all manifests valid
#   1   at least one manifest failed validation
#   2   bad usage / missing dependency / invalid path
#
# Examples:
#   ./plugin_registry/tests/install_plugin_manifest.sh
#   ./plugin_registry/tests/install_plugin_manifest.sh plugin_registry/plugins
#   ./plugin_registry/tests/install_plugin_manifest.sh plugin_registry/plugins/browser
#
set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve repo root (this script lives at <repo>/plugin_registry/tests/)
# so the command works from any CWD.
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." &>/dev/null && pwd)"

DEFAULT_DIR="plugin_registry/plugins"

# ---------------------------------------------------------------------------
# Args
# ---------------------------------------------------------------------------
if [[ $# -gt 1 ]]; then
    echo "usage: $(basename "$0") [DIR]" >&2
    exit 2
fi

TARGET_DIR="${1:-$DEFAULT_DIR}"

# Allow relative paths from the caller's CWD, but resolve them properly
if [[ ! -e "$TARGET_DIR" && -e "$REPO_ROOT/$TARGET_DIR" ]]; then
    TARGET_DIR="$REPO_ROOT/$TARGET_DIR"
fi

if [[ ! -d "$TARGET_DIR" ]]; then
    echo "error: '$TARGET_DIR' is not a directory" >&2
    exit 2
fi

TARGET_DIR="$(cd -- "$TARGET_DIR" &>/dev/null && pwd)"

# ---------------------------------------------------------------------------
# Sanity checks
# ---------------------------------------------------------------------------
if ! command -v python >/dev/null 2>&1; then
    echo "error: 'python' not found in PATH" >&2
    exit 2
fi

# Confirm the validator module is importable before invoking it, so we get a
# clean error instead of a Python traceback.
if ! python -c "import pai.plugins.manifest_install" >/dev/null 2>&1; then
    echo "error: cannot import 'pai.plugins.manifest_install'." >&2
    echo "       Is the project installed (pip install -e .) and are you" >&2
    echo "       running from inside the activated venv?" >&2
    exit 2
fi

# ---------------------------------------------------------------------------
# Run validator
# ---------------------------------------------------------------------------
echo "validating manifests under: $TARGET_DIR"
echo

set +e
python -m pai.plugins.manifest_install "$TARGET_DIR"
rc=$?
set -e

echo
if [[ $rc -eq 0 ]]; then
    echo "✓ all manifests valid"
else
    echo "✗ manifest validation failed (exit $rc)" >&2
fi

exit "$rc"