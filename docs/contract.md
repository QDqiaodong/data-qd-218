# Drill-core recovery contract

Geology data technicians provide non-overlapping drilled runs, recovered physical pieces assigned to runs, and non-overlapping lithology intervals. Depth and length values are exact nonnegative decimal strings. Each lithology interval must fit entirely within one drilled run; cross-run logging is rejected rather than silently split.

For every run the service compares summed physical recovery with drilled interval length and reports an exact recovery ratio. Recovery above drilled length remains explicit over_recovered evidence because stacked or duplicated pieces may require investigation. The service does not infer missing depth placement of individual pieces, orient core, interpret geology or manage a warehouse.

The output is a structured borehole reconciliation product. Future piece-depth reconstruction can reuse run identity, recovered lengths and lithology boundaries.
