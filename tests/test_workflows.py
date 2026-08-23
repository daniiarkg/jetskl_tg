from leadfinder.workflows import _remaining_sleep, passive_monitor_workflow


def test_workflow_uses_explicit_second_durations() -> None:
    assert _remaining_sleep(60, 15.125) == "44.875 seconds"
    assert _remaining_sleep(60, 80) == "1.000 seconds"
    assert (
        passive_monitor_workflow.workflow_id
        == "workflow//src.leadfinder.workflows//passive_monitor_workflow"
    )
