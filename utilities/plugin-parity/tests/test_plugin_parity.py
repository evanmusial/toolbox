"""Exercise macOS bundle inventory and cross-machine parity reporting."""

from io import StringIO
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest


TOOL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL_DIR))
import plugin_parity as parity  # noqa: E402


def write_bundle(root, name, suffix, info):
    """Create a synthetic macOS plug-in bundle with an Info.plist."""
    bundle = root / f"{name}{suffix}"
    (bundle / "Contents").mkdir(parents=True)
    with (bundle / "Contents/Info.plist").open("wb") as stream:
        plistlib.dump(info, stream)
    return bundle


def root_spec(plugin_format, path, scope="test"):
    """Build a scanner root record for an isolated fixture folder."""
    return {"format": plugin_format, "path": path, "scope": scope, "enabled": True}


def record(name, plugin_format, manufacturer=None, version=None):
    """Build a minimal normalized record for comparison fixtures."""
    return parity.coerce_record({
        "name": name, "format": plugin_format,
        "manufacturer": manufacturer, "version": version,
    }, "fixture")


def snapshot(computer, records):
    """Build a minimal valid saved snapshot."""
    return {
        "schema_version": 1,
        "computer": computer,
        "generated_at": "2026-11-19T00:00:00+00:00",
        "master_plugins": records,
    }


class PluginParityTests(unittest.TestCase):
    """Protect discovery, source merging, comparison, and compact CLI output."""

    def setUp(self):
        """Create an isolated plug-in folder tree."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.au_root = self.root / "Components"
        self.vst_root = self.root / "VST"
        self.vst3_root = self.root / "VST3"
        self.aax_root = self.root / "AAX"
        for path in (self.au_root, self.vst_root, self.vst3_root, self.aax_root):
            path.mkdir()
        write_bundle(self.au_root, "Test Synth", ".component", {
            "CFBundleIdentifier": "com.example.testsynth",
            "CFBundleShortVersionString": "3.4.5",
            "CFBundleVersion": "345",
            "AudioComponents": [{
                "name": "Example Audio: Test Synth",
                "type": "aumu", "subtype": "Tsyn", "manufacturer": "Exmp",
            }],
        })
        write_bundle(self.vst_root, "Legacy Delay", ".vst", {
            "CFBundleIdentifier": "com.example.legacydelay",
            "CFBundleName": "Legacy Delay",
            "CFBundleShortVersionString": "1.2.0",
            "Manufacturer": "Example Audio",
        })
        vst3 = write_bundle(self.vst3_root, "Example Shell", ".vst3", {
            "CFBundleIdentifier": "com.example.shell.vst3",
            "CFBundleName": "Example Shell",
            "CFBundleShortVersionString": "2.0.0",
        })
        resources = vst3 / "Contents/Resources"
        resources.mkdir()
        (resources / "moduleinfo.json").write_text(json.dumps({
            "Factory Info": {"Vendor": "Example Audio"},
            "Classes": [
                {"Name": "Stereo Tool", "CID": "0123456789ABCDEF"},
                {"Name": "Mono Tool", "CID": "FEDCBA9876543210"},
            ],
        }), encoding="utf-8")
        write_bundle(self.aax_root, "Example AAX Shell", ".aaxplugin", {
            "CFBundleIdentifier": "com.example.shell.aax",
            "CFBundleName": "Example AAX Shell",
            "CFBundleShortVersionString": "2.0.0",
        })
        self.roots = [
            root_spec("AU", self.au_root), root_spec("VST", self.vst_root),
            root_spec("VST3", self.vst3_root), root_spec("AAX", self.aax_root),
        ]

    def test_scans_all_formats_and_expands_au_and_vst3_identities(self):
        """Enumerate component and class records without loading binaries."""
        records, searched, warnings = parity.scan_installed(self.roots, include_auv3=False)
        self.assertEqual(len(records), 5)
        self.assertEqual({item["format"] for item in records}, {"AU", "VST", "VST3", "AAX"})
        self.assertTrue(all(item["exists"] for item in searched))
        self.assertEqual(warnings, [])
        au = next(item for item in records if item["format"] == "AU")
        self.assertEqual((au["manufacturer"], au["name"]), ("Example Audio", "Test Synth"))
        classes = [item for item in records if item["format"] == "VST3"]
        self.assertEqual([item["name"] for item in classes], ["Mono Tool", "Stereo Tool"])
        self.assertTrue(all(item["enumeration_level"] == "class" for item in classes))

    def test_finds_only_audio_unit_extensions_inside_apps(self):
        """Recognize AUv3 extensions while excluding unrelated appex bundles."""
        app_root = self.root / "Applications"
        plugin_folder = app_root / "Audio/Instrument.app/Contents/PlugIns"
        plugin_folder.mkdir(parents=True)
        write_bundle(plugin_folder, "App Synth", ".appex", {
            "CFBundleIdentifier": "com.example.appsynth.extension",
            "NSExtension": {"NSExtensionPointIdentifier": "com.apple.AudioUnit"},
            "AudioComponents": [{
                "name": "Example Audio: App Synth",
                "type": "aumu", "subtype": "Apps", "manufacturer": "Exmp",
            }],
        })
        write_bundle(plugin_folder, "Share", ".appex", {
            "CFBundleIdentifier": "com.example.share",
            "NSExtension": {"NSExtensionPointIdentifier": "com.apple.share-services"},
        })
        records, _searched, _warnings = parity.scan_installed(
            [], application_roots=[app_root], include_auv3=True,
        )
        self.assertEqual([(item["name"], item["format"]) for item in records],
                         [("App Synth", "AUv3")])

    def test_parses_the_existing_grouped_live_text_report(self):
        """Import the grouped text produced by the prior Live extractor."""
        text = """\
