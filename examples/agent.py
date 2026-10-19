# Эквивалентный агент на обычном Python: ожидание человека, страж, откат при отказе.
import time


class WaitingApproval(Exception):
    pass


class AgentError(Exception):
    pass


def run_task(q, llm, web, docs, mail, get_approval, sleep=time.sleep):
    plan = llm.plan(q)
    found = docs.top(web.search(plan["query"]), 3)
    draft = llm.write(q, found)

    approval = get_approval()
    deadline = 24 * 60 * 60
    waited = 0
    while approval is None and waited < deadline:
        sleep(5)
        waited += 5
        approval = get_approval()
    if approval is None:
        raise WaitingApproval("approval")
    if not approval:
        raise AgentError("человек отклонил действие")

    sent = mail.send(draft["to"], draft["text"])
    return {"to": draft["to"], "id": sent["id"]}
