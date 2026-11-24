import csv
import json
import unittest
from pathlib import Path


DATA = Path(__file__).resolve().parents[1] / "data"


class FixtureContractTests(unittest.TestCase):
    def test_declared_sources_have_headers_and_records(self):
        manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
        for filename, columns in manifest["sources"].items():
            with self.subTest(source=filename):
                with (DATA / filename).open(encoding="utf-8", newline="") as source:
                    reader = csv.DictReader(source)
                    self.assertEqual(reader.fieldnames, columns)
                    rows = list(reader)
                    self.assertTrue(rows)
                    for row in rows:
                        self.assertNotIn(None, row)
                        self.assertNotIn(None, row.values())

    def records(self, filename):
        with (DATA / filename).open(encoding="utf-8", newline="") as source:
            return list(csv.DictReader(source))

    def test_effort_and_works_use_declared_passages(self):
        links = self.records("links.csv")
        segments = {row["segment_id"] for row in links}
        for filename in ("effort.csv", "survey_slots.csv", "devices.csv", "works.csv"):
            self.assertLessEqual({row["segment_id"] for row in self.records(filename)}, segments)
        effort = self.records("effort.csv")
        self.assertTrue(any(row["observable"] == "无覆盖" for row in effort))
        obligations = self.records("commitments.csv")
        self.assertEqual({row["shared_passage"] for row in obligations}, {"S-03"})


if __name__ == "__main__":
    unittest.main()