Example Audio (2 named groups)
==============================

  Stereo Tool

    Stereo Tool [VST3]
      Version: 2.0.0
      Path:    /Library/Audio/Plug-Ins/VST3/Example Shell.vst3

  Remembered Tool

    Remembered Tool [VST3]
      Version: unknown
      Path:    unknown
"""
        path = self.root / "live.txt"
        path.write_text(text, encoding="utf-8")
        records = parity.load_inventory(path, "live")
        self.assertEqual([item["name"] for item in records], ["Stereo Tool", "Remembered Tool"])
        self.assertTrue(all(item["manufacturer"] == "Example Audio" for item in records))

    def test_terminal_diff_groups_missing_formats_and_hides_noise(self):
        """Group gaps by manufacturer while collapsing matches and extras by default."""
        baseline = snapshot("drift", [
            record("Pro-Q 4", "AU", "FabFilter", "4.10"),
            record("Pro-Q 4", "VST3", "FabFilter", "4.10"),
            record("EchoBoy", "AU", "Soundtoys", "5.4"),
            record("Legacy Tool", "VST", None, "1.0"),
            record("Matched Tool", "AU", "Example", "2.0"),
        ])
        candidate = snapshot("ramanujan", [
            record("Matched Tool", "AU", "Example", "2.1"),
            record("New Tool", "AAX", "Example", "1.0"),
        ])
        report = parity.compare_snapshots(baseline, candidate)
        output = StringIO()
        parity.write_terminal_report(report, output, color_mode="never")
        text = output.getvalue()
        self.assertIn("Plug-in parity  drift → ramanujan", text)
        self.assertIn("FabFilter", text)
        self.assertIn("Pro-Q 4  [AU, VST3]", text)
        self.assertIn("Soundtoys", text)
        self.assertIn("Unknown manufacturer", text)
        self.assertNotIn("New Tool", text)
        self.assertNotIn("Matched Tool  [AU]", text)
        self.assertIn("matching records hidden", text)
        self.assertIn("candidate-only records hidden", text)
        self.assertNotIn("\033[", text)

    def test_terminal_color_and_optional_sections_are_explicit(self):
        """Use ANSI color only when requested and reveal hidden sections on demand."""
        baseline = snapshot("drift", [record("Shared", "AU", "Maker", "1.0")])
        candidate = snapshot("ramanujan", [
            record("Shared", "AU", "Maker", "2.0"),
            record("Extra", "VST3", "Maker", "1.0"),
        ])
        report = parity.compare_snapshots(baseline, candidate)
        output = StringIO()
        parity.write_terminal_report(
            report, output, color_mode="always", show_matches=True,
            show_additional=True, show_versions=True,
        )
        text = output.getvalue()
        self.assertIn("\033[", text)
        self.assertIn("Shared", text)
        self.assertIn("Extra", text)
        self.assertIn("1.0", text)
        self.assertIn("2.0", text)

    def test_cli_scans_and_compares_saved_host_snapshots(self):
        """Support the drift-once and ramanujan-repeat workflow end to end."""
        script = TOOL_DIR / "plugin_parity.py"
        drift_path = self.root / "drift.json"
        ramanujan_path = self.root / "ramanujan.json"
        common = [sys.executable, str(script), "scan", "--no-default-roots", "--skip-auv3"]
        for item in self.roots:
            common.extend(("--root", f"{item['format']}={item['path']}"))
        for host, output in (("drift", drift_path), ("ramanujan", ramanujan_path)):
            result = subprocess.run(
                [*common, "--host", host, "--output", str(output)],
                text=True, capture_output=True, check=False,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        report_path = self.root / "parity.txt"
        result = subprocess.run(
            [sys.executable, str(script), "compare", "--baseline", str(drift_path),
             "--candidate", str(ramanujan_path), "--output", str(report_path),
             "--color", "never"],
            text=True, capture_output=True, check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("100.00%", result.stdout)
        self.assertIn("Gaps: 0", report_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
