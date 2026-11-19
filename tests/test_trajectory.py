import json
from pathlib import Path
import sys
import unittest
import tempfile
import os
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "adapters"))
import opencode_adapter

class TrajectoryTests(unittest.TestCase):
    def test_events_preserve_text_and_collect_tools(self):
        events = [{"type": "text", "part": {"text": "before"}},
                  {"type": "tool_use", "timestamp": 1795136400000, "part": {"tool": "read", "state": {"input": {"filePath": "a"}}}},
                  {"type": "text", "part": {"text": "final"}}]
        rows = []
        parsed = opencode_adapter.parse_events("\n".join(map(json.dumps, events)), rows)
        self.assertEqual(parsed[:2], ("final", 1))
        self.assertEqual(rows[0]["tool"], "read")
        self.assertEqual(json.loads(rows[0]["input"]), {"filePath": "a"})
        self.assertIn("T", rows[0]["ts"])
        self.assertNotIn("edit", rows[0])

    def test_budget_rejections_across_retries(self):
        event = {"type": "tool_use", "timestamp": 1795136400000, "part": {"tool": "read", "state": {"input": "文" * 2000}}}
        rows = opencode_adapter.Trajectory()
        stream = (json.dumps(event) + "\n") * 1000
        opencode_adapter.parse_events(stream, rows)
        opencode_adapter.parse_events(stream, rows)
        self.assertTrue(rows.truncated)
        self.assertLessEqual(len(json.dumps(rows).encode()), 1024 * 1024)
        self.assertTrue(all(row["rejected"] for row in rows))

    def test_large_success_and_failed_attempt_status_without_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = root / "task"
            task.write_text("test")
            for reason in (None, "timeout"):
                output = root / str(reason)
                def attempt(binary, model, task, output, env, timeout, usages, rows):
                    for _ in range(2000):
                        rows.append({"ts": 0, "tool": "read", "input": "文" * 2000, "rejected": True})
                    return "final", 2000, reason, False
                with mock.patch.dict(os.environ, {"OPENCODE_BIN": sys.executable}), mock.patch.object(opencode_adapter, "attempt", side_effect=attempt):
                    reply = opencode_adapter.generate({"output": str(output), "task_file": str(task), "model": "test-free", "timeout": 10})
                self.assertEqual(reply["state"], "failed" if reason else "staged_for_review")
                self.assertTrue(reply["metadata"]["trajectory_truncated"])
                self.assertLess(len(json.dumps(reply).encode()), 4 * 1024 * 1024)
                status = json.loads((output / "status.json").read_text())
                self.assertNotIn("trajectory", status)
                self.assertNotIn("metadata", status)
                self.assertEqual(Path(status["transcript_path"]).stat().st_mode & 0o777, 0o600)
