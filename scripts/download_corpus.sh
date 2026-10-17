#!/usr/bin/env bash
# download_corpus.sh
#
# Fetches the Loghub BGL and OpenStack corpora into data/raw/ and extracts them.
#   ./scripts/download_corpus.sh            full corpora from Zenodo (~770 MB)
#   ./scripts/download_corpus.sh --sample   2,000-line samples from GitHub (seconds)
#
# Re-running skips files that already exist.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RAW="$ROOT/data/raw"
ZENODO="https://zenodo.org/records/8196385/files"
LOGHUB_RAW="https://raw.githubusercontent.com/logpai/loghub/master"

PYTHON="${PYTHON:-$(command -v python3 || command -v python)}"

mkdir -p "$RAW"

fetch() {  # fetch <url> <dest>
    if [[ -s "$2" ]]; then
        echo "  exists: ${2#$ROOT/}"
    else
        echo "  downloading: ${2#$ROOT/}"
        # -L follows Zenodo redirects; ?download=1 avoids the HTML preview page
        curl -fL --retry 3 -o "$2.part" "$1"
        mv "$2.part" "$2"
    fi
}

if [[ "${1:-}" == "--sample" ]]; then
    echo "Fetching Loghub 2k samples"
    fetch "$LOGHUB_RAW/BGL/BGL_2k.log"             "$RAW/BGL_2k.log"
    fetch "$LOGHUB_RAW/OpenStack/OpenStack_2k.log" "$RAW/OpenStack_2k.log"
    exit 0
fi

echo "Fetching full corpora from Zenodo"
fetch "$ZENODO/BGL.zip?download=1"          "$RAW/BGL.zip"
fetch "$ZENODO/OpenStack.tar.gz?download=1" "$RAW/OpenStack.tar.gz"

if [[ ! -d "$RAW/BGL" ]]; then
    echo "  extracting BGL.zip"
    # Python's zipfile is used because unzip is missing from Git Bash on Windows
    "$PYTHON" -I -m zipfile -e "$RAW/BGL.zip" "$RAW/BGL"
fi

if [[ ! -d "$RAW/OpenStack" ]]; then
    echo "  extracting OpenStack.tar.gz"
    mkdir -p "$RAW/OpenStack"
    tar -xzf "$RAW/OpenStack.tar.gz" -C "$RAW/OpenStack"
fi

echo "Done. Raw files:"
find "$RAW/BGL" "$RAW/OpenStack" -type f \( -name '*.log' -o -name '*.txt' \) -exec ls -lh {} \;
