from browser_manager.provider import ChromeDockerConfig
from browser_manager.warm_policy import AdaptiveWarmPool


def test_adaptive_idle_reserve_follows_demand_then_returns_to_floor():
    now = [0.0]
    policy = AdaptiveWarmPool(clock=lambda: now[0])
    config = ChromeDockerConfig(max_containers=72, warm_pool_size=20, adaptive_warm_pool=True,
                                warm_pool_min_size=4, warm_demand_window_seconds=180)
    assert policy.target(config, demand=0) == 4
    assert policy.target(config, demand=12) == 12
    now[0] = 179
    assert policy.target(config, demand=0) == 12
    now[0] = 181
    assert policy.target(config, demand=0) == 4
    assert policy.target(config, demand=99) == 20


def test_legacy_static_reserve_is_unchanged_until_enabled():
    assert AdaptiveWarmPool().target(ChromeDockerConfig(max_containers=72, warm_pool_size=20), demand=0) == 20


def test_warm_configuration_is_bounded_by_pool_capacity():
    config = ChromeDockerConfig.from_dict({"max_containers": 3, "warm_pool_size": 20,
                                          "warm_pool_min_size": 8, "warm_demand_window_seconds": 1})
    assert config.warm_pool_min_size == 3
    assert config.warm_demand_window_seconds == 30
