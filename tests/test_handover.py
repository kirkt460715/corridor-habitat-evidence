"""现场交割：前后斑块对、替代路径、切换夜断电与接续观测。"""

from corridor.planning import RELEASE_BLOCKED, RELEASE_SURVEY_FIRST
from tests._engine import EngineTest


class HandoverTests(EngineTest):
    def test_package_covers_pre_post_and_retained_paths(self):
        package = self.handover.build_package("C")
        g01 = next(item for item in package.items if item.requirement_id == "G-01")
        # 改造前 H-01↔H-03 有两条候选路径
        self.assertIn(("S-03",), g01.pre_paths)
        self.assertIn(("S-01", "S-02"), g01.pre_paths)
        # 改造后（S-01 封闭）只剩 S-03
        self.assertEqual(g01.post_paths, [("S-03",)])
        # 施工期保留路径同样必须列出
        self.assertTrue(g01.retained_paths)
        self.assertIn(("S-03",), g01.retained_paths)

    def test_switch_night_without_effort_is_uncovered(self):
        package = self.handover.build_package("A")
        uncovered = [n for n in package.switch_nights if not n.covered]
        self.assertTrue(uncovered)
        # 6 月 11 日切换夜 S-01/S-03 只有设备台账、没有 effort 记录
        june11 = [n for n in uncovered if n.switch_date.isoformat() == "2025-06-11"]
        self.assertTrue(june11)
        self.assertTrue(any("无 effort" in n.detail or "无设备覆盖" in n.detail for n in june11))

    def test_same_relaxed_assumption_cannot_prove_two_routes(self):
        # 切换夜无覆盖 + 替代路径无完成证据时，必须进入接续观测而非放行
        for proposal_id in ("A", "B", "C"):
            package = self.handover.build_package(proposal_id)
            self.assertIn(package.overall, (RELEASE_BLOCKED, RELEASE_SURVEY_FIRST))
            self.assertTrue(package.follow_ups)
            for follow in package.follow_ups:
                self.assertIn(follow.required_observable.value, ("双向经过",))

    def test_blocked_proposal_lists_concrete_failed_reasons(self):
        package = self.handover.build_package("A")
        self.assertEqual(package.overall, RELEASE_BLOCKED)
        joined = "\n".join(package.failed_reasons)
        self.assertIn("容量", joined)
        self.assertIn("失去可走路径", joined)

    def test_follow_up_observation_targets_unproven_detour_segments(self):
        package = self.handover.build_package("B")
        target_segments = {f.segment_id for f in package.follow_ups}
        # B 方案把豹与野猪都引到 S-02，S-02 无完成证据，必须安排补测
        self.assertIn("S-02", target_segments)

    def test_lookup_work_arrangement_returns_actionable_fields(self):
        info = self.handover.lookup_work_arrangement("C")
        for key in (
            "releasable_steps",
            "retained_routes",
            "failed_reasons",
            "follow_up_observations",
            "survey_plan",
        ):
            self.assertIn(key, info)
        self.assertTrue(info["releasable_steps"])
        self.assertTrue(info["retained_routes"])
        self.assertTrue(info["follow_up_observations"])
        plan = info["survey_plan"]
        self.assertLessEqual(plan["device_days_used"], 3)
        self.assertTrue(plan["infeasible_slots"])

    def test_compare_periods_are_attached_to_items(self):
        package = self.handover.build_package("C")
        for item in package.items:
            self.assertEqual(item.compare_periods, ("Q-01", "Q-02"))
            self.assertTrue(item.comparison_notes)

    def test_unknown_proposal_raises(self):
        with self.assertRaises(KeyError):
            self.handover.build_package("Z")
