"""
服务层模块

包含各平台的信息采集流水线:
- XhsPipeline: 小红书信息采集
- DouyinPipeline: 抖音信息采集
- WebTaggingPipeline: 官网信息采集（Hunter + Agent 打标）
"""

__all__ = [
    "XhsPipeline",
    "run_xhs_pipeline",
    "DouyinPipeline",
    "run_douyin_pipeline",
    "WebTaggingPipeline",
    "run_web_tagging_pipeline",
]

_EXPORTS = {
    "XhsPipeline": (".xhs_pipeline", "XhsPipeline"),
    "run_xhs_pipeline": (".xhs_pipeline", "run_xhs_pipeline"),
    "DouyinPipeline": (".douyin_pipeline", "DouyinPipeline"),
    "run_douyin_pipeline": (".douyin_pipeline", "run_douyin_pipeline"),
    "WebTaggingPipeline": (".web_tagging_pipeline", "WebTaggingPipeline"),
    "run_web_tagging_pipeline": (".web_tagging_pipeline", "run_web_tagging_pipeline"),
}


def __getattr__(name: str):
    """Lazy-load heavy service exports on first access."""
    if name not in _EXPORTS:
        raise AttributeError(name)
    module_name, attr_name = _EXPORTS[name]
    from importlib import import_module

    value = getattr(import_module(module_name, __name__), attr_name)
    globals()[name] = value
    return value
