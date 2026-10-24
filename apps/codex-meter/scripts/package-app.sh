#!/bin/zsh
set -euo pipefail

SCRIPT_DIR=${0:A:h}
PROJECT_DIR=${SCRIPT_DIR:h}
CONFIGURATION=${1:-release}
APP_DIR="$PROJECT_DIR/dist/Codex Meter.app"
ICON_SVG_SOURCE="$PROJECT_DIR/design/icon-proposal.svg"
ICON_SOURCE="$PROJECT_DIR/.build/icon-proposal-transparent.png"
ICONSET_DIR="$PROJECT_DIR/.build/CodexMeter.iconset"

swift build \
  --package-path "$PROJECT_DIR" \
  --configuration "$CONFIGURATION"

mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources" "$ICONSET_DIR"
cp "$PROJECT_DIR/Info.plist" "$APP_DIR/Contents/Info.plist"
cp "$PROJECT_DIR/.build/$CONFIGURATION/CodexMeter" "$APP_DIR/Contents/MacOS/CodexMeter"
chmod +x "$APP_DIR/Contents/MacOS/CodexMeter"

swift "$PROJECT_DIR/scripts/render-icon.swift" "$ICON_SVG_SOURCE" "$ICON_SOURCE"
sips -z 16 16 "$ICON_SOURCE" --out "$ICONSET_DIR/icon_16x16.png" >/dev/null
sips -z 32 32 "$ICON_SOURCE" --out "$ICONSET_DIR/icon_16x16@2x.png" >/dev/null
sips -z 32 32 "$ICON_SOURCE" --out "$ICONSET_DIR/icon_32x32.png" >/dev/null
sips -z 64 64 "$ICON_SOURCE" --out "$ICONSET_DIR/icon_32x32@2x.png" >/dev/null
sips -z 128 128 "$ICON_SOURCE" --out "$ICONSET_DIR/icon_128x128.png" >/dev/null
sips -z 256 256 "$ICON_SOURCE" --out "$ICONSET_DIR/icon_128x128@2x.png" >/dev/null
sips -z 256 256 "$ICON_SOURCE" --out "$ICONSET_DIR/icon_256x256.png" >/dev/null
sips -z 512 512 "$ICON_SOURCE" --out "$ICONSET_DIR/icon_256x256@2x.png" >/dev/null
sips -z 512 512 "$ICON_SOURCE" --out "$ICONSET_DIR/icon_512x512.png" >/dev/null
sips -z 1024 1024 "$ICON_SOURCE" --out "$ICONSET_DIR/icon_512x512@2x.png" >/dev/null
iconutil -c icns "$ICONSET_DIR" -o "$APP_DIR/Contents/Resources/AppIcon.icns"
cp "$ICON_SOURCE" "$APP_DIR/Contents/Resources/AppIcon.png"
touch "$APP_DIR"

echo "$APP_DIR"
