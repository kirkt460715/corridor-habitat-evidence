"""命令行入口：证据复核、时期对比、方案审查、现场交割。

用法（在仓库根目录）::

    python3 -m corridor.cli evidence --data data
    python3 -m corridor.cli periods --data data --periods Q-01 Q-02
    python3 -m corridor.cli proposals --data data
    python3 -m corridor.cli handover --data data --proposal A
    python3 -m corridor.cli work --data data --proposal A

``--json`` 输出机读结构，默认输出中文复核报告。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path

from .events import EvidenceReview
from .handover import Handover
from .loading import load_dataset
from .network import Network
from .periods import ParallelPeriods
from .planning import Planner


def _json_default(obj):
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, tuple):
        return list(obj)
    raise TypeError(f"不可序列化对象: {type(obj)!r}")


def _emit(payload, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default))
        return
    print(_render_text(payload))


def _render_text(payload) -> str:
    lines: list[str] = []

    def walk(node, indent=0):
        pad = "  " * indent
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, (dict, list)):
                    lines.append(f"{pad}{key}:")
                    walk(value, indent + 1)
                else:
                    lines.append(f"{pad}{key}: {value}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                if isinstance(value, (dict, list)):
                    lines.append(f"{pad}- #{index + 1}")
                    walk(value, indent + 1)
                else:
                    lines.append(f"{pad}- {value}")
        else:
            lines.append(f"{pad}{node}")

    walk(payload)
    return "\n".join(lines)


def _build_context(data_dir: Path):
    dataset = load_dataset(data_dir)
    network = Network(dataset)
    evidence = EvidenceReview(dataset, network)
    parallels = ParallelPeriods(dataset)
    planner = Planner(dataset, network, evidence)
    handover = Handover(dataset, network, evidence, planner, parallels)
    return dataset, network, evidence, parallels, planner, handover


def cmd_evidence(args) -> dict:
    dataset, network, evidence, *_ = _build_context(Path(args.data))
    report = evidence.review()
    return {
        "事件分簇与路线解释": [
            {
                "簇": cluster.cluster_id,
                "物种": cluster.species,
                "观察区段": list(cluster.observed_segments),
                "合并依据": cluster.merge_evidence or ["独立记录，未与其他触发合并"],
                "相容路线": [
                    {
                        "路线": list(route.patches),
                        "区段": list(route.segments),
                        "状态": route.status,
                        "已证区段": list(route.proven_segments),
                        "未证区段": list(route.unproven_segments),
                        "说明": route.reasons,
                    }
                    for route in evidence.interpret_routes(cluster)
                ],
            }
            for cluster in report.clusters
        ],
        "斑块对可证范围": [
            {
                "斑块对": list(usage.pair),
                "物种": usage.species,
                "可证下限": usage.provable_min,
                "相容上界": usage.compatible_max,
                "相关事件簇": list(usage.contributing_clusters),
                "互斥情景": usage.scenarios,
            }
            for usage in report.pair_usage
        ],
        "阴性观察（仅有检测机会的覆盖内）": [
            {
                "努力区间": n.effort_id,
                "区段": n.segment_id,
                "物种": n.species,
                "强度": n.strength,
                "口径": n.observable.value,
            }
            for n in report.negatives
        ],
        "覆盖缺口（无记录不作阴性）": [
            {"努力区间": g.effort_id, "区段": g.segment_id, "原因": g.reason}
            for g in report.coverage_gaps
        ],
        "数据质量提示": [
            {"代码": f.code, "说明": f.message, "记录": list(f.refs)}
            for f in report.findings
        ],
        "情景数": report.scenario_count,
    }


def cmd_periods(args) -> dict:
    _, _, _, parallels, *_ = _build_context(Path(args.data))
    period_ids = args.periods or parallels.period_ids()
    return {
        "时期对比": [
            {
                "时期A": c.period_a,
                "时期B": c.period_b,
                "季节": c.season,
                "区段": c.segment_id,
                "可比": c.comparable,
                "每百小时强度A": c.rate_a,
                "每百小时强度B": c.rate_b,
                "强度变化": c.rate_change_pct,
                "原始计数A": c.raw_count_a,
                "原始计数B": c.raw_count_b,
                "说明": c.reasons,
            }
            for c in parallels.compare_period_sets(period_ids)
        ]
    }


def cmd_proposals(args) -> dict:
    dataset, network, evidence, parallels, planner, _ = _build_context(Path(args.data))
    reviews = planner.review_all()
    return {
        "方案审查": [
            {
                "方案": review.proposal_id,
                "总体结论": review.overall_decision,
                "资源": {
                    "月份": review.resources.period,
                    "用人天": review.resources.crew_days_used,
                    "预算元": review.resources.budget_used,
                    "满足上限": review.resources.within_limits,
                    "说明": review.resources.notes,
                },
                "分期": [
                    {
                        "阶段": stage.stage,
                        "日期": [stage.start_date, stage.end_date],
                        "封闭": list(stage.closed_segments),
                        "结论": stage.decision,
                        "承诺核对": [
                            {
                                "承诺": check.requirement_id,
                                "斑块对": list(check.pair),
                                "物种": check.species,
                                "可走路径": [list(p) for p in check.feasible_paths],
                                "绕道登记与图一致": check.detour_matches_graph,
                                "未辨清区段": check.unproven_segments,
                                "路径说明": check.detail,
                            }
                            for check in stage.commitment_checks
                        ],
                        "共用容量": [
                            {
                                "通道": cap.shared_passage,
                                "合并需求": cap.combined_demand,
                                "夜间上限": cap.capacity_per_night,
                                "需求构成": cap.demand_by_requirement,
                                "通过": cap.ok,
                            }
                            for cap in stage.capacity_checks
                        ],
                        "同期管护": stage.work_overlaps,
                        "未决事实": stage.unresolved_facts,
                        "可先行准备": stage.preparatory_work,
                        "阻断原因": stage.blocking_reasons,
                    }
                    for stage in review.stages
                ],
            }
            for review in reviews
        ],
        "有限设备布置": [
            _survey_payload(planner, period)
            for period in sorted(dataset.work_limits)
        ],
    }


def _survey_payload(planner: Planner, period: str) -> dict:
    plan = planner.plan_surveys(period)
    return {
        "月份": period,
        "设备日预算": plan.device_day_budget,
        "已用设备日": plan.device_days_used,
        "选用窗口": [
            {
                "窗口": chosen.slot.slot_id,
                "设备": chosen.slot.device_id,
                "区段": chosen.slot.segment_id,
                "口径": chosen.slot.observable.value,
                "告警": chosen.warnings,
                "回答的问题": chosen.resolves,
            }
            for chosen in plan.selected_slots
        ],
        "已覆盖问题": plan.unresolved_questions_covered,
        "部分回答": plan.partial_questions,
        "仍未辨清": plan.unresolved_questions_remaining,
        "硬不可行窗口": [
            {"窗口": f.slot.slot_id, "原因": f.reasons} for f in plan.infeasible_slots
        ],
    }


def cmd_handover(args) -> dict:
    *_, handover = _build_context(Path(args.data))
    package = handover.build_package(args.proposal)
    return {
        "方案": package.proposal_id,
        "断点区段": package.breakpoint_segment,
        "总体交割结论": package.overall,
        "斑块对交割项": [
            {
                "承诺": item.requirement_id,
                "斑块对": list(item.pair),
                "物种": item.species,
                "改造前路径": [list(p) for p in item.pre_paths],
                "改造后路径": [list(p) for p in item.post_paths],
                "施工期保留路径": [list(p) for p in item.retained_paths],
                "对照期": list(item.compare_periods),
                "对照说明": item.comparison_notes,
                "状态": item.status,
                "原因": item.reasons,
            }
            for item in package.items
        ],
        "切换夜覆盖": [
            {"日期": n.switch_date, "区段": n.segment_id, "有检测机会": n.covered, "说明": n.detail}
            for n in package.switch_nights
        ],
        "可放行步骤": package.releasable_steps,
        "仍应保留的通行路径": package.retained_routes,
        "复核未过原因": package.failed_reasons,
        "接续观测安排": [
            {
                "区段": f.segment_id,
                "原因": f.reason,
                "最早窗口": f.earliest_window,
                "要求口径": f.required_observable.value,
            }
            for f in package.follow_ups
        ],
    }


def cmd_work(args) -> dict:
    *_, handover = _build_context(Path(args.data))
    return handover.lookup_work_arrangement(args.proposal)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="corridor", description="生态廊道复核与交割工具")
    parser.add_argument("--data", default="data", help="数据目录（默认 data）")
    parser.add_argument("--json", action="store_true", help="输出机读 JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("evidence", help="廊道证据复核")

    periods = sub.add_parser("periods", help="平行时期对比")
    periods.add_argument("--periods", nargs="*", help="限定时期编号，如 Q-01 Q-02")

    sub.add_parser("proposals", help="改造方案分期审查与设备布置")

    handover = sub.add_parser("handover", help="单断点改造交割包")
    handover.add_argument("--proposal", required=True)

    work = sub.add_parser("work", help="按一次工程安排查询可执行信息")
    work.add_argument("--proposal", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "evidence": cmd_evidence,
        "periods": cmd_periods,
        "proposals": cmd_proposals,
        "handover": cmd_handover,
        "work": cmd_work,
    }
    payload = handlers[args.command](args)
    _emit(payload, args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
