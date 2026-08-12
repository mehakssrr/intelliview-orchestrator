from unittest.mock import Mock, patch

import pytest

from orchestrator.load_balancer import BalancingStrategy
from orchestrator.scheduler import Scheduler, TaskPriority


@pytest.fixture
def mock_load_balancer():
    """Create a mocked LoadBalancer."""
    load_balancer = Mock()

    load_balancer.strategy = BalancingStrategy.LEAST_LOADED

    return load_balancer


@pytest.fixture
def scheduler(mock_load_balancer):
    """
    Create Scheduler with mocked LoadBalancer.

    This keeps the tests focused on Scheduler logic
    instead of testing the real LoadBalancer.
    """
    scheduler = Scheduler(load_balancer=mock_load_balancer)

    # Replace external dependencies with mocks.
    scheduler.worker_registry = Mock()
    scheduler.session_manager = Mock()

    return scheduler


# ============================================================
# 1. TaskPriority tests
# ============================================================


def test_task_priority_values():
    """Verify all task priority levels exist."""

    assert TaskPriority.LOW.value == 0
    assert TaskPriority.MEDIUM.value == 1
    assert TaskPriority.HIGH.value == 2


# ============================================================
# 2. schedule_task() - session does not exist
# ============================================================


def test_schedule_task_session_not_found(scheduler):
    """
    If the session does not exist, scheduling should fail
    and return False.
    """

    scheduler.session_manager.get_session.return_value = None

    result = scheduler.schedule_task("session-123")

    assert result is False

    scheduler.session_manager.get_session.assert_called_once_with("session-123")

    # No worker should be selected when session doesn't exist.
    scheduler.load_balancer.get_best_worker_for_priority.assert_not_called()


# ============================================================
# 3. schedule_task() - worker available
# ============================================================


@patch("orchestrator.scheduler.process_interview_session")
def test_schedule_task_with_available_worker(mock_task, scheduler):
    """
    If a worker is available, the scheduler should:

    1. Verify session
    2. Select worker
    3. Increment worker active tasks
    4. Dispatch Celery task
    5. Return True
    """

    scheduler.session_manager.get_session.return_value = {"session_id": "session-123"}

    worker = {
        "worker_id": "worker-1",
        "active_tasks": 2,
        "capacity": 5,
    }

    scheduler.load_balancer.get_best_worker_for_priority.return_value = worker

    mock_task.delay.return_value = Mock(id="celery-task-1")

    result = scheduler.schedule_task("session-123")

    assert result is True

    scheduler.session_manager.get_session.assert_called_once_with("session-123")

    scheduler.load_balancer.get_best_worker_for_priority.assert_called_once_with(
        "medium"
    )

    scheduler.worker_registry.increment_active_tasks.assert_called_once_with("worker-1")

    mock_task.delay.assert_called_once_with("session-123")


# ============================================================
# 4. HIGH priority is passed correctly
# ============================================================


@patch("orchestrator.scheduler.process_interview_session")
def test_schedule_high_priority_task(mock_task, scheduler):
    """
    Verify HIGH priority is converted to 'high'
    and passed to the LoadBalancer.
    """

    scheduler.session_manager.get_session.return_value = {"session_id": "session-123"}

    worker = {
        "worker_id": "worker-1",
        "active_tasks": 1,
        "capacity": 5,
    }

    scheduler.load_balancer.get_best_worker_for_priority.return_value = worker

    mock_task.delay.return_value = Mock(id="task-123")

    result = scheduler.schedule_task("session-123", priority=TaskPriority.HIGH)

    assert result is True

    scheduler.load_balancer.get_best_worker_for_priority.assert_called_once_with("high")


# ============================================================
# 5. LOW priority is passed correctly
# ============================================================


