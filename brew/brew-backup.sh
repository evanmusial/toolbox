#!/usr/bin/env bash
# brew-backup.sh

set -euo pipefail

THIS_HOSTNAME="$(hostname -s)"
BACKUP_DIR="${HOME}/git/toolbox/brew/backups.${THIS_HOSTNAME}"
THIS_DATE="$(date -u +"%Y%m%d @ %H%M (UTC)")"

if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
  LIGHT_BLUE=$'\033[38;5;153m'
  RESET_COLOR=$'\033[0m'
else
  LIGHT_BLUE=""
  RESET_COLOR=""
fi

mkdir -p "$BACKUP_DIR"
cd "$BACKUP_DIR"

git pull --ff-only

echo
echo "Backing up Homebrew state for host ${LIGHT_BLUE}${THIS_HOSTNAME}${RESET_COLOR}"

TMP_BREWFILE="$(mktemp)"
trap 'rm -f "$TMP_BREWFILE"' EXIT

brew bundle dump --force --describe --file="$TMP_BREWFILE"

{
  echo "# backup: $THIS_DATE"
  echo "# host: $THIS_HOSTNAME"
  echo
  cat "$TMP_BREWFILE"
} > Brewfile

brew leaves --installed-on-request > brew-formulae.requested.txt
brew leaves --installed-as-dependency > brew-formulae.dependency-leaves.txt
brew list --formula --versions > brew-formulae.versions.txt
brew list --cask --versions > brew-casks.versions.txt
HOMEBREW_NO_ENV_HINTS=1 brew deps --installed --tree --annotate > brew-deps.declared.tree.txt
brew deps --installed > brew-deps.installed.txt
brew info --json=v2 --installed > brew-installed.json

git add \
  Brewfile \
  brew-formulae.requested.txt \
  brew-formulae.dependency-leaves.txt \
  brew-formulae.versions.txt \
  brew-casks.versions.txt \
  brew-deps.declared.tree.txt \
  brew-deps.installed.txt \
  brew-installed.json

if git diff --cached --quiet; then
  echo "No Homebrew changes to commit."
else
  git commit -m "Homebrew backup: ${THIS_DATE}"
  git push
fi

git pull --ff-only
echo "Homebrew backup complete for host ${LIGHT_BLUE}${THIS_HOSTNAME}${RESET_COLOR}"
echo
