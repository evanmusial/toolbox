#!/usr/bin/env bash
# brew-restore.sh

set -euo pipefail

BREWFILE="${1:-Brewfile}"

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is not installed."
  echo "Install it from https://brew.sh, then rerun this script."
  exit 1
fi

if [[ ! -f "$BREWFILE" ]]; then
  echo "Missing Brewfile: $BREWFILE"
  exit 1
fi

echo "Updating Homebrew..."
brew update

echo "Checking Brewfile..."
if brew bundle check --file="$BREWFILE" --verbose; then
  echo "Everything in $BREWFILE is already installed."
else
  echo "Installing from $BREWFILE..."
  brew bundle install --file="$BREWFILE" --no-upgrade
fi

echo "Restore complete."
echo "Optional cleanup preview:"
echo "  brew bundle cleanup --file=\"$BREWFILE\""
echo "Optional cleanup execute:"
echo "  brew bundle cleanup --file=\"$BREWFILE\" --force"