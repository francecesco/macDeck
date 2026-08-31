#!/bin/bash
# Compila l'eseguibile e lo impacchetta in una .app che Spotlight trova.
# Firma ad-hoc: basta per girare su questa macchina, e non serve un account.
set -euo pipefail
cd "$(dirname "$0")"

swift build -c release

APP="MacDeck.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp Resources/Info.plist "$APP/Contents/Info.plist"
cp "$(swift build -c release --show-bin-path)/MacDeck" "$APP/Contents/MacOS/MacDeck"
cp Resources/MacDeck.icns "$APP/Contents/Resources/MacDeck.icns"

codesign --force --sign - "$APP"
echo "fatto: $(pwd)/$APP"
echo "per averla in Spotlight:  cp -R $APP /Applications/"
