"""Build ModelConfig from UnifiedConfigManager effective settings."""

from __future__ import annotations

from AutoGLM_GUI.config import ModelConfig, disable_thinking_extra_body


def model_config_from_effective_config(effective_config: object) -> ModelConfig:
    """Map effective API config into ModelConfig for agents."""
    return ModelConfig(
        base_url=getattr(effective_config, "base_url"),
        api_key=getattr(effective_config, "api_key"),
        model_name=getattr(effective_config, "model_name"),
        max_tokens=getattr(effective_config, "max_tokens", 3000),
        temperature=getattr(effective_config, "temperature", 0.0),
        top_p=getattr(effective_config, "top_p", 0.85),
        frequency_penalty=getattr(effective_config, "frequency_penalty", 0.2),
        extra_body=disable_thinking_extra_body(
            getattr(effective_config, "extra_body", None)
        ),
        lang=getattr(effective_config, "lang", "cn"),
    )
