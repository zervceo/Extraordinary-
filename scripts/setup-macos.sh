#!/usr/bin/env bash
# One-time setup for mbtok on a Mac.
#
# Installs ffmpeg (the only external dependency), puts the mbtok command on
# your PATH, and points a new project at the folders where your photos and
# footage actually live.

set -euo pipefail

say() { printf '\n\033[1m%s\033[0m\n' "$1"; }
note() { printf '  %s\n' "$1"; }
die() { printf '\n\033[31m%s\033[0m\n' "$1" >&2; exit 1; }

cd "$(dirname "$0")/.."

say "1. Checking Python"
command -v python3 >/dev/null || die "python3 not found. Install it from python.org or with: brew install python"
note "$(python3 --version)"

say "2. Checking ffmpeg"
if command -v ffmpeg >/dev/null; then
  note "$(ffmpeg -version | head -1)"
else
  if command -v brew >/dev/null; then
    note "installing ffmpeg with Homebrew (this takes a few minutes)"
    brew install ffmpeg
  else
    die "ffmpeg is missing and Homebrew is not installed.
  Install Homebrew from https://brew.sh then run: brew install ffmpeg"
  fi
fi

say "3. Installing mbtok"
python3 -m pip install --user -e . >/dev/null
note "installed. Run it with: python3 -m mbtok"

say "4. Setting up your project"
ROOTS=()
for folder in "$HOME/Downloads" "$HOME/Pictures" "$HOME/Movies" "$HOME/Desktop"; do
  [ -d "$folder" ] && ROOTS+=("$folder")
done
python3 -m mbtok init "${ROOTS[@]}"

say "Done. Next steps:"
note "python3 -m mbtok scan       # index your media (slow the first time)"
note "python3 -m mbtok boards     # see which moods your library supports"
note "python3 -m mbtok make       # render your first post"
