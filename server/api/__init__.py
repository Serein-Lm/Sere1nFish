"""
FastAPI 应用模块
"""

# 让 Python 能从项目内置的 AutoGLM-GUI-main/ 源码导入 AutoGLM_GUI 包。
# 采用 sys.path 注入而非 editable 安装,避免 pip 解析 AutoGLM 的版本约束
# (fastapi>=0.124 / openai>=2.9 等) 而强升本项目 pin 的 pydantic==2.5.2。
import sys as _sys
from pathlib import Path as _Path

_autoglm_src = _Path(__file__).resolve().parent.parent / "AutoGLM-GUI-main"
if _autoglm_src.exists() and str(_autoglm_src) not in _sys.path:
    _sys.path.insert(0, str(_autoglm_src))
