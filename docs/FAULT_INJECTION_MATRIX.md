# Fault-injection matrix

Status: planning and coverage inventory for the next durability cycle. This document
does not claim that an unexecuted failure mode is safe. Existing coverage below is
based on the committed test suite at the time this matrix was added.

## Scope and interpretation

The local durability contract spans four boundaries:

1. **CAS publication**: serialize and persist an artifact under its content digest.
2. **Journal commit**: append the event that makes the artifact reachable.
3. **Replay and resume**: rebuild state from the verified journal and CAS.
4. **External effects**: call a handler or compensation whose side effect may not
   share a transaction with the local store.

A passing local test proves only the injected condition and assertions in that test.
It does not prove physical power-loss safety, distributed exactly-once execution,
or correctness of a remote service's idempotency implementation.

## Existing coverage inventory

| Boundary / scenario | Existing evidence | What it establishes | Remaining limitation |
|---|---|---|---|
| SQLite batch insert failure | `test_batch_failure_rolls_back_disk_and_memory` | Failed batch does not publish partial rows or mutate in-memory events | Trigger-injected SQL abort, not filesystem/device failure |
| Parallel frontier commit failure | `test_async_parallel_failure_publishes_no_partial_results` (async); sync frontier coverage exercises successful execution only | Async injected commit failure does not publish partial results; sync failure behavior is not yet established | Add deterministic sync commit-failure injection and retain broader crash points around CAS writes and commit acknowledgement |
| CAS write followed by event append failure | `test_activity_cas_write_before_sqlite_failure_reuses_idempotency_key` | Resume retries with the same idempotency key after an uncommitted activity event | Handler deduplication is simulated in-process |
| Completion event write failure | `test_failed_completion_write_can_resume_committed_activity` | A committed activity is replayed rather than re-executed | Does not simulate process or host power loss |
| Corrupt event chain / malformed event data | `test_corrupt_journal_stops_before_handler`, malformed JSON/schema tests | Invalid journal fails closed before another handler call | Does not exhaustively mutate every event field and sequence invariant |
| Corrupt or missing CAS result/snapshot | `test_corrupt_result_is_not_replayed`, compacted snapshot tests | Invalid artifacts are not silently replayed; verification reports bad refs | More storage-level partial-write and permission cases can be added |
| Archive corruption | malformed archived JSON/schema tests | Archive corruption is detected during run/verify | Archive truncation and conflicting duplicate rows need explicit cases |
| Signal deduplication and compaction | `test_duplicate_signal_is_idempotent_across_snapshot_compaction_and_reopen` | Duplicate signal is not appended twice across reopen | Concurrent duplicate delivery should be stressed separately |
| Active run vs signal/resume | `test_signal_and_resume_cannot_interleave_with_active_run` | POSIX execution lock rejects conflicting operations | Windows has no equivalent flock guarantee |
| Saga compensation interrupted after side effect | `test_saga_compensation_resumes_after_partial_commit_and_compaction` | Resume reuses the compensation key and relies on external deduplication | The external effect itself is not transactionally controlled by PICK |
| SQLite WAL close/reopen | PR #91 tests | Committed batches survive ordinary connection close/reopen; aborted batch leaves no partial rows | Not a physical power-cut or storage-cache durability test |

## Required next fault-injection cases

The following are **coverage targets**, not claims that the implementation currently
passes them. Add one regression test per independently observable failure boundary.

### CAS and journal

- Fail before temporary CAS file creation.
- Fail during temporary file write, before file flush.
- Fail after file flush but before atomic publication.
- Fail after atomic publication but before parent-directory sync.
- Fail after CAS publication but before the SQLite event transaction begins.
- Fail during SQLite insert and during commit acknowledgement.
- Reopen after each failure and classify the CAS object as reachable, orphaned,
  missing, or corrupt; never silently replay an invalid result.
- Verify that orphan cleanup cannot remove an object referenced by either active
  events or the archive.

### Replay, snapshot, and archive

- Fail while writing a snapshot, then resume from the journal.
- Fail during compaction before transaction commit and after commit acknowledgement.
- Re-run compaction and verify duplicate archive anchors remain consistent.
- Inject a conflicting duplicate archive sequence, a sequence gap, and a broken
  archive-to-active anchor.
- Corrupt each replay-relevant field independently and assert no handler runs.
- Reopen with an unreadable CAS object and distinguish verification failure from
  journal-chain failure in the report.

### Wait, signal, and resume

- Crash after a signal is committed but before a waiting workflow resumes.
- Deliver the same signal concurrently from two callers.
- Race signal delivery against resume and assert one serialized journal order.
- Fail the signal append and prove no in-memory signal becomes visible.
- Exercise timeout/cancellation while waiting without manufacturing an approval.
- Verify that only the explicit boolean value `true` can approve a wait.

### Parallel work and compensation

- Fail one branch before another branch starts; fail after all branches finish but
  before frontier commit; fail during frontier commit.
- Confirm no partial frontier is visible and retry keys remain stable.
- Interrupt compensation before the handler, after its external side effect, after
  CAS publication, and after journal commit.
- Verify already committed compensations are replayed, not repeated.
- Document that external exactly-once behavior still requires service-side
  idempotency keyed by the stable operation key.

## Test design rules

- Inject failures at named boundaries through narrow seams; avoid timing-dependent
  sleeps when a barrier or monkeypatch can make the order deterministic.
- Assert both durable state (reopened SQLite/CAS) and in-memory state.
- Assert handler call counts and exact idempotency keys, not only final output.
- Run verification after recovery and require explicit status for chain integrity,
  artifact integrity, and overall verification.
- Keep local tests offline and secret-free. Live APIs, real payment systems,
  production storage, and destructive power-cut experiments are separate opt-in
  activities.
- Do not describe process-level exception injection as proof of power-loss safety.

## Exit criteria

The durability stage is ready for review when every required case is either covered
by a deterministic regression test or explicitly marked unsupported with a documented
manual-recovery procedure. The matrix must link each test name to the guarantee it
proves and state what it does not prove.
