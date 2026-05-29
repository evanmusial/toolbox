#!/usr/bin/env bash
set -euo pipefail

BREWFILE="${1:-Brewfile}"

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is not installed yet."
  echo "Install it from https://brew.sh, then rerun this script."
  exit 1
fi

if [[ ! -f "$BREWFILE" ]]; then
  echo "Missing Brewfile: $BREWFILE"
  exit 1
fi

brew update

echo "Checking missing dependencies..."
brew bundle check --file="$BREWFILE" --verbose || true

echo "Installing from $BREWFILE..."
brew bundle install --file="$BREWFILE" --no-upgrade

echo
echo "Restore complete."
echo "To preview removals not listed in the Brewfile:"
echo "  brew bundle cleanup --file=\"$BREWFILE\""
echo "To actually remove them:"
echo "  brew bundle cleanup --file=\"$BREWFILE\" --force"