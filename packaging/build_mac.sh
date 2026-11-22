#!/bin/bash
# Сборка Mac-версии: dist/MOOOV_player.app и dist/MOOOV_player-<версия>-mac.dmg
set -e
cd "$(dirname "$0")/.."
VERSION=$(grep -m1 '^version' pyproject.toml | cut -d'"' -f2)
uv sync --quiet
uv run --quiet python packaging/make_icons.py >/dev/null
uv run --quiet pyinstaller --noconfirm --clean packaging/MOOOV_player.spec
codesign --force --deep --sign - dist/MOOOV_player.app   # ad-hoc подпись (без Apple ID)
STAGE=$(mktemp -d)
cp -R dist/MOOOV_player.app "$STAGE/"
ln -s /Applications "$STAGE/Applications"
DMG="dist/MOOOV_player-$VERSION-mac-$(uname -m).dmg"
rm -f "$DMG"
hdiutil create -volname "MOOOV_player" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
rm -rf "$STAGE"
echo "Готово: $DMG"
