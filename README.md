# toolbox

My public toolbox.

## Homebrew Backups

The `brew/` directory contains Python 3 scripts for backing up and restoring
Homebrew state on macOS.

### Usage

Install to `~/git/toolbox` and invoke directly for backup:

```~/git/toolbox/brew/brew-backup.py```

Integrate into a daily update shortcut called `brewup` inside `~/.zshrc`:

```
function brewup() {

  # Update omzsh if there's an update, but keep it from exiting 0 and stopping the rest of the tasks.
  ZSH="${ZSH:-$HOME/.oh-my-zsh}" zsh "$ZSH/tools/upgrade.sh" || true

  # Do brew updates and cleanup
  brew update && brew upgrade && brew cleanup &&

  # Back up brew installed components
  ~/git/toolbox/brew/brew-backup.py &&

  # Download (but don't automatically install) any updates to Apple components
  softwareupdate --download --all

}
```

### Important Forking Note

This repository includes backup folders for my own Macs. If you fork or copy
this repository for your own use, remove the existing `brew/backups*` folders
first unless you intentionally want to install the Homebrew package lists from
my computers.

### Host-Specific Backup Folders

Backups are keyed by the short host name returned by:

```sh
hostname -s
```

For example, a Mac whose short host name is `ramanujan` stores its Homebrew
snapshot in:

```text
brew/backups.ramanujan/
```

The scripts intentionally use `hostname -s` for the short name, not
`hostname -a`. The restore script uses the same value to choose its default
Brewfile, so backup and restore stay scoped to the same host unless you pass a
Brewfile path explicitly.

### Back Up This Mac

Clone or keep this repository at `~/git/toolbox`, then run:

```sh
brew/brew-backup.py
```

The backup script:

1. Uses `hostname -s` to choose `brew/backups.<host>/`.
2. Pulls the latest repo state before collecting data.
3. Writes a `Brewfile` and supporting Homebrew inventory files.
4. Commits and pushes changes when Homebrew state changed.
5. Pulls again at the end so the checkout is ready for the next run.

### Restore This Mac

To restore from the current host's default backup folder:

```sh
brew/brew-restore.py
```

To restore from a specific Brewfile:

```sh
brew/brew-restore.py brew/backups.some-host/Brewfile
```

The restore script runs `brew update`, checks the Brewfile, and installs missing
entries with `brew bundle install --no-upgrade`. It does not run cleanup
automatically; it prints the cleanup command for review after restore completes.
