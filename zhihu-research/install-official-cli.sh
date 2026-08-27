#!/bin/sh
# Install only the official Zhihu Open Platform CLI release.  The manifest
# supplies its pinned platform artifact, expected size and SHA-256.
set -eu

manifest_url='https://developer-cdn.zhihu.com/zhihu-cli/releases/stable/manifest.json'
case "$(uname -m)" in
  aarch64|arm64) platform='linux-arm64' ;;
  x86_64|amd64) platform='linux-amd64' ;;
  *) echo "unsupported architecture" >&2; exit 1 ;;
esac

apt-get update >/dev/null
apt-get install -y --no-install-recommends ca-certificates curl tar >/dev/null
rm -rf /var/lib/apt/lists/*

workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT
curl --fail --silent --show-error --connect-timeout 10 --max-time 60 -o "$workdir/manifest.json" "$manifest_url"
python3 - "$workdir/manifest.json" "$platform" >"$workdir/artifact.txt" <<'PY'
import json
import sys
from urllib.parse import urlparse

with open(sys.argv[1], encoding="utf-8") as source:
    manifest = json.load(source)
artifact = manifest["cli"]["artifacts"][sys.argv[2]]
url = artifact["url"]
host = urlparse(url).hostname
if host != "developer-cdn.zhihu.com" or not url.startswith("https://"):
    raise SystemExit("unexpected official artifact host")
size = artifact["size"]
checksum = artifact["sha256"]
if not isinstance(size, int) or size <= 0 or len(checksum) != 64:
    raise SystemExit("invalid artifact metadata")
print(url)
print(size)
print(checksum)
PY

artifact_url=$(sed -n '1p' "$workdir/artifact.txt")
expected_size=$(sed -n '2p' "$workdir/artifact.txt")
expected_sha=$(sed -n '3p' "$workdir/artifact.txt")
curl --fail --silent --show-error --connect-timeout 10 --max-time 60 -o "$workdir/cli.tar.gz" "$artifact_url"
[ "$(wc -c < "$workdir/cli.tar.gz" | tr -d '[:space:]')" = "$expected_size" ]
printf '%s  %s\n' "$expected_sha" "$workdir/cli.tar.gz" | sha256sum -c -
[ "$(tar -tzf "$workdir/cli.tar.gz")" = 'zhihu-cli' ]
tar -xOzf "$workdir/cli.tar.gz" zhihu-cli >/usr/local/bin/zhihu-cli
chmod 755 /usr/local/bin/zhihu-cli
/usr/local/bin/zhihu-cli version >/dev/null
