"""改造方案分期审查与有限设备布置。

审查口径：

1. 每个时段（阶段）必须保住当期每条承诺的斑块对连接；封闭集之外要有
   实际可走路径，且方案登记的绕道要与图上路径一致。
2. 同一 ``shared_passage`` 的多条承诺共享一个夜间容量上限，需求合并
   计算，不能各算一份余量。
3. 方案若只能依赖一条“尚未辨清”的路线解释（该区段只有入口记录或无
   完成穿越证据）才能保住承诺，结论是“先补查”，并给出仍可先行的准备
   工作与能辨清事实的设备布置。
4. 资源（人天、预算、设备日）按月度 work_limits 卡；可选调查窗还要
   检查设备台账可用性与同一设备时间冲突。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from .events import EvidenceReview
from .model import (
    Commitment,
    Dataset,
    Observable,
    ProposalStage,
    SurveySlot,
    WorkLimit,
)
from .network import Network

RELEASE_READY = "可放行"
RELEASE_BLOCKED = "不予放行"
RELEASE_SURVEY_FIRST = "先补查再放行"


@dataclass
class CommitmentCheck:
    requirement_id: str
    pair: tuple[str, str]
    species: str
    required_transits: int
    feasible_paths: list[tuple[str, ...]]
    detour_registered: tuple[str, ...]
    detour_matches_graph: bool
    unproven_segments: list[str]
    relies_on_unresolved_route: bool
    detail: list[str] = field(default_factory=list)


@dataclass
class CapacityCheck:
    shared_passage: str
    capacity_per_night: int
    combined_demand: int
    demand_by_requirement: dict[str, int]
    ok: bool
    detail: str


@dataclass
class StageReview:
    proposal_id: str
    stage: int
    start_date: date
    end_date: date
    closed_segments: tuple[str, ...]
    decision: str
    commitment_checks: list[CommitmentCheck]
    capacity_checks: list[CapacityCheck]
    work_overlaps: list[str]
    unresolved_facts: list[str]
    preparatory_work: list[str]
    blocking_reasons: list[str] = field(default_factory=list)


@dataclass
class ResourceAccount:
    period: str
    limit: WorkLimit
    crew_days_used: int
    budget_used: int
    within_limits: bool
    notes: list[str] = field(default_factory=list)


@dataclass
class SlotFeasibility:
    slot: SurveySlot
    feasible: bool
    reasons: list[str]
    warnings: list[str] = field(default_factory=list)


@dataclass
class SelectedSlot:
    slot: SurveySlot
    warnings: list[str]
    resolves: list[str]       # 该窗能回答的未决事实


@dataclass
class SurveyPlan:
    period: str
    device_day_budget: int
    selected_slots: list[SelectedSlot]
    device_days_used: int
    unresolved_questions_covered: list[str]
    partial_questions: list[str]
    unresolved_questions_remaining: list[str]
    infeasible_slots: list[SlotFeasibility]


@dataclass
class ProposalReview:
    proposal_id: str
    stages: list[StageReview]
    overall_decision: str
    resources: Optional[ResourceAccount]


class Planner:
    def __init__(self, dataset: Dataset, network: Network, evidence: EvidenceReview):
        self.data = dataset
        self.net = network
        self.evidence = evidence

    # -- 证据状态 --------------------------------------------------------
    def _segment_evidence(self) -> dict[str, set[str]]:
        """区段 -> 在该通道上已有完成穿越证据的物种集合。"""
        proven: dict[str, set[str]] = {}
        report = self.evidence.review()
        for cluster in report.clusters:
            for route in self.evidence.interpret_routes(cluster):
                if route.status == "proven":
                    for segment in route.proven_segments:
                        proven.setdefault(segment, set()).add(cluster.species)
        return proven

    def _unproven_segments(
        self, path: tuple[str, ...], species: str, segment_evidence: dict[str, set[str]]
    ) -> list[str]:
        return [
            segment
            for segment in path
            if species not in segment_evidence.get(segment, set())
        ]

    # -- 单阶段 ----------------------------------------------------------
    def review_stage(self, stage: ProposalStage) -> StageReview:
        commitments = [c for c in self.data.commitments if c.period == stage.start_date.strftime("%Y-%m")]
        segment_evidence = self._segment_evidence()
        commitment_checks: list[CommitmentCheck] = []
        blocking: list[str] = []
        unresolved: list[str] = []
        preparatory: list[str] = []
        mid_day = stage.start_date + (stage.end_date - stage.start_date) / 2

        for commitment in commitments:
            check = self._check_commitment(commitment, stage, segment_evidence, mid_day)
            commitment_checks.append(check)
            if not check.feasible_paths:
                blocking.append(
                    f"{commitment.requirement_id}（{commitment.species} {commitment.from_patch}↔"
                    f"{commitment.to_patch}）在封闭 {stage.closed_segments} 后无任何可走路径"
                )
            elif check.relies_on_unresolved_route:
                unresolved.append(
                    f"{commitment.requirement_id} 的可走路径含未辨清区段 "
                    f"{check.unproven_segments}（{commitment.species} 无完成穿越证据）"
                )
            if not check.detour_matches_graph:
                blocking.append(
                    f"{commitment.requirement_id}：登记绕道 {check.detour_registered} "
                    "与图上实际可走路径不一致"
                )

        capacity = self._check_capacity(stage, commitments, commitment_checks)
        for check in capacity:
            if not check.ok:
                blocking.append(
                    f"共用通道 {check.shared_passage} 夜间容量 {check.capacity_per_night}，"
                    f"合并需求 {check.combined_demand}（{check.detail}），不能各算一份余量"
                )

        overlaps = self._work_overlaps(stage)
        if overlaps:
            preparatory.append(
                "同期管护作业已登记：" + "；".join(overlaps) + "，施工组织需与其错峰或合并安排"
            )
        preparatory.append("不依赖未辨清路线的准备：围挡预制、设备检定、值班排班与安全交底可先行")

        if blocking:
            decision = RELEASE_BLOCKED
        elif unresolved:
            decision = RELEASE_SURVEY_FIRST
        else:
            decision = RELEASE_READY
        return StageReview(
            proposal_id=stage.proposal_id,
            stage=stage.stage,
            start_date=stage.start_date,
            end_date=stage.end_date,
            closed_segments=stage.closed_segments,
            decision=decision,
            commitment_checks=commitment_checks,
            capacity_checks=capacity,
            work_overlaps=overlaps,
            unresolved_facts=unresolved,
            preparatory_work=preparatory,
            blocking_reasons=blocking,
        )

    def _check_commitment(
        self,
        commitment: Commitment,
        stage: ProposalStage,
        segment_evidence: dict[str, set[str]],
        mid_day: date,
    ) -> CommitmentCheck:
        paths = self.net.simple_paths(
            commitment.from_patch,
            commitment.to_patch,
            day=mid_day,
            closed=stage.closed_segments,
        )
        detour_paths = self.net.detour_paths(
            commitment.from_patch,
            commitment.to_patch,
            day=mid_day,
            closed=stage.closed_segments,
            detour_segments=stage.detour_segments,
        )
        detail: list[str] = []
        unproven: list[str] = []
        for path in paths:
            missing = self._unproven_segments(path, commitment.species, segment_evidence)
            detail.append(
                f"路径 {'→'.join(path)}："
                + ("全部区段有完成穿越证据" if not missing else f"未辨清区段 {missing}")
            )
            unproven.extend(missing)
        # 一条路径上所有区段都有证据，才不依赖未决解释
        relies = any(
            self._unproven_segments(path, commitment.species, segment_evidence)
            for path in paths
        )
        return CommitmentCheck(
            requirement_id=commitment.requirement_id,
            pair=(commitment.from_patch, commitment.to_patch),
            species=commitment.species,
            required_transits=commitment.required_transits,
            feasible_paths=paths,
            detour_registered=stage.detour_segments,
            detour_matches_graph=bool(detour_paths) if paths else False,
            unproven_segments=sorted(set(unproven)),
            relies_on_unresolved_route=relies,
            detail=detail,
        )

    def _check_capacity(
        self,
        stage: ProposalStage,
        commitments: list[Commitment],
        checks: list[CommitmentCheck],
    ) -> list[CapacityCheck]:
        result: list[CapacityCheck] = []
        by_passage: dict[str, list[Commitment]] = {}
        for commitment in commitments:
            by_passage.setdefault(commitment.shared_passage, []).append(commitment)
        check_by_req = {c.requirement_id: c for c in checks}
        for passage, group in by_passage.items():
            # 只有当承诺的可走路径实际经过该共用通道时才占用其容量
            demand: dict[str, int] = {}
            for commitment in group:
                check = check_by_req[commitment.requirement_id]
                uses = any(passage in path for path in check.feasible_paths)
                if uses:
                    demand[commitment.requirement_id] = commitment.required_transits
            combined = sum(demand.values())
            capacity = group[0].capacity_per_night
            result.append(
                CapacityCheck(
                    shared_passage=passage,
                    capacity_per_night=capacity,
                    combined_demand=combined,
                    demand_by_requirement=demand,
                    ok=combined <= capacity,
                    detail="，".join(f"{req}={n} 次/夜" for req, n in demand.items()) or "无占用",
                )
            )
        return result

    def _work_overlaps(self, stage: ProposalStage) -> list[str]:
        return [
            f"{work.work_id} {work.change}（{work.segment_id} {work.start_date}~{work.end_date}）"
            for work in self.data.works
            if work.overlaps(stage.start_date, stage.end_date)
        ]

    # -- 方案与月度资源 --------------------------------------------------
    def review_proposal(self, proposal_id: str) -> ProposalReview:
        stages = sorted(
            (s for s in self.data.stages if s.proposal_id == proposal_id),
            key=lambda s: s.stage,
        )
        reviews = [self.review_stage(stage) for stage in stages]
        period = stages[0].start_date.strftime("%Y-%m")
        limit = self.data.work_limits.get(period)
        account = self._resource_account(period, stages, limit)
        if any(r.decision is RELEASE_BLOCKED for r in reviews):
            overall = RELEASE_BLOCKED
        elif any(r.decision is RELEASE_SURVEY_FIRST for r in reviews):
            overall = RELEASE_SURVEY_FIRST
        else:
            overall = RELEASE_READY
        return ProposalReview(proposal_id, reviews, overall, account)

    def review_all(self) -> list[ProposalReview]:
        ids = sorted({s.proposal_id for s in self.data.stages})
        return [self.review_proposal(pid) for pid in ids]

    def _resource_account(
        self,
        period: str,
        stages: list[ProposalStage],
        limit: Optional[WorkLimit],
    ) -> ResourceAccount:
        crew = sum(s.crew_days for s in stages)
        budget = sum(s.cost_yuan for s in stages)
        notes: list[str] = []
        ok = True
        if limit is None:
            notes.append(f"未登记 {period} 的 work_limits，无法核对资源")
            ok = False
        else:
            if crew > limit.crew_days:
                ok = False
                notes.append(f"人天 {crew} 超上限 {limit.crew_days}")
            if budget > limit.budget_yuan:
                ok = False
                notes.append(f"预算 {budget} 元超上限 {limit.budget_yuan} 元")
            notes.append(
                f"设备日上限 {limit.available_device_days}，"
                "调查窗布置见 survey_plan"
            )
        return ResourceAccount(period, limit, crew, budget, ok, notes)

    # -- 调查窗与设备布置 ------------------------------------------------
    def assess_slot(self, slot: SurveySlot) -> SlotFeasibility:
        """硬不可行：设备不存在/台账过期。

        迁移安装只是告警；同设备时间重叠是“选择互斥”，窗口本身仍可
        布设，只是不能与互斥窗口同时入选，因此放在告警里。
        """
        hard: list[str] = []
        warnings: list[str] = []
        device = self.data.devices.get(slot.device_id)
        if device is None:
            hard.append("设备台账无此设备")
        else:
            if not device.available_on(slot.from_at.date()):
                hard.append(
                    f"{slot.device_id} 台账有效期 {device.active_from}~{device.active_to}，"
                    f"窗口 {slot.from_at.date()} 不在其内，需重新安装检定后才能布设"
                )
            if device.segment_id != slot.segment_id:
                warnings.append(
                    f"{slot.device_id} 原驻地 {device.segment_id}，窗口要求 {slot.segment_id}，需迁移安装"
                )
        # 同一设备同一时段只能布设一次：记录互斥对象，不使窗口失效
        for other in self.data.survey_slots:
            if other.slot_id == slot.slot_id or other.device_id != slot.device_id:
                continue
            if other.from_at <= slot.to_at and slot.from_at <= other.to_at:
                warnings.append(f"与 {other.slot_id} 在同一设备上时间重叠，二者互斥，只能选其一")
        return SlotFeasibility(slot, not hard, hard, warnings)

    def _questions(self) -> dict[str, str]:
        """当前证据下影响交割的未决事实（区段 -> 问题描述）。"""
        report = self.evidence.review()
        questions: dict[str, str] = {}
        for cluster in report.clusters:
            for route in self.evidence.interpret_routes(cluster):
                if route.status != "proven":
                    for segment in route.unproven_segments:
                        questions.setdefault(
                            segment,
                            f"{segment} 对 {cluster.species} 只有进入/无完成证据，"
                            "绕行能否实际完成穿越未辨清",
                        )
        for gap in report.coverage_gaps:
            questions.setdefault(
                gap.segment_id,
                f"{gap.segment_id} 存在停机/无覆盖窗，过境窗内是否使用无证据",
            )
        return questions

    def plan_surveys(self, period: str) -> SurveyPlan:
        limit = self.data.work_limits[period]
        budget = limit.available_device_days
        feasibility = [self.assess_slot(slot) for slot in self.data.survey_slots]
        feasible = [f.slot for f in feasibility if f.feasible]
        questions = self._questions()

        chosen: list[SelectedSlot] = []
        used_days = 0
        covered: list[str] = []
        partial: list[str] = []
        # 优先选能直接回答未决事实、且观察口径最强的窗口
        strength_rank = {
            Observable.BOTH: 0,
            Observable.ENTRY: 1,
            Observable.EXIT: 1,
            Observable.DEPARTURE: 1,
            Observable.NONE: 2,
        }
        ordered = sorted(
            feasible,
            key=lambda s: (
                s.segment_id not in questions,
                strength_rank.get(s.observable, 3),
                s.from_at,
            ),
        )
        occupied_devices: set[str] = set()
        occupied_windows: list[tuple] = []
        feasibility_by_id = {f.slot.slot_id: f for f in feasibility}
        for slot in ordered:
            if used_days + slot.crew_days > budget:
                continue
            if slot.device_id in occupied_devices and any(
                start <= slot.to_at and slot.from_at <= end
                for _, start, end in occupied_windows
            ):
                continue
            resolves = [questions[slot.segment_id]] if slot.segment_id in questions else []
            chosen.append(
                SelectedSlot(
                    slot=slot,
                    warnings=feasibility_by_id[slot.slot_id].warnings,
                    resolves=resolves,
                )
            )
            used_days += slot.crew_days
            occupied_devices.add(slot.device_id)
            occupied_windows.append((slot.device_id, slot.from_at, slot.to_at))
            if slot.segment_id in questions:
                if slot.observable is Observable.BOTH:
                    covered.append(slot.segment_id)
                else:
                    partial.append(slot.segment_id)

        remaining = [
            text
            for seg, text in questions.items()
            if seg not in set(covered)
        ]
        # 部分回答（仅入口/出口口径）在剩余问题中注明已有线索但仍未辨清
        for seg in sorted(set(partial) - set(covered)):
            index = next((i for i, text in enumerate(remaining) if text.startswith(seg)), None)
            if index is not None:
                remaining[index] = remaining[index] + "（已有入口/出口窗口提供部分线索，但不能证明完成穿越）"
        return SurveyPlan(
            period=period,
            device_day_budget=budget,
            selected_slots=chosen,
            device_days_used=used_days,
            unresolved_questions_covered=sorted(set(covered)),
            partial_questions=sorted(set(partial)),
            unresolved_questions_remaining=remaining,
            infeasible_slots=[f for f in feasibility if not f.feasible],
        )
