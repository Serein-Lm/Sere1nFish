from __future__ import annotations

import pytest

from Sere1nGraph.graph.observability.pricing import calc_cost


def test_qwen37_plus_uses_discounted_online_price_through_256k() -> None:
    assert calc_cost("qwen3.7-plus", 200_000, 56_000) == pytest.approx(
        (200_000 * 1.6 + 56_000 * 6.4) / 1_000_000
    )


def test_qwen37_plus_moves_to_long_context_price_above_256k() -> None:
    assert calc_cost("qwen3.7-plus", 256_000, 1) == pytest.approx(
        (256_000 * 4.8 + 19.2) / 1_000_000
    )


def test_qwen37_plus_dated_alias_uses_same_price() -> None:
    current = calc_cost("qwen3.7-plus", 400_000, 100_000)
    dated = calc_cost("qwen3.7-plus-2026-05-26", 400_000, 100_000)

    assert dated == pytest.approx(current)


def test_qwen37_plus_batch_is_half_of_discounted_online_price() -> None:
    online = calc_cost("qwen3.7-plus", 100_000, 20_000)
    batch = calc_cost("qwen3.7-plus", 100_000, 20_000, batch=True)

    assert batch == pytest.approx(online * 0.5)
