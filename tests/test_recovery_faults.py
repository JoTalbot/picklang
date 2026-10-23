import asyncio
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
