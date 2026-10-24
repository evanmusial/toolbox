from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sqlite3
import sys
from pathlib import Path

from . import __version__
from .config import ConfigError, load_configuration, summary
from .service import fetch_preview, run_daemon
from .state import InstanceLock, LockError, State, readonly_report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Configuration-driven radio metadata daemon")
    parser.add_argument("--config", default="/etc/radio-now-playing/radio-now-playing.conf")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check-config", help="Validate all configuration without network access or posting")
    preview = commands.add_parser("fetch", help="Fetch and format only; never post or modify state")
    preview.add_argument("--station")
    commands.add_parser("daemon")
    commands.add_parser("status", help="Read the delivery state without modifying it")
    migration = commands.add_parser("import-bpm", help="Import an old BPM identity without posting")
    migration.add_argument("--from-db", required=True)
    legacy = commands.add_parser("import-legacy", help="Import a preserved text identity without posting")
    legacy.add_argument("--station", required=True)
    legacy.add_argument("--from-file", required=True)
    args = parser.parse_args(argv)
    try:
        config = load_configuration(args.config)
        logging.basicConfig(level=config.log_level, format="%(message)s", stream=sys.stdout)
        if args.command == "check-config":
            print(json.dumps(summary(config), indent=2))
        elif args.command == "fetch":
            results = asyncio.run(fetch_preview(config, args.station))
            print(json.dumps(results, indent=2, ensure_ascii=False))
            return 0 if all(result["ok"] for result in results) else 1
        elif args.command == "status":
            print(json.dumps(readonly_report(config.state_db), indent=2, ensure_ascii=False))
        elif args.command in ("import-bpm", "import-legacy"):
            with InstanceLock(config.state_db):
                state = State(config.state_db)
                try:
                    if args.command == "import-bpm":
                        target = next((s for s in config.enabled if s.id == "bpm"), None)
                        if target is None or target.identity != "{play_id}":
                            raise ConfigError("BPM import requires an enabled bpm configuration with play_id identity")
                        old = sqlite3.connect(Path(args.from_db).resolve().as_uri() + "?mode=ro", uri=True)
                        old.row_factory = sqlite3.Row
                        old.execute("PRAGMA query_only=ON")
                        try:
                            row = old.execute("SELECT * FROM station_state WHERE source='xmplaylist' AND station='bpm'").fetchone()
                        finally:
                            old.close()
                        if row is None or not row["last_play_id"]:
                            raise ConfigError("Old database has no BPM play identity")
                        imported = state.import_identity("bpm", row["last_play_id"], "Imported BPM state", row["last_checked_at"], row["last_posted_at"])
                        print(json.dumps({"station": "bpm", "imported": imported}))
                    else:
                        target = next((s for s in config.enabled if s.id == args.station), None)
                        if target is None or target.identity != "{display_text}":
                            raise ConfigError("Legacy text import requires display_text identity")
                        text = " ".join(Path(args.from_file).read_text().split()).strip()
                        if not text or text.lower().startswith(("error:", "error fetching", "current song information not found")):
                            raise ConfigError("Legacy state is empty or an error; use first-observation baseline")
                        imported = state.import_identity(target.id, text, text)
                        print(json.dumps({"station": target.id, "imported": imported}))
                finally:
                    state.close()
        else:
            asyncio.run(run_daemon(config))
        return 0
    except LockError:
        print(json.dumps({"error": "daemon_already_running"}), file=sys.stderr)
        return 3
    except ConfigError as exc:
        print(json.dumps({"error": "configuration", "reason": str(exc)}), file=sys.stderr)
        return 2
    except Exception as exc:
        # Network exceptions can embed credential-bearing URLs; never stringify them.
        print(json.dumps({"error": "operation_failed", "type": type(exc).__name__}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
