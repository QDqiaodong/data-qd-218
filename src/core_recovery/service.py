from fractions import Fraction


def num(value,name):
    if not isinstance(value,str) or not value or len(value)>32: raise ValueError(f"{name} must be decimal string")
    try: return Fraction(value)
    except (ValueError,ZeroDivisionError) as exc: raise ValueError(f"invalid {name}") from exc


def txt(value): return str(value.numerator) if value.denominator==1 else f"{value.numerator}/{value.denominator}"


def process(data):
    runs=data.get("runs") if isinstance(data,dict) else None
    pieces=data.get("pieces") if isinstance(data,dict) else None
    logs=data.get("lithology") if isinstance(data,dict) else None
    if not isinstance(runs,list) or not 1<=len(runs)<=1000: raise ValueError("runs must contain 1..1000 items")
    if not isinstance(pieces,list) or not 0<=len(pieces)<=10000: raise ValueError("pieces must be a list")
    if not isinstance(logs,list) or not 0<=len(logs)<=10000: raise ValueError("lithology must be a list")
    run_map={}
    for row in runs:
        rid=row.get("id") if isinstance(row,dict) else None
        start=num(row.get("from_m"),"from_m"); end=num(row.get("to_m"),"to_m")
        if not isinstance(rid,str) or not rid or rid in run_map or start<0 or end<=start: raise ValueError("invalid run")
        run_map[rid]=(start,end)
    ordered=sorted((a,b,rid) for rid,(a,b) in run_map.items())
    for left,right in zip(ordered,ordered[1:]):
        if left[1] > right[0]: raise ValueError("runs overlap")
    piece_ids=set(); recovered={rid:Fraction(0) for rid in run_map}; piece_rows=[]
    for row in pieces:
        pid=row.get("id") if isinstance(row,dict) else None; rid=row.get("run_id") if isinstance(row,dict) else None
        if not isinstance(pid,str) or not pid or pid in piece_ids or rid not in run_map: raise ValueError("invalid piece identity")
        length=num(row.get("length_m"),"length_m")
        if length<=0: raise ValueError("piece length must be positive")
        piece_ids.add(pid); recovered[rid]+=length; piece_rows.append({"id":pid,"run_id":rid,"length_m":txt(length)})
    log_ids=set(); intervals=[]
    for row in logs:
        lid=row.get("id") if isinstance(row,dict) else None; code=row.get("code") if isinstance(row,dict) else None
        start=num(row.get("from_m"),"lithology from"); end=num(row.get("to_m"),"lithology to")
        if not isinstance(lid,str) or not lid or lid in log_ids or not isinstance(code,str) or not code or end<=start: raise ValueError("invalid lithology interval")
        if not any(a<=start and end<=b for a,b,_ in ordered): raise ValueError("lithology must fit within one run")
        log_ids.add(lid); intervals.append((start,end,lid,code))
    intervals.sort()
    for left,right in zip(intervals,intervals[1:]):
        if left[1]>right[0]: raise ValueError("lithology intervals overlap")
    run_rows=[]
    for start,end,rid in ordered:
        drilled=end-start; got=recovered[rid]
        run_rows.append({"id":rid,"from_m":txt(start),"to_m":txt(end),"drilled_m":txt(drilled),"recovered_m":txt(got),"recovery_ratio":txt(got/drilled),"status":"within_run" if got<=drilled else "over_recovered"})
    coverage=[]
    for start,end,lid,code in intervals:
        containing=next(rid for a,b,rid in ordered if a<=start and end<=b)
        coverage.append({"id":lid,"run_id":containing,"code":code,"from_m":txt(start),"to_m":txt(end),"length_m":txt(end-start)})
    return {"runs":run_rows,"pieces":sorted(piece_rows,key=lambda x:x["id"]),"lithology":coverage}
