"""Validation and canonical payload handling for recovery archives."""

from __future__ import annotations

import json
from fractions import Fraction
from typing import Any, Optional

from .decimals import decimal_to_string, parse_decimal, parse_nonnegative
from .service import process


class ArchiveInputError(ValueError):
    """The caller supplied structurally invalid archive or revision data."""


class RevisionPayloadConflict(ArchiveInputError):
    """An existing revision ID was reused with a different business payload."""


def _require_id(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ArchiveInputError(f"{field} must be a non-empty string")
    return value


def _first(mapping: dict[str, Any], names: tuple[str, ...]) -> tuple[Any, Optional[str]]:
    for name in names:
        if name in mapping and mapping[name] is not None:
            return mapping[name], name
    return None, None


def _decimal_field(
    mapping: dict[str, Any],
    names: tuple[str, ...],
    field: str,
    *,
    nonnegative: bool = False,
) -> Fraction:
    value, _ = _first(mapping, names)
    if value is None:
        raise ArchiveInputError(f"missing {field}")
    if nonnegative:
        return parse_nonnegative(value, field)
    return parse_decimal(value, field)


def _range_field(
    mapping: dict[str, Any],
    range_names: tuple[str, ...],
    low_names: tuple[str, ...],
    high_names: tuple[str, ...],
    field_prefix: str,
    scalar_names: tuple[str, ...] = (),
) -> tuple[Fraction, Fraction]:
    range_value, _ = _first(mapping, range_names)
    scalar_value = None
    if range_value is None:
        scalar_value, _ = _first(mapping, scalar_names)
    if range_value is not None:
        if not isinstance(range_value, (list, tuple)) or len(range_value) != 2:
            raise ArchiveInputError(f"{field_prefix} interval must contain two decimals")
        low = parse_decimal(range_value[0], f"{field_prefix} lower bound")
        high = parse_decimal(range_value[1], f"{field_prefix} upper bound")
    elif scalar_value is not None:
        low = high = parse_decimal(scalar_value, field_prefix)
    else:
        low = _decimal_field(mapping, low_names, f"{field_prefix} lower bound")
        high = _decimal_field(mapping, high_names, f"{field_prefix} upper bound")
    if low > high:
        raise ArchiveInputError(f"{field_prefix} bounds are reversed")
    return low, high


def build_catalog(
    data: Any,
    piece_order_arg: Any = None,
    samples_arg: Any = None,
) -> dict[str, Any]:
    """Validate base data and build the fixed in-memory catalog."""

    if not isinstance(data, dict):
        raise ArchiveInputError("archive data must be an object")

    for key in ("runs", "pieces", "lithology"):
        value = data.get(key)
        if not isinstance(value, list):
            raise ArchiveInputError(f"{key} must be a list")
        if any(not isinstance(row, dict) for row in value):
            raise ArchiveInputError(f"every {key} row must be an object")

    # Reuse the legacy validator so base identity/depth/structure rules stay
    # identical to process(data).
    process(data)

    runs: dict[str, dict[str, Any]] = {}
    for row in data["runs"]:
        run_id = _require_id(row.get("id"), "run id")
        if run_id in runs:
            raise ArchiveInputError(f"duplicate run id: {run_id}")
        start = parse_nonnegative(row.get("from_m"), "run from_m")
        end = parse_decimal(row.get("to_m"), "run to_m")
        if end <= start:
            raise ArchiveInputError(f"invalid run interval: {run_id}")
        runs[run_id] = {"id": run_id, "from": start, "to": end}

    run_order = [
        row["id"]
        for row in sorted(runs.values(), key=lambda item: (item["from"], item["to"], item["id"]))
    ]

    pieces: dict[str, dict[str, Any]] = {}
    recovered = {run_id: Fraction(0) for run_id in runs}
    for row in data["pieces"]:
        piece_id = _require_id(row.get("id"), "piece id")
        run_id = _require_id(row.get("run_id"), "piece run_id")
        if piece_id in pieces:
            raise ArchiveInputError(f"duplicate piece id: {piece_id}")
        if run_id not in runs:
            raise ArchiveInputError(f"piece {piece_id} references unknown run {run_id}")
        length = parse_decimal(row.get("length_m"), "piece length_m")
        if length <= 0:
            raise ArchiveInputError(f"piece {piece_id} length must be positive")
        pieces[piece_id] = {
            "id": piece_id,
            "run_id": run_id,
            "length": length,
        }
        recovered[run_id] += length

    lithology: dict[str, list[tuple[Fraction, Fraction, str, str]]] = {
        run_id: [] for run_id in runs
    }
    for row in data["lithology"]:
        lith_id = _require_id(row.get("id"), "lithology id")
        code = row.get("code")
        if not isinstance(code, str) or not code:
            raise ArchiveInputError(f"lithology {lith_id} has invalid code")
        start = parse_decimal(row.get("from_m"), "lithology from_m")
        end = parse_decimal(row.get("to_m"), "lithology to_m")
        containing = [
            run_id
            for run_id, run in runs.items()
            if run["from"] <= start and end <= run["to"]
        ]
        if len(containing) != 1:
            raise ArchiveInputError(f"lithology {lith_id} must fit within one run")
        lithology[containing[0]].append((start, end, lith_id, code))

    for intervals in lithology.values():
        intervals.sort(key=lambda item: (item[0], item[1], item[2]))

    if piece_order_arg is None:
        piece_order_arg = data.get("piece_order", data.get("piece_orders"))
    if not isinstance(piece_order_arg, dict):
        raise ArchiveInputError("piece_order must be an object mapping run id to piece ids")
    if set(piece_order_arg) != set(runs):
        raise ArchiveInputError("piece_order must contain exactly one sequence for every run")

    run_pieces: dict[str, list[str]] = {}
    for run_id in run_order:
        ordered_ids = piece_order_arg[run_id]
        if not isinstance(ordered_ids, (list, tuple)):
            raise ArchiveInputError(f"piece_order[{run_id}] must be a list")
        assigned = {
            piece_id
            for piece_id, piece in pieces.items()
            if piece["run_id"] == run_id
        }
        if len(ordered_ids) != len(assigned):
            raise ArchiveInputError(
                f"piece_order[{run_id}] must list every piece in the run exactly once"
            )
        seen: set[str] = set()
        result: list[str] = []
        for ordinal, piece_id in enumerate(ordered_ids):
            if not isinstance(piece_id, str) or not piece_id:
                raise ArchiveInputError(f"piece_order[{run_id}] contains an invalid piece id")
            if piece_id in seen:
                raise ArchiveInputError(f"piece_order[{run_id}] repeats piece {piece_id}")
            if piece_id not in pieces:
                raise ArchiveInputError(f"piece_order[{run_id}] references unknown piece {piece_id}")
            if pieces[piece_id]["run_id"] != run_id:
                raise ArchiveInputError(
                    f"piece {piece_id} is not assigned to run {run_id}"
                )
            seen.add(piece_id)
            result.append(piece_id)
            pieces[piece_id]["ordinal"] = ordinal
        if seen != assigned:
            raise ArchiveInputError(
                f"piece_order[{run_id}] must list every piece in the run exactly once"
            )
        run_pieces[run_id] = result

    if samples_arg is None:
        samples_arg = data.get("samples", data.get("sample_catalog", []))
    raw_samples: list[Any]
    if isinstance(samples_arg, dict):
        raw_samples = []
        for sample_id, spec in samples_arg.items():
            if not isinstance(spec, dict):
                raise ArchiveInputError(f"sample {sample_id} must be an object")
            row = dict(spec)
            row.setdefault("sample_id", sample_id)
            raw_samples.append(row)
    elif isinstance(samples_arg, (list, tuple)):
        raw_samples = list(samples_arg)
    else:
        raise ArchiveInputError("samples must be a list or object")

    samples: dict[str, dict[str, Any]] = {}
    sample_order: list[str] = []
    for row_index, row in enumerate(raw_samples):
        if not isinstance(row, dict):
            raise ArchiveInputError("every sample must be an object")
        sample_id = _require_id(
            _first(row, ("sample_id", "id", "sampleId"))[0], "sample id"
        )
        if sample_id in samples:
            raise ArchiveInputError(f"duplicate sample id: {sample_id}")
        piece_id = _require_id(
            _first(row, ("piece_id", "piece", "pieceId"))[0],
            "sample piece_id",
        )
        if piece_id not in pieces:
            raise ArchiveInputError(f"sample {sample_id} references unknown piece {piece_id}")

        interval_value, _ = _first(
            row, ("interval_m", "offset_interval_m", "range_m")
        )
        if interval_value is not None:
            if not isinstance(interval_value, (list, tuple)) or len(interval_value) != 2:
                raise ArchiveInputError(f"sample {sample_id} interval must contain two decimals")
            start_offset = parse_nonnegative(
                interval_value[0], f"sample {sample_id} start offset"
            )
            end_offset = parse_decimal(
                interval_value[1], f"sample {sample_id} end offset"
            )
        else:
            start_offset = _decimal_field(
                row,
                (
                    "from_offset_m",
                    "start_offset_m",
                    "offset_from_m",
                    "from_offset",
                    "start_m",
                    "from_m",
                ),
                f"sample {sample_id} start offset",
                nonnegative=True,
            )
            end_offset = _decimal_field(
                row,
                (
                    "to_offset_m",
                    "end_offset_m",
                    "offset_to_m",
                    "to_offset",
                    "end_m",
                    "to_m",
                ),
                f"sample {sample_id} end offset",
            )

        piece_length = pieces[piece_id]["length"]
        if start_offset >= end_offset:
            raise ArchiveInputError(f"sample {sample_id} interval must be non-empty")
        if end_offset > piece_length:
            raise ArchiveInputError(f"sample {sample_id} interval extends beyond its piece")

        samples[sample_id] = {
            "id": sample_id,
            "piece_id": piece_id,
            "run_id": pieces[piece_id]["run_id"],
            "start_offset": start_offset,
            "end_offset": end_offset,
            "row_order": row_index,
        }
        sample_order.append(sample_id)

    return {
        "runs": runs,
        "run_order": run_order,
        "pieces": pieces,
        "run_pieces": run_pieces,
        "lithology": lithology,
        "recovered": recovered,
        "samples": samples,
        "sample_order": sample_order,
    }


def validate_revision_id(revision_id: object) -> str:
    if not isinstance(revision_id, str) or not revision_id:
        raise ArchiveInputError("revision_id must be a non-empty string")
    return revision_id


def parse_revision_payload(
    catalog: dict[str, Any],
    evidence_arg: Any,
    receipts_arg: Any,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """Structurally validate and canonically encode a revision business payload."""

    if evidence_arg is None:
        evidence_arg = []
    if not isinstance(evidence_arg, (list, tuple)):
        raise ArchiveInputError("evidence must be a list")

    canonical_evidence: set[tuple[str, ...]] = set()
    prepared_evidence: list[dict[str, Any]] = []

    for position, row in enumerate(evidence_arg):
        if not isinstance(row, dict):
            raise ArchiveInputError(f"evidence[{position}] must be an object")
        kind = row.get("kind", row.get("type"))
        if kind in ("point", "tick", "point_depth", "tick_depth", "absolute", "absolute_tick"):
            piece_id = _require_id(
                _first(row, ("piece", "piece_id", "id"))[0],
                "evidence piece id",
            )
            if piece_id not in catalog["pieces"]:
                raise ArchiveInputError(f"evidence references unknown piece {piece_id}")
            piece = catalog["pieces"][piece_id]
            offset = _decimal_field(
                row,
                ("offset_m", "offset", "tick_offset_m", "tick_m"),
                f"evidence offset for {piece_id}",
                nonnegative=True,
            )
            if offset > piece["length"]:
                raise ArchiveInputError(
                    f"tick offset for piece {piece_id} is outside the piece"
                )
            low, high = _range_field(
                row,
                ("depth_range_m", "range_m", "interval_m", "depth_interval_m", "depth_range", "depth_interval"),
                (
                    "low_m",
                    "min_depth_m",
                    "depth_from_m",
                    "from_depth_m",
                    "depth_low_m",
                    "low_depth",
                    "from_m",
                ),
                (
                    "high_m",
                    "max_depth_m",
                    "depth_to_m",
                    "to_depth_m",
                    "depth_high_m",
                    "high_depth",
                    "to_m",
                ),
                f"point evidence on {piece_id}",
                scalar_names=("depth_m", "absolute_depth_m"),
            )
            key = (
                "point",
                piece_id,
                decimal_to_string(offset),
                decimal_to_string(low),
                decimal_to_string(high),
            )
            if key not in canonical_evidence:
                canonical_evidence.add(key)
                prepared_evidence.append(
                    {
                        "kind": "point",
                        "run_id": piece["run_id"],
                        "piece": piece_id,
                        "offset": offset,
                        "low": low,
                        "high": high,
                    }
                )

        elif kind in ("distance", "piece_distance", "pair_distance", "relative"):
            a_id = _require_id(
                _first(
                    row,
                    (
                        "piece_a",
                        "piece_a_id",
                        "a_piece_id",
                        "piece_1",
                        "piece_1_id",
                        "piece_id_1",
                        "first_piece_id",
                        "from_piece",
                        "from_piece_id",
                        "piece_from",
                        "earlier_piece",
                        "earlier_piece_id",
                    ),
                )[0],
                "distance evidence piece A",
            )
            b_id = _require_id(
                _first(
                    row,
                    (
                        "piece_b",
                        "piece_b_id",
                        "b_piece_id",
                        "piece_2",
                        "piece_2_id",
                        "piece_id_2",
                        "second_piece_id",
                        "to_piece",
                        "to_piece_id",
                        "piece_to",
                        "later_piece",
                        "later_piece_id",
                    ),
                )[0],
                "distance evidence piece B",
            )
            if a_id not in catalog["pieces"] or b_id not in catalog["pieces"]:
                raise ArchiveInputError("distance evidence references an unknown piece")
            if a_id == b_id:
                raise ArchiveInputError("distance evidence requires two different pieces")
            piece_a = catalog["pieces"][a_id]
            piece_b = catalog["pieces"][b_id]
            if piece_a["run_id"] != piece_b["run_id"]:
                raise ArchiveInputError("distance evidence cannot cross runs")

            offset_a = _decimal_field(
                row,
                (
                    "offset_a_m",
                    "a_offset_m",
                    "offset_a",
                    "offset_1_m",
                    "offset_1",
                    "tick_m_1",
                    "tick_1_m",
                    "first_offset_m",
                    "from_offset_m",
                    "earlier_offset_m",
                ),
                f"offset for piece {a_id}",
                nonnegative=True,
            )
            offset_b = _decimal_field(
                row,
                (
                    "offset_b_m",
                    "b_offset_m",
                    "offset_b",
                    "offset_2_m",
                    "offset_2",
                    "tick_m_2",
                    "tick_2_m",
                    "second_offset_m",
                    "to_offset_m",
                    "later_offset_m",
                ),
                f"offset for piece {b_id}",
                nonnegative=True,
            )
            if offset_a > piece_a["length"] or offset_b > piece_b["length"]:
                raise ArchiveInputError("distance evidence tick offset is outside its piece")

            low, high = _range_field(
                row,
                (
                    "distance_range_m",
                    "range_m",
                    "interval_m",
                    "distance_interval_m",
                    "distance_range",
                    "distance_interval",
                    "difference_range_m",
                    "difference_interval_m",
                ),
                (
                    "low_m",
                    "min_distance_m",
                    "distance_from_m",
                    "from_distance_m",
                    "distance_min_m",
                    "from_m",
                ),
                (
                    "high_m",
                    "max_distance_m",
                    "distance_to_m",
                    "to_distance_m",
                    "distance_max_m",
                    "to_m",
                ),
                f"distance evidence {a_id}-{b_id}",
                scalar_names=("distance_m", "difference_m", "exact_distance_m"),
            )

            if piece_a["ordinal"] < piece_b["ordinal"]:
                earlier_id, earlier_offset = a_id, offset_a
                later_id, later_offset = b_id, offset_b
            else:
                earlier_id, earlier_offset = b_id, offset_b
                later_id, later_offset = a_id, offset_a

            key = (
                "distance",
                earlier_id,
                decimal_to_string(earlier_offset),
                later_id,
                decimal_to_string(later_offset),
                decimal_to_string(low),
                decimal_to_string(high),
            )
            if key not in canonical_evidence:
                canonical_evidence.add(key)
                prepared_evidence.append(
                    {
                        "kind": "distance",
                        "run_id": piece_a["run_id"],
                        "earlier_piece": earlier_id,
                        "earlier_offset": earlier_offset,
                        "later_piece": later_id,
                        "later_offset": later_offset,
                        "low": low,
                        "high": high,
                    }
                )
        else:
            raise ArchiveInputError(f"evidence[{position}] has unsupported type/kind: {kind!r}")

    if receipts_arg is None:
        receipts_arg = []
    if not isinstance(receipts_arg, (list, tuple)):
        raise ArchiveInputError("receipts must be a list")

    seen_assays: set[str] = set()
    canonical_receipts: list[tuple[str, ...]] = []
    prepared_receipts: list[dict[str, Any]] = []

    for position, row in enumerate(receipts_arg):
        if not isinstance(row, dict):
            raise ArchiveInputError(f"receipts[{position}] must be an object")
        assay_id = _require_id(
            _first(row, ("assay_id", "id", "assayId"))[0], "assay id"
        )
        if assay_id in seen_assays:
            raise ArchiveInputError(f"duplicate assay id in revision batch: {assay_id}")
        seen_assays.add(assay_id)

        sample_id = _require_id(
            _first(row, ("sample_id", "sample", "sampleId"))[0],
            f"receipt {assay_id} sample_id",
        )
        if sample_id not in catalog["samples"]:
            raise ArchiveInputError(
                f"receipt {assay_id} references unregistered sample {sample_id}"
            )
        analyte, _ = _first(
            row, ("analyte", "analysis_item", "analysis", "item", "determinand")
        )
        unit = row.get("unit")
        if not isinstance(analyte, str) or not analyte:
            raise ArchiveInputError(f"receipt {assay_id} has invalid analyte")
        if not isinstance(unit, str) or not unit:
            raise ArchiveInputError(f"receipt {assay_id} has invalid unit")
        value, _ = _first(row, ("value", "assay_value", "result", "value_decimal"))
        parsed_value = parse_decimal(value, f"receipt {assay_id} value")

        value_string = decimal_to_string(parsed_value)
        canonical_receipts.append(
            (assay_id, sample_id, analyte, unit, value_string)
        )
        prepared_receipts.append(
            {
                "assay_id": assay_id,
                "sample_id": sample_id,
                "analyte": analyte,
                "unit": unit,
                "value": parsed_value,
                "value_string": value_string,
            }
        )

    canonical_receipts.sort(key=lambda item: item[0])
    payload = {
        "evidence": sorted(canonical_evidence),
        "receipts": canonical_receipts,
    }
    canonical_json = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return canonical_json, prepared_evidence, prepared_receipts
