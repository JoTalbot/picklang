"""Сквозная приёмка локального примера, не интеграция с razborka/складом."""
from pathlib import Path

import pytest

from durable_weave.pickrun import run_pick
from examples.availability_contract import checked_observer

SOURCE = Path('examples/availability_review.pick').read_text(encoding='utf-8')
FACT = {'id': 'item-1', 'status': 'available', 'freshness': 'current', 'conflict_status': 'none'}


def registry_for(fact, calls):
    def observe(requested_id):
        calls.append('observe')
        return dict(fact)
    def draft(kind, id):
        calls.append(kind)
        return {'id': id, 'asserting': kind == 'strict', 'kind': kind}
    def submit(draft):
        calls.append('submit')
        return {'draft': draft, 'manual_review_requested': True}
    return {'inventory.observe': checked_observer(observe),
            'strict.draft': lambda id: draft('strict', id),
            'review.draft': lambda id: draft('review', id), 'review.submit': submit}


@pytest.mark.parametrize(('updates', 'kind'), [
    ({}, 'strict'), ({'status': 'unknown'}, 'review'),
    ({'status': 'conflict'}, 'review'), ({'conflict_status': 'present'}, 'review'),
    ({'freshness': 'stale'}, 'review'),
])
def test_selected_draft_then_manual_approval_then_resume(tmp_path, updates, kind):
    calls = []
    reg = registry_for(FACT | updates, calls)
    result, ex = run_pick(SOURCE, reg, {'requested_id': 'item-1'}, tmp_path, 'e')
    assert result.status == 'WAITING' and calls == ['observe', kind]
    ex.deliver_signal('e', 'approval', True)
    result, _ = run_pick(SOURCE, reg, {'requested_id': 'item-1'}, tmp_path, 'e')
    assert result.status == 'COMPLETED'
    assert result.output['draft']['asserting'] is (kind == 'strict')
    assert result.output['manual_review_requested'] is True
    assert calls == ['observe', kind, 'submit']
    assert run_pick(SOURCE, reg, {'requested_id': 'item-1'}, tmp_path, 'e')[0].status == 'COMPLETED'
    assert calls == ['observe', kind, 'submit']


@pytest.mark.parametrize('approval', [False, 1, 'yes', {}, []])
def test_denied_or_invalid_approval_never_submits(tmp_path, approval):
    calls = []
    reg = registry_for(FACT | {'status': 'unknown'}, calls)
    _, ex = run_pick(SOURCE, reg, {'requested_id': 'item-1'}, tmp_path, 'e')
    ex.deliver_signal('e', 'approval', approval)
    result, _ = run_pick(SOURCE, reg, {'requested_id': 'item-1'}, tmp_path, 'e')
    assert result.status == 'FAILED'
    assert calls == ['observe', 'review']


@pytest.mark.parametrize('updates', [
    {'status': 'error'}, {'status': 'AVAILABLE'}, {'freshness': None},
    {'conflict_status': 'invalid'}, {'id': 'other'}, {'extra': 'unexpected'},
])
def test_bad_facts_never_reach_draft_or_submit(tmp_path, updates):
    calls = []
    result, _ = run_pick(SOURCE, registry_for(FACT | updates, calls),
                         {'requested_id': 'item-1'}, tmp_path, 'e')
    assert result.status == 'FAILED'
    assert calls == ['observe']


def test_missing_field_is_rejected_before_activity_commit(tmp_path):
    calls = []
    fact = dict(FACT)
    del fact['freshness']
    result, ex = run_pick(SOURCE, registry_for(fact, calls), {'requested_id': 'item-1'}, tmp_path, 'e')
    assert result.status == 'FAILED'
    assert not any(event['type'] == 'ActivityCompleted' for event in ex.log.events)
    assert calls == ['observe']


def test_committed_steps_survive_crash_before_wait_and_after_submit(tmp_path):
    calls = []
    reg = registry_for(FACT | {'status': 'unknown'}, calls)
    args = (SOURCE, reg, {'requested_id': 'item-1'}, tmp_path, 'e')
    result, _ = run_pick(*args, crash_after=2)
    assert result.status == 'CRASHED' and calls == ['observe', 'review']
    result, ex = run_pick(*args)
    assert result.status == 'WAITING'
    ex.deliver_signal('e', 'approval', True)
    result, _ = run_pick(*args, crash_after=3)
    assert result.status == 'CRASHED' and calls == ['observe', 'review', 'submit']
    result, ex = run_pick(*args)
    assert result.status == 'COMPLETED' and calls == ['observe', 'review', 'submit']
    assert ex.verify('e')['verified']
