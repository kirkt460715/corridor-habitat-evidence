"""单个断点改造完毕后的现场交割。

交割包必须同时覆盖：

- 改造前后合同约定的斑块对与替代路径（对照期由 work_limits 指定）；
- 施工切换夜的设备状态：切换恰逢断电时，同一条放宽假设不能同时证明
  两条路线都改善，只能列为未证事实并安排接续观测；
- 一次工程安排下可放行的步骤、仍应保留的通行路径、复核未过原因和
  接续观测安排，供项目人员按工程安排逐次查询。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from .events import EvidenceReview
from .model import Dataset, Observable, ProposalStage
from .network import Network
from .periods import ParallelPeriods
from .planning import (
    Planner,
    RELEASE_BLOCKED,
    RELEASE_READY,
    RELEASE_SURVEY_FIRST,
    StageReview,
    SurveyPlan,
)


@dataclass
class SwitchNight:
    switch_date: date
    segment_id: str
    covered: bool
    detail: str


@dataclass
class HandoverItem:
    requirement_id: str
    pair: tuple[str, str]
    species: str
    pre_paths: list[tuple[str, ...]]
    post_paths: list[tuple[str, ...]]
    retained_paths: list[tuple[str, ...]]
    compare_periods: tuple[str, ...]
    comparison_notes: list[str]
    status: str                      # 满足 / 证据不足 / 路径缺失
    reasons: list[str] = field(default_factory=list)


@dataclass
class FollowUpObservation:
    segment_id: str
    reason: str
    earliest_window: Optional[datetime]
    required_observable: Observable


@dataclass
class HandoverPackage:
    proposal_id: str
    breakpoint_segment: str
    stages: list[StageReview]
    items: list[HandoverItem]
    switch_nights: list[SwitchNight]
    follow_ups: list[FollowUpObservation]
    releasable_steps: list[str]
    retained_routes: list[str]
    failed_reasons: list[str]
    overall: str


class Handover:
    def __init__(
        self,
        dataset: Dataset,
        network: Network,
        evidence: EvidenceReview,
        planner: Planner,
        parallels: ParallelPeriods,
    ):
        self.data = dataset
        self.net = network
        self.evidence = evidence
        self.planner = planner
        self.parallels = parallels

    # -- 切换夜覆盖 ------------------------------------------------------
    def switch_nights(self, stages: list[ProposalStage]) -> list[SwitchNight]:
        nights: list[SwitchNight] = []
        for index, stage in enumerate(stages):
            switch = stage.start_date
            if index == 0:
                detail = "首期开工切换，封闭区段进入施工状态"
            else:
                detail = f"由第 {index} 期切换至第 {stage.stage} 期，封闭 {stage.closed_segments}"
            for segment in (*stage.closed_segments, *stage.detour_segments):
                covered, why = self._coverage_on(segment, switch)
                nights.append(
                    SwitchNight(
                        switch_date=switch,
                        segment_id=segment,
                        covered=covered,
                        detail=f"{detail}；{why}",
                    )
                )
        return nights

    def _coverage_on(self, segment: str, day: date) -> tuple[bool, str]:
        """该日夜间（18:00 至次日 06:00）是否有有效检测机会。"""
        window_start = datetime.combine(day, datetime.min.time()).replace(hour=18)
        window_end = datetime.combine(day + timedelta(days=1), datetime.min.time()).replace(hour=6)
        relevant = [
            w
            for w in self.data.effort
            if w.segment_id == segment
            and w.to_at >= window_start
            and w.from_at <= window_end
        ]
        if not relevant:
            # 设备台账在役但没有 effort 记录，同样视为无检测机会
            device = next(
                (d for d in self.data.devices.values() if d.segment_id == segment), None
            )
            if device and device.available_on(day):
                return False, (
                    f"{segment} 设备 {device.device_id} 台账在役但当夜无 effort 记录，"
                    "按无检测机会处理"
                )
            return False, f"{segment} 切换夜无设备覆盖"
        usable = [w for w in relevant if w.has_opportunity]
        if not usable:
            return False, f"{segment} 切换夜设备停机/无覆盖（{relevant[0].effort_id}）"
        return True, f"{segment} 切换夜由 {','.join(w.effort_id for w in usable)} 覆盖"

    # -- 单方案交割 ------------------------------------------------------
    def build_package(self, proposal_id: str) -> HandoverPackage:
        stages = sorted(
            (s for s in self.data.stages if s.proposal_id == proposal_id),
            key=lambda s: s.stage,
        )
        if not stages:
            raise KeyError(f"未知方案 {proposal_id}")
        breakpoint_segment = stages[0].closed_segments[0]
        period = stages[0].start_date.strftime("%Y-%m")
        limit = self.data.work_limits[period]
        stage_reviews = [self.planner.review_stage(stage) for stage in stages]
        nights = self.switch_nights(stages)

        items: list[HandoverItem] = []
        follow_ups: list[FollowUpObservation] = []
        retained_routes: list[str] = []
        failed: list[str] = []
        releasable: list[str] = []

        commitments = [c for c in self.data.commitments if c.period == period]
        segment_evidence = self.planner._segment_evidence()
        for commitment in commitments:
            item = self._build_item(commitment, stages, limit.handover_compare_period)
            items.append(item)
            for path in item.retained_paths:
                retained_routes.append(
                    f"{commitment.requirement_id} {commitment.from_patch}↔{commitment.to_patch} 保留 {'→'.join(path)}"
                )
            if item.status != "满足":
                failed.extend(f"{commitment.requirement_id}：{r}" for r in item.reasons)
                for path in item.post_paths:
                    for segment in path:
                        if commitment.species not in segment_evidence.get(segment, set()):
                            follow_ups.append(
                                FollowUpObservation(
                                    segment_id=segment,
                                    reason=(
                                        f"{commitment.species} 经 {segment} 的完成穿越无证据，"
                                        "不能与未覆盖的切换夜共用同一放宽假设"
                                    ),
                                    earliest_window=self._next_available_window(segment),
                                    required_observable=Observable.BOTH,
                                )
                            )

        for night in nights:
            if not night.covered:
                failed.append(f"切换夜 {night.switch_date} {night.detail}")
                follow_ups.append(
                    FollowUpObservation(
                        segment_id=night.segment_id,
                        reason="施工切换恰逢设备断电/无覆盖，改善不能归因，需补测",
                        earliest_window=self._next_available_window(night.segment_id),
                        required_observable=Observable.BOTH,
                    )
                )

        # 阶段审查的阻断原因（共用容量超标、绕道与图不一致等）直接进入交割未过清单
        for review in stage_reviews:
            for reason in review.blocking_reasons:
                failed.append(f"第 {review.stage} 期阻断：{reason}")
            for fact in review.unresolved_facts:
                failed.append(f"第 {review.stage} 期待补查：{fact}")

        for index, review in enumerate(stage_reviews, start=1):
            if review.decision is RELEASE_READY:
                releasable.append(f"第 {index} 期（{review.start_date}~{review.end_date}）可放行")
            elif review.decision is RELEASE_SURVEY_FIRST:
                releasable.append(f"第 {index} 期暂缓：先补查 {review.unresolved_facts}")
            else:
                releasable.append(f"第 {index} 期不予放行：{review.blocking_reasons}")

        if any(item.status != "满足" for item in items) or any(not n.covered for n in nights):
            overall = RELEASE_SURVEY_FIRST if not any(
                review.decision is RELEASE_BLOCKED for review in stage_reviews
            ) else RELEASE_BLOCKED
        else:
            overall = RELEASE_READY

        # 去重接续观测
        unique_follow: dict[tuple[str, str], FollowUpObservation] = {}
        for follow in follow_ups:
            unique_follow.setdefault((follow.segment_id, follow.reason), follow)

        return HandoverPackage(
            proposal_id=proposal_id,
            breakpoint_segment=breakpoint_segment,
            stages=stage_reviews,
            items=items,
            switch_nights=nights,
            follow_ups=list(unique_follow.values()),
            releasable_steps=releasable,
            retained_routes=sorted(set(retained_routes)),
            failed_reasons=failed,
            overall=overall,
        )

    def _build_item(
        self,
        commitment,
        stages: list[ProposalStage],
        compare_periods: tuple[str, ...],
    ) -> HandoverItem:
        pre_day = stages[0].start_date - timedelta(days=1)
        post_day = stages[-1].end_date + timedelta(days=1)
        pre_paths = self.net.simple_paths(
            commitment.from_patch, commitment.to_patch, day=pre_day
        )
        # 改造完成后仍处封闭状态的区段（链路在 post_day 仍有效才计入）
        post_closed = tuple(
            sorted({
                segment
                for stage in stages
                for segment in stage.closed_segments
                if self.net.link_on(segment, post_day) is not None
            })
        )
        post_paths = self.net.simple_paths(
            commitment.from_patch,
            commitment.to_patch,
            day=post_day,
            closed=post_closed,
        )
        # 施工期实际保留路径：各阶段封闭集下仍可走的路径
        retained: list[tuple[str, ...]] = []
        for stage in stages:
            mid = stage.start_date + (stage.end_date - stage.start_date) / 2
            retained.extend(
                self.net.simple_paths(
                    commitment.from_patch,
                    commitment.to_patch,
                    day=mid,
                    closed=stage.closed_segments,
                )
            )
        retained = sorted(set(retained))

        notes: list[str] = []
        reasons: list[str] = []
        relevant_segments = sorted({seg for path in pre_paths + post_paths for seg in path})
        # 对照期由 work_limits 指定；只取与本斑块对路径相关的区段
        comparisons = self.parallels.compare_period_sets(list(compare_periods))
        for comparison in comparisons:
            if comparison.segment_id not in relevant_segments:
                continue
            if comparison.comparable:
                direction = "上升" if (comparison.rate_change_pct or 0) > 0 else "下降"
                notes.append(
                    f"{comparison.segment_id} {comparison.period_a}→{comparison.period_b}："
                    f"可比，每百小时事件组 {comparison.rate_a:.1f}→{comparison.rate_b:.1f}"
                    f"（{direction} {abs(comparison.rate_change_pct):.0%}），"
                    f"原始计数 {comparison.raw_count_a}→{comparison.raw_count_b} 不单独使用"
                )
            else:
                notes.append(
                    f"{comparison.segment_id} {comparison.period_a} vs {comparison.period_b}：不可比——"
                    + "；".join(comparison.reasons)
                )
        if not pre_paths:
            reasons.append("改造前无斑块对路径，无法建立对照基线")
        if not post_paths:
            reasons.append("改造后约定斑块对失去可走路径")
        if not retained:
            reasons.append("施工各阶段均无保留通行路径，违反连通承诺")

        status = "满足" if not reasons else "路径缺失"
        # 证据充分性：替代路径上每个区段都要有该物种的完成穿越证据
        segment_evidence = self.planner._segment_evidence()
        weak = sorted({
            segment
            for path in retained
            for segment in path
            if commitment.species not in segment_evidence.get(segment, set())
        })
        if weak:
            status = "证据不足"
            reasons.append(f"替代路径区段 {weak} 无 {commitment.species} 完成穿越证据")
        if not notes:
            notes.append(f"对照期 {compare_periods} 无同区段平行记录，按无对照处理")
        return HandoverItem(
            requirement_id=commitment.requirement_id,
            pair=(commitment.from_patch, commitment.to_patch),
            species=commitment.species,
            pre_paths=pre_paths,
            post_paths=post_paths,
            retained_paths=retained,
            compare_periods=compare_periods,
            comparison_notes=notes,
            status=status,
            reasons=reasons,
        )

    def _next_available_window(self, segment: str) -> Optional[datetime]:
        future = [
            slot.from_at
            for slot in self.data.survey_slots
            if slot.segment_id == segment and slot.observable is not Observable.NONE
        ]
        return min(future) if future else None

    # -- 项目人员查询 ----------------------------------------------------
    def lookup_work_arrangement(self, proposal_id: str) -> dict:
        """按一次工程安排汇总现场可执行信息。"""
        package = self.build_package(proposal_id)
        survey_plan: Optional[SurveyPlan] = None
        period = self.data.stages[0].start_date.strftime("%Y-%m")
        for stage in self.data.stages:
            if stage.proposal_id == proposal_id:
                period = stage.start_date.strftime("%Y-%m")
                break
        if period in self.data.work_limits:
            survey_plan = self.planner.plan_surveys(period)
        return {
            "proposal_id": package.proposal_id,
            "overall": package.overall,
            "releasable_steps": package.releasable_steps,
            "retained_routes": package.retained_routes,
            "failed_reasons": package.failed_reasons,
            "follow_up_observations": [
                {
                    "segment": f.segment_id,
                    "reason": f.reason,
                    "earliest_window": f.earliest_window.isoformat() if f.earliest_window else None,
                    "required_observable": f.required_observable.value,
                }
                for f in package.follow_ups
            ],
            "survey_plan": None
            if survey_plan is None
            else {
                "selected_slots": [
                    {
                        "slot": chosen.slot.slot_id,
                        "warnings": chosen.warnings,
                        "resolves": chosen.resolves,
                    }
                    for chosen in survey_plan.selected_slots
                ],
                "device_days_used": survey_plan.device_days_used,
                "questions_remaining": survey_plan.unresolved_questions_remaining,
                "infeasible_slots": [
                    {"slot": f.slot.slot_id, "reasons": f.reasons}
                    for f in survey_plan.infeasible_slots
                ],
            },
        }
