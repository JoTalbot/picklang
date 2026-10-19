def report(month, db, data, viz, doc, cas, get_ok):
    raw = data.agg(data.clean(db.query(month)))
    chart = viz.chart(raw)
    pdf = doc.render(raw, chart)
    ok = get_ok()
    if ok is None: raise TimeoutError("waiting finance_ok")
    if not ok: raise ValueError("финансы не подтвердили")
    arc = cas.store(pdf)
    return {"report": arc, "rows": raw["n"]}
