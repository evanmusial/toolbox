#!/bin/bash

# Get the hostname of this machine
THIS_HOSTNAME=$(hostname | sed -e 's/.local//g' | | cut -d'.' -f1)

# Bounce to the directory where I have git installed.
mkdir -p ~/git/toolbox/brew/backups.${THIS_HOSTNAME}
cd ~/git/toolbox/brew/backups.${THIS_HOSTNAME}

# Pull since multiple machines use this
git pull

# Handy visual indicator that the backup is happening.
echo "🦺 Backup of brew casks and formulae..."

# An eyeball friendly date string, but use UTC for consistency.
THIS_DATE=$(date -u +"%Y%m%d @ %H%M (UTC)")

# Mark the files more handily.
echo "# backup: $THIS_DATE" > brew-formulas.list.txt
echo "# backup: $THIS_DATE" > brew-casks.list.txt
echo "# backup: $THIS_DATE" > brew-casks.versions.txt
echo "# backup: $THIS_DATE" > brew-formulas.versions.txt

# Get list of brew formulas, and add that list to git.
brew list --formula -1 >> brew-formulas.list.txt
brew list --cask -1 >> brew-casks.list.txt
brew list --cask --versions >> brew-casks.versions.txt
brew list --formula --versions >> brew-formulas.versions.txt

# Add the text files to a git commit, then push it.
git add brew-formulas.list.txt
git add brew-casks.list.txt
git add brew-casks.versions.txt
git add brew-formulas.versions.txt

# Make a git push with the date and time referenced, then push that to the remove repo.
git commit -m "Brew formula & casks backup: ${THIS_DATE}"
git push
