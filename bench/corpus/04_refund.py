def refund(order_id, reason, db, bank, mail):
    o = db.order(order_id)
    if not o["paid"]: raise ValueError("заказ не оплачен")
    r = bank.refund(o["tx"])
    try:
        db.mark_refunded(order_id, r["id"])
    except Exception:
        bank.refund_undo(r["id"]); raise
    mail.send(o["user"], r["id"])
    return {"refund": r["id"]}
