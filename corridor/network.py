"""斑块—通道图与可走路径枚举。

通道是无向的（动物两个方向都可能走），但带月份有效期。施工封闭集在
查询时临时扣除，不改动原始链路。
"""

from __future__ import annotations

from datetime import date
from typing import Iterable, Optional

from .model import Dataset, Link


class Network:
    def __init__(self, dataset: Dataset):
        self._dataset = dataset
        self._adjacency: dict[str, dict[str, Link]] = {}
        for link in dataset.links:
            self._adjacency.setdefault(link.from_patch, {})[link.to_patch] = link
            self._adjacency.setdefault(link.to_patch, {})[link.from_patch] = link

    # -- 基础查询 --------------------------------------------------------
    def patches(self) -> set[str]:
        return set(self._adjacency)

    def active_links(self, day: date) -> list[Link]:
        """当天在有效期内的通道。"""
        return [link for link in self._dataset.links if link.valid_from <= day <= link.valid_to]

    def link_on(self, segment_id: str, day: date) -> Optional[Link]:
        for link in self._dataset.links:
            if link.segment_id == segment_id and link.valid_from <= day <= link.valid_to:
                return link
        return None

    def segment_between(self, patch_a: str, patch_b: str) -> Optional[str]:
        """两个斑块是否直接相邻，返回共用区段编号。"""
        link = self._adjacency.get(patch_a, {}).get(patch_b)
        return link.segment_id if link else None

    # -- 路径 ------------------------------------------------------------
    def simple_paths(
        self,
        source: str,
        target: str,
        *,
        day: date,
        closed: Iterable[str] = (),
    ) -> list[tuple[str, ...]]:
        """枚举 source→target 的所有无环可走路径（区段序列）。

        - 只使用 ``day`` 当天有效的链路；
        - ``closed`` 中的区段视为临时封闭，不可进入；
        - 返回区段编号元组，按区段名字典序稳定输出。
        """
        closed_set = set(closed)
        results: list[tuple[str, ...]] = []

        def walk(current: str, visited: set[str], segments: list[str]) -> None:
            if current == target:
                results.append(tuple(segments))
                return
            for neighbor in sorted(self._adjacency.get(current, {})):
                link = self._adjacency[current][neighbor]
                if neighbor in visited:
                    continue
                if not (link.valid_from <= day <= link.valid_to):
                    continue
                if link.segment_id in closed_set:
                    continue
                visited.add(neighbor)
                segments.append(link.segment_id)
                walk(neighbor, visited, segments)
                segments.pop()
                visited.remove(neighbor)

        walk(source, {source}, [])
        return results

    def is_connected(
        self,
        source: str,
        target: str,
        *,
        day: date,
        closed: Iterable[str] = (),
    ) -> bool:
        return bool(self.simple_paths(source, target, day=day, closed=closed))

    def detour_paths(
        self,
        source: str,
        target: str,
        *,
        day: date,
        closed: Iterable[str] = (),
        detour_segments: Iterable[str] = (),
    ) -> list[tuple[str, ...]]:
        """可走路径中，只保留由方案登记的绕道区段组成的路径。

        方案声称的绕道必须与实际可走路径一致；登记了但图上走不通的
        绕道不会出现在结果里（由规划层另作一致性检查）。
        """
        detour_set = set(detour_segments)
        return [
            path
            for path in self.simple_paths(source, target, day=day, closed=closed)
            if set(path) <= detour_set
        ]
