"""设备布设、季节与观测努力相近时期的对比。

原始“已知事件组计数”不能直接横比：努力小时数、可观察对象不同，看到
的多少首先是“看得清不清”的差别。本模块只在同季节、同区段、观察口径
兼容且努力差距可控的时期之间给出对比，否则显式标注不可比及原因。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .model import Dataset, Observable, ParallelPeriodRow

# 努力小时相对差距不超过该比例才视为“努力相近”
EFFORT_RATIO_LIMIT = 0.25
# 低于该小时数的时期不参与强度对比（努力过低）
MIN_EFFORT_HOURS = 24.0


@dataclass
class PeriodCell:
    period_id: str
    season: str
    segment_id: str
    effort_hours: float
    observable: Optional[Observable]
    known_group_count: Optional[int]

    @property
    def has_coverage(self) -> bool:
        return self.observable is not Observable.NONE and self.effort_hours > 0

    @property
    def groups_per_100h(self) -> Optional[float]:
        if not self.has_coverage or self.known_group_count is None or self.effort_hours <= 0:
            return None
        return self.known_group_count / self.effort_hours * 100.0


@dataclass
class Comparison:
    period_a: str
    period_b: str
    season: str
    segment_id: str
    comparable: bool
    reasons: list[str]
    rate_a: Optional[float]
    rate_b: Optional[float]
    rate_change_pct: Optional[float]
    raw_count_a: Optional[int]
    raw_count_b: Optional[int]


class ParallelPeriods:
    def __init__(self, dataset: Dataset):
        self.data = dataset
        self.cells: dict[tuple[str, str], PeriodCell] = {}
        self.periods: dict[str, list[ParallelPeriodRow]] = {}
        for row in dataset.parallels:
            self.cells[(row.period_id, row.segment_id)] = PeriodCell(
                period_id=row.period_id,
                season=row.season,
                segment_id=row.segment_id,
                effort_hours=row.effort_hours,
                observable=row.observable,
                known_group_count=row.known_group_count,
            )
            self.periods.setdefault(row.period_id, []).append(row)

    def period_ids(self, season: Optional[str] = None) -> list[str]:
        ids = sorted(self.periods)
        if season is None:
            return ids
        return [pid for pid in ids if any(r.season == season for r in self.periods[pid])]

    def compare(self, period_a: str, period_b: str, segment_id: str) -> Comparison:
        cell_a = self.cells[(period_a, segment_id)]
        cell_b = self.cells[(period_b, segment_id)]
        reasons: list[str] = []
        comparable = True

        if cell_a.season != cell_b.season:
            comparable = False
            reasons.append(
                f"季节不同（{cell_a.season} vs {cell_b.season}），动物活动基线不同，不可比"
            )

        if not cell_a.has_coverage or not cell_b.has_coverage:
            comparable = False
            missing = [
                cell.period_id for cell in (cell_a, cell_b) if not cell.has_coverage
            ]
            reasons.append(f"时期 {missing} 在该区段无检测机会，无记录不能解释为使用下降")

        if cell_a.has_coverage and cell_b.has_coverage:
            low = [
                cell.period_id
                for cell in (cell_a, cell_b)
                if cell.effort_hours < MIN_EFFORT_HOURS
            ]
            if low:
                comparable = False
                reasons.append(f"时期 {low} 努力小时低于 {MIN_EFFORT_HOURS:.0f}h，样本不足")
            else:
                hi, lo_h = max(cell_a.effort_hours, cell_b.effort_hours), min(
                    cell_a.effort_hours, cell_b.effort_hours
                )
                if (hi - lo_h) / hi > EFFORT_RATIO_LIMIT:
                    comparable = False
                    reasons.append(
                        f"努力差距 {(hi - lo_h) / hi:.0%} 超过 {EFFORT_RATIO_LIMIT:.0%}，需按强度折算后谨慎使用"
                    )
            if not self._observables_compatible(cell_a.observable, cell_b.observable):
                comparable = False
                reasons.append(
                    f"观察口径不兼容（{cell_a.observable.value if cell_a.observable else '-'} "
                    f"vs {cell_b.observable.value if cell_b.observable else '-'}），"
                    "全经过型与入口/出口型计数含义不同"
                )

        rate_a, rate_b = cell_a.groups_per_100h, cell_b.groups_per_100h
        change = None
        if comparable and rate_a and rate_b is not None:
            change = (rate_b - rate_a) / rate_a
        if comparable:
            reasons.append("同季节、同区段、口径兼容且努力相近，按每百小时事件组强度对比")
        return Comparison(
            period_a=period_a,
            period_b=period_b,
            season=cell_a.season,
            segment_id=segment_id,
            comparable=comparable,
            reasons=reasons,
            rate_a=rate_a,
            rate_b=rate_b,
            rate_change_pct=change,
            raw_count_a=cell_a.known_group_count,
            raw_count_b=cell_b.known_group_count,
        )

    @staticmethod
    def _observables_compatible(a: Optional[Observable], b: Optional[Observable]) -> bool:
        if a is None or b is None:
            return False
        if a is b:
            return True
        # 出口与“离开”是同一口径的两种写法
        return {a, b} <= {Observable.EXIT, Observable.DEPARTURE}

    def compare_period_sets(
        self, period_ids: list[str]
    ) -> list[Comparison]:
        """对给定时期集合做两两、同区段对比（用于 work_limits 指定的参照组）。"""
        results: list[Comparison] = []
        segments = sorted({row.segment_id for pid in period_ids for row in self.periods[pid]})
        for i, pa in enumerate(period_ids):
            for pb in period_ids[i + 1 :]:
                for segment in segments:
                    results.append(self.compare(pa, pb, segment))
        return results
