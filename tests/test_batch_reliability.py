"""Регрессии аудита: replay, snapshots, CAS, идемпотентность."""
import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest

from durable_weave.executor import DurableExecutor
from durable_weave.async_executor import AsyncDurableExecutor
from durable_weave.cas import ContentAddressedStore
from durable_weave.idempotent import IdempotentStore, idempotent
from durable_weave.pickrun import run_pick


def test_parallel_groups_have_distinct_positions(tmp_path):
    calls = []
    def handler(p):
        calls.append(p['n'])
        return p['n']
    def workflow(ctx, inp):
        return [ctx.execute_parallel([('f', {'n': n}, 'key')]) for n in (1, 2)]
    ex = DurableExecutor(tmp_path, {'f': handler})
    assert ex.run('e', workflow, {}).output == [[1], [2]]
    assert ex.run('e', workflow, {}).output == [[1], [2]]
    assert calls == [1, 2]


def test_replay_rejects_changed_payload(tmp_path):
    current = {'n': 1}
    def workflow(ctx, inp):
        return ctx.execute_activity('f', dict(current))
    ex = DurableExecutor(tmp_path, {'f': lambda p: p['n']})
    assert ex.run('e', workflow, {}).output == 1
    current['n'] = 2
    result = ex.run('e', workflow, {})
    assert result.status == 'FAILED'
    assert 'input mismatch' in result.error


def test_replay_rejects_changed_input_and_version_after_compaction(tmp_path):
    def workflow(ctx, inp):
        return ctx.execute_activity('f', {'n': inp})
    ex = DurableExecutor(tmp_path, {'f': lambda p: p['n']}, snapshot_every=1)
    assert ex.run('e', workflow, 1, workflow_version='v1').output == 1
    ex.log.compact()
    assert ex.run('e', workflow, 2, workflow_version='v1').status == 'FAILED'
    assert ex.run('e', workflow, 1, workflow_version='v2').status == 'FAILED'
    assert ex.run('e', workflow, 1, workflow_version='v1').output == 1


def test_pick_source_is_bound_to_execution(tmp_path):
    assert run_pick('out 1', {}, {}, tmp_path, 'e')[0].status == 'COMPLETED'
    assert run_pick('out 2', {}, {}, tmp_path, 'e')[0].status == 'FAILED'


@pytest.mark.parametrize('compact', [False, True])
def test_snapshot_preserves_signal(tmp_path, compact):
    calls = []
    def workflow(ctx, inp):
        ctx.wait_signal('go')
        return ctx.execute_activity('f')
    ex = DurableExecutor(tmp_path, {'f': lambda p: calls.append(1) or 42}, snapshot_every=1)
    assert ex.run('e', workflow, {}).status == 'WAITING'
    ex.deliver_signal('e', 'go', True)
    assert ex.run('e', workflow, {}).output == 42
    if compact:
        ex.log.compact()
    assert ex.run('e', workflow, {}).output == 42
    assert calls == [1]


def test_none_is_a_cached_result(tmp_path):
    calls = []
    wrapped = idempotent(lambda p: calls.append(1), IdempotentStore(tmp_path / 'idem.db'))
    for _ in range(2):
        assert wrapped({'_execution_id': 'e', '_idempotency_key': 'k'}) is None
    assert calls == [1]


def test_concurrent_cas_publication(tmp_path):
    cas = ContentAddressedStore(tmp_path)
    with ThreadPoolExecutor(max_workers=16) as pool:
        refs = list(pool.map(lambda _: cas.put_bytes(b'x' * 100_000), range(100)))
    assert len(set(refs)) == 1
    assert cas.verify(refs[0])
    assert cas.stats()['objects'] == 1


def test_async_parallel_and_payload_validation(tmp_path):
    calls = []
    async def handler(p):
        calls.append(p['n'])
        return p['n']
    ex = AsyncDurableExecutor(tmp_path, {'f': handler})
    async def exercise():
        ex._load('e')
        assert await ex.parallel([('f', {'n': 1}, 'k')]) == [1]
        assert await ex.parallel([('f', {'n': 2}, 'k')]) == [2]
        ex._load('e')
        assert await ex.parallel([('f', {'n': 1}, 'k')]) == [1]
        with pytest.raises(ValueError, match='input mismatch'):
            await ex.parallel([('f', {'n': 3}, 'k')])
    asyncio.run(exercise())
    assert calls == [1, 2]


def test_legacy_parallel_keys_fail_closed(tmp_path):
    ex = DurableExecutor(tmp_path, {'f': lambda p: pytest.fail('must not run')})
    ex._load('e')
    ref = ex.cas.put_json({'result': 1})
    ex.log.append('ActivityCompleted', {'step': 'par0:f:k', 'result_ref': ref})
    def workflow(ctx, inp):
        return ctx.execute_parallel([('f', {}, 'k')])
    result = ex.run('e', workflow, {})
    assert result.status == 'FAILED'
    assert 'explicit migration' in result.error


def test_old_compacted_snapshot_recovers_metadata_from_archive(tmp_path):
    ex = DurableExecutor(tmp_path, {'f': lambda p: 7})
    def workflow(ctx, inp):
        ctx.wait_signal('go')
        return ctx.execute_activity('f')
    assert ex.run('e', workflow, {}).status == 'WAITING'
    ex.deliver_signal('e', 'go', True)
    assert ex.run('e', workflow, {}).output == 7
    ref = ex.cas.put_json({'completed': dict(ex._completed)})
    ex.log.append('StateSnapshot', {'snapshot_ref': ref})
    ex.log.compact()
    assert ex.run('e', workflow, {}).output == 7
    assert ex.run('e', workflow, {'changed': True}).status == 'FAILED'
