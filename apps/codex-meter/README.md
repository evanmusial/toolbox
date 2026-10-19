# Codex Meter

Codex Meter is a small native macOS dashboard for the quota and credit data
provided by the local Codex app-server. It launches `codex app-server`, reads
`account/rateLimits/read`, and refreshes the snapshot every five seconds.

The app includes:

- a compact, translucent notch/status-bar readout with remaining quota and
  workspace credits;
- hover expansion for quota reset details;
- a full dark-mode dashboard;
- a menu-bar control for opening, refreshing, hiding, or quitting the app; and
- a packaged macOS Dock icon.

## Requirements

- macOS 13 or newer
- Swift 5.10 or newer
- The `codex` CLI installed and signed in with ChatGPT

The app discovers `codex` from `PATH`, `/opt/homebrew/bin`,
`/usr/local/bin`, `~/.local/bin`, or `~/bin`. It does not read or store Codex
credentials itself.

## Run from source

```sh
cd apps/codex-meter
swift run CodexMeter
```

## Build an app bundle

```sh
cd apps/codex-meter
./scripts/package-app.sh
open "dist/Codex Meter.app"
```

The unsigned local app bundle is written to `dist/Codex Meter.app`.
The packaging script renders the Dock icon with transparency and, when the
ChatGPT app is installed in `/Applications`, bundles its Codex brand icon for
the compact quota readout.

## Test

```sh
swift test --package-path apps/codex-meter
```

Codex Meter displays percentages returned by Codex. “Available” is calculated
as `100 - usedPercent`; the service does not provide an absolute token quota.
Workspace credit balance and earned reset credits are shown only when the
account returns those fields.
