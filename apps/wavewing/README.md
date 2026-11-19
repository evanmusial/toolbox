# Wavewing

A native macOS menu-bar app that shows now-playing metadata on the left side
of the notch. Click the readout or the music-note menu-bar icon to configure
stations and the display interval.

The readout cycles every five seconds by default. Ordinary station changes
dissolve. New-track animations are opt-in per station and default to off.
For a station with the option enabled, a new song jumps into view with a
horizontal glide and a brief blue tint on the bold station name. A fresh song
stays visible for at least six seconds. Other stations update quietly and remain
in the normal cycle. Artist and title appear in regular text to the left of the
cutout; the bold station name appears to its right. Both wings dissolve together
while cycling and glide together for an opted-in new track.

Each wing grows to fit its text within available menu-bar space. Wavewing can
follow menu items as other apps draw or redraw them when Accessibility access is
allowed from settings. Without it, the wings stay compact. The complete text
is available in the settings window and as a tooltip when it still truncates.
Stations join the cycle only after a current song is available. The HUD stays
hidden if none has usable metadata.

## Stations and polling

All nominated stations are selected on first launch. Turn any station off in
the settings window. The network cadence is separate from the display cycle:

| Source | Stations | Minimum interval |
| --- | --- | ---: |
| SLAM API | SLAM | 30 seconds |
| C89.5 Icecast page | C89.5 | 30 seconds |
| xmplaylist shared feed | BPM, Diplo's Revolution, SiriusXM Chill, Utopia | 120 seconds |
| Revolution current-song endpoints | Revolution 93.5 Miami, Revolution 99.7 Las Vegas | 30 seconds each |
| SecureNet player XML | KGAY | 60 seconds |
| Icecast status JSON | My959, Pulse 87 | 60 seconds each |
| Triton public now-playing history | Party 105 | 60 seconds |

The app polls each source only while at least one of its stations is selected.
SiriusXM uses one shared feed request for all four stations and honors its
120-second cache header. The app sends a User-Agent, honors longer cache
durations and Retry-After responses, and backs off after errors. ETags are used
when provided. It never downloads an audio stream. An advertisement, a DJ mix,
or a delayed SiriusXM report is not displayed as a current song.

If Codex Meter is running, Wavewing's panels cover the quota and credit readouts
on the two sides of the cutout.

## Build and run

Requires macOS 13 or later and Swift 5.10 or later.

    swift test --disable-sandbox --package-path apps/wavewing
    apps/wavewing/scripts/package-app.sh
    open "apps/wavewing/dist/Wavewing.app"

The package script creates and ad-hoc signs the app bundle in the dist folder.
The Dock and Finder icon comes from the design folder's ICNS; its editable SVG
and 2048-pixel transparent PNG are kept alongside it.
No credentials are stored; station selection, visibility, and the display
interval use local UserDefaults.
