# Эквивалент задачи «подписка» на обычном Python — для честного сравнения токенов живого цикла.
def subscribe(user, plan, trial_days, db, bank, mail, log):
    comp = []
    try:
        sub = db.create_sub(user, plan)
        comp.append(lambda: db.cancel_sub(sub["id"]))
        if trial_days > 0:
            db.set_trial(sub["id"], trial_days)
            return {"sub": sub["id"], "trial": True}
        p = bank.charge(user, plan["price"])
        comp.append(lambda: bank.refund(p["tx"]))
        if not p["ok"]:
            raise ValueError("банк отклонил списание")
        db.mark_paid(sub["id"], p["tx"])
        mail.send(user, "Подписка активна")
        log.event("subscription_active")
        return {"sub": sub["id"], "tx": p["tx"]}
    except Exception:
        for undo in reversed(comp):
            undo()
        raise