@patch("orchestrator.scheduler.process_interview_session")
def test_schedule_low_priority_task(mock_task, scheduler):
    """
    Verify LOW priority is converted to 'low'.
    """

    scheduler.session_manager.get_session.return_value = {"session_id": "session-123"}

    worker = {
        "worker_id": "worker-1",
        "active_tasks": 0,
        "capacity": 5,
    }

    scheduler.load_balancer.get_best_worker_for_priority.return_value = worker

    mock_task.delay.return_value = Mock(id="task-123")

    result = scheduler.schedule_task("session-123", priority=TaskPriority.LOW)

    assert result is True

    scheduler.load_balancer.get_best_worker_for_priority.assert_called_once_with("low")


# ============================================================
# 6. Delayed task execution
# ============================================================


@patch("orchestrator.scheduler.process_interview_session")
def test_schedule_task_with_delay(mock_task, scheduler):
    """
    If delay_seconds > 0, the scheduler should use
    Celery apply_async() with countdown.
    """

    scheduler.session_manager.get_session.return_value = {"session_id": "session-123"}

    worker = {
        "worker_id": "worker-1",
        "active_tasks": 1,
        "capacity": 5,
    }

    scheduler.load_balancer.get_best_worker_for_priority.return_value = worker

    mock_task.apply_async.return_value = Mock(id="delayed-task-123")

    result = scheduler.schedule_task("session-123", delay_seconds=60)

    assert result is True

    mock_task.apply_async.assert_called_once_with(args=["session-123"], countdown=60)

    mock_task.delay.assert_not_called()


# ============================================================
# 7. No worker available → queue task
# ============================================================


@patch("orchestrator.scheduler.process_interview_session")
def test_schedule_task_without_worker(mock_task, scheduler):
    """
    If no worker is available, the scheduler should
    queue the task instead of assigning a worker.
    """

    scheduler.session_manager.get_session.return_value = {"session_id": "session-123"}

    scheduler.load_balancer.get_best_worker_for_priority.return_value = None

    mock_task.delay.return_value = Mock(id="queued-task-123")

    result = scheduler.schedule_task("session-123")

    assert result is True

    mock_task.delay.assert_called_once_with("session-123")

    # No worker should be incremented.
    scheduler.worker_registry.increment_active_tasks.assert_not_called()


# ============================================================
# 8. No worker + delayed task
# ============================================================


@patch("orchestrator.scheduler.process_interview_session")
def test_queue_task_with_delay(mock_task, scheduler):
    """
    If no worker is available and a delay is specified,
    apply_async() should be used.
    """

    scheduler.session_manager.get_session.return_value = {"session_id": "session-123"}

    scheduler.load_balancer.get_best_worker_for_priority.return_value = None

    mock_task.apply_async.return_value = Mock(id="queued-delayed-task")

    result = scheduler.schedule_task("session-123", delay_seconds=120)

    assert result is True

    mock_task.apply_async.assert_called_once_with(args=["session-123"], countdown=120)


# ============================================================
# 9. Celery dispatch failure
# ============================================================


@patch("orchestrator.scheduler.process_interview_session")
def test_schedule_task_dispatch_failure(mock_task, scheduler):
    """
    If Celery dispatch fails:

    1. Worker active task count should be rolled back.
    2. Session should be marked failed.
    3. schedule_task() should return False.
    """

    scheduler.session_manager.get_session.return_value = {"session_id": "session-123"}

    worker = {
        "worker_id": "worker-1",
        "active_tasks": 2,
        "capacity": 5,
    }

    scheduler.load_balancer.get_best_worker_for_priority.return_value = worker

    mock_task.delay.side_effect = Exception("Celery unavailable")

    result = scheduler.schedule_task("session-123")

    assert result is False

    scheduler.worker_registry.increment_active_tasks.assert_called_once_with("worker-1")

    scheduler.worker_registry.decrement_active_tasks.assert_called_once_with("worker-1")

    scheduler.session_manager.mark_session_failed.assert_called_once()

    # Verify correct session ID was used.
    args = scheduler.session_manager.mark_session_failed.call_args[0]

    assert args[0] == "session-123"


# ============================================================
# 10. Queue failure
# ============================================================


