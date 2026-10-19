def task(q, llm, web, docs, mail, get_approval):
    plan = llm.plan(q)
    found = docs.top(web.search(plan["query"]), 3)
    draft = llm.write(q, found)
    approval = get_approval()
    if approval is None: raise TimeoutError("waiting approval")
    if not approval: raise ValueError("отклонил человек")
    sent = mail.send(draft["to"], draft["text"])
    return {"id": sent["id"]}
