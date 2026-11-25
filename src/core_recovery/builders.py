"""Small dict constructors for a readable public Python API."""

from __future__ import annotations

from typing import Any


def sample(sample_id: str, piece_id: str, from_offset_m: str, to_offset_m: str) -> dict[str, Any]:
    return {
        "sample_id": sample_id,
        "piece_id": piece_id,
        "from_offset_m": from_offset_m,
        "to_offset_m": to_offset_m,
    }


def point_evidence(
    piece_id: str,
    offset_m: str,
    low_depth_m: str,
    high_depth_m: str,
) -> dict[str, Any]:
    return {
        "kind": "point",
        "piece_id": piece_id,
        "offset_m": offset_m,
        "low_m": low_depth_m,
        "high_m": high_depth_m,
    }


def distance_evidence(
    piece_a_id: str,
    offset_a_m: str,
    piece_b_id: str,
    offset_b_m: str,
    low_distance_m: str,
    high_distance_m: str,
) -> dict[str, Any]:
    """Distance evidence between ticks on two pieces in the same run.

    The two piece arguments may be supplied in either order; the archive always
    canonicalizes the constraint using the shallow-to-deep archive order.
    """

    return {
        "kind": "distance",
        "piece_a_id": piece_a_id,
        "offset_a_m": offset_a_m,
        "piece_b_id": piece_b_id,
        "offset_b_m": offset_b_m,
        "low_m": low_distance_m,
        "high_m": high_distance_m,
    }


def assay_receipt(
    assay_id: str,
    sample_id: str,
    analyte: str,
    unit: str,
    value: str,
) -> dict[str, Any]:
    return {
        "assay_id": assay_id,
        "sample_id": sample_id,
        "analyte": analyte,
        "unit": unit,
        "value": value,
    }