@patch("orchestrator.scheduler.process_interview_session")
def test_queue_task_failure(mock_task, scheduler):
    """
    If there is no worker and queueing fails, schedule_task() should
    return False and mark the session as failed with the queueing error.
    """

    scheduler.session_manager.get_session.return_value = {"session_id": "session-123"}

    scheduler.load_balancer.get_best_worker_for_priority.return_value = None

    mock_task.delay.side_effect = Exception("Redis unavailable")

    result = scheduler.schedule_task("session-123")

    assert result is False

    # The queue failure is handled inside _queue_task(), which marks the
    # session as failed with the underlying error message.
    scheduler.session_manager.mark_session_failed.assert_called_once_with(
        "session-123", "Queueing error: Redis unavailable"
    )

    # No worker was assigned, so worker load should not change.
    scheduler.worker_registry.increment_active_tasks.assert_not_called()


# ============================================================
# 11. get_scheduling_status()
# ============================================================


def test_get_scheduling_status(scheduler):
    """
    Verify that scheduling status contains
    load balancer and worker information.
    """

    scheduler.load_balancer.get_load_status.return_value = {
        "system_overloaded": False,
        "worker_stats": {"worker-1": {"active_tasks": 2}},
        "available_workers": 1,
    }

    result = scheduler.get_scheduling_status()

    assert result["load_balancer_strategy"] == (BalancingStrategy.LEAST_LOADED.value)

    assert result["system_overloaded"] is False

    assert result["available_workers"] == 1

    assert result["worker_stats"] == {"worker-1": {"active_tasks": 2}}

    assert result["recommendation"] is None

    assert "timestamp" in result


# ============================================================
# 12. get_scheduling_status() - overloaded system
# ============================================================


def test_get_scheduling_status_overloaded(scheduler):
    """
    Verify overloaded system detection.
    """

    scheduler.load_balancer.get_load_status.return_value = {
        "system_overloaded": True,
        "worker_stats": {},
        "available_workers": 0,
    }

    result = scheduler.get_scheduling_status()

    assert result["system_overloaded"] is True
    assert result["available_workers"] == 0


# ============================================================
# 13. can_accept_task() - workers available
# ============================================================


def test_can_accept_task_when_worker_available(scheduler):
    """
    If at least one worker is available,
    the scheduler should accept a task.
    """

    scheduler.worker_registry.get_available_workers.return_value = [
        {"worker_id": "worker-1"}
    ]

    result = scheduler.can_accept_task()

    assert result is True


# ============================================================
# 14. can_accept_task() - no workers
# ============================================================


def test_can_accept_task_when_no_workers_available(scheduler):
    """
    If there are no available workers,
    the scheduler should not accept the task.
    """

    scheduler.worker_registry.get_available_workers.return_value = []

    result = scheduler.can_accept_task()

    assert result is False


# ============================================================
# 15. Estimated wait time - worker available
# ============================================================


def test_estimated_wait_time_with_available_worker(scheduler):
    """
    If a worker is available, estimated wait time
    should be zero.
    """

    scheduler.worker_registry.get_available_workers.return_value = [
        {"worker_id": "worker-1"}
    ]

    result = scheduler.get_estimated_wait_time()

    assert result == 0


# ============================================================
# 16. Estimated wait time - no workers
# ============================================================


def test_estimated_wait_time_with_no_workers(scheduler):
    """
    If there are zero workers, wait time cannot be estimated.
    The scheduler should return -1.
    """

    scheduler.worker_registry.get_available_workers.return_value = []

    scheduler.worker_registry.get_worker_statistics.return_value = {
        "total_active_tasks": 5,
        "total_workers": 0,
    }

    result = scheduler.get_estimated_wait_time()

    assert result == -1


# ============================================================
# 17. Estimated wait time under load
# ============================================================


def test_estimated_wait_time_under_load(scheduler):
    """
    Verify the scheduler's rough wait-time calculation.

    Formula:
        (active_tasks / workers) * 600
    """

    scheduler.worker_registry.get_available_workers.return_value = []

    scheduler.worker_registry.get_worker_statistics.return_value = {
        "total_active_tasks": 4,
        "total_workers": 2,
    }

    result = scheduler.get_estimated_wait_time()

    assert result == 1200
