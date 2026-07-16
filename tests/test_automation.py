import json
import tempfile
import unittest
from pathlib import Path

import automation


class AutomationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.output = Path(self.temp.name) / "output"
        self.sample_input = automation.ROOT / "samples" / "inbox.jsonl"
        self.decisions = automation.ROOT / "samples" / "review-decisions.json"
        self.config = automation.ROOT / "config" / "automation.json"

    def tearDown(self):
        self.temp.cleanup()

    def test_prepare_deduplicates_and_stops_for_review(self):
        result = automation.prepare(self.sample_input, self.output, "prepare-only", self.config)
        run_dir = Path(result["run_dir"])
        self.assertEqual(result["received"], 6)
        self.assertEqual(result["unique"], 5)
        self.assertEqual(result["duplicates"], 1)
        self.assertEqual(result["status"], "awaiting_human_review")
        manifest = json.loads((run_dir / "run-manifest.json").read_text())
        self.assertEqual(manifest["input_path"], "samples/inbox.jsonl")
        self.assertNotIn(str(automation.ROOT), json.dumps(manifest))
        self.assertFalse((run_dir / "research").exists())
        self.assertFalse((run_dir / "briefs").exists())

    def test_full_run_preserves_categories_evidence_and_review(self):
        prepared = automation.prepare(self.sample_input, self.output, "full", self.config)
        result = automation.publish(Path(prepared["run_dir"]), self.decisions)
        run_dir = Path(prepared["run_dir"])
        self.assertEqual(result["approved"], 4)
        self.assertEqual(result["rejected"], 1)
        self.assertEqual(result["briefs"], 1)
        for category in automation.CATEGORIES:
            self.assertEqual(len(list((run_dir / "categorized" / category).glob("*.json"))), 1)
        project_record = json.loads(next((run_dir / "categorized" / "project").glob("*.json")).read_text())
        self.assertTrue(project_record["source_url"].startswith("https://"))
        self.assertTrue(project_record["evidence"])
        self.assertEqual(project_record["review_status"], "approved")
        applied = json.loads((run_dir / "review" / "applied-decisions.json").read_text())
        self.assertEqual(applied["source"], "samples/review-decisions.json")
        self.assertEqual(automation.verify(run_dir, write_report=False)["status"], "pass")

    def test_config_rejects_enabled_schedule(self):
        config = json.loads(self.config.read_text())
        config["schedule"]["enabled"] = True
        bad_config = Path(self.temp.name) / "bad-config.json"
        bad_config.write_text(json.dumps(config))
        with self.assertRaises(automation.PipelineError):
            automation.load_config(bad_config)


if __name__ == "__main__":
    unittest.main()
