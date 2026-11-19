#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIGURATION="release"
if (( $# > 0 )); then CONFIGURATION="$1"; fi
APP_DIR="$PROJECT_DIR/dist/Wavewing.app"
ICON_SOURCE="$PROJECT_DIR/design/wavewing-icon.icns"

export CLANG_MODULE_CACHE_PATH="$PROJECT_DIR/.build/clang-cache"
export SWIFT_MODULECACHE_PATH="$PROJECT_DIR/.build/swift-cache"
swift build --disable-sandbox --cache-path "$PROJECT_DIR/.build/package-cache" \
  --package-path "$PROJECT_DIR" --configuration "$CONFIGURATION" -debug-info-format none
BIN_DIR="$(swift build --disable-sandbox --cache-path "$PROJECT_DIR/.build/package-cache" \
  --package-path "$PROJECT_DIR" --configuration "$CONFIGURATION" --show-bin-path)"
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"
cp "$PROJECT_DIR/Info.plist" "$APP_DIR/Contents/Info.plist"
cp "$BIN_DIR/Wavewing" "$APP_DIR/Contents/MacOS/Wavewing"
chmod +x "$APP_DIR/Contents/MacOS/Wavewing"
cp "$ICON_SOURCE" "$APP_DIR/Contents/Resources/AppIcon.icns"
codesign --force --sign - "$APP_DIR"
touch "$APP_DIR"
print -r -- "$APP_DIR"
