#!/bin/sh
set -eu
cd "$(dirname "$0")"

python3 -m PyInstaller --noconfirm --onedir --windowed \
    --name newINCA --osx-bundle-identifier io.github.pplechov.newinca \
    --add-data "calibration:calibration" --add-data "points:points" \
    src/main.py
