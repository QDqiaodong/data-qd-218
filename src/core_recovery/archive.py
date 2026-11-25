"""SQLite-backed offline archive, revision transactions, and queries."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Optional

from .catalog import (
    ArchiveInputError,
    RevisionPayloadConflict,
    build_catalog,
    parse_revision_payload,
    validate_revision_id,
)
from .decimals import decimal_to_string
from .geometry import solve_run

SCHEMA = """
CREATE TABLE archive_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE revisions (
    seq INTEGER PRIMARY KEY,
    revision_id TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    handoff_json TEXT NOT NULL
);
CREATE TABLE receipts (
    assay_id TEXT PRIMARY KEY,
    sample_id TEXT NOT NULL,
    analyte TEXT NOT NULL,
    unit TEXT NOT NULL,
    value_text TEXT NOT NULL,
    revision_seq INTEGER NOT NULL
);
CREATE TABLE current_delivery (
    assay_id TEXT PRIMARY KEY,
    record_json TEXT NOT NULL,
    revision_seq INTEGER NOT NULL
);
CREATE TABLE run_results (
    seq INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    status TEXT NOT NULL,
    over_recovered INTEGER NOT NULL,
    result_json TEXT NOT NULL,
    PRIMARY KEY (seq, run_id)
);
CREATE TABLE sample_results (
    seq INTEGER NOT NULL,
    sample_id TEXT NOT NULL,
    status TEXT NOT NULL,
    result_json TEXT NOT NULL,
    PRIMARY KEY (seq, sample_id)
);
"""


class ArchiveNotFoundError(ArchiveInputError, FileNotFoundError):
    """No valid core recovery archive exists at the supplied path."""


class ArchiveExistsError(ArchiveInputError, FileExistsError):
    """An archive already exists at the requested creation path."""


def resolve_database_path(path: str | os.PathLike[str]) -> Path:
    path_text = os.fspath(path)
    if path_text == ":memory:":
        return Path(":memory:")
    resolved = Path(path_text).expanduser()
    if resolved.is_dir():
        return resolved / "core_archive.sqlite3"
    return resolved


def create_archive(
    path: str | os.PathLike[str],
    data: Any = None,
    *,
    piece_order: Any = None,
    piece_orders: Any = None,
    samples: Any = None,
) -> "CoreArchive":
    return CoreArchive.create(
        path,
        data,
        piece_order=piece_order if piece_order is not None else piece_orders,
        samples=samples,
    )


def open_archive(path: str | os.PathLike[str]) -> "CoreArchive":
    return CoreArchive(path)


init_archive = create_archive


class CoreArchive:
    """A single-borehole archive that can be closed and reopened offline."""

    def __init__(self, path: str | os.PathLike[str]):
        self.requested_path = path
        self.db_path = resolve_database_path(path)
        if os.fspath(self.db_path) != ":memory:" and not self.db_path.is_file():
            raise ArchiveNotFoundError(f"no archive at {path}")

        self.db = sqlite3.connect(
            os.fspath(self.db_path), isolation_level=None
        )
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA query_only=OFF")
        self._verify_schema()
        self.catalog = self._load_catalog()

    @classmethod
    def open(cls, path: str | os.PathLike[str]) -> "CoreArchive":
        return cls(path)

    @classmethod
    def create(
        cls,
        path: str | os.PathLike[str],
        data: Any = None,
        *,
        piece_order: Any = None,
        piece_orders: Any = None,
        samples: Any = None,
    ) -> "CoreArchive":
        if piece_order is None:
            piece_order = piece_orders
        if data is None:
            data = {}
        # Validate and solve in memory before creating any persistent file.
        catalog = build_catalog(data, piece_order, samples)
        initial_results = compute_results(catalog, [])

        try:
            base_json = json.dumps(data, ensure_ascii=False, sort_keys=True)
            order_json = json.dumps(
                piece_order, ensure_ascii=False, sort_keys=True
            )
            samples_json = json.dumps(
                samples, ensure_ascii=False, sort_keys=True
            )
        except (TypeError, ValueError) as exc:
            raise ArchiveInputError("archive input is not JSON-serializable") from exc

        db_path = resolve_database_path(path)
        if os.fspath(db_path) == ":memory:":
            raise ArchiveInputError(
                ":memory: archives cannot be reopened by path; use a local file"
            )
        if db_path.exists():
            raise ArchiveExistsError(f"archive path already exists: {path}")
        db_path.parent.mkdir(parents=True, exist_ok=True)

        db = sqlite3.connect(os.fspath(db_path), isolation_level=None)
        try:
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("PRAGMA synchronous=FULL")
            db.executescript(SCHEMA)
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO archive_meta(key, value) VALUES (?, ?)",
                ("schema_version", "1"),
            )
            db.execute(
                "INSERT INTO archive_meta(key, value) VALUES (?, ?)",
                ("base_json", base_json),
            )
            db.execute(
                "INSERT INTO archive_meta(key, value) VALUES (?, ?)",
                ("piece_order_json", order_json),
            )
            db.execute(
                "INSERT INTO archive_meta(key, value) VALUES (?, ?)",
                ("samples_json", samples_json),
            )
            _insert_results(db, 0, initial_results)
            db.execute("COMMIT")
        except BaseException:
            try:
                db.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            db.close()
            try:
                db_path.unlink()
            except FileNotFoundError:
                pass
            raise
        else:
            db.close()

        return cls(path)

    def _verify_schema(self) -> None:
        try:
            row = self.db.execute(
                "SELECT value FROM archive_meta WHERE key = 'schema_version'"
            ).fetchone()
        except sqlite3.Error as exc:
            raise ArchiveNotFoundError(
                f"path is not a core recovery archive: {self.requested_path}"
            ) from exc
        if row is None:
            raise ArchiveNotFoundError(
                f"path is not a core recovery archive: {self.requested_path}"
            )

    def _load_catalog(self) -> dict[str, Any]:
        rows = {
            row["key"]: row["value"]
            for row in self.db.execute("SELECT key, value FROM archive_meta")
        }
        data = json.loads(rows["base_json"])
        piece_order = json.loads(rows["piece_order_json"])
        samples = json.loads(rows["samples_json"])
        return build_catalog(data, piece_order, samples)

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "CoreArchive":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def latest_seq(self) -> int:
        row = self.db.execute("SELECT COALESCE(MAX(seq), 0) AS seq FROM revisions").fetchone()
        return int(row["seq"])

    def submit_revision(
        self,
        revision_id: Any = None,
        evidence: Any = None,
        receipts: Any = None,
        *,
        _crash_before_commit: bool = False,
    ) -> dict[str, Any]:
        """Validate and commit a revision, or replay an idempotent retry.

        The first positional argument may also be a complete revision mapping
        containing ``revision_id``, ``evidence`` and ``receipts``.
        """

        if isinstance(revision_id, dict) and evidence is None and receipts is None:
            revision = revision_id
            evidence = revision.get("evidence")
            receipts = revision.get("receipts", revision.get("assays"))
            revision_id = revision.get("revision_id", revision.get("id"))

        revision_id = validate_revision_id(revision_id)
        canonical_payload, prepared_evidence, prepared_receipts = (
            parse_revision_payload(self.catalog, evidence, receipts)
        )

        existing = self.db.execute(
            "SELECT seq, payload_json, handoff_json FROM revisions WHERE revision_id = ?",
            (revision_id,),
        ).fetchone()
        if existing is not None:
            if existing["payload_json"] != canonical_payload:
                raise RevisionPayloadConflict(
                    f"revision id {revision_id!r} was already used with a different payload"
                )
            return {
                "seq": int(existing["seq"]),
                "revision_id": revision_id,
                "handoff": json.loads(existing["handoff_json"]),
            }

        for receipt in prepared_receipts:
            row = self.db.execute(
                "SELECT 1 FROM receipts WHERE assay_id = ?",
                (receipt["assay_id"],),
            ).fetchone()
            if row is not None:
                raise ArchiveInputError(
                    f"assay id {receipt['assay_id']!r} already exists in this archive"
                )

        results = compute_results(self.catalog, prepared_evidence)
        result_by_sample = results["sample_map"]

        old_delivery = {
            row["assay_id"]: json.loads(row["record_json"])
            for row in self.db.execute(
                "SELECT assay_id, record_json FROM current_delivery"
            ).fetchall()
        }

        all_receipts: list[dict[str, Any]] = []
        for row in self.db.execute(
            "SELECT assay_id, sample_id, analyte, unit, value_text FROM receipts"
        ).fetchall():
            all_receipts.append(
                {
                    "assay_id": row["assay_id"],
                    "sample_id": row["sample_id"],
                    "analyte": row["analyte"],
                    "unit": row["unit"],
                    "value_string": row["value_text"],
                }
            )
        all_receipts.extend(prepared_receipts)

        new_delivery: dict[str, dict[str, Any]] = {}
        for receipt in all_receipts:
            location = result_by_sample[receipt["sample_id"]]
            if location["status"] != "located":
                continue
            new_delivery[receipt["assay_id"]] = _delivery_record(
                receipt, location
            )

        seq = self.latest_seq() + 1
        withdrawals: list[dict[str, Any]] = []
        additions: list[dict[str, Any]] = []
        for assay_id in sorted(set(old_delivery) | set(new_delivery)):
            old_record = old_delivery.get(assay_id)
            new_record = new_delivery.get(assay_id)
            if old_record is not None and new_record is None:
                withdrawals.append(old_record)
            elif new_record is not None and old_record is None:
                additions.append(new_record)
            elif old_record is not None and new_record is not None:
                if old_record != new_record:
                    withdrawals.append(old_record)
                    additions.append(new_record)

        handoff = {
            "seq": seq,
            "revision_id": revision_id,
            "withdrawals": withdrawals,
            "additions": additions,
        }
        handoff_json = json.dumps(
            handoff, ensure_ascii=False, sort_keys=False
        )

        self.db.execute("BEGIN IMMEDIATE")
        try:
            # Re-check identities inside the write transaction.
            duplicate_revision = self.db.execute(
                "SELECT payload_json FROM revisions WHERE revision_id = ?",
                (revision_id,),
            ).fetchone()
            if duplicate_revision is not None:
                if duplicate_revision["payload_json"] != canonical_payload:
                    raise RevisionPayloadConflict("revision id payload conflict")
                raise ArchiveInputError("concurrent duplicate revision submission")

            self.db.execute(
                "INSERT INTO revisions(seq, revision_id, payload_json, handoff_json)"
                " VALUES (?, ?, ?, ?)",
                (seq, revision_id, canonical_payload, handoff_json),
            )
            for receipt in prepared_receipts:
                self.db.execute(
                    "INSERT INTO receipts(assay_id, sample_id, analyte, unit,"
                    " value_text, revision_seq) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        receipt["assay_id"],
                        receipt["sample_id"],
                        receipt["analyte"],
                        receipt["unit"],
                        receipt["value_string"],
                        seq,
                    ),
                )

            # Apply the same transition given to downstream: withdrawals
            # first, then additions.  Unchanged rows are left untouched.
            for record in withdrawals:
                self.db.execute(
                    "DELETE FROM current_delivery WHERE assay_id = ?",
                    (record["assay_id"],),
                )
            self.db.executemany(
                "INSERT OR REPLACE INTO current_delivery"
                "(assay_id, record_json, revision_seq) VALUES (?, ?, ?)",
                [
                    (
                        record["assay_id"],
                        json.dumps(record, ensure_ascii=False, sort_keys=False),
                        seq,
                    )
                    for record in additions
                ],
            )
            _insert_results(self.db, seq, results)

            if _crash_before_commit:
                # Test-only hard interruption: no COMMIT is sent.  Reopening
                # performs SQLite crash recovery and sees the old version.
                os._exit(88)

            self.db.execute("COMMIT")
        except BaseException:
            try:
                self.db.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise

        return {
            "seq": seq,
            "revision_id": revision_id,
            "handoff": handoff,
        }

    def available_assays(self) -> list[dict[str, Any]]:
        """Current model-deliverable assay records, sorted by assay ID."""

        rows = self.db.execute(
            "SELECT record_json FROM current_delivery ORDER BY assay_id ASC"
        ).fetchall()
        return [json.loads(row["record_json"]) for row in rows]

    def available_map(self) -> dict[str, dict[str, Any]]:
        return {record["assay_id"]: record for record in self.available_assays()}

    def stored_receipts(self) -> list[dict[str, Any]]:
        """All legally stored receipts, including currently undeliverable ones."""

        rows = self.db.execute(
            "SELECT assay_id, sample_id, analyte, unit, value_text, revision_seq"
            " FROM receipts ORDER BY assay_id ASC"
        ).fetchall()
        return [
            {
                "assay_id": row["assay_id"],
                "sample_id": row["sample_id"],
                "analyte": row["analyte"],
                "unit": row["unit"],
                "value": row["value_text"],
                "revision_seq": int(row["revision_seq"]),
            }
            for row in rows
        ]

    def get_assay(self, assay_id: str) -> Optional[dict[str, Any]]:
        row = self.db.execute(
            "SELECT record_json FROM current_delivery WHERE assay_id = ?",
            (assay_id,),
        ).fetchone()
        return json.loads(row["record_json"]) if row else None

    def handoff(self, seq: int) -> dict[str, Any]:
        return self.get_handoff(seq)

    def get_handoff(self, seq: int) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT handoff_json FROM revisions WHERE seq = ?", (seq,)
        ).fetchone()
        if row is None:
            raise ArchiveInputError(f"no committed revision with seq {seq!r}")
        return json.loads(row["handoff_json"])

    def read_handoff(self, seq: int) -> dict[str, Any]:
        return self.get_handoff(seq)

    def revision(self, seq: int) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT seq, revision_id, payload_json, handoff_json"
            " FROM revisions WHERE seq = ?",
            (seq,),
        ).fetchone()
        if row is None:
            raise ArchiveInputError(f"no committed revision with seq {seq!r}")
        return {
            "seq": int(row["seq"]),
            "revision_id": row["revision_id"],
            "payload": json.loads(row["payload_json"]),
            "handoff": json.loads(row["handoff_json"]),
        }

    def handoff_for_revision_id(self, revision_id: str) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT handoff_json FROM revisions WHERE revision_id = ?",
            (revision_id,),
        ).fetchone()
        if row is None:
            raise ArchiveInputError(
                f"no committed revision with id {revision_id!r}"
            )
        return json.loads(row["handoff_json"])

    def sample_location(
        self, sample_id: str, seq: Optional[int] = None
    ) -> dict[str, Any]:
        if sample_id not in self.catalog["samples"]:
            raise ArchiveInputError(f"unknown sample id {sample_id!r}")
        if seq is None:
            seq = self.latest_seq()
        if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
            raise ArchiveInputError("seq must be a nonnegative integer")
        row = self.db.execute(
            "SELECT result_json FROM sample_results WHERE seq = ? AND sample_id = ?",
            (seq, sample_id),
        ).fetchone()
        if row is None:
            raise ArchiveInputError(f"no sample result for sample {sample_id!r} at seq {seq}")
        return json.loads(row["result_json"])

    def sample_location_for_revision_id(
        self, sample_id: str, revision_id: str
    ) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT seq FROM revisions WHERE revision_id = ?", (revision_id,)
        ).fetchone()
        if row is None:
            raise ArchiveInputError(
                f"no committed revision with id {revision_id!r}"
            )
        return self.sample_location(sample_id, int(row["seq"]))

    def locations(self, seq: Optional[int] = None) -> dict[str, Any]:
        if seq is None:
            seq = self.latest_seq()
        if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
            raise ArchiveInputError("seq must be a nonnegative integer")

        run_rows = self.db.execute(
            "SELECT result_json FROM run_results WHERE seq = ?", (seq,)
        ).fetchall()
        if not run_rows:
            raise ArchiveInputError(f"no committed results for seq {seq}")
        run_by_id = {
            json.loads(row["result_json"])["run_id"]: json.loads(row["result_json"])
            for row in run_rows
        }
        runs = [run_by_id[run_id] for run_id in self.catalog["run_order"]]

        sample_rows = self.db.execute(
            "SELECT result_json FROM sample_results WHERE seq = ?", (seq,)
        ).fetchall()
        sample_by_id = {
            json.loads(row["result_json"])["sample_id"]: json.loads(row["result_json"])
            for row in sample_rows
        }
        samples = [
            sample_by_id[sample_id]
            for sample_id in self.catalog["sample_order"]
            if sample_id in sample_by_id
        ]
        return {"seq": seq, "runs": runs, "samples": samples}

    def current_locations(self) -> dict[str, Any]:
        return self.locations()

    def current_available_assays(self) -> list[dict[str, Any]]:
        return self.available_assays()

    # Small vocabulary aliases for downstream callers.
    current_available = available_assays
    current_data = available_assays
    deliveries = available_assays
    get_available = available_assays
    get_sample_location = sample_location
    get_locations = locations


def compute_results(
    catalog: dict[str, Any], prepared_evidence: list[dict[str, Any]]
) -> dict[str, Any]:
    evidence_by_run: dict[str, list[dict[str, Any]]] = {
        run_id: [] for run_id in catalog["run_order"]
    }
    for item in prepared_evidence:
        evidence_by_run[item["run_id"]].append(item)

    run_results: list[dict[str, Any]] = []
    sample_results: list[dict[str, Any]] = []

    for run_id in catalog["run_order"]:
        run = catalog["runs"][run_id]
        sample_ids = [
            sample_id
            for sample_id in catalog["sample_order"]
            if catalog["samples"][sample_id]["run_id"] == run_id
        ]
        result = solve_run(
            run_id=run_id,
            run_start=run["from"],
            run_end=run["to"],
            piece_ids=catalog["run_pieces"][run_id],
            piece_lengths={
                piece_id: catalog["pieces"][piece_id]["length"]
                for piece_id in catalog["run_pieces"][run_id]
            },
            lithology=catalog["lithology"][run_id],
            samples=[catalog["samples"][sample_id] for sample_id in sample_ids],
            evidence=evidence_by_run[run_id],
            recovered_length=catalog["recovered"][run_id],
        )
        run_results.append(_encode_run_result(result["run"]))
        sample_results.extend(
            _encode_sample_result(item) for item in result["samples"]
        )

    return {
        "runs": run_results,
        "samples": sample_results,
        "sample_map": {item["sample_id"]: item for item in sample_results},
    }


def _encode_run_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": result["run_id"],
        "status": result["status"],
        "over_recovered": bool(result["over_recovered"]),
    }


def _encode_sample_result(result: dict[str, Any]) -> dict[str, Any]:
    minimum = result["shallowest_from"]
    maximum = result["deepest_to"]
    return {
        "sample_id": result["sample_id"],
        "run_id": result["run_id"],
        "piece_id": result["piece_id"],
        "status": result["status"],
        "shallowest_from_m": (
            decimal_to_string(minimum) if minimum is not None else None
        ),
        "deepest_to_m": (
            decimal_to_string(maximum) if maximum is not None else None
        ),
        "lithology_id": result["lithology_id"],
        "code": result["code"],
    }


def _delivery_record(
    receipt: dict[str, Any], location: dict[str, Any]
) -> dict[str, Any]:
    return {
        "assay_id": receipt["assay_id"],
        "run_id": location["run_id"],
        "piece_id": location["piece_id"],
        "sample_id": receipt["sample_id"],
        "lithology_id": location["lithology_id"],
        "code": location["code"],
        "analyte": receipt["analyte"],
        "unit": receipt["unit"],
        "value": receipt["value_string"],
    }


def _insert_results(
    db: sqlite3.Connection, seq: int, results: dict[str, Any]
) -> None:
    for run in results["runs"]:
        db.execute(
            "INSERT INTO run_results(seq, run_id, status, over_recovered, result_json)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                seq,
                run["run_id"],
                run["status"],
                1 if run["over_recovered"] else 0,
                json.dumps(run, ensure_ascii=False, sort_keys=True),
            ),
        )
    for sample in results["samples"]:
        db.execute(
            "INSERT INTO sample_results(seq, sample_id, status, result_json)"
            " VALUES (?, ?, ?, ?)",
            (
                seq,
                sample["sample_id"],
                sample["status"],
                json.dumps(sample, ensure_ascii=False, sort_keys=True),
            ),
        )


# Descriptive aliases for callers that prefer a borehole/archive vocabulary.
BoreholeArchive = CoreArchive
Archive = CoreArchive


def submit_revision(
    path: str | os.PathLike[str],
    revision_id: Any = None,
    evidence: Any = None,
    receipts: Any = None,
) -> dict[str, Any]:
    """Open an archive, submit one revision, and close it durably."""

    with open_archive(path) as archive:
        return archive.submit_revision(revision_id, evidence, receipts)


def read_available_assays(
    path: str | os.PathLike[str],
) -> list[dict[str, Any]]:
    with open_archive(path) as archive:
        return archive.available_assays()


def read_locations(
    path: str | os.PathLike[str], seq: Optional[int] = None
) -> dict[str, Any]:
    with open_archive(path) as archive:
        return archive.locations(seq)


def read_sample_location(
    path: str | os.PathLike[str],
    sample_id: str,
    seq: Optional[int] = None,
) -> dict[str, Any]:
    with open_archive(path) as archive:
        return archive.sample_location(sample_id, seq)


def read_handoff(path: str | os.PathLike[str], seq: int) -> dict[str, Any]:
    with open_archive(path) as archive:
        return archive.handoff(seq)
