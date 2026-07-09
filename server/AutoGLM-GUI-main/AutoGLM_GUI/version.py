"""Package version helper."""

from importlib.metadata import version as get_version

try:
    APP_VERSION = get_version("autoglm-gui")
except Exception:
    APP_VERSION = "dev"
