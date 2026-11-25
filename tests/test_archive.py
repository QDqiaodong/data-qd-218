from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core_recovery import (  # noqa: E402
    ArchiveInputError,
    RevisionPayloadConflict,
    assay_receipt,
    create_archive,
    distance_evidence,
    open_archive,
    point_evidence,
    sample,
)


def acceptance_borehole():
    return {
        "runs": [{"id": "r", "from_m": "0", "to_m": "10"}],
        "pieces": [
            {"id": "p", "run_id": "r", "length_m": "2"},
            {"id": "q", "run_id": "r", "length_m": "2"},
        ],
        "lithology": [
            {"id": "L", "from_m": "0", "to_m": "4", "code": "same"},
            {"id": "M", "from_m": "4", "to_m": "10", "code": "same"},
        ],
        "piece_order": {"r": ["p", "q"]},
        "samples": [
            sample("A", "p", "0", "1"),
            sample("B", "q", "0", "1"),
        ],
    }


def rev1_evidence():
    return [
        point_evidence("q", "0", "5", "6"),
        distance_evidence("p", "0", "q", "0", "3", "3"),
    ]


class RevisionArchiveTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._temp.name)
        self.path = self.tmp / "hole.core"

    def tearDown(self):
        self._temp.cleanup()

    def create(self, data=None):
        return create_archive(self.path, data or acceptance_borehole())

    def test_acceptance_narrow_withdraw_then_fix_readds(self):
        archive = self.create()
        first = archive.submit_revision(
            "rev-1",
            rev1_evidence(),
            [
                assay_receipt("aA", "A", "Cu", "%", "1"),
                assay_receipt("aB", "B", "Cu", "%", "2"),
            ],
        )

        a1 = archive.sample_location("A", 1)
        b1 = archive.sample_location("B", 1)
        self.assertEqual((a1["shallowest_from_m"], a1["deepest_to_m"]), ("2", "4"))
        self.assertEqual(a1["status"], "located")
        self.assertEqual(a1["lithology_id"], "L")
        self.assertEqual((b1["shallowest_from_m"], b1["deepest_to_m"]), ("5", "7"))
        self.assertEqual(b1["lithology_id"], "M")
        self.assertEqual(
            [row["assay_id"] for row in first["handoff"]["additions"]],
            ["aA", "aB"],
        )

        second = archive.submit_revision(
            "rev-2",
            [
                point_evidence("q", "0", "5", "7"),
                distance_evidence("p", "0", "q", "0", "3", "3"),
            ],
            [],
        )
        a2 = archive.sample_location("A")
        self.assertEqual((a2["shallowest_from_m"], a2["deepest_to_m"]), ("2", "5"))
        self.assertEqual(a2["status"], "unresolved")
        self.assertEqual(
            [row["assay_id"] for row in second["handoff"]["withdrawals"]], ["aA"]
        )
        self.assertEqual(second["handoff"]["additions"], [])
        self.assertEqual(
            [row["assay_id"] for row in archive.available_assays()], ["aB"]
        )

        third = archive.submit_revision(
            "rev-3",
            [
                point_evidence("q", "0", "7", "7"),
                distance_evidence("p", "0", "q", "0", "3", "3"),
            ],
            [],
        )
        a3 = archive.sample_location("A")
        self.assertEqual((a3["shallowest_from_m"], a3["deepest_to_m"]), ("4", "5"))
        self.assertEqual(a3["lithology_id"], "M")
        self.assertEqual(third["handoff"]["withdrawals"], [])
        self.assertEqual(
            [row["assay_id"] for row in third["handoff"]["additions"]], ["aA"]
        )
        self.assertEqual(third["handoff"]["additions"][0]["lithology_id"], "M")

    def test_direct_switch_withdraws_old_l_and_adds_m_even_same_code(self):
        archive = self.create()
        archive.submit_revision(
            "rev-1",
            rev1_evidence(),
            [assay_receipt("aA", "A", "Cu", "%", "1")],
        )
        direct = archive.submit_revision(
            "rev-direct",
            [
                point_evidence("q", "0", "7", "7"),
                distance_evidence("p", "0", "q", "0", "3", "3"),
            ],
            [],
        )
        self.assertEqual(len(direct["handoff"]["withdrawals"]), 1)
        self.assertEqual(direct["handoff"]["withdrawals"][0]["assay_id"], "aA")
        self.assertEqual(direct["handoff"]["withdrawals"][0]["lithology_id"], "L")
        self.assertEqual(direct["handoff"]["withdrawals"][0]["code"], "same")
        self.assertEqual(len(direct["handoff"]["additions"]), 1)
        self.assertEqual(direct["handoff"]["additions"][0]["assay_id"], "aA")
        self.assertEqual(direct["handoff"]["additions"][0]["lithology_id"], "M")
        self.assertEqual(direct["handoff"]["additions"][0]["code"], "same")

    def test_joint_conflict_withdraws_only_that_run_and_later_resolves(self):
        data = acceptance_borehole()
        data["runs"].append({"id": "r2", "from_m": "20", "to_m": "30"})
        data["pieces"].append({"id": "z", "run_id": "r2", "length_m": "2"})
        data["lithology"].append(
            {"id": "N", "from_m": "20", "to_m": "30", "code": "x"}
        )
        data["piece_order"]["r2"] = ["z"]
        data["samples"].append(sample("C", "z", "0", "1"))
        archive = create_archive(self.path, data)

        archive.submit_revision(
            "rev-1",
            rev1_evidence(),
            [
                assay_receipt("aA", "A", "Cu", "%", "1"),
                assay_receipt("aC", "C", "Cu", "%", "2"),
            ],
        )
        conflict = archive.submit_revision(
            "rev-conflict",
            rev1_evidence() + [point_evidence("p", "0", "4", "4")],
            [assay_receipt("aA2", "A", "Zn", "%", "3")],
        )
        self.assertEqual(
            [row["run_id"] for row in archive.locations()["runs"]], ["r", "r2"]
        )
        statuses = {row["run_id"]: row["status"] for row in archive.locations()["runs"]}
        self.assertEqual(statuses, {"r": "conflict", "r2": "feasible"})
        self.assertEqual(
            [row["assay_id"] for row in conflict["handoff"]["withdrawals"]], ["aA"]
        )
        self.assertEqual(conflict["handoff"]["additions"], [])
        self.assertEqual(
            [row["assay_id"] for row in archive.available_assays()], ["aC"]
        )

        resolved = archive.submit_revision(
            "rev-resolved",
            [
                point_evidence("q", "0", "7", "7"),
                distance_evidence("p", "0", "q", "0", "3", "3"),
            ],
            [],
        )
        self.assertEqual(
            [row["assay_id"] for row in resolved["handoff"]["additions"]],
            ["aA", "aA2"],
        )
        self.assertEqual(
            [row["assay_id"] for row in archive.available_assays()],
            ["aA", "aA2", "aC"],
        )

    def test_over_recovery_and_gap_statuses(self):
        data = {
            "runs": [{"id": "r", "from_m": "0", "to_m": "10"}],
            "pieces": [
                {"id": "p", "run_id": "r", "length_m": "6"},
                {"id": "q", "run_id": "r", "length_m": "5"},
            ],
            "lithology": [{"id": "L", "from_m": "0", "to_m": "10", "code": "x"}],
            "piece_order": {"r": ["p", "q"]},
            "samples": [sample("A", "p", "0", "1")],
        }
        archive = create_archive(self.path, data)
        self.assertEqual(archive.locations(0)["runs"][0]["status"], "conflict")
        self.assertEqual(archive.sample_location("A", 0)["status"], "conflict")
        archive.submit_revision("empty-conflict", [], [])
        self.assertEqual(archive.latest_seq(), 1)
        self.assertEqual(archive.available_assays(), [])

        gap_path = self.tmp / "gap"
        gap_data = {
            "runs": [{"id": "r", "from_m": "0", "to_m": "10"}],
            "pieces": [{"id": "p", "run_id": "r", "length_m": "2"}],
            "lithology": [
                {"id": "L", "from_m": "0", "to_m": "3", "code": "x"},
                {"id": "M", "from_m": "5", "to_m": "10", "code": "y"},
            ],
            "piece_order": {"r": ["p"]},
            "samples": [sample("S", "p", "0", "1")],
        }
        gap_archive = create_archive(gap_path, gap_data)
        result = gap_archive.sample_location("S", 0)
        self.assertEqual(result["status"], "unresolved")
        self.assertEqual(
            (result["shallowest_from_m"], result["deepest_to_m"]), ("0", "9")
        )

    def test_reopen_retry_history_and_current_state(self):
        archive = self.create()
        first_receipts = [
            assay_receipt("a1", "A", "Cu", "%", "1.00"),
            assay_receipt("a0", "A", "Fe", "%", "2"),
        ]
        first = archive.submit_revision(
            "rev-1",
            rev1_evidence(),
            first_receipts,
        )
        archive.submit_revision(
            "rev-2",
            [
                point_evidence("q", "0", "5", "7"),
                distance_evidence("p", "0", "q", "0", "3", "3"),
            ],
            [],
        )
        archive.close()

        archive = open_archive(self.path)
        self.assertEqual(
            [row["assay_id"] for row in archive.available_assays()], []
        )
        retry = archive.submit_revision(
            "rev-1",
            list(reversed(rev1_evidence())),
            list(reversed(first_receipts)),
        )
        self.assertEqual(retry["seq"], 1)
        self.assertEqual(retry["handoff"], first["handoff"])
        self.assertEqual(archive.latest_seq(), 2)
        self.assertEqual(archive.handoff(1), first["handoff"])
        self.assertEqual(archive.sample_location("A", 1)["deepest_to_m"], "4")
        self.assertEqual(archive.sample_location("A")["status"], "unresolved")
        self.assertEqual(
            [row["assay_id"] for row in archive.available_assays()], []
        )

        with self.assertRaises(RevisionPayloadConflict):
            archive.submit_revision(
                "rev-1",
                [point_evidence("q", "0", "5", "7")],
                first_receipts,
            )

        # Structural validation happens before idempotent payload comparison.
        with self.assertRaises(ArchiveInputError):
            archive.submit_revision(
                "rev-1",
                [point_evidence("missing", "0", "5", "6")],
                first_receipts,
            )

    def test_reused_assay_and_unknown_sample_batch_are_atomic(self):
        archive = self.create()
        archive.submit_revision(
            "rev-1",
            rev1_evidence(),
            [assay_receipt("a1", "A", "Cu", "%", "1")],
        )
        with self.assertRaises(ArchiveInputError):
            archive.submit_revision(
                "rev-2", [], [assay_receipt("a1", "B", "Fe", "%", "2")]
            )
        self.assertEqual(archive.latest_seq(), 1)
        with self.assertRaises(ArchiveInputError):
            archive.submit_revision(
                "rev-dup-batch",
                [],
                [
                    assay_receipt("x", "A", "Cu", "%", "1"),
                    assay_receipt("x", "B", "Cu", "%", "2"),
                ],
            )
        self.assertEqual(archive.latest_seq(), 1)

        before_a = archive.sample_location("A")
        with self.assertRaises(ArchiveInputError):
            archive.submit_revision(
                "mixed-bad",
                [
                    point_evidence("q", "0", "7", "7"),
                    distance_evidence("p", "0", "q", "0", "3", "3"),
                ],
                [assay_receipt("new-good-id", "NO_SUCH_SAMPLE", "Cu", "%", "1")],
            )
        self.assertEqual(archive.latest_seq(), 1)
        self.assertEqual(archive.sample_location("A"), before_a)
        with self.assertRaises(ArchiveInputError):
            archive.handoff_for_revision_id("mixed-bad")

        archive.close()
        reopened = open_archive(self.path)
        self.assertEqual(reopened.latest_seq(), 1)
        self.assertEqual(
            [row["assay_id"] for row in reopened.available_assays()], ["a1"]
        )

    def test_reject_invalid_evidence_without_consuming_sequence(self):
        data = acceptance_borehole()
        data["runs"].append({"id": "r2", "from_m": "20", "to_m": "30"})
        data["pieces"].append({"id": "z", "run_id": "r2", "length_m": "2"})
        data["piece_order"]["r2"] = ["z"]
        archive = create_archive(self.path, data)

        invalid_cases = [
            [point_evidence("missing", "0", "1", "2")],
            [distance_evidence("p", "0", "z", "0", "1", "2")],
            [point_evidence("p", "3", "1", "2")],
            [point_evidence("p", "0", "6", "5")],
            [distance_evidence("p", "0", "q", "0", "3", "2")],
            [distance_evidence("p", "0", "p", "0", "3", "2")],
        ]
        for index, evidence in enumerate(invalid_cases):
            with self.subTest(index=index):
                with self.assertRaises(ArchiveInputError):
                    archive.submit_revision(f"bad-{index}", evidence, [])
                self.assertEqual(archive.latest_seq(), 0)

    def test_hard_crash_before_commit_leaves_no_half_revision(self):
        data = acceptance_borehole()
        crash_path = self.tmp / "crash.core"
        create_archive(crash_path, data).close()
        child = f"""
import sys
sys.path.insert(0, {str(SRC)!r})
from core_recovery import open_archive, assay_receipt
archive = open_archive({str(crash_path)!r})
archive.submit_revision(
    'crash-rev', [],
    [assay_receipt('a1', 'A', 'Cu', '%', '1.25')],
    _crash_before_commit=True,
)
"""
        completed = subprocess.run(
            [sys.executable, "-c", child], capture_output=True, text=True
        )
        self.assertEqual(completed.returncode, 88, completed.stderr)

        archive = open_archive(crash_path)
        self.assertEqual(archive.latest_seq(), 0)
        self.assertEqual(archive.available_assays(), [])
        with self.assertRaises(ArchiveInputError):
            archive.handoff(1)

        result = archive.submit_revision(
            "crash-rev", [], [assay_receipt("a1", "A", "Cu", "%", "1.25")]
        )
        self.assertEqual(result["seq"], 1)
        self.assertEqual(result["handoff"]["additions"], [])
        self.assertEqual(archive.available_assays(), [])

        archive.submit_revision("locate-receipt", rev1_evidence(), [])
        self.assertEqual(
            [row["assay_id"] for row in archive.available_assays()], ["a1"]
        )
        archive.close()

        reopened = open_archive(crash_path)
        self.assertEqual(reopened.latest_seq(), 2)
        self.assertEqual(
            [row["assay_id"] for row in reopened.available_assays()], ["a1"]
        )


if __name__ == "__main__":
    unittest.main()
