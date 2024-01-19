#!/bin/bash

# Bounce to the directory where I have git installed.
cd ~/git/toolbox/brew/backups

# Handy visual indicator that the backup is happening.
echo "🦺 Backup of brew casks and formulae..."

# Get list of brew formulas, and add that list to git.
brew list --formula -1 > brew-formulas.list.txt
brew list --cask -1 > brew-casks.list.txt
brew list --cask --versions > brew-casks.versions.txt
brew list --formula --versions > brew-formulas.versions.txt

# Add the text files to a git commit, then push it.
git add brew-formulas.list.txt
git add brew-casks.list.txt
git add brew-casks.versions.txt
git add brew-formulas.versions.txt

# An eyeball friendly date string, but use UTC for consistency.
THIS_DATE=$(date -u +"%Y%m%d @ %H%M (UTC)")

# Make a git push with the date and time referenced, then push that to the remove repo.
git commit -m "Statutory backup of brew formulas and casks installed: ${THIS_DATE}"
git push