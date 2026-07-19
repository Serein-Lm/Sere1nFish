from Sere1nGraph.graph.config.models import AppConfig, McpServerConfig
from Sere1nGraph.graph.tools.mcp import (
    CHROME_DEVTOOLS_MCP_COMMAND,
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
        "args": ["--wsEndpoint=ws://chrome-test:8250/cdp-proxy"],
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
        "--wsEndpoint=ws://chrome-test:8250/cdp-proxy"
    ]
    assert connection["env"] == {"KEEP": "1"}
