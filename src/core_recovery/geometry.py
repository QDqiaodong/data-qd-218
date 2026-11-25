"""Exact difference-constraint solving and lithology classification."""

from __future__ import annotations

from collections import deque
from fractions import Fraction
from typing import Any, Iterable, Optional


Edge = tuple[int, int, Fraction]


def bellman_ford_longest(
    node_count: int, edges: list[Edge], source: int = 0
) -> tuple[list[Optional[Fraction]], bool]:
    """Longest paths under constraints ``d[v] >= d[u] + w``.

    Returns ``(distances, has_positive_cycle)``.  A positive cycle is exactly
    an infeasible system of these lower-bound difference constraints.
    """

    dist: list[Optional[Fraction]] = [None] * node_count
    dist[source] = Fraction(0)

    for iteration in range(node_count):
        changed = False
        for u, v, weight in edges:
            if dist[u] is None:
                continue
            candidate = dist[u] + weight
            if dist[v] is None or candidate > dist[v]:
                dist[v] = candidate
                changed = True
        if not changed:
            return dist, False
        if iteration == node_count - 1:
            return dist, True

    return dist, True  # pragma: no cover - loop always returns


def longest_paths(
    node_count: int, edges: list[Edge], source: int = 0
) -> tuple[list[Optional[Fraction]], bool]:
    """SPFA longest paths, with Bellman-Ford verification on cycle suspicion."""

    if node_count <= 16:
        return bellman_ford_longest(node_count, edges, source)

    adjacency: list[list[tuple[int, Fraction]]] = [[] for _ in range(node_count)]
    for u, v, weight in edges:
        adjacency[u].append((v, weight))

    dist: list[Optional[Fraction]] = [None] * node_count
    path_length = [0] * node_count
    in_queue = [False] * node_count
    dist[source] = Fraction(0)
    queue: deque[int] = deque([source])
    in_queue[source] = True

    while queue:
        u = queue.popleft()
        in_queue[u] = False
        current = dist[u]
        if current is None:  # pragma: no cover - source reaches every node
            continue

        for v, weight in adjacency[u]:
            candidate = current + weight
            if dist[v] is None or candidate > dist[v]:
                dist[v] = candidate
                path_length[v] = path_length[u] + 1
                if path_length[v] >= node_count:
                    # The depth heuristic can traverse a zero/negative cycle
                    # before a better simple path is processed; verify exactly.
                    return bellman_ford_longest(node_count, edges, source)
                if not in_queue[v]:
                    queue.append(v)
                    in_queue[v] = True

    return dist, False


def build_run_edges(
    run_start: Fraction,
    run_end: Fraction,
    piece_ids: list[str],
    piece_lengths: dict[str, Fraction],
    evidence: Iterable[dict[str, Any]],
) -> tuple[Optional[list[Edge]], Optional[dict[str, int]]]:
    """Build difference constraints for one ordered run.

    Node ``0`` is the fixed zero-depth variable.  Piece ``i`` uses node
    ``i + 1`` and stores the absolute depth of that piece's shallow end.
    """

    count = len(piece_ids)
    if count == 0:
        return [], {}

    index = {piece_id: i + 1 for i, piece_id in enumerate(piece_ids)}
    zero = 0
    edges: list[Edge] = []

    first = piece_ids[0]
    edges.append((zero, index[first], run_start))

    for earlier_id, later_id in zip(piece_ids, piece_ids[1:]):
        earlier_node = index[earlier_id]
        later_node = index[later_id]
        # s_later >= s_earlier + length(earlier): pieces cannot overlap.
        edges.append((earlier_node, later_node, piece_lengths[earlier_id]))

    last_id = piece_ids[-1]
    last_length = piece_lengths[last_id]
    # 0 >= s_last + length(last) - run_end: last piece is inside the run.
    edges.append((index[last_id], zero, last_length - run_end))

    for item in evidence:
        kind = item["kind"]
        if kind == "point":
            node = index[item["piece"]]
            offset = item["offset"]
            low = item["low"]
            high = item["high"]
            # low <= s + offset <= high
            edges.append((zero, node, low - offset))
            edges.append((node, zero, offset - high))
        elif kind == "distance":
            earlier = item["earlier_piece"]
            later = item["later_piece"]
            earlier_node = index[earlier]
            later_node = index[later]
            earlier_offset = item["earlier_offset"]
            later_offset = item["later_offset"]
            low = item["low"]
            high = item["high"]
            # low <= (s_later + o_later) - (s_earlier + o_earlier) <= high
            edges.append(
                (
                    earlier_node,
                    later_node,
                    low - later_offset + earlier_offset,
                )
            )
            edges.append(
                (
                    later_node,
                    earlier_node,
                    later_offset - earlier_offset - high,
                )
            )

    return edges, index


