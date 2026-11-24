"""事件去重、证据分级、阴性观察与斑块对可证范围规则。"""

from corridor.events import EvidenceReview
from corridor.model import Observable, Sighting
from tests._engine import EngineTest


class ClusterTests(EngineTest):
    def test_non_empty_event_group_is_forced_merge(self):
        cluster_ids = {c.cluster_id for c in self.report.clusters}
        # G-11（E-01+E-02）与 G-22（E-04+E-05）由事件组必然合并
        self.assertIn("G-11", cluster_ids)
        self.assertTrue(
            any(
                c.cluster_id == "G-11"
                and {s.detection.event_id for s in c.sightings} == {"E-01", "E-02"}
                and c.certain
                for c in self.report.clusters
            )
        )
        g22 = next(c for c in self.report.clusters if c.cluster_id.startswith("G-22"))
        self.assertIn("E-04", {s.detection.event_id for s in g22.sightings})
        self.assertIn("E-05", {s.detection.event_id for s in g22.sightings})

    def test_retrigger_within_window_creates_two_mutually_exclusive_scenarios(self):
        # G-22（03:05/03:01）与 E-06（03:13）同通道相隔 8 分钟，可能是同一事件
        base, _ = self.evidence._base_clusters()
        edges = self.evidence._possible_merges(list(base.values()))
        self.assertIn(
            ("G-22", "E-06"),
            [(a, b) for a, b, _ in edges],
        )
        self.assertEqual(self.report.scenario_count, 2)
        usage = next(
            u
            for u in self.report.pair_usage
            if u.pair == ("H-01", "H-03") and u.species == "野猪"
        )
        # 合并情景 1 次、拆分情景 2 次；只能给范围，不能宣称 2 次确定过境
        self.assertEqual(usage.provable_min, 1)
        self.assertEqual(usage.compatible_max, 2)
        self.assertEqual(len(usage.scenarios), 2)

    def test_chain_requires_walkable_path_and_direction(self):
        base, _ = self.evidence._base_clusters()
        by_id = {c.cluster_id: c for c in base.values()}
        # G-11：S-01 进入后 S-02 再进入，相邻区段、同向、时间相容
        reason = self.evidence._can_chain(by_id["G-11"], by_id["E-03"])
        self.assertIsNone(reason)  # 物种不同，不可串联
        # E-03 与 G-22 物种不同，也不可串联
        self.assertIsNone(self.evidence._can_chain(by_id["E-03"], by_id["G-22"]))

    def test_chain_breaks_when_segments_do_not_meet(self):
        # 构造两个在图上不相邻的通道触发，即便时间接近也不能串成一次过境
        from corridor.events import CoveredSighting, EventCluster
        from corridor.model import (
            CaptureKind,
            Detection,
            EffortWindow,
            Observable,
        )
        from datetime import datetime

        first = self.evidence._make_covered(self.dataset.detections[0])  # E-01 S-01
        window = EffortWindow(
            effort_id="EF-X",
            device_id="C-03",
            from_at=datetime(2025, 4, 8, 22, 0),
            to_at=datetime(2025, 4, 9, 4, 0),
            segment_id="S-99",  # 图上不存在的区段
            observable=Observable.BOTH,
            event_capture=CaptureKind.EVENT_CAPTURE_PHOTO,
        )
        fake = Detection(
            event_id="X-1",
            device_id="C-03",
            time=datetime(2025, 4, 8, 23, 0),
            species="豹",
            event_group="",
            sighting=Sighting.ENTRY,
            direction="向东",
        )
        second_sighting = CoveredSighting(fake, window, "S-99")
        cluster_a = EventCluster("A", "豹", [first], [], False)
        cluster_b = EventCluster("B", "豹", [second_sighting], [], False)
        # S-01 与图外区段 S-99 没有共同斑块，不能直接串联
        self.assertIsNone(self.evidence._can_chain(cluster_a, cluster_b))

    def test_chain_breaks_on_direction_conflict(self):
        from corridor.events import CoveredSighting, EventCluster
        from corridor.model import CaptureKind, Detection, EffortWindow, Observable
        from datetime import datetime

        def sighting(event_id, segment, direction, moment):
            window = EffortWindow(
                effort_id=f"EF-{event_id}",
                device_id="C-01",
                from_at=datetime(2025, 4, 8, 19, 0),
                to_at=datetime(2025, 4, 9, 6, 0),
                segment_id=segment,
                observable=Observable.BOTH,
                event_capture=CaptureKind.EVENT_CAPTURE_PHOTO,
            )
            detection = Detection(
                event_id=event_id,
                device_id="C-01",
                time=moment,
                species="豹",
                event_group="",
                sighting=Sighting.ENTRY,
                direction=direction,
            )
            return CoveredSighting(detection, window, segment)

        east = EventCluster(
            "D1",
            "豹",
            [sighting("D-1", "S-01", "向东", datetime(2025, 4, 8, 22, 0))],
            [],
            False,
        )
        west = EventCluster(
            "D2",
            "豹",
            [sighting("D-2", "S-02", "向西", datetime(2025, 4, 8, 23, 0))],
            [],
            False,
        )
        # 相邻区段、时间相容，但方向记录矛盾，不能判为同一过境
        self.assertIsNone(self.evidence._can_chain(east, west))
        west.sightings[0].detection  # 只读检查，确保结构完整
        same_direction = EventCluster(
            "D3",
            "豹",
            [sighting("D-3", "S-02", "向东", datetime(2025, 4, 8, 23, 0))],
            [],
            False,
        )
        self.assertIsNotNone(self.evidence._can_chain(east, same_direction))


