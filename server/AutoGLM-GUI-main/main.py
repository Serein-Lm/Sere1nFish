"""AutoGLM-GUI Backend API Server.

This module is kept for backward compatibility and development.
For production use, run: autoglm-gui (or uvx autoglm-gui)
"""

# Re-export app from the package
from AutoGLM_GUI.server import app

if __name__ == "__main__":
    import uvicorn

    from AutoGLM_GUI.config_manager import init_runtime_config

    init_runtime_config()
    uvicorn.run(app, host="0.0.0.0", port=8000)
