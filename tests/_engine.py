"""测试共享夹具：加载合成数据并组装各引擎。"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from corridor import load_dataset
from corridor.events import EvidenceReview
from corridor.handover import Handover
from corridor.network import Network
from corridor.periods import ParallelPeriods
from corridor.planning import Planner

DATA = ROOT / "data"


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.dataset = load_dataset(DATA)
        self.network = Network(self.dataset)
        self.evidence = EvidenceReview(self.dataset, self.network)
        self.parallels = ParallelPeriods(self.dataset)
        self.planner = Planner(self.dataset, self.network, self.evidence)
        self.handover = Handover(
            self.dataset, self.network, self.evidence, self.planner, self.parallels
        )
        self.report = self.evidence.review()


if __name__ == "__main__":
    unittest.main()
