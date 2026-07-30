from node_runtime.reconcile import ReconcileState


def test_reconcile_reports_durable_progress_and_versions() -> None:
    state = ReconcileState(
        last_acknowledged_event=42,
        interrupted_task_ids=["task-1"],
        spool_first_sequence=40,
        spool_last_sequence=43,
        config_revision=7,
        artifact_versions={"skills": "v3", "mcp": "v2"},
    )
    payload = state.model_dump()
    assert payload["last_acknowledged_event"] == 42
    assert payload["interrupted_task_ids"] == ["task-1"]
    assert payload["artifact_versions"]["skills"] == "v3"
