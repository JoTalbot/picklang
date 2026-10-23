"""verify(): несуществующее исполнение не может быть verified (аудит 19.10.2026)."""
from __future__ import annotations

import pytest

from durable_weave import DurableExecutor


def test_verify_reports_missing_execution(tmp_path):
    executor = DurableExecutor(tmp_path, {"inc": lambda p: 1})
    info = executor.verify("no-such-execution")
    assert info["verified"] is False
    assert info["status"] == "MISSING"
    assert info["events"] == 0
    # проверка несуществующего исполнения не должна создавать его каталог
    assert not (tmp_path / "executions" / "no-such-execution").exists()


def test_verify_empty_execution_is_not_verified(tmp_path):
    executor = DurableExecutor(tmp_path, {})
    executor.run("empty", lambda ctx, inp: None, {})
    info = executor.verify("empty")
    assert info["events"] > 0 and info["verified"] is True


@pytest.mark.parametrize('compact', [False, True])
@pytest.mark.parametrize('kind', ['input_ref', 'result_ref', 'snapshot_ref'])
@pytest.mark.parametrize('damage', ['tamper', 'delete'])
def test_verify_all_artifacts_including_archive(tmp_path, compact, kind, damage):
    executor = DurableExecutor(tmp_path, {'f': lambda p: {'answer': 42}}, snapshot_every=1)
    def workflow(ctx, inp):
        return ctx.execute_activity('f', inp)
    assert executor.run('e', workflow, {'request': 'unique input'}).status == 'COMPLETED'
    ref = next(e['data'][kind] for e in executor.log.events if kind in e['data'])
    if compact:
        executor.log.compact()
    assert executor.verify('e')['verified']
    path = executor.cas._path(ref)
    if damage == 'tamper':
        path.write_text('{"changed":true}')
    else:
        path.unlink()
    report = executor.verify('e')
    assert report['chain_ok']  # журнал цел, но данные — нет
    assert not report['verified']
    assert not report['artifacts_ok']
    assert ref in report['bad_artifacts']


def test_verify_old_snapshot_after_repeated_compaction(tmp_path):
    executor = DurableExecutor(tmp_path, {'f': lambda p: p}, snapshot_every=1)
    def workflow(ctx, inp):
        ctx.execute_activity('f', {'first': 1})
        return ctx.execute_activity('f', {'second': 2})
    executor.run('e', workflow, {})
    old_ref = next(e['data']['snapshot_ref'] for e in executor.log.events if e['type'] == 'StateSnapshot')
    executor.log.compact()
    executor.run('e', workflow, {})
    executor.log.compact()
    assert executor.verify('e')['verified']
    executor.cas._path(old_ref).unlink()
    assert old_ref in executor.verify('e')['bad_artifacts']
