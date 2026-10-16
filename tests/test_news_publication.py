"""Offline regression and acceptance tests; no market/broker endpoints."""
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
import io
from unittest.mock import patch

from app.config import Settings
import app.database as database
from tools.agents.lib.news_acceptance import canonical, evaluate
from tools.agents.lib import news_publication as publication

COMMIT = "a" * 40


def news(identity="news_test"):
    return {"run_at": "2026-10-16T09:00:00+09:00", "mode": "news_first_market_issue_scout",
            "real_trading": False, "contract": {"status": "ok"}, "summary": {"issue_count": 1},
            "issues": [{"issue_id": identity, "label": "반도체", "sources": [
                {"title": "공급 전망 수정", "url": "https://example.org/news/1"}]}]}


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        settings = Settings("test", "test", "test", "", str(self.root / "application.db"), 100000)
        self.addCleanup(patch.stopall)
        patch.object(database, "settings", settings).start()
        patch.object(publication, "code_revision", return_value=COMMIT).start()
        # Create only the publication integration's real application schema;
        # skip demo portfolio/price seeding in isolated regression fixtures.
        with patch.object(database, "seed_demo_data"):
            database.init_db()
        patch.object(publication, "init_db", return_value=None).start()
        self.target = str(self.root / "news_latest.json")

    def latest(self):
        with database.get_connection() as conn:
            row = conn.execute("SELECT payload_json FROM latest_artifacts WHERE artifact_key='news_latest'").fetchone()
        return json.loads(row[0]) if row else None

    def test_twenty_synthetic_acceptance_cases_and_pure_scoring(self):
        fixtures = json.loads((Path(__file__).parent / "fixtures/news_acceptance.json").read_text())
        before = (self.root / "application.db").read_bytes()
        counts = {"pass": 0, "block": 0}
        for fixture in fixtures:
            with self.subTest(fixture=fixture["case_id"]):
                original = canonical(fixture["packet"])
                first = evaluate(fixture["packet"], run_id=fixture["case_id"], commit=COMMIT)
                second = evaluate(fixture["packet"], run_id=fixture["case_id"], commit=COMMIT)
                self.assertEqual(first, second)
                self.assertEqual(original, canonical(fixture["packet"]))
                self.assertEqual(fixture["expected"], first["decision"])
                counts[first["decision"]] += 1
        self.assertEqual(counts, {"pass": 10, "block": 10})
        self.assertEqual(before, (self.root / "application.db").read_bytes())

    def test_blocked_candidate_preserves_last_good_and_keeps_evidence(self):
        good = publication.begin([self.target])
        publication.publish(good, {self.target: news()})
        before_file, before_db = Path(self.target).read_bytes(), self.latest()
        bad = news(); bad["issues"][0]["sources"] = []
        run = publication.begin([self.target])
        with self.assertRaises(publication.PublicationBlocked):
            publication.publish(run, {self.target: bad})
        self.assertEqual(before_db, self.latest())
        self.assertEqual(before_file, Path(self.target).read_bytes())
        with database.get_connection() as conn:
            row = conn.execute("SELECT * FROM news_publication_runs WHERE run_id=?", (run,)).fetchone()
        self.assertEqual(row["status"], "blocked")
        self.assertIn("NEWS-001", row["reports_json"])
        self.assertIn("sources", row["snapshots_json"])

    def test_cancel_fences_late_callbacks_without_further_writes(self):
        run = publication.begin([self.target])
        self.assertTrue(publication.cancel(run))
        before = (self.root / "application.db").read_bytes()
        for _ in range(5):
            with self.assertRaises(publication.PublicationBlocked):
                publication.publish(run, {self.target: news()})
        self.assertEqual(before, (self.root / "application.db").read_bytes())
        self.assertFalse(Path(self.target).exists())
        self.assertIsNone(self.latest())

    def test_duplicate_publication_and_stale_run_are_rejected(self):
        first = publication.begin([self.target])
        stale = publication.begin([self.target])
        publication.publish(first, {self.target: news()})
        before = Path(self.target).read_bytes()
        for run in (first, stale):
            with self.assertRaises(publication.PublicationBlocked):
                publication.publish(run, {self.target: news("late")})
        self.assertEqual(before, Path(self.target).read_bytes())

    def test_revision_change_blocks_and_legacy_source_change_blocks(self):
        run = publication.begin([self.target])
        with patch.object(publication, "code_revision", return_value="b" * 40):
            with self.assertRaises(publication.PublicationBlocked):
                publication.publish(run, {self.target: news()})
        self.assertIsNone(self.latest())
        run = publication.begin([self.target])
        Path(self.target).write_text("legacy changed input")
        with self.assertRaises(publication.PublicationBlocked):
            publication.publish(run, {self.target: news()})
        self.assertEqual(Path(self.target).read_text(), "legacy changed input")

    def test_multi_target_validation_is_all_or_nothing(self):
        other = str(self.root / "narrative_latest.json")
        run = publication.begin([self.target, other])
        broken = news(); broken["summary"]["issue_count"] = 2
        with self.assertRaises(publication.PublicationBlocked):
            publication.publish(run, {self.target: news(), other: broken})
        self.assertFalse(Path(self.target).exists())
        self.assertFalse(Path(other).exists())
        self.assertIsNone(self.latest())

    def test_reconcile_uses_current_publication_not_old_run(self):
        old = publication.begin([self.target]); publication.publish(old, {self.target: news("old")})
        current = publication.begin([self.target]); publication.publish(current, {self.target: news("new")})
        Path(self.target).write_text("interrupted mirror")
        publication.reconcile_mirrors(old)
        self.assertEqual(json.loads(Path(self.target).read_text()), self.latest())
        self.assertEqual(self.latest()["issues"][0]["issue_id"], "new")

    def test_io_failure_rolls_back_db_and_earlier_mirror(self):
        other = str(self.root / "second_latest.json")
        run = publication.begin([self.target, other])
        real_replace = publication._replace
        def fail_second(path, data):
            if path == other:
                raise OSError("disk error")
            real_replace(path, data)
        with patch.object(publication, "_replace", side_effect=fail_second):
            with self.assertRaises(OSError):
                publication.publish(run, {self.target: news(), other: news()})
        self.assertFalse(Path(self.target).exists())
        self.assertIsNone(self.latest())

    def test_agents_reach_gate_without_network(self):
        from tools.agents import market_news_issue_scout_agent as scout
        from tools.agents import market_issue_narrative_agent as narrative
        hit = {"query": "AI 상승", "title": "AI 반도체 상승", "domain": "example.org",
               "url": "https://example.org/ai", "published_at": "2026-10-16",
               "source_age_days": 0, "title_date": None}
        with patch.object(scout, "get_settings", return_value=database.settings), \
             patch.object(scout, "load_universe", return_value=[]), \
             patch.object(scout, "ddg", return_value=[hit]), \
             patch("sys.argv", ["scout", "--output", self.target, "--max-queries", "1"]), \
             redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            scout.main()
        self.assertEqual(self.latest()["acceptance"]["decision"], "pass")
        source = self.root / "price_clusters.json"
        source.write_text(canonical({"issues": [{"issue_id": "cluster1", "label": "반도체", "members": []}], "summary": {"issue_count": 1}}))
        output = str(self.root / "narrative_latest.json")
        with patch("sys.argv", ["narrative", "--input", str(source), "--output", output, "--no-web"]), \
             redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            narrative.main()
        self.assertEqual(json.loads(source.read_text())["acceptance"]["decision"], "pass")
        self.assertEqual(json.loads(Path(output).read_text())["issues"][0]["narrative_status"], "heuristic_only")


if __name__ == "__main__":
    unittest.main()
