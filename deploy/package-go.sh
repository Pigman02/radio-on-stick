#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${RADIO_VERSION:-0.11.0}"
STAMP="$(date +%Y%m%d-%H%M)"
OUT="$ROOT/dist"
mkdir -p "$OUT"

build() {
  local os="$1" arch="$2" ext="$3"
  local dir="$OUT/radio-go-${VERSION}-${STAMP}-${os}-${arch}"
  local bin="$dir/radio-station-go${ext}"
  mkdir -p "$dir"
  (cd "$ROOT/station-go" && GOOS="$os" GOARCH="$arch" CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o "$bin" ./cmd/station)
  cp -a "$ROOT/app" "$dir/app"
  if [ -d "$ROOT/private" ]; then cp -a "$ROOT/private" "$dir/private"; fi
  cat > "$dir/README.txt" <<EOF
Radio Go Station ${VERSION}

Run:
  RADIO_GO_ADDR=127.0.0.1:8091 \\
  RADIO_GO_ADMIN_TOKEN=change-me \\
  RADIO_LIBRARY_DIRS=/path/to/music \\
  ./radio-station-go${ext}

Optional:
  RADIO_STATIC_DIR=app/static
  RADIO_PRIVATE_HELPER=private/plugins/commercial/helper.py
  RADIO_DIRECTORY_URL=https://join.example.com
  RADIO_DIRECTORY_REGISTER_TOKEN=...
  RADIO_DIRECTORY_PUBLIC_URL=https://station.example.com
  RADIO_JOIN_REQUIRED=1

Requires ffmpeg for source streaming and Python 3 for the optional commercial helper.
EOF
  (cd "$OUT" && tar -czf "radio-go-${VERSION}-${STAMP}-${os}-${arch}.tar.gz" "radio-go-${VERSION}-${STAMP}-${os}-${arch}")
  rm -rf "$dir"
  echo "built radio-go-${VERSION}-${STAMP}-${os}-${arch}.tar.gz"
}

build linux amd64 ""
build linux arm64 ""
build darwin arm64 ""
build darwin amd64 ""
