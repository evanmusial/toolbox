# Mac Audio Plug-in Parity

`plugin_parity.py` creates durable, host-named inventories of AU/AUv3, VST/VST3,
and AAX plug-ins, optionally merges the richer Ableton Live catalog, and compares
two Macs for migration parity. It is designed for a one-time `drift` baseline and
repeatable `ramanujan` progress scans.

The tool reads bundle metadata only. It does not execute plug-in binaries,
instantiate them in a host, or contact license managers.

## 1. Capture the `drift` baseline

Clone or update this repository on `drift`, then run:

```bash
cd ~/git/toolbox
python3 utilities/plugin-parity/plugin_parity.py scan
```

The default output is:

```text
utilities/plugin-parity/inventories/drift.json
```

To merge the existing Ableton Live report as a second source of evidence:

```bash
python3 utilities/plugin-parity/plugin_parity.py scan \
  --live-inventory "/path/to/Ableton Live Plug-in Inventory - drift.txt"
```

The Live input can be JSON, CSV, TSV, or the grouped text report created by
`ableton-live-tools/src/extract_plugin_inventory.py`. The filesystem scan remains
authoritative for bundles Live does not know about, including AAX.

Commit and push `inventories/drift.json` so the baseline is available on the new
Mac. The snapshot contains paths and bundle metadata; review it before publishing
the repository if any local path information is sensitive.

## 2. Scan `ramanujan`

Pull the repository on `ramanujan`, then run:

```bash
cd ~/git/toolbox
python3 utilities/plugin-parity/plugin_parity.py scan
```

This writes `inventories/ramanujan.json`. If a current Live inventory is
available, merge it with the same `--live-inventory` option.

## 3. Compare parity

On `ramanujan`:

```bash
python3 utilities/plugin-parity/plugin_parity.py compare \
  --baseline utilities/plugin-parity/inventories/drift.json
```

The candidate defaults to the current host's snapshot. The CLI prints a compact
diff and also writes the complete uncolored report to:

```text
utilities/plugin-parity/reports/drift-vs-ramanujan.txt
```

The interactive view:

- hides matching records by default;
- groups missing products by manufacturer;
- folds missing formats for the same product onto one line;
- uses restrained 256-color ANSI styling only on a TTY;
- respects `NO_COLOR` and supports `--color auto|always|never`;
- collapses candidate-only records and version differences unless requested.

Useful detail switches:

```bash
python3 utilities/plugin-parity/plugin_parity.py compare \
  --baseline utilities/plugin-parity/inventories/drift.json \
  --show-versions

python3 utilities/plugin-parity/plugin_parity.py compare \
  --baseline utilities/plugin-parity/inventories/drift.json \
  --show-additional --show-matches
```

`--fail-on-gaps` returns exit code `3` while baseline records remain missing,
which is useful for a final automated parity gate. Input and output failures
return `1`.

Re-run `scan` and `compare` as installation progresses. The host snapshot and
report use stable filenames, so Git shows the migration's progress without
creating a new artifact for every pass.

## What is scanned

Default roots include:

- `/Library/Audio/Plug-Ins/Components` and the user equivalent;
- `/System/Library/Components`;
- system and user VST and VST3 folders;
- system and user Avid `Audio/Plug-Ins` folders;
- Avid `Plug-Ins (Unused)` folders, marked disabled;
- AUv3 extensions inside applications under `/Applications` and
  `~/Applications`.

Add nonstandard locations with a repeatable `FORMAT=PATH` option:

```bash
python3 utilities/plugin-parity/plugin_parity.py scan \
  --root 'VST3=/Volumes/Audio/Custom VST3' \
  --root 'AAX=/Volumes/Audio/AAX Plug-Ins'
```

Use `--no-default-roots` to scan only explicit roots and `--skip-auv3` to skip
application-bundle inspection.

The scanner expands AU `AudioComponents` declarations and public VST3
`moduleinfo.json` classes. VST2 and AAX are generally bundle-level inventories.
An AAX shell can contain several products that standardized bundle metadata does
not enumerate; the snapshot records that limitation rather than claiming class-
level completeness.

## Limits

Presence is not loadability or activation. The report does not validate CPU
architecture, code signatures, licenses, presets, samples, support libraries,
or successful recall in a DAW session. Vendor installers may place important
content outside plug-in directories. After the presence gaps reach zero, rescan
in each DAW and open representative projects before retiring `drift`.

## Tests

```bash
PYTHONDONTWRITEBYTECODE=1 \
python3 -m unittest discover -s utilities/plugin-parity/tests -v
```
