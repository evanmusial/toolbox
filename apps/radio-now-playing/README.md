# Radio Now Playing

One persistent Python daemon polls configuration-defined metadata sources and posts song changes to Slack. No shell or PHP scripts run in its polling or delivery pipeline.

This directory contains the application, regression tests, systemd unit, pinned dependencies, and example configurations. Real webhooks exist only in protected configuration on carrot and in root-only backups. The example files contain placeholders.

## Install dependencies and run tests

From this directory, create a virtual environment and install the pinned dependencies:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v
```

The deployed environment uses Python 3.10. The service unit is in `deploy/`; actual configuration, state, and recovery archives remain outside this repository. Populate webhook placeholders only in the protected server configuration, not in the tracked examples.

## Deployment paths

| Purpose | Path on carrot |
| --- | --- |
| Application and isolated dependencies | `/opt/radio-now-playing` |
| Main configuration | `/etc/radio-now-playing/radio-now-playing.conf` |
| Station configurations | `/etc/radio-now-playing/conf.d/*.conf` |
| SQLite state | `/var/lib/radio-now-playing/state.sqlite3` |
| Instance lock, containing the owning PID | `/var/lib/radio-now-playing/daemon.lock` |
| Service | `radio-now-playing.service` |
| Protected rollback backups and receipts | `/var/backups/now-playing-bots` |

The service runs as `evan`. Configuration directories are `root:evan 0750`; configuration files are `root:evan 0640`. World-readable files and files writable by group/others are rejected. The service has an immutable system view and write access to its state directory.

Active schedules: **SLAM 30 seconds**, **C89.5 30 seconds**, **BPM 120 seconds**. Each station has an independent asynchronous task. The same station never overlaps itself. Slow cycles skip catch-up bursts.

## Operations

Run application commands from `/opt/radio-now-playing`:

```sh
sudo systemctl status radio-now-playing.service
sudo journalctl -u radio-now-playing.service -n 60 --no-pager
sudo journalctl -u radio-now-playing.service -f
sudo systemctl restart radio-now-playing.service
sudo systemctl stop radio-now-playing.service

.venv/bin/python -m radio_now_playing check-config
.venv/bin/python -m radio_now_playing fetch
.venv/bin/python -m radio_now_playing fetch --station c895
.venv/bin/python -m radio_now_playing status
```

`check-config` performs no network access. `fetch` fetches and formats only: it never posts and never modifies state. `status` opens SQLite read-only. These commands never print configured webhook URLs. The instance lock rejects a second daemon with exit code 3 before any fetch or post.

To change or add a station, use `sudoedit` on a protected `.conf` file, set its permissions, validate, then reload:

```sh
sudoedit /etc/radio-now-playing/conf.d/example.conf
sudo chown root:evan /etc/radio-now-playing/conf.d/example.conf
sudo chmod 0640 /etc/radio-now-playing/conf.d/example.conf
.venv/bin/python -m radio_now_playing check-config
sudo systemctl reload radio-now-playing.service
```

Reload validates every configuration before changing the running tasks. Invalid configuration is rejected while the old tasks continue. Valid reloads drain current checks/deliveries, then replace the task set. Changing the database path requires a separate migration. Boot enablement uses the normal `multi-user.target`; no timer or tmux is needed.

## Configuration

INI parsing has interpolation disabled, so `%` in URLs/values is literal. Do not use a `[DEFAULT]` section. Values may be JSON-quoted to retain surrounding spaces, escapes, or line breaks: `join = ", "`. Templates accept only plain field names, not Python expressions, attribute access, indexing, or formatting conversions.

The main file contains `[daemon]` and optional `[defaults]`. See `examples/radio-now-playing.conf`. Station-specific HTTP settings in `[http]` override those defaults.

Each station requires a unique lowercase `id`, `enabled`, a `method` (`json`, `html`, or `xml`), a source `url`, `webhook_url`, `poll_interval`, and one or more `[field.NAME]` mappings. `identity` defaults to `{display_text}`. BPM uses `{play_id}` so a later airing of the same song is still a new play. `initial_post=false` suppresses the first observation of a new, unseeded configuration.

**JSON mappings** use JSON Pointer (not JSONPath), supporting object keys and zero-based array indices:

```ini
[station]
id = example-json
enabled = false
method = json
url = https://example.com/metadata
webhook_url = REPLACE_ON_SERVER
poll_interval = 30
identity = {play_id}

[field.title]
path = /results/0/track/title

[field.artists]
path = /results/0/track/artists
multiple = true
join = ", "

[field.play_id]
path = /results/0/id
```

Escape `/` in a JSON object key as `~1`, and `~` as `~0`. An empty path selects the whole JSON value. Missing optional values require `required=false`; objects are never silently converted into song text.

**HTML mappings** use CSS selectors. For old Icecast tables, label-relative extraction tolerates nested markup, case differences, extra whitespace, and an optional trailing colon:

```ini
[field.display_text]
selector = td.streamdata
after_label = Current Song:
label_selector = td
```

Without `after_label`, `selector` selects directly from the page. With it, the engine finds the matching label, then selects a following sibling. `attribute=text` (the default) extracts text; `attribute=src` or another attribute extracts that value. Set `multiple=true` to collect matches. Parsing is explicitly tolerant HTML, even when Icecast includes an XML prolog.

**XML mappings** use XPath 1.0, including attributes and namespace mappings:

```ini
[namespaces]
np = urn:example:now-playing

[field.title]
path = /np:now/np:title

[field.play_id]
path = /np:now/@id
```

DTD/entity documents are rejected and external resolution is disabled. This method is implemented and covered by tests, although none of the three initial feeds needs XML extraction.

Field options also include `required`, `multiple`, `join`, `collapse_whitespace`, `html_unescape`, `regex`, and `regex_group`. HTML/XML parsers already decode their document entities; additional `html_unescape` is off by default. Regex extraction uses capture group 1 by default, or group 0 when configured. No arbitrary code or external command execution is supported.

Mapped fields can be referenced by name in templates. Built-in values include `station`, `display_text`, `title`, `artists`, `play_id`, `image_url`, and `match_key`. When no display text is mapped, the engine constructs it from artists and title. A `match_key` normalizes title and sorted artists using the previous BPM implementation's rules.

`[message]` supports `layout=field` (the original SLAM/C89.5 attachment) or `layout=section` (the existing BPM block with optional artwork), plus `title`, `text`, `fallback`, and `color_key` templates. The color is the first six SHA-1 hex digits of the configured color key. Template field values are Slack-escaped, and the final payload is serialized with JSON rather than textual substitution. Preserve BPM's multiline text with an indented continuation line as shown in its example file.

## Timeouts and retries

Current defaults are a 5-second connect timeout, 8-second read timeout, 12-second whole-request timeout, three total attempts, and a 25-second total fetch/retry budget. Backoff is 1 second then 2 seconds plus up to 0.25 seconds of jitter. Response size is capped at 1 MiB.

Connection failures, timeouts, HTTP 408/425/429/5xx, and missing/invalid required metadata are retryable. Ordinary permanent HTTP errors are not retried. `Retry-After` is honored when it fits within the cycle's retry budget. Exhaustion leaves the last valid identity intact and the next scheduled cycle tries again. Error pages and error strings never become song announcements.

## Delivery and duplicate handling

SQLite is updated durably before starting a POST. Confirmed Slack `200 ok` responses mark the delivery sent. Repeated observations and process restarts suppress the same observed play. A song returning after a different song is a new occurrence and may post again. Last-observed identity and last-delivered identity are separate.

A POST timeout or interrupted delivery can have an ambiguous server outcome. To avoid duplicate posts, it is recorded as **uncertain** and is not automatically resent. A known pre-connection failure or HTTP 429 may retry on a later poll, up to three delivery attempts. Rejected/uncertain deliveries appear in the journal and `status`; investigate them rather than deleting state. Exactly-once external delivery cannot be guaranteed across an unacknowledged network operation.

`status` reports checks, suppressed observations, errors, last successful fetch/delivery times, delivery counts, and recent delivery dispositions. Journal events include `posted`, `unchanged`, `fetch_retry`, `fetch_exhausted`, `delivery_uncertain`, `configuration_reloaded`, and `reload_rejected`. Logs omit raw network exception text and webhook URLs.

## Translation and preserved originals

The original `urlencode.php` generated attachment colors and replaced placeholders in JSON; these operations now use `hashlib`, dictionaries, and JSON serialization. The original `now_playing.php` primarily printed artist/title/mix diagnostics and contained a disabled integration. That disabled integration and its old embedded credential were not carried into the new application. The active legacy message text and existing BPM API/block/artwork behavior were retained.

Originals remain in `/home/evan/slam`, `/home/evan/c895`, `/home/evan/bpm`, `/home/evan/c895-now-playing`, `/home/evan/now-playing-bpm`, and `/opt/now-playing-bot`. The old `now-playing-bot.service` was **BPM only**, with a two-minute timer. Its timer is disabled after cutover. The old files are preserved for reference; they are not invoked by the new daemon. PHP was removed after all three feeds demonstrated successful live delivery and unchanged-track suppression.

## Backups and recovery

The initial archive is `/var/backups/now-playing-bots/pre-migration-20261024T133700Z.tar.gz`, with a SHA-256 sidecar. It contains credentials, so keep it root-only. The archive's manifest verifies contents, ownership, modes, and symlinks. `consistent-state/` contains the integrity-checked SQLite snapshot; `original-files/` mirrors original paths. Read its `RESTORE.txt` before restoring.

The timestamped `cutover-*` directory contains another final old BPM state snapshot, legacy text files, imported initial state, and a phased receipt. PHP package/configuration recovery information is kept separately before uninstalling PHP. Final verification receipts are stored alongside these backups.

For immediate rollback to the previous Python BPM service, first stop the replacement and confirm it has exited. Then reconcile the last BPM play identity from the new state into the old state before re-enabling the old timer; otherwise it could replay the latest song. The old service cannot restore SLAM/C89.5 automatically. Reusing their original scripts after PHP removal requires reinstalling the recorded PHP packages. Never run both implementations simultaneously, never remove the new database to "reset" delivery history, and never extract credential backups into a repository.

## Verification and dependencies

Run `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v`. Tests use local HTTP servers and mock webhook delivery; they never contact real Slack. Coverage includes real read-timeout recovery, missing DOM recovery, HTTP retry classifications, bounded retries, independent scheduling, JSON/XML/HTML extraction, message parity, migration, locking, duplicate suppression across restarts, ambiguous deliveries, and valid/invalid live reload.

`requirements.lock` in the deployed application records every installed dependency version. Python's SQLite module and the `sqlite3` CLI use SQLite 3; the separate old `sqlite` command is unnecessary for this daemon.
