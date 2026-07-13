import stat

import pytest
from node_runtime.spool import EventSpool, SpoolConflict


def test_unacked_result_survives_process_restart(tmp_path) -> None:
    path = tmp_path / "spool.db"
    first = EventSpool(path)
    first.append("task-1", 8, "result", {"text": "done"})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    first.close()
    second = EventSpool(path)
    pending = second.pending("task-1")
    assert pending[0].sequence == 8
    assert pending[0].payload == {"text": "done"}


def test_acknowledge_deletes_only_contiguous_prefix(tmp_path) -> None:
    spool = EventSpool(tmp_path / "spool.db")
    spool.append("task-1", 1, "status", {"state": "running"})
    spool.append("task-1", 2, "result", {"text": "done"})
    spool.acknowledge("task-1", 1)
    assert [event.sequence for event in spool.pending("task-1")] == [2]
    assert spool.reconciliation_range() == (1, 2, 2)


def test_conflicting_sequence_is_rejected(tmp_path) -> None:
    spool = EventSpool(tmp_path / "spool.db")
    spool.append("task-1", 1, "status", {"state": "running"})
    with pytest.raises(SpoolConflict):
        spool.append("task-1", 1, "error", {"message": "different"})


def test_accepted_dispatch_is_reported_interrupted_after_restart(tmp_path) -> None:
    path = tmp_path / "spool.db"
    first = EventSpool(path)
    assert first.record_dispatch("task-1", 1, {"model": "x"}) == "accepted"
    first.close()
    second = EventSpool(path)
    assert second.recover_interrupted_dispatches() == ["task-1"]
    assert second.recover_interrupted_dispatches() == []


def test_runtime_job_result_is_durable_and_new_revision_can_retry(tmp_path) -> None:
    path = tmp_path / "spool.db"
    first = EventSpool(path)
    command = {"kind": "repository_probe", "payload": {"path": "/workspace"}}
    assert first.record_runtime_job_dispatch("job-1", 1, command) == "accepted"
    result = {"job_id": "job-1", "revision": 1, "status": "succeeded"}
    first.complete_runtime_job("job-1", 1, result)
    first.close()

    second = EventSpool(path)
    assert second.record_runtime_job_dispatch("job-1", 1, command) == "duplicate"
    assert second.runtime_job_result("job-1", 1) == result
    assert second.record_runtime_job_dispatch("job-1", 2, command) == "accepted"
    assert second.runtime_job_result("job-1", 2) is None
