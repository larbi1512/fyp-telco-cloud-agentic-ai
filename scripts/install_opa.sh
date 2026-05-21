#!/usr/bin/env bash
# Download the static OPA binary into <repo>/bin/opa so the policy validator
# can shell out to `opa eval`. No sudo required.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="$REPO_ROOT/bin"
BIN_PATH="$BIN_DIR/opa"

case "$(uname -m)" in
    x86_64)  ARCH_SUFFIX="amd64_static" ;;
    aarch64) ARCH_SUFFIX="arm64_static" ;;
    arm64)   ARCH_SUFFIX="arm64_static" ;;
    *)
        echo "Unsupported architecture: $(uname -m)" >&2
        exit 1
        ;;
esac

URL="https://openpolicyagent.org/downloads/latest/opa_linux_${ARCH_SUFFIX}"

mkdir -p "$BIN_DIR"
echo "Downloading OPA from $URL"
curl -fsSL -o "$BIN_PATH" "$URL"
chmod +x "$BIN_PATH"

echo
echo "Installed: $BIN_PATH"
"$BIN_PATH" version
