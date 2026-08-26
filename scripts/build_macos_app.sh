#!/bin/bash
# Build FragileVision.app: a self-contained macOS app bundle that launches
# from Finder or the Dock with no terminal, no pip install and no bundler
# dependency. Everything it needs is the macOS tools already on the machine
# (sips, iconutil, codesign) — consistent with the project shipping zero
# required runtime dependencies.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_SUPPORT="$REPO_ROOT/scripts/macos_app"
OUT_DIR="$REPO_ROOT/dist"
APP="$OUT_DIR/FragileVision.app"
VERSION="$(python3 -c "import sys; sys.path.insert(0,'$REPO_ROOT'); import fragilevision; print(fragilevision.__version__)")"

echo "Building FragileVision.app $VERSION"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/app"

# --- launcher -----------------------------------------------------------
cp "$BUILD_SUPPORT/launcher.sh" "$APP/Contents/MacOS/FragileVision"
chmod +x "$APP/Contents/MacOS/FragileVision"

# --- Info.plist -----------------------------------------------------------
sed "s/__VERSION__/$VERSION/g" "$BUILD_SUPPORT/Info.plist.template" > "$APP/Contents/Info.plist"

# --- icon -----------------------------------------------------------------
# sips rasterizes the existing SVG favicon directly; no separate art asset
# and no new dependency. iconutil (also stock macOS) packs the .iconset.
ICONSET="$(mktemp -d)/AppIcon.iconset"
mkdir -p "$ICONSET"
SOURCE_SVG="$REPO_ROOT/fragilevision/static/favicon.svg"
for spec in "16 icon_16x16" "32 icon_16x16@2x" "32 icon_32x32" "64 icon_32x32@2x" \
           "128 icon_128x128" "256 icon_128x128@2x" "256 icon_256x256" \
           "512 icon_256x256@2x" "512 icon_512x512" "1024 icon_512x512@2x"; do
    set -- $spec
    sips -s format png -z "$1" "$1" "$SOURCE_SVG" --out "$ICONSET/$2.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"
rm -rf "$(dirname "$ICONSET")"

# --- app payload ------------------------------------------------------------
# The fragilevision package has zero required runtime dependencies (see
# pyproject.toml), so a plain source copy is the whole payload: no pip
# install step, nothing to go stale relative to a lockfile that doesn't exist.
cp -R "$REPO_ROOT/fragilevision" "$APP/Contents/Resources/app/fragilevision"
find "$APP/Contents/Resources/app/fragilevision" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
find "$APP/Contents/Resources/app/fragilevision" -name '*.pyc' -delete

# --- signing ----------------------------------------------------------------
# Ad-hoc signing (no Developer ID, no notarization — this is a local build,
# never downloaded, so Gatekeeper's quarantine dialog never applies to it).
# On Apple Silicon a completely unsigned executable will not run at all, so
# this step is required, not cosmetic.
codesign --force --deep --sign - "$APP" 2>&1 | grep -v "^$" || true

# --- sanity check -----------------------------------------------------------
# Catches a broken build (bad plist, missing payload, unsigned binary) right
# here rather than at the next double-click, when there is no terminal left
# to explain what went wrong.
plutil -lint -s "$APP/Contents/Info.plist" >/dev/null || { echo "Info.plist non valido"; exit 1; }
[ -x "$APP/Contents/MacOS/FragileVision" ] || { echo "Il launcher non è eseguibile"; exit 1; }
[ -f "$APP/Contents/Resources/app/fragilevision/__main__.py" ] || { echo "Il pacchetto Python non è stato copiato"; exit 1; }
codesign --verify "$APP" || { echo "La firma ad-hoc non ha superato la verifica"; exit 1; }

echo "Built: $APP"
echo "Trascina FragileVision.app su /Applications o sul Dock. Log: ~/Library/Logs/FragileVision/fragilevision.log"
