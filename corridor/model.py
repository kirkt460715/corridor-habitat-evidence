"""领域数据类与受控词表。

所有资料均为合成资料：没有经纬度，地点以斑块编号表示，通道以区段编号
表示。时间精确到分钟（``datetime``），通道与工程日期为日历日期
（``date``），链路有效期为月份粒度（取该月首日/末日）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# 受控词表
# ---------------------------------------------------------------------------
class Sighting(str, Enum):
    """观察到的是什么，含义不同，不能互相替代。"""

    ENTRY = "进入"        # 只看到动物进入通道，不等于完成穿越
    EXIT = "离开"         # 看到动物从通道出来，不自带来自哪个斑块
    PASS = "经过"         # 双向经过型覆盖下看到完整经过，可作完成穿越证据
    NONE = ""             # 占位，不应出现在真实记录中

    @classmethod
    def parse(cls, raw: str) -> "Sighting":
        for member in cls:
            if member.value == raw:
                return member
        raise ValueError(f"未知 sighting 取值: {raw!r}")


class Observable(str, Enum):
    """设备/巡查在一个努力区间内实际能看到的对象。"""

    BOTH = "双向经过"     # 可看到完整经过，阴性观察可直接落到通道上
    ENTRY = "入口"        # 只能看到多个通道共同入口，不能把一次穿越算到每条支路
    EXIT = "出口"         # 只能看到离开，不自带进入侧
    DEPARTURE = "离开"    # 出口侧的同义记录口径（平行期汇总使用）
    NONE = "无覆盖"       # 该区间没有检测机会，无记录不构成阴性观察

    @property
    def is_terminal_only(self) -> bool:
        """只覆盖通道一端（入口或出口），阴性观察只能是部分强度。"""

        return self in (Observable.ENTRY, Observable.EXIT, Observable.DEPARTURE)

    @classmethod
    def parse(cls, raw: str) -> "Observable":
        for member in cls:
            if member.value == raw:
                return member
        raise ValueError(f"未知 observable 取值: {raw!r}")


class CaptureKind(str, Enum):
    EVENT_CAPTURE_PHOTO = "照片"
    EVENT_CAPTURE_PATROL = "巡护"
    EVENT_CAPTURE_OFF = "停机"

    @classmethod
    def parse(cls, raw: str) -> "CaptureKind":
        for member in cls:
            if member.value == raw:
                return member
        raise ValueError(f"未知 event_capture 取值: {raw!r}")


# ---------------------------------------------------------------------------
# 输入记录
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Patch:
    patch_id: str
    area_ha: float
    vegetation: str
    survey_period: str


@dataclass(frozen=True)
class Link:
    """一条通道区段在某月份有效区间内连接两个稳定斑块。"""

    link_id: str
    from_patch: str
    to_patch: str
    segment_id: str
    barrier: str
    valid_from: date   # 该月首日
    valid_to: date     # 该月末日


@dataclass(frozen=True)
class Device:
    device_id: str
    segment_id: str
    active_from: date
    active_to: date
    uptime_ratio: float

    def available_on(self, day: date) -> bool:
        return self.active_from <= day <= self.active_to


@dataclass(frozen=True)
class Detection:
    event_id: str
    device_id: str
    time: datetime
    species: str
    event_group: str          # 非空且相同 = 已被现场认定为同一事件的旁证
    sighting: Sighting
    direction: str            # 向东 / 向西 / 未知


@dataclass(frozen=True)
class EffortWindow:
    """实际可观察的区间。设备台账只是大致部署期，以此处为准。"""

    effort_id: str
    device_id: str
    from_at: datetime
    to_at: datetime
    segment_id: str
    observable: Observable
    event_capture: CaptureKind

    @property
    def has_opportunity(self) -> bool:
        return self.observable is not Observable.NONE

    def covers(self, moment: datetime) -> bool:
        return self.from_at <= moment <= self.to_at


@dataclass(frozen=True)
class Work:
    work_id: str
    segment_id: str
    change: str
    start_date: date
    end_date: date

    def overlaps(self, start: date, end: date) -> bool:
        return self.start_date <= end and start <= self.end_date


@dataclass(frozen=True)
class ProposalStage:
    proposal_id: str
    stage: int
    closed_segments: tuple[str, ...]
    detour_segments: tuple[str, ...]
    start_date: date
    end_date: date
    reserved_demand: int
    crew_days: int
    cost_yuan: int


@dataclass(frozen=True)
class Commitment:
    """合同指定要保住的斑块对连接；同一 shared_passage 的多行共享上限。"""

    requirement_id: str
    from_patch: str
    to_patch: str
    species: str
    period: str               # YYYY-MM
    required_transits: int
    shared_passage: str
    capacity_per_night: int


@dataclass(frozen=True)
class SurveySlot:
    """调查表中的可选窗口；一台设备同一时段只能布设一次。"""

    slot_id: str
    device_id: str
    segment_id: str
    observable: Observable
    from_at: datetime
    to_at: datetime
    crew_days: int


@dataclass(frozen=True)
class ParallelPeriodRow:
    period_id: str
    season: str
    period_start: date
    period_end: date
    segment_id: str
    effort_hours: float
    observable: Optional[Observable]   # 0 小时且“无覆盖”时为 NONE
    known_group_count: Optional[int]


@dataclass(frozen=True)
class WorkLimit:
    period: str
    crew_days: int
    budget_yuan: int
    available_device_days: int
    handover_compare_period: tuple[str, ...]


# ---------------------------------------------------------------------------
# 汇总数据集
# ---------------------------------------------------------------------------
@dataclass
class Dataset:
    patches: dict[str, Patch] = field(default_factory=dict)
    links: list[Link] = field(default_factory=list)
    devices: dict[str, Device] = field(default_factory=dict)
    detections: list[Detection] = field(default_factory=list)
    effort: list[EffortWindow] = field(default_factory=list)
    works: list[Work] = field(default_factory=list)
    stages: list[ProposalStage] = field(default_factory=list)
    commitments: list[Commitment] = field(default_factory=list)
    survey_slots: list[SurveySlot] = field(default_factory=list)
    parallels: list[ParallelPeriodRow] = field(default_factory=list)
    work_limits: dict[str, WorkLimit] = field(default_factory=dict)
