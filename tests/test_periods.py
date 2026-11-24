"""平行时期对比：同季节、同口径、努力相近才可比。"""

from corridor.periods import MIN_EFFORT_HOURS
from tests._engine import EngineTest


class ParallelPeriodTests(EngineTest):
    def test_same_season_comparable_when_effort_and_observable_match(self):
        # Q-01 vs Q-02，S-01：64h vs 61h，同为双向经过
        comparison = self.parallels.compare("Q-01", "Q-02", "S-01")
        self.assertTrue(comparison.comparable)
        self.assertIsNotNone(comparison.rate_change_pct)
        self.assertEqual(comparison.raw_count_a, 4)
        self.assertEqual(comparison.raw_count_b, 3)

    def test_low_effort_period_is_not_comparable(self):
        # S-02：55h vs 12h，努力差距过大且 Q-02 低于最低努力
        comparison = self.parallels.compare("Q-01", "Q-02", "S-02")
        self.assertFalse(comparison.comparable)
        self.assertTrue(any("努力" in reason for reason in comparison.reasons))

    def test_no_coverage_period_cannot_show_decline(self):
        # Q-03 的 S-02 为 0 小时无覆盖
        comparison = self.parallels.compare("Q-01", "Q-03", "S-02")
        self.assertFalse(comparison.comparable)
        self.assertTrue(any("无检测机会" in reason for reason in comparison.reasons))
        self.assertIsNone(comparison.rate_b)

    def test_different_seasons_are_not_compared(self):
        comparison = self.parallels.compare("Q-02", "Q-03", "S-01")
        self.assertFalse(comparison.comparable)
        self.assertTrue(any("季节" in reason for reason in comparison.reasons))

    def test_exit_and_departure_wording_are_same_observable(self):
        # S-03 口径为“离开”，与“出口”同口径；Q-01/Q-02 努力 62h vs 60h
        comparison = self.parallels.compare("Q-01", "Q-02", "S-03")
        self.assertTrue(comparison.comparable)

    def test_rate_normalizes_effort(self):
        comparison = self.parallels.compare("Q-01", "Q-02", "S-01")
        self.assertAlmostEqual(comparison.rate_a, 4 / 64 * 100)
        self.assertAlmostEqual(comparison.rate_b, 3 / 61 * 100)

    def test_min_effort_constant_positive(self):
        self.assertGreater(MIN_EFFORT_HOURS, 0)
