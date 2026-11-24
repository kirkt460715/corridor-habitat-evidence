"""相机/人工事件复核与过境证据分级。

规则要点（对应业务口径）：

1. 相同非空 ``event_group`` 是现场已认定的同一事件旁证，必然合并；
   空组记录只按“实际可走路径 + 时间窗 + 方向不矛盾”判断是否 *可能*
   关联，合并与否保留为互斥情景，不直接当成一次或两次过境。
2. “进入”只证明动物进入通道，不证明完成穿越；下游区段再出现、或
   本通道出现“离开/经过”，才把本区段升级为已完成穿越。
3. “无覆盖/停机”的努力区间没有检测机会：无记录不产生阴性观察，只
   产生覆盖缺口说明。阴性观察按物种逐个生成，并区分全经过型与仅
   入口/出口型的强度。
4. 入口型覆盖位于多通道枢纽时，一次进入可能对应多条支路，输出互斥
   路线解释；斑块对用量按情景取最小/最大，每个事件至多计一次，绝不
   把互斥解释相加。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import product
from typing import Optional

from .model import Dataset, Detection, EffortWindow, Observable, Sighting
from .network import Network

# 同一晚相邻触发可被视为同一过境的最大时间间隔（可走路径上的宽松窗）
DEFAULT_CHAIN_WINDOW = timedelta(hours=8)
# 同一通道连续触发的去重窗：窗内重复触发“可能”是同一事件
DEFAULT_RETRIGGER_WINDOW = timedelta(minutes=30)
# 情景枚举安全上限，超出后保留已枚举情景并在报告中注明
SCENARIO_CAP = 64


# ---------------------------------------------------------------------------
# 输出结构
# ---------------------------------------------------------------------------
@dataclass
class CoveredSighting:
    detection: Detection
    effort: Optional[EffortWindow]   # 无登记努力区间时为 None（数据质量问题）
    segment_id: str

    @property
    def has_opportunity_record(self) -> bool:
        return self.effort is not None and self.effort.has_opportunity


@dataclass
class RouteInterpretation:
    """一条与事件证据相容的定向路线解释。"""

    patches: tuple[str, ...]          # 定向斑块序列
    segments: tuple[str, ...]         # 对应区段序列
    status: str                       # proven / entry_only / exit_only / ambiguous
    proven_segments: tuple[str, ...]  # 已有完成穿越证据的区段
    unproven_segments: tuple[str, ...]
    reasons: list[str] = field(default_factory=list)


@dataclass
class EventCluster:
    cluster_id: str
    species: str
    sightings: list[CoveredSighting]
    merge_evidence: list[str]         # 合并依据：event_group / path+time / retrigger
    certain: bool                     # False 表示该簇本身是“可能合并”的产物

    @property
    def time_start(self) -> datetime:
        return min(item.detection.time for item in self.sightings)

    @property
    def time_end(self) -> datetime:
        return max(item.detection.time for item in self.sightings)

    @property
    def observed_segments(self) -> tuple[str, ...]:
        seen: list[str] = []
        for item in sorted(self.sightings, key=lambda x: x.detection.time):
            segment = item.segment_id
            if not seen or seen[-1] != segment:
                seen.append(segment)
        return tuple(seen)


@dataclass
class NegativeObservation:
    effort_id: str
    segment_id: str
    species: str
    strength: str        # strong（双向经过覆盖）/ partial（仅入口或出口）
    observable: Observable
    from_at: datetime
    to_at: datetime


@dataclass
class CoverageGap:
    effort_id: str
    segment_id: str
    from_at: datetime
    to_at: datetime
    reason: str


@dataclass
class PairUsage:
    pair: tuple[str, str]              # 归一化（无序）斑块对
    species: str
    provable_min: int                  # 必有证据支持的完成穿越次数
    compatible_max: int                # 所有相容解释下的上界（每事件至多计一次）
    contributing_clusters: tuple[str, ...]
    scenarios: list[dict]              # 互斥情景，禁止跨情景求和
    route_status: dict[str, str]       # cluster_id -> 路线状态说明


@dataclass
class DataQualityFinding:
    code: str
    message: str
    refs: tuple[str, ...]


@dataclass
class EvidenceReport:
    clusters: list[EventCluster]
    pair_usage: list[PairUsage]
    negatives: list[NegativeObservation]
    coverage_gaps: list[CoverageGap]
    findings: list[DataQualityFinding]
    scenario_count: int
    scenario_truncated: bool


# ---------------------------------------------------------------------------
# 复核引擎
# ---------------------------------------------------------------------------
class EvidenceReview:
    def __init__(
        self,
        dataset: Dataset,
        network: Network,
        *,
        chain_window: timedelta = DEFAULT_CHAIN_WINDOW,
        retrigger_window: timedelta = DEFAULT_RETRIGGER_WINDOW,
    ):
        self.data = dataset
        self.net = network
        self.chain_window = chain_window
        self.retrigger_window = retrigger_window

    # -- 努力区间与覆盖 --------------------------------------------------
    def _effort_for(self, detection: Detection) -> Optional[EffortWindow]:
        for window in self.data.effort:
            if window.device_id != detection.device_id:
                continue
            if window.covers(detection.time):
                return window
        return None

    def _make_covered(self, detection: Detection) -> CoveredSighting:
        effort = self._effort_for(detection)
        if effort is not None:
            segment = effort.segment_id
        else:
            device = self.data.devices.get(detection.device_id)
            if device is None:
                raise ValueError(
                    f"{detection.event_id} 缺少努力区间且无法从设备台账定位区段"
                )
            segment = device.segment_id
        return CoveredSighting(detection, effort, segment)

    def coverage_gaps(self) -> list[CoverageGap]:
        gaps: list[CoverageGap] = []
        for window in self.data.effort:
            if not window.has_opportunity:
                gaps.append(
                    CoverageGap(
                        effort_id=window.effort_id,
                        segment_id=window.segment_id,
                        from_at=window.from_at,
                        to_at=window.to_at,
                        reason="无检测机会（停机/无覆盖），该窗内无记录不能作为阴性观察",
                    )
                )
        return gaps

    def negatives(self) -> list[NegativeObservation]:
        species_set = sorted({d.species for d in self.data.detections})
        results: list[NegativeObservation] = []
        for window in self.data.effort:
            if not window.has_opportunity:
                continue
            present = {
                d.species
                for d in self.data.detections
                if self._effort_for(d) is window
            }
            strength = "strong" if window.observable is Observable.BOTH else "partial"
            for species in species_set:
                if species not in present:
                    results.append(
                        NegativeObservation(
                            effort_id=window.effort_id,
                            segment_id=window.segment_id,
                            species=species,
                            strength=strength,
                            observable=window.observable,
                            from_at=window.from_at,
                            to_at=window.to_at,
                        )
                    )
        return results

    def data_quality_findings(self, covered: list[CoveredSighting]) -> list[DataQualityFinding]:
        findings: list[DataQualityFinding] = []
        for item in covered:
            if item.effort is None:
                findings.append(
                    DataQualityFinding(
                        code="detection_without_effort",
                        message=(
                            f"{item.detection.event_id} 没有登记的努力区间覆盖，"
                            "阳性记录仍可采信，但该时段不能产出阴性观察"
                        ),
                        refs=(item.detection.event_id,),
                    )
                )
        return findings

    # -- 分簇与可能合并 --------------------------------------------------
    def _base_clusters(self) -> tuple[dict[str, EventCluster], list[CoveredSighting]]:
        covered = [self._make_covered(d) for d in self.data.detections]
        groups: dict[str, list[CoveredSighting]] = {}
        for item in covered:
            key = item.detection.event_group or f"~{item.detection.event_id}"
            groups.setdefault(key, []).append(item)

        clusters: dict[str, EventCluster] = {}
        for key, items in groups.items():
            certain = not key.startswith("~")
            evidence = ["event_group：相同非空事件组，现场旁证认定同一事件"] if certain else []
            cluster_id = key[1:] if key.startswith("~") else key
            clusters[cluster_id] = EventCluster(
                cluster_id=cluster_id,
                species=items[0].detection.species,
                sightings=items,
                merge_evidence=evidence,
                certain=certain,
            )
        return clusters, covered

    def _can_chain(self, a: EventCluster, b: EventCluster) -> Optional[str]:
        """b 紧随 a 之后是否可能属于同一过境，返回合并依据或 None。"""
        if a.species != b.species:
            return None
        gap = b.time_start - a.time_end
        if gap < timedelta(0) or gap > self.chain_window:
            return None
        if not self._directions_compatible(a, b):
            return None
        seg_a = a.observed_segments
        seg_b = b.observed_segments
        last_a, first_b = seg_a[-1], seg_b[0]
        day = b.time_start.date()
        if last_a == first_b:
            if gap <= self.retrigger_window:
                return "retrigger：同一通道短间隔连续触发，可能为同一事件"
            return None
        # 相邻区段：在当天的图上经共同斑块相连，即存在实际可走路径
        link_a = self.net.link_on(last_a, day)
        link_b = self.net.link_on(first_b, day)
        if link_a is None or link_b is None:
            return None
        endpoints_a = {link_a.from_patch, link_a.to_patch}
        endpoints_b = {link_b.from_patch, link_b.to_patch}
        if endpoints_a & endpoints_b:
            return "path+time：相邻通道经共同斑块相连且时间、方向相容"
        return None

    @staticmethod
    def _directions_compatible(a: EventCluster, b: EventCluster) -> bool:
        directions = [
            item.detection.direction
            for item in (*a.sightings, *b.sightings)
            if item.detection.direction != "未知"
        ]
        return len(set(directions)) <= 1

    def _possible_merges(self, clusters: list[EventCluster]) -> list[tuple[str, str, str]]:
        ordered = sorted(clusters, key=lambda c: c.time_start)
        edges: list[tuple[str, str, str]] = []
        for i, earlier in enumerate(ordered):
            for later in ordered[i + 1 :]:
                reason = self._can_chain(earlier, later)
                if reason:
                    edges.append((earlier.cluster_id, later.cluster_id, reason))
        return edges

    def _enumerate_scenarios(
        self, clusters: dict[str, EventCluster]
    ) -> tuple[list[dict[str, EventCluster]], bool]:
        """枚举“可能合并”边的取舍情景。

        带非空事件组的簇本身必然成立，但其与空组记录之间仍可能存在
        重触发/路径合并，因此合并边对所有簇计算，只是带事件组的簇不
        会被拆回单条记录。返回 (情景列表, 是否因上限截断)。
        """
        edges = self._possible_merges(list(clusters.values()))
        base_certain = {k: c for k, c in clusters.items() if c.certain}

        scenarios: list[dict[str, EventCluster]] = []
        truncated = False
        for accepted_mask in product((False, True), repeat=len(edges)):
            if len(scenarios) >= SCENARIO_CAP:
                truncated = True
                break
            merged: dict[str, EventCluster] = {k: v for k, v in base_certain.items()}
            pending = {k: c for k, c in clusters.items() if not c.certain}
            for accept, (a, b, reason) in zip(accepted_mask, edges):
                if not accept:
                    continue
                left = self._find_owner(merged, pending, a)
                right = self._find_owner(merged, pending, b)
                if left is None or right is None or left == right:
                    continue
                keeper = merged.get(left) or pending.get(left)
                other = merged.get(right) or pending.get(right)
                combined = EventCluster(
                    cluster_id=f"{keeper.cluster_id}+{other.cluster_id}",
                    species=keeper.species,
                    sightings=[*keeper.sightings, *other.sightings],
                    merge_evidence=[*keeper.merge_evidence, *other.merge_evidence, reason],
                    certain=keeper.certain and other.certain,
                )
                merged.pop(left, None)
                merged.pop(right, None)
                pending.pop(left, None)
                pending.pop(right, None)
                merged[combined.cluster_id] = combined
            for cluster in pending.values():
                merged[cluster.cluster_id] = cluster
            scenarios.append(merged)

        # 去重（不同取舍可能产生同一划分）
        unique: dict[frozenset, dict[str, EventCluster]] = {}
        for scenario in scenarios:
            signature = frozenset(
                (cid, tuple(s.detection.event_id for s in c.sightings))
                for cid, c in scenario.items()
            )
            unique.setdefault(signature, scenario)
        return list(unique.values()), truncated

    @staticmethod
    def _find_owner(
        merged: dict[str, EventCluster], pending: dict[str, EventCluster], cluster_id: str
    ) -> Optional[str]:
        if cluster_id in pending:
            return cluster_id
        for cid, cluster in merged.items():
            if cluster_id in cid.split("+"):
                return cid
        return None

    # -- 路线解释 --------------------------------------------------------
    def interpret_routes(self, cluster: EventCluster) -> list[RouteInterpretation]:
        """枚举与观察相容的路线解释。

        相容性采用紧口径：路线必须以观察到的区段为骨架，且首段和末段
        都在观察序列中——没有任何观察的延伸段不得凭空加入路线。动物
        从骨架之后继续走向未覆盖区域的可能性，只在 ``reasons`` 中作为
        未决问题保留，不生成伪路线。
        """
        day = cluster.time_start.date()
        observed = cluster.observed_segments
        directions = {
            item.detection.direction
            for item in cluster.sightings
            if item.detection.direction != "未知"
        }
        interpretations: list[RouteInterpretation] = []
        for source in sorted(self.net.patches()):
            for target in sorted(self.net.patches()):
                if source == target:
                    continue
                for path in self.net.simple_paths(source, target, day=day):
                    if not self._tight_match(observed, path):
                        continue
                    proven, unproven, reasons = self._grade_path(path, cluster, day)
                    if directions:
                        reasons.append(
                            f"方向记录一致：{sorted(directions)[0]}（无坐标，不定向到具体端点）"
                        )
                    if unproven:
                        reasons.append("路线含仅见进入或未观察区段，完整穿越尚不可证")
                    status = self._overall_status(proven, unproven, path)
                    interpretations.append(
                        RouteInterpretation(
                            patches=tuple(self._patches_of(path, source, target, day)),
                            segments=path,
                            status=status,
                            proven_segments=tuple(proven),
                            unproven_segments=tuple(unproven),
                            reasons=reasons,
                        )
                    )
        return self._dedupe_interpretations(interpretations)

    @staticmethod
    def _tight_match(observed: tuple[str, ...], path: tuple[str, ...]) -> bool:
        """观察区段按顺序出现在路径中，且与路径首尾对齐。"""
        if not observed:
            return False
        if path[0] != observed[0] or path[-1] != observed[-1]:
            return False
        return EvidenceReview._is_subsequence(observed, path)

    def _grade_path(
        self, path: tuple[str, ...], cluster: EventCluster, day
    ) -> tuple[list[str], list[str], list[str]]:
        ordered = sorted(cluster.sightings, key=lambda x: x.detection.time)
        by_segment: dict[str, list[CoveredSighting]] = {}
        for item in ordered:
            by_segment.setdefault(item.segment_id, []).append(item)

        proven: list[str] = []
        unproven: list[str] = []
        reasons: list[str] = []
        for index, segment in enumerate(path):
            sightings = by_segment.get(segment, [])
            kinds = {s.detection.sighting for s in sightings}
            if Sighting.PASS in kinds:
                proven.append(segment)
                reasons.append(f"{segment} 记录到“经过”，完成穿越可证")
            elif Sighting.EXIT in kinds:
                proven.append(segment)
                reasons.append(f"{segment} 记录到“离开”，结构已走完（进入侧未知）")
            elif Sighting.ENTRY in kinds:
                downstream_seen = self._has_downstream_observation(path, index, by_segment)
                if downstream_seen:
                    proven.append(segment)
                    reasons.append(f"{segment} 仅见“进入”，但下游区段再出现，本区段穿越可证")
                else:
                    unproven.append(segment)
                    reasons.append(f"{segment} 仅见“进入”，完成穿越不可证")
            else:
                unproven.append(segment)
                reasons.append(f"{segment} 无观察记录，是否使用未知")
        return proven, unproven, reasons

    @staticmethod
    def _has_downstream_observation(
        path: tuple[str, ...], index: int, by_segment: dict[str, list]
    ) -> bool:
        return any(by_segment.get(segment) for segment in path[index + 1 :])

    @staticmethod
    def _overall_status(proven: list[str], unproven: list[str], path) -> str:
        if not unproven:
            return "proven"
        if proven:
            return "ambiguous"
        # 完全没有完成证据：区分入口型与出口型
        return "entry_only"

    @staticmethod
    def _is_subsequence(observed: tuple[str, ...], path: tuple[str, ...]) -> bool:
        cursor = 0
        for segment in observed:
            while cursor < len(path) and path[cursor] != segment:
                cursor += 1
            if cursor >= len(path):
                return False
            cursor += 1
        return True

    def _patches_of(self, path: tuple[str, ...], source: str, target: str, day) -> list[str]:
        patches = [source]
        current = source
        for segment in path:
            link = self.net.link_on(segment, day)
            nxt = link.to_patch if link.from_patch == current else link.from_patch
            patches.append(nxt)
            current = nxt
        assert current == target
        return patches

    @staticmethod
    def _dedupe_interpretations(
        interpretations: list[RouteInterpretation],
    ) -> list[RouteInterpretation]:
        seen: set[tuple] = set()
        unique: list[RouteInterpretation] = []
        for item in interpretations:
            key = (item.patches, item.status, item.proven_segments, item.unproven_segments)
            if key not in seen:
                seen.add(key)
                unique.append(item)
        return sorted(unique, key=lambda x: (x.patches, x.status))

    # -- 斑块对用量 ------------------------------------------------------
    def pair_usage(self, scenarios: list[dict[str, EventCluster]]) -> list[PairUsage]:
        """计算每个（无序斑块对, 物种）的可证范围。

        - ``provable_min``：在 *所有* 情景下都有完成穿越证据的次数
         （即只计状态为 proven 的相容路线，且按情景取最小）；
        - ``compatible_max``：任一情景下可与证据相容的最大次数
          （含 entry_only/ambiguous 路线）；
        - 每个事件在一个情景内对一个斑块对至多计一次；互斥路线解释
          保留在 ``scenarios`` 中，绝不跨解释求和。
        """
        per_key: dict[
            tuple[tuple[str, str], str],
            list[tuple[int, int, set[str], dict]],
        ] = {}
        for scenario in scenarios:
            proven_local: dict[tuple[tuple[str, str], str], tuple[int, set[str], dict]] = {}
            compat_local: dict[tuple[tuple[str, str], str], tuple[int, set[str], dict]] = {}
            for cluster in scenario.values():
                routes = self.interpret_routes(cluster)
                # 一个斑块对在本事件的多条解释中只取一次
                pair_grades: dict[tuple[str, str], str] = {}
                for route in routes:
                    pair = tuple(sorted((route.patches[0], route.patches[-1])))
                    grade = "proven" if route.status == "proven" else route.status
                    if pair_grades.get(pair) != "proven":
                        pair_grades[pair] = grade
                for pair, grade in pair_grades.items():
                    key = (pair, cluster.species)
                    bucket = proven_local if grade == "proven" else compat_local
                    count, ids, statuses = bucket.get(key, (0, set(), {}))
                    bucket[key] = (
                        count + 1,
                        {*ids, cluster.cluster_id},
                        {**statuses, cluster.cluster_id: grade},
                    )
            keys = set(proven_local) | set(compat_local)
            for key in keys:
                p_count, p_ids, p_status = proven_local.get(key, (0, set(), {}))
                c_count, c_ids, c_status = compat_local.get(key, (0, set(), {}))
                per_key.setdefault(key, []).append(
                    (
                        p_count,
                        p_count + c_count,
                        p_ids | c_ids,
                        {**c_status, **p_status},
                    )
                )

        usages: list[PairUsage] = []
        for (pair, species), rows in sorted(per_key.items()):
            cluster_ids = tuple(sorted({cid for row in rows for cid in row[2]}))
            usages.append(
                PairUsage(
                    pair=pair,
                    species=species,
                    provable_min=min(row[0] for row in rows),
                    compatible_max=max(row[1] for row in rows),
                    contributing_clusters=cluster_ids,
                    scenarios=[
                        {
                            "provable_crossings": provable,
                            "compatible_crossings": compatible,
                            "clusters": sorted(ids),
                            "route_status": statuses,
                        }
                        for provable, compatible, ids, statuses in rows
                    ],
                    route_status={
                        cid: grade for row in rows for cid, grade in row[3].items()
                    },
                )
            )
        return usages

    # -- 总入口 ----------------------------------------------------------
    def review(self) -> EvidenceReport:
        base_clusters, covered = self._base_clusters()
        scenarios, truncated = self._enumerate_scenarios(base_clusters)
        # 报告主体展示“最保守合并”情景（所有可能合并都接受）
        merged_view = max(scenarios, key=lambda s: sum(len(c.sightings) for c in s.values()) / len(s))
        display_clusters = sorted(merged_view.values(), key=lambda c: c.time_start)
        return EvidenceReport(
            clusters=display_clusters,
            pair_usage=self.pair_usage(scenarios),
            negatives=self.negatives(),
            coverage_gaps=self.coverage_gaps(),
            findings=self.data_quality_findings(covered),
            scenario_count=len(scenarios),
            scenario_truncated=truncated,
        )
