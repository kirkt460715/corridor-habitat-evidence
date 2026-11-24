"""数据加载与命令行入口测试。"""

import io
import json
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path

from corridor.cli import build_parser, main
from corridor.loading import load_dataset
from corridor.model import Observable, Sighting
from tests._engine import DATA, EngineTest


class LoadingTests(EngineTest):
    def test_month_validity_expanded_to_bounds(self):
        s02 = next(link for link in self.dataset.links if link.segment_id == "S-02")
        self.assertEqual(s02.valid_from, date(2025, 4, 1))
        self.assertEqual(s02.valid_to, date(2025, 6, 30))

    def test_controlled_vocabularies_parsed(self):
        detection = next(d for d in self.dataset.detections if d.event_id == "E-01")
        self.assertIs(detection.sighting, Sighting.ENTRY)
        no_cover = next(e for e in self.dataset.effort if e.effort_id == "EF-3")
        self.assertIs(no_cover.observable, Observable.NONE)
        self.assertFalse(no_cover.has_opportunity)

    def test_pipe_fields_split(self):
        stage = next(s for s in self.dataset.stages if s.proposal_id == "A" and s.stage == 1)
        self.assertEqual(stage.detour_segments, ("S-01", "S-03"))
        limit = self.dataset.work_limits["2025-06"]
        self.assertEqual(limit.handover_compare_period, ("Q-01", "Q-02"))

    def test_loading_unknown_directory_error_is_native(self):
        with self.assertRaises(FileNotFoundError):
            load_dataset(Path(DATA) / "does-not-exist")


class CliTests(EngineTest):
    def _run(self, *argv):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main(["--data", str(DATA), *argv])
        self.assertEqual(code, 0)
        return buffer.getvalue()

    def test_evidence_json_is_valid_and_contains_ranges(self):
        payload = json.loads(self._run("--json", "evidence"))
        pairs = {(tuple(item["斑块对"]), item["物种"]): item for item in payload["斑块对可证范围"]}
        leopard = pairs[(("H-01", "H-03"), "豹")]
        self.assertEqual(leopard["可证下限"], 0)
        self.assertEqual(leopard["相容上界"], 1)

    def test_proposals_json_includes_survey_plan(self):
        payload = json.loads(self._run("--json", "proposals"))
        self.assertTrue(payload["方案审查"])
        self.assertTrue(payload["有限设备布置"])

    def test_handover_and_work_commands(self):
        handover = json.loads(self._run("--json", "handover", "--proposal", "B"))
        self.assertIn("接续观测安排", handover)
        work = json.loads(self._run("--json", "work", "--proposal", "C"))
        self.assertIn("releasable_steps", work)

    def test_text_output_is_chinese_report(self):
        text = self._run("evidence")
        self.assertIn("事件分簇与路线解释", text)

    def test_parser_requires_subcommand(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args([])


if __name__ == "__main__":
    unittest.main()