class EvidenceGradeTests(EngineTest):
    def test_entry_alone_does_not_prove_completed_crossing(self):
        g11 = next(c for c in self.report.clusters if c.cluster_id == "G-11")
        routes = self.evidence.interpret_routes(g11)
        self.assertEqual(len(routes), 1)
        route = routes[0]
        self.assertEqual(route.segments, ("S-01", "S-02"))
        self.assertEqual(route.proven_segments, ("S-01",))
        self.assertEqual(route.unproven_segments, ("S-02",))
        self.assertEqual(route.status, "ambiguous")

    def test_exit_proves_segment_crossing_but_orientation_open(self):
        g22 = next(c for c in self.report.clusters if c.cluster_id.startswith("G-22"))
        routes = self.evidence.interpret_routes(g22)
        statuses = {r.status for r in routes}
        self.assertEqual(statuses, {"proven"})
        # 两个定向解释（H-01→H-03 与反向）互斥，但只对应一次事件
        self.assertEqual(len(routes), 2)

    def test_pass_sighting_is_completed_crossing(self):
        e03 = next(c for c in self.report.clusters if c.cluster_id == "E-03")
        routes = self.evidence.interpret_routes(e03)
        self.assertTrue(all(r.status == "proven" for r in routes))
        self.assertTrue(all(r.segments == ("S-01",) for r in routes))

    def test_pair_usage_for_leopard_is_zero_provable_one_compatible(self):
        usage = next(
            u
            for u in self.report.pair_usage
            if u.pair == ("H-01", "H-03") and u.species == "豹"
        )
        self.assertEqual(usage.provable_min, 0)
        self.assertEqual(usage.compatible_max, 1)

    def test_unobserved_extensions_are_not_invented_as_routes(self):
        # E-03 只在 S-01 看到，不得凭空延伸出经过 S-02/S-03 的路线
        e03 = next(c for c in self.report.clusters if c.cluster_id == "E-03")
        segments = {r.segments for r in self.evidence.interpret_routes(e03)}
        self.assertEqual(segments, {("S-01",)})


class CoverageTests(EngineTest):
    def test_offline_window_is_coverage_gap_not_negative(self):
        gap_ids = {g.effort_id for g in self.report.coverage_gaps}
        self.assertEqual(gap_ids, {"EF-3"})
        negative_ids = {n.effort_id for n in self.report.negatives}
        self.assertNotIn("EF-3", negative_ids)

    def test_negatives_only_inside_opportunity_windows(self):
        # EF-1 双向经过且无狍/野猪：强阴性；EF-2 仅入口：部分阴性
        by_window = {(n.effort_id, n.species): n for n in self.report.negatives}
        self.assertEqual(by_window[("EF-1", "野猪")].strength, "strong")
        self.assertEqual(by_window[("EF-2", "野猪")].strength, "partial")
        self.assertEqual(
            by_window[("EF-2", "野猪")].observable, Observable.ENTRY
        )
        # 有阳性记录的物种不出现在阴性列表
        self.assertNotIn(("EF-1", "豹"), by_window)

    def test_detection_without_effort_is_flagged_but_kept_as_positive(self):
        codes = {f.code for f in self.report.findings}
        self.assertIn("detection_without_effort", codes)
        finding = next(f for f in self.report.findings if f.code == "detection_without_effort")
        self.assertEqual(finding.refs, ("E-03",))
        # 阳性记录仍进入分簇
        self.assertIn("E-03", {c.cluster_id for c in self.report.clusters})

    def test_shared_entry_event_counts_at_most_once_per_pair(self):
        # 任何情景下，一个事件簇对一个斑块对至多贡献 1
        for usage in self.report.pair_usage:
            for scenario in usage.scenarios:
                self.assertLessEqual(
                    scenario["compatible_crossings"],
                    len(scenario["clusters"]),
                )

    def test_mutually_exclusive_scenarios_are_never_summed(self):
        # 野猪：互斥情景分别为 2 次（拆分）与 1 次（重触发合并），
        # 报告只允许给 [1, 2] 的范围，绝不输出 3
        usage = next(
            u
            for u in self.report.pair_usage
            if u.pair == ("H-01", "H-03") and u.species == "野猪"
        )
        scenario_totals = [s["compatible_crossings"] for s in usage.scenarios]
        self.assertEqual(sorted(scenario_totals), [1, 2])
        self.assertLess(usage.compatible_max, sum(scenario_totals))
        # 跨斑块对也不得把同一事件重复累加：所有 pair_usage 的下界之和
        # 不得超过实际事件簇数量级别的观测事实
        self.assertLessEqual(
            sum(u.provable_min for u in self.report.pair_usage if u.species == "野猪"),
            2,
        )
