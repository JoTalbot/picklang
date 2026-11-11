import asyncio
import json
import sqlite3

import pytest

from durable_weave.executor import DurableExecutor
from durable_weave.async_executor import AsyncDurableExecutor
from durable_weave.sqlite_backend import SQLiteEventLog


def test_batch_failure_rolls_back_disk_and_memory(tmp_path):
    log = SQLiteEventLog(tmp_path / 'log.db')
    log.append('Before', {})
    before = list(log.events)
    log.db.execute("CREATE TRIGGER reject_bad BEFORE INSERT ON events WHEN NEW.type='Bad' BEGIN SELECT RAISE(ABORT, 'injected'); END")
    with pytest.raises(sqlite3.IntegrityError):
        log.append_many([('Good', {}), ('Bad', {})])
    assert log.events == before
    assert log.db.execute('SELECT count(*) FROM events').fetchone()[0] == 1
    log.append('After', {})
    assert log.verify_chain()[0]


def test_parallel_atomic_crash_resume_and_snapshot(tmp_path):
    calls = []
    def handler(p):
        calls.append(p['_idempotency_key'])
        return p['n']
    def workflow(ctx, inp):
        return ctx.execute_parallel([('f', {'n': 1}, 'a'), ('f', {'n': 2}, 'b')])
    ex = DurableExecutor(tmp_path, {'f': handler}, crash_after=1, snapshot_every=1)
    assert ex.run('e', workflow, {}).status == 'CRASHED'
    assert len(ex._completed) == 2
    assert any(e['type'] == 'StateSnapshot' for e in ex.log.events)
    ex.log.compact()
    resumed = DurableExecutor(tmp_path, {'f': handler})
    assert resumed.run('e', workflow, {}).output == [1, 2]
    assert len(calls) == 2


@pytest.mark.parametrize('damage', ['delete', 'tamper', 'schema'])
def test_bad_compacted_snapshot_rebuilds_without_effects(tmp_path, damage):
    calls = []
    def workflow(ctx, inp):
        ctx.wait_signal('go')
        return ctx.execute_activity('f')
    ex = DurableExecutor(tmp_path, {'f': lambda p: calls.append(1) or 42}, snapshot_every=1)
    ex.run('e', workflow, {})
    ex.deliver_signal('e', 'go', True)
    assert ex.run('e', workflow, {}).output == 42
    ref = next(e['data']['snapshot_ref'] for e in ex.log.events if e['type'] == 'StateSnapshot')
    if damage == 'schema':
        ref = ex.cas.put_json(['invalid snapshot'])
        ex.log.append('StateSnapshot', {'snapshot_ref': ref})
    ex.log.compact()
    if damage == 'delete':
        ex.cas._path(ref).unlink()
    elif damage == 'tamper':
        ex.cas._path(ref).write_text('{"completed":{}}')
    assert ex.run('e', workflow, {}).output == 42
    assert calls == [1]
    assert ref in ex.verify('e')['bad_artifacts']


def test_corrupt_journal_stops_before_handler(tmp_path):
    calls = []
    def workflow(ctx, inp):
        return ctx.execute_activity('f')
    ex = DurableExecutor(tmp_path, {'f': lambda p: calls.append(1)})
    ex.run('e', workflow, {})
    with ex.log.db:
        ex.log.db.execute("UPDATE events SET hash='corrupt' WHERE seq=0")
    result = ex.run('e', workflow, {})
    assert result.status == 'FAILED' and 'corrupt journal' in result.error
    assert calls == [1]


def test_malformed_archived_event_json_fails_closed_in_verify(tmp_path):
    def workflow(ctx, inp):
        return ctx.execute_activity('f')
    ex = DurableExecutor(tmp_path, {'f': lambda p: 42}, snapshot_every=1)
    assert ex.run('e', workflow, {}).status == 'COMPLETED'
    ex.log.compact()
    with ex.log.db:
        ex.log.db.execute("UPDATE archive SET data='{' WHERE seq=0")
    result = ex.verify('e')
    assert result['status'] == 'CORRUPT'
    assert result['verified'] is False
    assert 'invalid JSON' in result['chain_detail']


