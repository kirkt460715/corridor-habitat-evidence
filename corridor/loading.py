"""读取 ``data/`` 下的合成 CSV 并解析为 :mod:`corridor.model` 数据类。

只依赖标准库。月份有效期 ``YYYY-MM`` 展开为该月首日至末日。
"""

from __future__ import annotations

import csv
import calendar
from datetime import date, datetime
from pathlib import Path

from .model import (
    CaptureKind,
    Commitment,
    Dataset,
    Detection,
    Device,
    EffortWindow,
    Link,
    Observable,
    ParallelPeriodRow,
    Patch,
    ProposalStage,
    Sighting,
    SurveySlot,
    Work,
    WorkLimit,
)


def _rows(directory: Path, filename: str):
    with (directory / filename).open(encoding="utf-8", newline="") as handle:
        yield from csv.DictReader(handle)


def _month_bounds(raw: str) -> tuple[date, date]:
    year, month = (int(part) for part in raw.split("-"))
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def _date(raw: str) -> date:
    return date.fromisoformat(raw)


def _dt(raw: str) -> datetime:
    return datetime.fromisoformat(raw)


def _split_pipe(raw: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in raw.split("|") if part.strip())


def load_dataset(directory: str | Path) -> Dataset:
    directory = Path(directory)
    data = Dataset()

    for row in _rows(directory, "patches.csv"):
        data.patches[row["patch_id"]] = Patch(
            patch_id=row["patch_id"],
            area_ha=float(row["area_ha"]),
            vegetation=row["vegetation"],
            survey_period=row["survey_period"],
        )

    for row in _rows(directory, "links.csv"):
        valid_from = _month_bounds(row["valid_from"])[0]
        valid_to = _month_bounds(row["valid_to"])[1]
        data.links.append(
            Link(
                link_id=row["link_id"],
                from_patch=row["from_patch"],
                to_patch=row["to_patch"],
                segment_id=row["segment_id"],
                barrier=row["barrier"],
                valid_from=valid_from,
                valid_to=valid_to,
            )
        )

    for row in _rows(directory, "devices.csv"):
        data.devices[row["device_id"]] = Device(
            device_id=row["device_id"],
            segment_id=row["segment_id"],
            active_from=_date(row["active_from"]),
            active_to=_date(row["active_to"]),
            uptime_ratio=float(row["uptime_ratio"]),
        )

    for row in _rows(directory, "detections.csv"):
        data.detections.append(
            Detection(
                event_id=row["event_id"],
                device_id=row["device_id"],
                time=_dt(row["time"]),
                species=row["species"],
                event_group=row["event_group"],
                sighting=Sighting.parse(row["sighting"]),
                direction=row["direction"],
            )
        )

    for row in _rows(directory, "effort.csv"):
        data.effort.append(
            EffortWindow(
                effort_id=row["effort_id"],
                device_id=row["device_id"],
                from_at=_dt(row["from_at"]),
                to_at=_dt(row["to_at"]),
                segment_id=row["segment_id"],
                observable=Observable.parse(row["observable"]),
                event_capture=CaptureKind.parse(row["event_capture"]),
            )
        )

    for row in _rows(directory, "works.csv"):
        data.works.append(
            Work(
                work_id=row["work_id"],
                segment_id=row["segment_id"],
                change=row["change"],
                start_date=_date(row["start_date"]),
                end_date=_date(row["end_date"]),
            )
        )

    for row in _rows(directory, "proposals.csv"):
        data.stages.append(
            ProposalStage(
                proposal_id=row["proposal_id"],
                stage=int(row["stage"]),
                closed_segments=_split_pipe(row["closed_segments"]),
                detour_segments=_split_pipe(row["detour_segments"]),
                start_date=_date(row["start_date"]),
                end_date=_date(row["end_date"]),
                reserved_demand=int(row["reserved_demand"]),
                crew_days=int(row["crew_days"]),
                cost_yuan=int(row["cost_yuan"]),
            )
        )

    for row in _rows(directory, "commitments.csv"):
        data.commitments.append(
            Commitment(
                requirement_id=row["requirement_id"],
                from_patch=row["from_patch"],
                to_patch=row["to_patch"],
                species=row["species"],
                period=row["period"],
                required_transits=int(row["required_transits"]),
                shared_passage=row["shared_passage"],
                capacity_per_night=int(row["capacity_per_night"]),
            )
        )

    for row in _rows(directory, "survey_slots.csv"):
        data.survey_slots.append(
            SurveySlot(
                slot_id=row["slot_id"],
                device_id=row["device_id"],
                segment_id=row["segment_id"],
                observable=Observable.parse(row["observable"]),
                from_at=_dt(row["from_at"]),
                to_at=_dt(row["to_at"]),
                crew_days=int(row["crew_days"]),
            )
        )

    for row in _rows(directory, "effort_parallels.csv"):
        data.parallels.append(
            ParallelPeriodRow(
                period_id=row["period_id"],
                season=row["season"],
                period_start=_date(row["period_start"]),
                period_end=_date(row["period_end"]),
                segment_id=row["segment_id"],
                effort_hours=float(row["effort_hours"]),
                observable=Observable.parse(row["observable"]),
                known_group_count=int(row["known_group_count"]) if row["known_group_count"] else None,
            )
        )

    for row in _rows(directory, "work_limits.csv"):
        data.work_limits[row["period"]] = WorkLimit(
            period=row["period"],
            crew_days=int(row["crew_days"]),
            budget_yuan=int(row["budget_yuan"]),
            available_device_days=int(row["available_device_days"]),
            handover_compare_period=_split_pipe(row["handover_compare_period"]),
        )

    return data
