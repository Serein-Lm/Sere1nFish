"""
模型定价计算

支持两种模式：
- 阶梯定价 (tiered): qwen3-max, qwen3.5-plus, qwen3.7-plus
- 固定定价 (flat): kimi, glm, claude 系列

基于 token_cost_guide.md 的定价数据。
"""

from __future__ import annotations

# ── 阶梯定价（元/百万 token）──

QWEN3_MAX_TIERS = [
    (32_000, 2.5, 10.0),
    (128_000, 4.0, 16.0),
    (252_000, 7.0, 28.0),
]

QWEN35_PLUS_TIERS = [
    (128_000, 0.8, 4.8),
    (256_000, 2.0, 12.0),
    (1_000_000, 4.0, 24.0),
]

# 阿里云百炼中国内地限时 8 折在线调用价。qwen3.7-plus 的思考与
# 非思考输出同价；Batch 可在 calc_cost(batch=True) 时再享 5 折。
QWEN37_PLUS_TIERS = [
    (256_000, 1.6, 6.4),
    (1_000_000, 4.8, 19.2),
]

# ── 固定定价（元/百万 token）──

USD_TO_CNY = 1.5  # 中转 API 汇率，官方 API 改为 ~7.2

FLAT_PRICING: dict[str, tuple[float, float]] = {
    # (input_per_M, output_per_M)
    "kimi-k2.5": (4.0, 21.0),
    "glm-4.7": (4.0, 21.0),
    "glm-5": (4.0, 21.0),
    "claude-opus-4-6": (5.0 * USD_TO_CNY, 25.0 * USD_TO_CNY),
    "claude-opus-4-5": (5.0 * USD_TO_CNY, 25.0 * USD_TO_CNY),
    "claude-opus-4-1": (15.0 * USD_TO_CNY, 75.0 * USD_TO_CNY),
    "claude-opus-4": (15.0 * USD_TO_CNY, 75.0 * USD_TO_CNY),
    "claude-sonnet-4": (3.0 * USD_TO_CNY, 15.0 * USD_TO_CNY),
    "claude-sonnet-4-5": (3.0 * USD_TO_CNY, 15.0 * USD_TO_CNY),
}

# ── 模型名 → 定价类型映射 ──

MODEL_PRICING_TYPE: dict[str, str] = {
    "qwen3-max": "qwen3_tiered",
    "qwen3-max-2026-01-23": "qwen3_tiered",
    "qwen3.5-plus": "qwen35_tiered",
    "qwen3.7-plus": "qwen37_tiered",
    "qwen3.7-plus-2026-05-26": "qwen37_tiered",
}
# flat 模型直接用名字查 FLAT_PRICING


def _calc_tiered(input_tokens: int, output_tokens: int, tiers: list[tuple]) -> float:
    """阶梯定价：按 input+output 总量落入区间"""
    total = input_tokens + output_tokens
    for limit, input_price, output_price in tiers:
        if total <= limit:
            return (input_tokens * input_price + output_tokens * output_price) / 1_000_000
    # 超出最大阶梯，用最后一档
    _, input_price, output_price = tiers[-1]
    return (input_tokens * input_price + output_tokens * output_price) / 1_000_000


def calc_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    batch: bool = False,
) -> float:
    """
    计算单次调用费用（元）

    Args:
        model: 模型名
        input_tokens: 输入 token 数
        output_tokens: 输出 token 数
        batch: qwen3.7-plus 是否通过 Batch 调用；Batch 在在线折后价基础上半价

    Returns:
        费用（元），未知模型按 glm-4.7 计算
    """
    pricing_type = MODEL_PRICING_TYPE.get(model)

    if pricing_type == "qwen3_tiered":
        return _calc_tiered(input_tokens, output_tokens, QWEN3_MAX_TIERS)
    if pricing_type == "qwen35_tiered":
        return _calc_tiered(input_tokens, output_tokens, QWEN35_PLUS_TIERS)
    if pricing_type == "qwen37_tiered":
        cost = _calc_tiered(input_tokens, output_tokens, QWEN37_PLUS_TIERS)
        return cost * 0.5 if batch else cost

    # flat 定价
    if model in FLAT_PRICING:
        inp, out = FLAT_PRICING[model]
        return (input_tokens * inp + output_tokens * out) / 1_000_000

    # 模糊匹配
    model_lower = model.lower()
    for key, (inp, out) in FLAT_PRICING.items():
        if key in model_lower:
            return (input_tokens * inp + output_tokens * out) / 1_000_000

    # 未知模型，默认 glm-4.7
    inp, out = FLAT_PRICING["glm-4.7"]
    return (input_tokens * inp + output_tokens * out) / 1_000_000
