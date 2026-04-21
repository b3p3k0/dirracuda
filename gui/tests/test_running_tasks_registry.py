"""Unit tests for reusable running task registry."""

from gui.utils.running_tasks import (
    RunningTaskRegistry,
    _reset_running_task_registry_for_tests,
    get_running_task_registry,
)


def setup_function():
    _reset_running_task_registry_for_tests()


def test_registry_create_update_remove_lifecycle():
    registry = RunningTaskRegistry()

    task_id = registry.create_task(
        task_type="scan",
        name="SMB Scan",
        state="running",
        progress="0/1",
    )
    assert registry.count() == 1

    registry.update_task(task_id, state="queued", progress="1/3")
    snapshot = registry.get_task(task_id)
    assert snapshot is not None
    assert snapshot.state == "queued"
    assert snapshot.progress == "1/3"

    registry.remove_task(task_id)
    assert registry.count() == 0
    assert registry.get_task(task_id) is None


def test_registry_cancel_all_invokes_callbacks():
    registry = RunningTaskRegistry()
    called = {"a": 0, "b": 0}

    def _cancel_a():
        called["a"] += 1

    def _cancel_b():
        called["b"] += 1

    registry.create_task(task_type="probe", name="Probe A", cancel_callback=_cancel_a)
    registry.create_task(task_type="extract", name="Extract B", cancel_callback=_cancel_b)
    registry.cancel_all()

    assert called == {"a": 1, "b": 1}


def test_registry_subscriber_receives_updates():
    registry = RunningTaskRegistry()
    events = []

    def _listener(tasks):
        events.append(len(tasks))

    registry.subscribe(_listener)
    task_id = registry.create_task(task_type="scan", name="HTTP Scan")
    registry.remove_task(task_id)

    # subscribe() immediately emits current snapshot; then create/remove.
    assert events[:3] == [0, 1, 0]


def test_shared_registry_singleton_and_reset():
    registry_a = get_running_task_registry()
    registry_b = get_running_task_registry()
    assert registry_a is registry_b

    task_id = registry_a.create_task(task_type="scan", name="Shared Task")
    assert registry_b.get_task(task_id) is not None

    _reset_running_task_registry_for_tests()
    registry_c = get_running_task_registry()
    assert registry_c is not registry_a
    assert registry_c.count() == 0