def test_corrupt_result_is_not_replayed(tmp_path):
    calls = []
    def workflow(ctx, inp):
        return ctx.execute_activity('f')
    ex = DurableExecutor(tmp_path, {'f': lambda p: calls.append(1) or 42})
    ex.run('e', workflow, {})
    ex.cas._path(next(iter(ex._completed.values()))).write_text('{"result":0}')
    result = ex.run('e', workflow, {})
    assert result.status == 'FAILED' and 'corrupt replay result' in result.error
    assert calls == [1]


def test_failed_completion_write_can_resume_committed_activity(tmp_path, monkeypatch):
    calls = []
    original = SQLiteEventLog.append
    def failing(self, type_, data):
        if type_ == 'WorkflowCompleted':
            raise sqlite3.OperationalError('injected full disk')
        return original(self, type_, data)
    def workflow(ctx, inp):
        return ctx.execute_activity('f')
    ex = DurableExecutor(tmp_path, {'f': lambda p: calls.append(1) or 42})
    with monkeypatch.context() as patch:
        patch.setattr(SQLiteEventLog, 'append', failing)
        assert ex.run('e', workflow, {}).status == 'FAILED'
    assert ex.run('e', workflow, {}).output == 42
    assert calls == [1]


def test_async_parallel_failure_publishes_no_partial_results(tmp_path):
    async def handler(p):
        return p['n']
    async def run():
        ex = AsyncDurableExecutor(tmp_path, {'f': handler}, snapshot_every=1)
        ex._load('e')
        ex.log.db.execute("CREATE TRIGGER reject_second BEFORE INSERT ON events WHEN NEW.seq=1 BEGIN SELECT RAISE(ABORT, 'injected'); END")
        with pytest.raises(sqlite3.IntegrityError):
            await ex.parallel([('f', {'n': 1}, 'a'), ('f', {'n': 2}, 'b')])
        assert ex._completed == {} and ex.log.events == []
        assert ex.log.db.execute('SELECT count(*) FROM events').fetchone()[0] == 0
        ex.log.db.execute('DROP TRIGGER reject_second')
        ex._load('e')
        assert await ex.parallel([('f', {'n': 1}, 'a'), ('f', {'n': 2}, 'b')]) == [1, 2]
        assert ex._steps_done == 2
        assert ex.log.events[-1]['type'] == 'StateSnapshot'
    asyncio.run(run())


def test_async_failed_frontier_waits_for_other_started_handlers(tmp_path):
    completed = []
    async def bad(p):
        raise ValueError('failed handler')
    async def slow(p):
        await asyncio.sleep(0.01)
        completed.append('settled')
        return 1
    async def run():
        ex = AsyncDurableExecutor(tmp_path, {'bad': bad, 'slow': slow})
        ex._load('e')
        with pytest.raises(ValueError, match='failed handler'):
            await ex.parallel([('bad', {}, 'a'), ('slow', {}, 'b')])
        assert completed == ['settled']
        assert ex._completed == {} and ex.log.events == []
    asyncio.run(run())

def test_malformed_event_json_fails_closed_in_run_and_verify(tmp_path):
    calls = []
    def workflow(ctx, inp):
        return ctx.execute_activity('f')
    ex = DurableExecutor(tmp_path, {'f': lambda p: calls.append(1) or 42})
    assert ex.run('e', workflow, {}).status == 'COMPLETED'
    db_path = tmp_path / 'executions' / 'e' / 'events.db'
    with sqlite3.connect(db_path) as db:
        db.execute("UPDATE events SET data='{' WHERE seq=0")

    result = DurableExecutor(tmp_path, {'f': lambda p: calls.append(1) or 99}).run('e', workflow, {})
    assert result.status == 'FAILED'
    assert 'invalid JSON' in result.error
    verification = DurableExecutor(tmp_path, {}).verify('e')
    assert verification['status'] == 'CORRUPT'
    assert verification['verified'] is False
    assert calls == [1]

