#!/usr/bin/env python3
"""Share dual-read tests (postcard v4 + share-fix option 2).

Acceptance 7: new-shape uses line+date; old-shape uses dailyTask+lastUpdated;
prefix unchanged. Origin injected in JS eval (advisory).

Run from repo root:
  python -m unittest test.test_share
"""
from __future__ import annotations

import datetime
import json
import re
import subprocess
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
INDEX = REPO / "public" / "index.html"

BUTTON_LABEL = "✉ pass it on ↗"
SHARE_PREFIX = "Today's kindness from Get Happy: "
TEST_ORIGIN = "https://gethappyinfo.com"


def read_index() -> str:
    return INDEX.read_text(encoding="utf-8")


def extract_helpers(html: str) -> str:
    """Pull laTodayIso + datedShareUrl + shape helpers from the page script."""
    m = re.search(
        r"function laTodayIso\(\) \{.*?function datedShareUrl\(iso\) \{.*?\}",
        html,
        re.S,
    )
    if not m:
        raise AssertionError("laTodayIso / datedShareUrl helpers not found")
    shape = re.search(
        r"function isNewShape\(data\) \{.*?function isOldShape\(data\) \{.*?\}",
        html,
        re.S,
    )
    if not shape:
        raise AssertionError("isNewShape / isOldShape helpers not found")
    return m.group(0) + "\n" + shape.group(0)


def node_eval(helpers: str, expression: str, origin: str = TEST_ORIGIN) -> str:
    """Evaluate a JS expression with page helpers; origin injected (not ambient)."""
    script = f"""
const window = {{ location: {{ origin: {json.dumps(origin)} }} }};
{helpers}
const __out = ({expression});
process.stdout.write(String(__out));
"""
    proc = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout


def share_text_for(data: dict) -> str:
    """Mirror dual-read share line selection."""
    if (
        isinstance(data.get("line"), str) and data["line"].strip()
        and isinstance(data.get("paragraph"), str) and data["paragraph"].strip()
    ):
        return SHARE_PREFIX + data["line"]
    return SHARE_PREFIX + (data.get("dailyTask") or "")


def share_iso_for(data: dict) -> str:
    if (
        isinstance(data.get("line"), str) and data["line"].strip()
        and isinstance(data.get("paragraph"), str) and data["paragraph"].strip()
    ):
        return data.get("date") or ""
    return data.get("lastUpdated") or ""


class ShareFixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = read_index()
        cls.helpers = extract_helpers(cls.html)

    def test_button_label_unchanged(self):
        self.assertIn(f">{BUTTON_LABEL}</button>", self.html)
        self.assertIn('id="send"', self.html)

    def test_on_voice_share_prefix(self):
        self.assertIn(f'var text = "{SHARE_PREFIX}" + line;', self.html)
        self.assertIn("navigator.share({ title: 'Get Happy Info', text: text, url: shareUrl })", self.html)
        self.assertIn("var shareMessage = text + ' ' + shareUrl;", self.html)
        self.assertNotIn("Take a look at the Happiness Postcard site I found.", self.html)

    def test_home_path_uses_la_today(self):
        la_today = datetime.datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()
        js_today = node_eval(self.helpers, "laTodayIso()")
        self.assertEqual(js_today, la_today)
        url = node_eval(self.helpers, "datedShareUrl((null) || laTodayIso())")
        self.assertEqual(url, f"{TEST_ORIGIN}/{la_today}")
        self.assertNotEqual(url, f"{TEST_ORIGIN}/")

    def test_origin_injection_is_honored(self):
        """Advisory: tests inject origin rather than reading ambient location."""
        other = "https://example.test"
        url = node_eval(self.helpers, "datedShareUrl('2026-09-04')", origin=other)
        self.assertEqual(url, f"{other}/2026-09-04")

    def test_archive_path_uses_archive_date(self):
        archive = "2026-05-27"
        url = node_eval(self.helpers, f"datedShareUrl({json.dumps(archive)} || laTodayIso())")
        self.assertEqual(url, f"{TEST_ORIGIN}/{archive}")

    def test_archive_miss_sets_dated_url(self):
        self.assertRegex(
            self.html,
            r"function renderArchiveMiss\(reqDate\) \{[\s\S]*?shareUrl = datedShareUrl\(reqDate\);",
        )

    def test_new_shape_share_uses_line_and_date(self):
        """Acceptance 7: new-shape → line + date."""
        data = {
            "date": "2026-09-04",
            "line": "Thank a bus driver, cashier, or janitor by name.",
            "paragraph": "She said his name at the register. That was enough.",
        }
        self.assertEqual(
            node_eval(self.helpers, f"isNewShape({json.dumps(data)})"),
            "true",
        )
        self.assertEqual(share_text_for(data), SHARE_PREFIX + data["line"])
        self.assertEqual(share_iso_for(data), "2026-09-04")
        url = node_eval(self.helpers, f"datedShareUrl({json.dumps(data['date'])})")
        self.assertEqual(url, f"{TEST_ORIGIN}/2026-09-04")

    def test_old_shape_share_uses_dailytask_and_lastupdated(self):
        """Acceptance 7: old-shape → dailyTask + lastUpdated."""
        data = {
            "lastUpdated": "2026-09-03",
            "dailyTask": "Smile at a stranger.",
            "topNews": [{"text": "x", "url": "https://example.com"}],
        }
        self.assertEqual(
            node_eval(self.helpers, f"isOldShape({json.dumps(data)})"),
            "true",
        )
        self.assertEqual(share_text_for(data), SHARE_PREFIX + data["dailyTask"])
        self.assertEqual(share_iso_for(data), "2026-09-03")

    def test_shape_gate_rejects_date_only(self):
        """Acceptance 12: date-only is neither new nor old enough to paint new chrome."""
        data = {"date": "2026-09-04"}
        self.assertEqual(node_eval(self.helpers, f"isNewShape({json.dumps(data)})"), "false")
        # lastUpdated/dailyTask absent → not old either
        self.assertEqual(node_eval(self.helpers, f"isOldShape({json.dumps(data)})"), "false")


if __name__ == "__main__":
    unittest.main(verbosity=2)
