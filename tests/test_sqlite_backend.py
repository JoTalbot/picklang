from durable_weave.sqlite_backend import SQLiteEventLog


def test_sampled_verification_accepts_compacted_snapshot_anchor(tmp_path):
    log = SQLiteEventLog(tmp_path / "events.sqlite")
    log.append("Started", {"n": 1})
    log.append("ActivityCompleted", {"n": 2})
    log.append("StateSnapshot", {"state": {"n": 2}})
    log.append("ActivityCompleted", {"n": 3})

    result = log.compact()
    assert result["archived"] == 2
    assert log.events[0]["type"] == "StateSnapshot"
    assert log.verify_chain() == (True, "ok")
    ok, detail = log.verify_sampled()
    assert ok, detail
    assert detail.startswith("sampled ok")

    log.db.close()
