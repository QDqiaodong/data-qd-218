# Drill-core recovery contract

Geology data technicians provide non-overlapping drilled runs, recovered physical pieces assigned to runs, and non-overlapping lithology intervals. Depth and length values are exact nonnegative decimal strings. Each lithology interval must fit entirely within one drilled run; cross-run logging is rejected rather than silently split.

For every run the service compares summed physical recovery with drilled interval length and reports an exact recovery ratio. Recovery above drilled length remains explicit over_recovered evidence because stacked or duplicated pieces may require investigation. The service does not infer missing depth placement of individual pieces, orient core, interpret geology or manage a warehouse.

The output is a structured borehole reconciliation product. Future piece-depth reconstruction can reuse run identity, recovered lengths and lithology boundaries.

## Offline localization-revision extension

The newer archive API is additive and leaves `process(data)` unchanged. Its executable contract is covered by `tests/test_archive.py` and `examples/revision_workflow.py`.

- Creation fixes `runs`, `pieces`, `lithology`, the shallow-to-deep ID order of every run's pieces, and the sample catalog.
- Piece starts are exact difference-constraint variables. Run bounds, fixed piece lengths, non-overlap/order, absolute tick intervals and pairwise tick-distance intervals become inequalities of the form `d[v] >= d[u] + w`. A positive cycle is exactly a joint infeasible/conflict run.
- Feasible systems use longest paths from a fixed zero node for minimum piece starts and longest paths in the reversed graph for maximum starts. Both extrema are attainable.
- A sample is `located` only when one lithology interval contains it at both extreme placements and therefore at every legal placement. Otherwise a feasible run returns `unresolved`; an infeasible/over-recovered run returns `conflict`.
- Each committed revision stores its canonical payload, all receipts, immutable per-seq sample/run results, and the original handoff in one SQLite transaction. Sequence numbers are `MAX(seq)+1`; rolled-back/rejected batches cannot consume one.
- Idempotent retries compare structurally validated canonical payloads by archive-unique revision ID and return the stored seq/handoff without recomputation.
- Handoffs contain full old records in `withdrawals` and full new records in `additions`; callers apply withdrawals first. Both lists are sorted by assay ID.
