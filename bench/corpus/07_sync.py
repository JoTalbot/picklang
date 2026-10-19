def sync(src, io, db, search):
    items = io.batch(src.list(), 100)
    ok = db.upsert(items)
    try:
        if not ok["n"]: raise ValueError("ничего не записано")
        search.reindex(ok["batch"])
    except Exception:
        db.rollback(ok["batch"]); raise
    return {"n": ok["n"], "batch": ok["batch"]}
