from core.process_watchdog import ConsecutiveFailureState, ProcessWatchdogConfig


def test_consecutive_failure_state_resets_after_success() -> None:
    state = ConsecutiveFailureState(failure_threshold=3)

    assert state.record(False) is False
    assert state.record(False) is False
    assert state.record(True) is False
    assert state.consecutive_failures == 0
    assert state.record(False) is False


def test_consecutive_failure_state_reaches_threshold() -> None:
    state = ConsecutiveFailureState(failure_threshold=2)

    assert state.record(False) is False
    assert state.record(False) is True


def test_process_watchdog_config_normalizes_unsafe_values() -> None:
    config = ProcessWatchdogConfig(
        startup_grace_seconds=-1,
        interval_seconds=0,
        probe_timeout_seconds=0,
        failure_threshold=0,
    ).normalized()

    assert config.startup_grace_seconds == 0
    assert config.interval_seconds == 1
    assert config.probe_timeout_seconds == 1
    assert config.failure_threshold == 1
