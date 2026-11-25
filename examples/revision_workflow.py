"""Runnable offline API example for localization revisions and assay handoff.

Run directly with:
    python examples/revision_workflow.py

The script uses a temporary SQLite archive and prints stable JSON records.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from core_recovery import (  # noqa: E402
    assay_receipt,
    create_archive,
    distance_evidence,
    open_archive,
    point_evidence,
    sample,
)


def show(title: str, value) -> None:
    print(f"\n== {title} ==")
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main() -> None:
    archive_path = Path(tempfile.mkdtemp(prefix="core-recovery-api-")) / "hole.core"

    # Existing runs/pieces/lithology keep the process(data) rules.  The extra
    # piece_order and samples arguments are the fixed reconstruction catalog.
    base = {
        "runs": [{"id": "r1", "from_m": "0", "to_m": "10"}],
        "pieces": [
            {"id": "p", "run_id": "r1", "length_m": "2"},
            {"id": "q", "run_id": "r1", "length_m": "2"},
        ],
        "lithology": [
            {"id": "L", "from_m": "0", "to_m": "4", "code": "same-code"},
            {"id": "M", "from_m": "4", "to_m": "10", "code": "same-code"},
        ],
    }
    piece_order = {"r1": ["p", "q"]}
    samples = [
        sample("A", "p", "0", "1"),
        sample("B", "q", "0", "1"),
    ]

    archive = create_archive(
        archive_path, base, piece_order=piece_order, samples=samples
    )

    first_evidence = [
        point_evidence("q", "0", "5", "6"),
        distance_evidence("p", "0", "q", "0", "3", "3"),
    ]
    first = archive.submit_revision(
        "field-rev-001",
        first_evidence,
        [
            assay_receipt("assay-A", "A", "Cu", "pct", "0.420"),
            assay_receipt("assay-B", "B", "Cu", "pct", "0.310"),
        ],
    )
    show("revision 1 handoff", first["handoff"])
    show("A at seq 1", archive.sample_location("A", 1))
    show("current model records", archive.available_assays())

    second = archive.submit_revision(
        "field-rev-002",
        [
            point_evidence("q", "0", "5", "7"),
            distance_evidence("p", "0", "q", "0", "3", "3"),
        ],
        [],
    )
    show("revision 2 handoff (A withdraws; B unchanged)", second["handoff"])
    show("A after widening", archive.sample_location("A"))

    third = archive.submit_revision(
        "field-rev-003",
        [
            point_evidence("q", "0", "7", "7"),
            distance_evidence("p", "0", "q", "0", "3", "3"),
        ],
        [],
    )
    show("revision 3 handoff (A is newly added to M)", third["handoff"])

    archive.close()
    archive = open_archive(archive_path)
    show("reopened current records", archive.available_assays())
    show("original seq 1 handoff after reopen", archive.handoff(1))

    # Equivalent retry: reordered lists and equivalent decimal spellings do not
    # change the business payload.  It returns the original seq/handoff and
    # does not move the current state back to seq 1.
    retry = archive.submit_revision(
        "field-rev-001",
        list(reversed(first_evidence)),
        [
            assay_receipt("assay-B", "B", "Cu", "pct", "0.31"),
            assay_receipt("assay-A", "A", "Cu", "pct", "0.42"),
        ],
    )
    show("idempotent retry of field-rev-001", retry)
    show("latest sequence after retry", {"latest_seq": archive.latest_seq()})
    archive.close()

    print(f"\nArchive file: {archive_path}")


if __name__ == "__main__":
    main()