def test_malformed_archived_event_json_stops_run_before_handler(tmp_path):
    calls = []

    def workflow(ctx, inp):
        return ctx.execute_activity('f')

    ex = DurableExecutor(tmp_path, {'f': lambda p: calls.append(1) or 42}, snapshot_every=1)
    assert ex.run('e', workflow, {}).status == 'COMPLETED'
    ex.log.compact()
    with ex.log.db:
        ex.log.db.execute("UPDATE archive SET data='{' WHERE seq=0")

    result = DurableExecutor(tmp_path, {'f': lambda p: calls.append(1) or 99}).run('e', workflow, {})
    assert result.status == 'FAILED'
    assert 'invalid JSON' in result.error
    assert calls == [1]


@pytest.mark.parametrize("location", ["events", "archive"])
def test_non_object_event_json_fails_closed_in_run_and_verify(tmp_path, location):
    calls = []

    def workflow(ctx, inp):
        return ctx.execute_activity("f")

    ex = DurableExecutor(tmp_path, {"f": lambda p: calls.append(1) or 42},
                         snapshot_every=1 if location == "archive" else None)
    assert ex.run("e", workflow, {}).status == "COMPLETED"
    if location == "archive":
        ex.log.compact()
        table = "archive"
    else:
        table = "events"
    with ex.log.db:
        ex.log.db.execute(f"UPDATE {table} SET data='[]' WHERE seq=0")

    result = DurableExecutor(tmp_path, {"f": lambda p: calls.append(1) or 99}).run("e", workflow, {})
    assert result.status == "FAILED"
    assert "invalid event schema" in result.error
    verification = DurableExecutor(tmp_path, {}).verify("e")
    assert verification["status"] == "CORRUPT"
    assert verification["verified"] is False
    assert "invalid event schema" in verification["chain_detail"]
    assert calls == [1]


@pytest.mark.parametrize(
    ("event_type", "payload"),
    [
        ("ActivityCompleted", {"activity": "f", "result_ref": "0" * 64}),
        ("SignalReceived", {"name": "go"}),
        ("StateSnapshot", {"steps": 1}),
        ("WorkflowCompleted", {}),
    ],
)
def test_missing_replay_event_fields_fail_closed(tmp_path, event_type, payload):
    calls = []

    def workflow(ctx, inp):
        return ctx.execute_activity("f")

    ex = DurableExecutor(
        tmp_path, {"f": lambda p: calls.append(1) or 42}, snapshot_every=1
    )
    assert ex.run("e", workflow, {}).status == "COMPLETED"
    ex.deliver_signal("e", "go", True)
    with ex.log.db:
        ex.log.db.execute(
            "UPDATE events SET data=? WHERE type=?",
            (json.dumps(payload), event_type),
        )

    result = DurableExecutor(
        tmp_path, {"f": lambda p: calls.append(1) or 99}
    ).run("e", workflow, {})
    assert result.status == "FAILED"
    assert "invalid" in result.error and "schema" in result.error

    verification = DurableExecutor(tmp_path, {}).verify("e")
    assert verification["status"] == "CORRUPT"
    assert verification["verified"] is False
    assert "schema" in verification["chain_detail"]
    assert calls == [1]


def test_missing_replay_fields_in_archive_fail_closed(tmp_path):
    calls = []

    def workflow(ctx, inp):
        return ctx.execute_activity("f")

    ex = DurableExecutor(
        tmp_path, {"f": lambda p: calls.append(1) or 42}, snapshot_every=1
    )
    assert ex.run("e", workflow, {}).status == "COMPLETED"
    ex.log.compact()
    with ex.log.db:
        ex.log.db.execute(
            "UPDATE archive SET data=? WHERE type='ActivityCompleted'",
            (json.dumps({"activity": "f", "result_ref": "0" * 64}),),
        )

    result = DurableExecutor(
        tmp_path, {"f": lambda p: calls.append(1) or 99}
    ).run("e", workflow, {})
    assert result.status == "FAILED"
    assert "invalid ActivityCompleted schema" in result.error

    verification = DurableExecutor(tmp_path, {}).verify("e")
    assert verification["status"] == "CORRUPT"
    assert verification["verified"] is False
    assert "invalid ActivityCompleted schema" in verification["chain_detail"]
    assert calls == [1]