def solve_run(
    run_id: str,
    run_start: Fraction,
    run_end: Fraction,
    piece_ids: list[str],
    piece_lengths: dict[str, Fraction],
    lithology: list[tuple[Fraction, Fraction, str, str]],
    samples: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    recovered_length: Fraction,
) -> dict[str, Any]:
    """Solve one run and classify all samples belonging to its pieces."""

    drilled_length = run_end - run_start
    over_recovered = recovered_length > drilled_length
    count = len(piece_ids)

    run_result: dict[str, Any] = {
        "run_id": run_id,
        "status": "feasible",
        "over_recovered": over_recovered,
    }

    if over_recovered:
        # The ordered lengths form a positive cycle against the run bounds.
        # Mark this explicitly and never deliver assays from this run.
        run_result["status"] = "conflict"
        return {
            "run": run_result,
            "samples": [_conflict_sample(sample, run_id) for sample in samples],
        }

    if count == 0:
        # A run without pieces has no sample variables and is always placeable.
        sample_results = [
            _conflict_sample(sample, run_id) for sample in samples
        ]
        return {"run": run_result, "samples": sample_results}

    edges, index = build_run_edges(
        run_start, run_end, piece_ids, piece_lengths, evidence
    )
    assert edges is not None and index is not None

    forward, bad_forward = longest_paths(count + 1, edges, 0)
    if bad_forward:
        run_result["status"] = "conflict"
        return {
            "run": run_result,
            "samples": [_conflict_sample(sample, run_id) for sample in samples],
        }

    reverse_edges = [(v, u, weight) for u, v, weight in edges]
    reverse, bad_reverse = longest_paths(count + 1, reverse_edges, 0)
    if bad_reverse:
        run_result["status"] = "conflict"
        return {
            "run": run_result,
            "samples": [_conflict_sample(sample, run_id) for sample in samples],
        }

    piece_bounds: dict[str, tuple[Fraction, Fraction]] = {}
    for piece_id in piece_ids:
        node = index[piece_id]
        minimum = forward[node]
        reverse_distance = reverse[node]
        if minimum is None or reverse_distance is None:
            run_result["status"] = "conflict"
            return {
                "run": run_result,
                "samples": [
                    _conflict_sample(sample, run_id) for sample in samples
                ],
            }
        maximum = -reverse_distance
        if minimum > maximum:
            run_result["status"] = "conflict"
            return {
                "run": run_result,
                "samples": [
                    _conflict_sample(sample, run_id) for sample in samples
                ],
            }
        piece_bounds[piece_id] = (minimum, maximum)

    sample_results: list[dict[str, Any]] = []
    for sample in samples:
        minimum_start_variable, maximum_start_variable = piece_bounds[
            sample["piece_id"]
        ]
        shallowest_start = minimum_start_variable + sample["start_offset"]
        deepest_end = maximum_start_variable + sample["end_offset"]

        universal_containers = []
        for lith_from, lith_to, lith_id, code in lithology:
            if (
                lith_from <= shallowest_start
                and deepest_end <= lith_to
            ):
                universal_containers.append((lith_id, code))

        if len(universal_containers) == 1:
            lith_id, code = universal_containers[0]
            sample_results.append(
                {
                    "sample_id": sample["id"],
                    "run_id": run_id,
                    "piece_id": sample["piece_id"],
                    "status": "located",
                    "shallowest_from": shallowest_start,
                    "deepest_to": deepest_end,
                    "lithology_id": lith_id,
                    "code": code,
                }
            )
        else:
            sample_results.append(
                {
                    "sample_id": sample["id"],
                    "run_id": run_id,
                    "piece_id": sample["piece_id"],
                    "status": "unresolved",
                    "shallowest_from": shallowest_start,
                    "deepest_to": deepest_end,
                    "lithology_id": None,
                    "code": None,
                }
            )

    return {"run": run_result, "samples": sample_results}


def _conflict_sample(sample: dict[str, Any], run_id: str) -> dict[str, Any]:
    return {
        "sample_id": sample["id"],
        "run_id": run_id,
        "piece_id": sample["piece_id"],
        "status": "conflict",
        "shallowest_from": None,
        "deepest_to": None,
        "lithology_id": None,
        "code": None,
    }
