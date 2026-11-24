"""改造方案审查：连通承诺、共用容量、资源、补查与设备布置。"""

from corridor.planning import (
    RELEASE_BLOCKED,
    RELEASE_READY,
    RELEASE_SURVEY_FIRST,
)
from tests._engine import EngineTest


class StageReviewTests(EngineTest):
    def _review(self, proposal_id, stage_no):
        review = self.planner.review_proposal(proposal_id)
        return review, next(s for s in review.stages if s.stage == stage_no)

    def test_proposal_a_stage1_fails_shared_capacity_not_double_counted(self):
        _, stage = self._review("A", 1)
        cap = next(c for c in stage.capacity_checks if c.shared_passage == "S-03")
        # G-01 与 G-02 同夜都要经 S-03：3+3=6 > 5，共享上限不可各算一份
        self.assertEqual(cap.combined_demand, 6)
        self.assertEqual(cap.capacity_per_night, 5)
        self.assertFalse(cap.ok)
        self.assertEqual(stage.decision, RELEASE_BLOCKED)
        self.assertTrue(any("容量" in reason for reason in stage.blocking_reasons))

    def test_proposal_a_stage2_detour_mismatch_is_blocking(self):
        _, stage = self._review("A", 2)
        check = next(c for c in stage.commitment_checks if c.requirement_id == "G-02")
        # 登记绕道 S-03，但 H-02↔H-03 经 S-03 在图上走不通
        self.assertFalse(check.detour_matches_graph)
        self.assertEqual(stage.decision, RELEASE_BLOCKED)

    def test_proposal_b_keeps_connectivity_but_requires_survey(self):
        review, stage = self._review("B", 1)
        self.assertEqual(stage.decision, RELEASE_SURVEY_FIRST)
        by_req = {c.requirement_id: c for c in stage.commitment_checks}
        self.assertTrue(by_req["G-01"].feasible_paths)
        self.assertTrue(by_req["G-02"].feasible_paths)
        # 绕行依赖 S-02，而野猪/豹在 S-02 都没有完成穿越证据
        self.assertIn("S-02", by_req["G-02"].unproven_segments)
        self.assertTrue(stage.unresolved_facts)
        self.assertEqual(review.overall_decision, RELEASE_SURVEY_FIRST)

    def test_proposal_c_capacity_ok_but_survey_first(self):
        review, stage = self._review("C", 1)
        cap = next(c for c in stage.capacity_checks if c.shared_passage == "S-03")
        # 只有 G-01 占用 S-03，G-02 改走 S-02：需求 3 <= 5
        self.assertEqual(cap.combined_demand, 3)
        self.assertTrue(cap.ok)
        self.assertEqual(stage.decision, RELEASE_SURVEY_FIRST)
        self.assertEqual(review.overall_decision, RELEASE_SURVEY_FIRST)

    def test_commitment_paths_are_actual_graph_paths(self):
        _, stage = self._review("C", 1)
        for check in stage.commitment_checks:
            for path in check.feasible_paths:
                self.assertNotIn("S-01", path)  # 封闭区段不出现在可走路径中

    def test_overlapping_maintenance_is_reported_not_auto_closed(self):
        _, stage = self._review("B", 1)
        self.assertTrue(any("W-02" in note for note in stage.work_overlaps))


class ResourceTests(EngineTest):
    def test_june_resources_within_limits(self):
        review = self.planner.review_proposal("A")
        self.assertTrue(review.resources.within_limits)
        self.assertEqual(review.resources.crew_days_used, 10)
        self.assertEqual(review.resources.budget_used, 128000)
        self.assertLessEqual(review.resources.crew_days_used, 12)
        self.assertLessEqual(review.resources.budget_used, 135000)


class SurveySlotTests(EngineTest):
    def test_expired_device_slot_is_hard_infeasible(self):
        slot = next(s for s in self.dataset.survey_slots if s.slot_id == "SL-4")
        feasibility = self.planner.assess_slot(slot)
        self.assertFalse(feasibility.feasible)
        self.assertTrue(any("台账有效期" in reason for reason in feasibility.reasons))

    def test_relocation_is_warning_not_hard_block(self):
        slot = next(s for s in self.dataset.survey_slots if s.slot_id == "SL-1")
        feasibility = self.planner.assess_slot(slot)
        # 窗口本身可布设：迁移安装与同设备互斥都只是告警
        self.assertTrue(feasibility.feasible)
        self.assertTrue(any("迁移安装" in w for w in feasibility.warnings))
        self.assertTrue(any("互斥" in w for w in feasibility.warnings)
        )

    def test_survey_plan_respects_device_day_budget_and_prioritizes_questions(self):
        plan = self.planner.plan_surveys("2025-06")
        self.assertLessEqual(plan.device_days_used, plan.device_day_budget)
        chosen_ids = {chosen.slot.slot_id for chosen in plan.selected_slots}
        # SL-3 是唯一双向经过窗，直接回答 S-03 是否使用，应优先入选
        self.assertIn("SL-3", chosen_ids)
        # SL-4 设备已过期，永远不入选
        self.assertNotIn("SL-4", chosen_ids)
        # SL-1 入口窗只能部分回答 S-02：问题保留并注明线索性质
        self.assertIn("SL-1", chosen_ids)
        self.assertIn("S-02", plan.partial_questions)
        self.assertTrue(
            any("S-02" in question for question in plan.unresolved_questions_remaining)
        )
        self.assertTrue(
            all("不能证明完成穿越" in q for q in plan.unresolved_questions_remaining if "S-02" in q)
        )

    def test_no_solution_claims_both_routes_improved_without_coverage(self):
        # 所有方案在当前证据下都不能直接“可放行”
        for review in self.planner.review_all():
            self.assertIn(
                review.overall_decision,
                (RELEASE_BLOCKED, RELEASE_SURVEY_FIRST),
            )
            self.assertNotEqual(review.overall_decision, RELEASE_READY)
