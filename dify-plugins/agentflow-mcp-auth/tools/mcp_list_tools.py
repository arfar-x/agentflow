from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from utils.constants import MCP_SERVER_URL
from utils.mcp_client import McpError, McpStreamableHttpClient


class McpListTools(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        client = None
        try:
            client = McpStreamableHttpClient(MCP_SERVER_URL)
            client.initialize()
            tools = client.list_tools()
        except McpError as exc:
            yield self.create_text_message(f"could not list agent-skills tools: {exc}")
            return
        finally:
            if client:
                client.close()

        yield self.create_json_message({"tools": tools})
