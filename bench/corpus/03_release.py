def release(ver, rel, ci, build, sign):
    notes = rel.notes(ver)
    result = ci.run(ver)
    if not result["ok"]: raise ValueError("CI красный")
    art = build.make(ver)
    try:
        total = sign.sha(art)
        rel.publish(ver, notes, total)
    except Exception:
        build.drop(art["id"]); raise
    return {"ver": ver, "sha": total}
