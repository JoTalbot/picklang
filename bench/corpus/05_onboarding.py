def onboard(user, crm, kyc, pay, get_manager_ok):
    comp = []
    try:
        acc = crm.create(user); comp.append(lambda: crm.remove(acc["id"]))
        docs = kyc.verify(kyc.collect(user))
        if not docs["ok"]: raise ValueError("KYC не пройден")
        card = pay.issue(acc["id"]); comp.append(lambda: pay.close(card["id"]))
        ok = get_manager_ok()
        if ok is None: raise TimeoutError("waiting manager_ok")
        if not ok: raise ValueError("менеджер отклонил")
        crm.activate(acc["id"])
        return {"acc": acc["id"], "card": card["id"]}
    except Exception:
        for undo in reversed(comp): undo()
        raise
