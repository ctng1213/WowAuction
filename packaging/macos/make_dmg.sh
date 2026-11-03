#!/bin/sh
# Build the macOS disk image from the stapled dist/AHSync.app (SPEC §3.7.12).
# Run from the repository root: packaging/macos/make_dmg.sh <version>
# The dmg holds AHSync.app and an Applications symlink, so the user drags the
# app into Applications (an app run from the dmg would be translocated, §3.7.13).
set -eu

if [ "$#" -ne 1 ]; then
    echo "usage: $0 <version>" >&2
    exit 2
fi

version="$1"
stage="dist/dmg"

rm -rf "$stage"
mkdir -p "$stage"
# ditto, not cp: it keeps symlinks and the code signature intact.
ditto dist/AHSync.app "$stage/AHSync.app"
ln -s /Applications "$stage/Applications"
hdiutil create -volname AHSync -srcfolder "$stage" -ov -format UDZO \
    "dist/AHSync-$version-macos-arm64.dmg"
rm -rf "$stage"