def test_corrupt_sqlite_database_fails_closed_in_run_and_verify(tmp_path):
    def workflow(ctx, inp):
        return ctx.execute_activity("f")

    ex = DurableExecutor(tmp_path, {"f": lambda p: 42})
    assert ex.run("e", workflow, {}).status == "COMPLETED"
    db_path = tmp_path / "executions" / "e" / "events.db"
    ex.log.db.close()
    db_path.write_bytes(b"not a sqlite database")

    result = DurableExecutor(tmp_path, {"f": lambda p: 99}).run("e", workflow, {})
    assert result.status == "FAILED"
    assert "storage failure" in result.error

    verification = DurableExecutor(tmp_path, {}).verify("e")
    assert verification["status"] == "CORRUPT"
    assert verification["verified"] is False
    assert "invalid SQLite journal" in verification["chain_detail"]


def test_unreadable_cas_object_fails_closed_in_verify(tmp_path, monkeypatch):
    def workflow(ctx, inp):
        return ctx.execute_activity("f")

    ex = DurableExecutor(tmp_path, {"f": lambda p: 42})
    assert ex.run("e", workflow, {}).status == "COMPLETED"
    ref = next(iter(ex._completed.values()))

    original_get_bytes = ex.cas.get_bytes

    def unreadable(target_ref):
        if target_ref == ref:
            raise PermissionError("injected unreadable CAS object")
        return original_get_bytes(target_ref)

    monkeypatch.setattr(ex.cas, "get_bytes", unreadable)
    result = ex.verify("e")
    assert result["status"] == "COMPLETED"
    assert result["artifacts_ok"] is False
    assert result["bad_artifacts"] == [ref]
    assert result["verified"] is False

def test_signal_and_resume_cannot_interleave_with_active_run(tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from durable_weave.locking import ExecutionBusy, fcntl

    if fcntl is None:
        pytest.skip("POSIX flock is required for the execution-lock contract")

    entered = threading.Event()
    release = threading.Event()

    def workflow(ctx, inp):
        entered.set()
        assert release.wait(timeout=5)
        return "finished-before-signal"

    ex = DurableExecutor(tmp_path, {})
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(ex.run, "e", workflow, {})
        assert entered.wait(timeout=5)

        with pytest.raises(ExecutionBusy):
            ex.deliver_signal("e", "approval", True)
        assert ex.run("e", workflow, {}).status == "BUSY"

        release.set()
        assert running.result(timeout=5).status == "COMPLETED"

    ex.deliver_signal("e", "approval", True)
    event_types = [event["type"] for event in ex.log.events]
    assert event_types.index("WorkflowCompleted") < event_types.index("SignalReceived")


def test_activity_cas_write_before_sqlite_failure_reuses_idempotency_key(tmp_path, monkeypatch):
    calls = []
    effects = {}
    original_append = SQLiteEventLog.append
    failed_once = False

    def append_with_one_activity_failure(self, type_, data):
        nonlocal failed_once
        if type_ == "ActivityCompleted" and not failed_once:
            failed_once = True
            raise sqlite3.OperationalError("injected commit failure after CAS write")
        return original_append(self, type_, data)

    def handler(payload):
        key = payload["_idempotency_key"]
        calls.append(key)
        if key not in effects:
            effects[key] = 23
        return effects[key]

    def workflow(ctx, inp):
        return ctx.execute_activity("charge", {"amount": 23})

    ex = DurableExecutor(tmp_path, {"charge": handler})
    monkeypatch.setattr(SQLiteEventLog, "append", append_with_one_activity_failure)
    first = ex.run("e", workflow, {})
    assert first.status == "FAILED"
    assert "injected commit failure" in first.error

    monkeypatch.setattr(SQLiteEventLog, "append", original_append)
    resumed = ex.run("e", workflow, {})
    assert resumed.status == "COMPLETED"
    assert resumed.output == 23
    assert len(calls) == 2
    assert calls[0] == calls[1] == "0:charge:auto"
    assert effects == {"0:charge:auto": 23}
