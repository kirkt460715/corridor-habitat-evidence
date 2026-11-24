"""斑块—通道图与封闭/有效期路径规则。"""

from datetime import date

from tests._engine import EngineTest


class NetworkTests(EngineTest):
    def test_full_network_paths_before_construction(self):
        day = date(2025, 4, 9)
        paths_h1_h3 = self.network.simple_paths("H-01", "H-03", day=day)
        self.assertIn(("S-03",), paths_h1_h3)
        self.assertIn(("S-01", "S-02"), paths_h1_h3)

    def test_validity_window_excludes_expired_links(self):
        # S-02 只在 2025-04~2025-06 有效
        self.assertEqual(
            self.network.simple_paths(
                "H-01", "H-03", day=date(2025, 7, 1), closed=("S-03",)
            ),
            [],
        )

    def test_closed_segment_removes_only_paths_using_it(self):
        day = date(2025, 6, 5)
        remaining = self.network.simple_paths(
            "H-01", "H-03", day=day, closed=("S-03",)
        )
        self.assertEqual(remaining, [("S-01", "S-02")])
        self.assertTrue(
            self.network.is_connected("H-01", "H-03", day=day, closed=("S-02",))
        )
        # 同时封闭两条候选通道即断开
        self.assertFalse(
            self.network.is_connected(
                "H-01", "H-03", day=day, closed=("S-02", "S-03")
            )
        )

    def test_graph_is_undirected(self):
        day = date(2025, 5, 2)
        forward = self.network.simple_paths("H-01", "H-02", day=day)
        backward = self.network.simple_paths("H-02", "H-01", day=day)
        # 无向图：反向路径是同区段序列的逆序
        self.assertEqual(backward, [tuple(reversed(p)) for p in forward])
        self.assertIn(("S-01",), forward)

    def test_segment_between_neighbors(self):
        self.assertEqual(self.network.segment_between("H-02", "H-03"), "S-02")
        # H-01 与 H-03 既有直接通道 S-03，也有经 H-02 的两跳路线
        self.assertEqual(self.network.segment_between("H-01", "H-03"), "S-03")
        self.assertIsNone(self.network.segment_between("H-01", "不存在"))
