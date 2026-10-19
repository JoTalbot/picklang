def order(id, amt, stock, bank, post, mail):
    comp = []
    try:
        r = stock.reserve(id); comp.append(lambda: stock.release(id))
        p = bank.charge(id, amt); comp.append(lambda: bank.refund(p["tx"]))
        if not p["ok"]: raise ValueError("банк отклонил")
        s = post.book(id); comp.append(lambda: post.cancel(s["track"]))
        mail.send(id, s["track"])
        return {"tx": p["tx"], "track": s["track"]}
    except Exception:
        for undo in reversed(comp): undo()
        raise
