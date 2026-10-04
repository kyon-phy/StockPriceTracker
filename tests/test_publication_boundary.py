"""Offline checks for the default-deny publication boundary, not scanner tests."""

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PublicationBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        shutil.copyfile(ROOT / ".gitignore", self.repo / ".gitignore")

    def ignored(self, path):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("synthetic test content\n")
        result = subprocess.run(
            ["git", "check-ignore", "--quiet", "--no-index", "--", path],
            cwd=self.repo,
        )
        self.assertIn(result.returncode, (0, 1))
        return result.returncode == 0

    def test_private_and_unreviewed_files_are_ignored(self):
        for path in (
            "prices.csv", "daily.parquet", "data/prices.json", "quotes.db",
            "original.zip", "source/original.py", "state.json", "state/dedup.json",
            "cache/response.json", "run.log", "logs/run.txt", ".env",
            "credentials.json", "private_key.pem", "chat.md", "private/notes.md",
            "docs/private.md", "tests/fixtures/real_prices.json",
            "tests/__pycache__/test.cpython-311.pyc", ".github/workflows/run.yml",
            ".agents/skills/anything/SKILL.md", "unknown.py",
            "stock_monitor/state.json", "stock_monitor/private.py",
            "state-next.json", "latest.json", "raw/NVDA.json",
        ):
            with self.subTest(path=path):
                self.assertTrue(self.ignored(path))

    def test_only_reviewed_files_are_allowed(self):
        for path in (
            "README.md", "docs/SCANNER_CONTRACT.md",
            "tests/test_publication_boundary.py", "tests/fixtures/signal_cases.json",
            "config.json", "verified_exchange_sessions_2024_2027.json",
            "stock_monitor/__main__.py", "stock_monitor/calendars.json",
            "tests/test_cycle.py", "tests/test_publication_contract.py",
        ):
            with self.subTest(path=path):
                self.assertFalse(self.ignored(path))

    def test_fixture_is_explicitly_synthetic_and_has_unique_cases(self):
        fixture = json.loads((ROOT / "tests/fixtures/signal_cases.json").read_text())
        self.assertTrue(fixture["synthetic_only"])
        names = [case["name"] for case in fixture["cases"]]
        self.assertEqual(len(names), len(set(names)))
        self.assertGreaterEqual(len(names), 12)


if __name__ == "__main__":
    unittest.main()
