from Sere1nGraph.graph.config.models import AppConfig, McpServerConfig
from Sere1nGraph.graph.tools.mcp import (
    CHROME_ACCEPT_INSECURE_CERTS_ARG,
    CHROME_DEVTOOLS_MCP_COMMAND,
    CHROME_MCP_PRELOAD_PATH,
    build_chrome_mcp_connection,
    build_mcp_connections,
)


def test_dynamic_chrome_connection_uses_image_pinned_binary() -> None:
    connection = build_chrome_mcp_connection(
        "ws://chrome-test:8250/cdp-proxy"
    )["chrome-devtools"]

    assert connection == {
        "transport": "stdio",
        "command": CHROME_DEVTOOLS_MCP_COMMAND,
        "args": [
            "--wsEndpoint=ws://chrome-test:8250/cdp-proxy",
            CHROME_ACCEPT_INSECURE_CERTS_ARG,
        ],
        "env": {
            "NODE_OPTIONS": f"--require={CHROME_MCP_PRELOAD_PATH}",
        },
    }


def test_configured_chrome_connection_normalizes_legacy_npx_arguments() -> None:
    app_config = AppConfig(
        mcp_servers={
            "chrome-devtools": McpServerConfig(
                name="chrome-devtools",
                command="npx",
                args=[
                    "-y",
                    "chrome-devtools-mcp@latest",
                    "--wsEndpoint=ws://chrome-test:8250/cdp-proxy",
                ],
                env={"KEEP": "1"},
            )
        }
    )

    connection = build_mcp_connections(
        app_config,
        server_names="chrome-devtools",
    )["chrome-devtools"]

    assert connection["command"] == CHROME_DEVTOOLS_MCP_COMMAND
    assert connection["args"] == [
        "--wsEndpoint=ws://chrome-test:8250/cdp-proxy",
        CHROME_ACCEPT_INSECURE_CERTS_ARG,
    ]
    assert connection["env"] == {
        "KEEP": "1",
        "NODE_OPTIONS": f"--require={CHROME_MCP_PRELOAD_PATH}",
    }


def test_readonly_research_profile_keeps_navigation_and_snapshot_runtime() -> None:
    app_config = AppConfig(
        mcp_servers={
            "chrome-devtools": McpServerConfig(
                name="chrome-devtools",
                command="chrome-devtools-mcp",
                args=["--wsEndpoint=ws://chrome-test:8250/cdp-proxy"],
            )
        }
    )

    connection = build_mcp_connections(
        app_config,
        server_names="chrome-devtools",
        server_profile="readonly_research",
    )["chrome-devtools"]

    assert connection["args"] == [
        "--wsEndpoint=ws://chrome-test:8250/cdp-proxy",
        CHROME_ACCEPT_INSECURE_CERTS_ARG,
        "--no-category-emulation",
        "--no-category-performance",
        "--no-category-network",
        "--no-performance-crux",
        "--no-usage-statistics",
    ]
    assert connection["env"]["NODE_OPTIONS"] == (
        f"--require={CHROME_MCP_PRELOAD_PATH}"
    )
