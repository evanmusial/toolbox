# Codex Meter

Codex Meter is a small native macOS dashboard for the quota and credit data
provided by the local Codex app-server. It launches `codex app-server`, reads
`account/rateLimits/read`, and refreshes the snapshot every five seconds.

The app includes:

- a compact, translucent notch/status-bar readout with remaining quota and
  workspace credits;
- hover expansion for quota reset details;
- blue background trends in a lower band beneath quota and credits, with crisp text and black
  text shadows;
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
The packaging script renders the Dock icon with transparency.

## Test

```sh
swift test --package-path apps/codex-meter
```

Codex Meter displays percentages returned by Codex. “Available” is calculated
as `100 - usedPercent`; the service does not provide an absolute token quota.
Workspace credit balance and earned reset credits are shown only when the
account returns those fields.

## Trend lines

The dashboard and expanded notch info center share a **Trend lines** switch
and a time-range selector: **7d, 3d, 24h, 12h, 6h, 3h, 1h**. The default is
24 hours. Both settings survive an app restart; the menu-bar menu can also
toggle the lines.

History begins with readings collected while Codex Meter is running. It is
stored per account in `~/Library/Application Support/Codex Meter/usage-history.json`,
with one reading per minute retained for seven days. The live point updates
with each refresh. Switching the lines off hides them while collection
continues. Missing readings and periods when the app is asleep or offline
are left blank rather than filled in. Available readings fill the trend band;
the controls show how much history has actually been recorded within the selected
lookback. Quota always uses a fixed 0–100% scale. Credit balances use the
observed minimum and maximum inside the selected lookback so small changes
remain visible. A constant credit balance sits at the center of the band.
There is no fixed or rounded credit ceiling. Recorded history survives app
restarts.

## Hourly usage rotation

The compact and expanded HUDs alternate between balances for **15 seconds**
and active-use averages for **5 seconds**, without changing the HUD dimensions.
The number remains large and bold; `pts/hour` or `credits/hour` is smaller and
regular-weight. When no consumption has been observed for a metric, its normal
balance stays visible.

Change **Numbers** and **Rates** under **HUD rotation** in the full app; both
durations are saved between launches. Rates use the selected history range and
are estimated as total observed decreases divided by the duration of those
decreasing intervals, converted to an hourly rate. Flat intervals, increases
(quota resets or credit additions), missing readings, and gaps over five
minutes are excluded. Quota rates are percentage points per hour. History is
sampled once per minute, so active time is estimated from those observations.
