# YouTube Subscription Review

A safe, interactive CLI for reviewing every channel subscription on the authenticated YouTube account. It presents one complete channel at a time and includes:

- channel name, `@handle`, and description;
- subscriber, public-video, and total-view counts;
- how long ago the account subscribed;
- channel creation date and country, when supplied;
- latest public upload title, date, link, and relative age.

The default mode is read-only. Press Enter (or `k`) to keep a channel or `u` to stage it for unsubscribe; neither choice changes the YouTube account. Both kinds of decisions are saved between runs, and the next run resumes with the first undecided channel. Applying the unsubscribe list is unavailable unless the program is launched with `--allow-unsubscribe`, and the complete batch requires a count-specific confirmation such as `UNSUBSCRIBE 4`.

Decision files are stored under `~/.local/state/youtube-subscription-review/` as `kept-subscriptions.json` and `marked-for-unsubscribe.json`.

The interactive display uses aligned, responsive columns and xterm-256 colors for channel names, counts, activity, navigation, and marked rows. Set `NO_COLOR=1` or pass `--no-color` for plain output.

Before showing the first channel, every run writes a timestamped JSON backup of the complete subscription inventory. The program does not enter the review loop—and therefore cannot unsubscribe—unless that backup succeeds. Backups are retained indefinitely by the program at:

```text
~/.local/state/youtube-subscription-review/backups/
```

Every successful unsubscribe also appends an entry to `unsubscribe-receipts.jsonl` in that directory, recording the channel metadata and the exact pre-change backup containing it. Use `--backup-dir` to choose another durable location. The backup's channel IDs are sufficient to identify channels for manual recovery or a future resubscribe tool; the script never automatically deletes old backups.

## Credentials: OAuth, not an API key

An API key alone cannot read the current user's private subscription feed. This program uses an **OAuth 2.0 Desktop app client**. It does not need a separate API key.

1. Create or select a project in [Google Cloud Console](https://console.cloud.google.com/).
2. Enable **YouTube Data API v3** for the project.
3. Configure the OAuth consent screen. If the app is in testing, add the Google account that owns the YouTube subscriptions as a test user.
4. Create an OAuth client ID with application type **Desktop app**.
5. Download its JSON file and save it as `client_secret.json` in this directory, or pass its location with `--client-secrets`.

Do not commit the client JSON or token cache. Matching files in this directory are gitignored. The default token cache is stored with owner-only permissions at:

```text
~/.config/youtube-subscription-review/token.json
```

The first run opens Google's consent page in a browser. Be careful to authorize the Google account connected to the intended YouTube profile/channel.

## Install and run

```sh
cd apps/youtube-subscription-review
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python youtube_subscription_review.py
```

The interactive commands are:

```text
Enter/k keep   u unsubscribe later   ←/p previous   → next   x undecide
marked show unsubscribe list   apply staged removals   q quit
```

The `u` decision is always non-destructive. `marked` shows the complete persistent unsubscribe list. In read-only mode, `apply` explains how to restart in management mode without changing anything.

To apply the saved queue with a separate confirmation:

```sh
.venv/bin/python youtube_subscription_review.py --allow-unsubscribe
```

`apply` processes at most 150 marked channels per invocation by default, leaving the remainder staged. This keeps a fresh run below the standard daily quota under ordinary use. If YouTube reports quota exhaustion anyway, the script exits cleanly: completed removals remain receipted and only unprocessed channels remain marked. YouTube resets API quota at midnight Pacific Time. Override the batch cap with `--max-unsubscribes N` when the project has a different quota allocation.

Google does not provide a narrowly scoped “delete subscriptions” OAuth permission. Management mode therefore requests the broader `youtube.force-ssl` scope required by `subscriptions.delete`. The read-only default requests only `youtube.readonly`.

Useful options:

```sh
# Skip per-channel latest-upload requests
.venv/bin/python youtube_subscription_review.py --no-last-upload

# Use credentials stored elsewhere
.venv/bin/python youtube_subscription_review.py \
  --client-secrets /secure/path/youtube-desktop-client.json

# Store backups on another volume
.venv/bin/python youtube_subscription_review.py \
  --backup-dir /secure/path/youtube-subscription-backups

# Disable xterm-256 styling
.venv/bin/python youtube_subscription_review.py --no-color

# Use a smaller unsubscribe batch
.venv/bin/python youtube_subscription_review.py \
  --allow-unsubscribe --max-unsubscribes 75

# Archive all local decisions and review the current live list again from the start
.venv/bin/python youtube_subscription_review.py --reset-decisions
```

`--reset-decisions` does not call YouTube and does not resubscribe removed channels. It archives both decision files under `~/.local/state/youtube-subscription-review/decision-history/`, clears the active keep/unsubscribe sets, and lets the next normal run begin with the first channel in the current subscription list.

Each subscriptions, channels, or playlist-items list request normally costs one YouTube Data API quota unit. Latest uploads are fetched lazily only for the channel being displayed. A confirmed unsubscribe costs 50 units. Counts and upload availability are exactly what the API exposes: subscriber counts may be rounded or hidden, video counts cover public videos, and a latest upload may be unavailable for an empty or inaccessible uploads playlist.

## Tests

The tests use a fake API service and make no Google or YouTube requests:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```
