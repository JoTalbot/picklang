import sqlite3

import pytest

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


def test_full_verification_detects_tampering_in_compacted_archive(tmp_path):
    log = SQLiteEventLog(tmp_path / "tampered.sqlite")
    log.append("Started", {"n": 1})
    log.append("ActivityCompleted", {"n": 2})
    log.append("StateSnapshot", {"state": {"n": 2}})
    log.append("ActivityCompleted", {"n": 3})
    log.compact()
    assert log.verify_chain() == (True, "ok")

    log.db.execute("UPDATE archive SET data = ? WHERE seq = 0", ('{"n":999}',))
    log.db.commit()
    ok, detail = log.verify_chain()
    assert not ok
    assert "archive event" in detail
    log.db.close()


def test_reopen_recovers_committed_wal_batch_and_hash_chain(tmp_path):
    path = tmp_path / "committed.sqlite"
    first = SQLiteEventLog(path)
    first.append("Started", {"n": 1})
    first.append_many([("Step", {"n": 2}), ("StateSnapshot", {"n": 3})])
    expected = list(first.events)
    assert first.db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    first.db.close()

    reopened = SQLiteEventLog(path)
    assert reopened.events == expected
    assert reopened.verify_chain() == (True, "ok")
    reopened.append("Resumed", {"n": 4})
    reopened.db.close()

    final = SQLiteEventLog(path)
    assert [event["type"] for event in final.events] == [
        "Started", "Step", "StateSnapshot", "Resumed"
    ]
    assert final.verify_chain() == (True, "ok")
    final.db.close()


def test_reopen_excludes_entire_batch_when_sqlite_transaction_aborts(tmp_path):
    path = tmp_path / "rolled-back.sqlite"
    first = SQLiteEventLog(path)
    first.append("Committed", {"n": 1})
    before = list(first.events)
    first.db.execute(
        "CREATE TRIGGER reject_second BEFORE INSERT ON events "
        "WHEN NEW.type='Rejected' BEGIN SELECT RAISE(ABORT, 'injected'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        first.append_many([("Uncommitted", {"n": 2}), ("Rejected", {"n": 3})])
    assert first.events == before
    first.db.close()

    reopened = SQLiteEventLog(path)
    assert reopened.events == before
    assert [event["type"] for event in reopened.events] == ["Committed"]
    assert reopened.verify_chain() == (True, "ok")
    reopened.db.close()

