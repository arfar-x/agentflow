import base64
import json
import logging
from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.config.logger_format import plugin_logger_handler
from dify_plugin.entities.tool import ToolInvokeMessage

from utils import user_credentials
from utils.constants import MCP_SERVER_URL, toolset_for_tool_name
from utils.mcp_client import McpError, McpStreamableHttpClient

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(plugin_logger_handler)


class McpCallTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        tool_name = (tool_parameters.get("tool_name") or "").strip()
        if not tool_name:
            yield self.create_text_message("tool_name is required.")
            return

        arguments_raw = tool_parameters.get("arguments") or "{}"
        try:
            arguments = json.loads(arguments_raw)
            if not isinstance(arguments, dict):
                raise ValueError("arguments must be a JSON object")
        except (json.JSONDecodeError, ValueError) as exc:
            yield self.create_text_message(f"arguments must be a JSON object string: {exc}")
            return

        headers: dict[str, str] = {}
        toolset = toolset_for_tool_name(tool_name)
        user_id = self.runtime.user_id
        if toolset and user_id:
            stored = user_credentials.load(self.session.storage, user_id=user_id, toolset=toolset)
            if stored:
                headers = {f"X-Agent-Skills-Env-{env_var}": value for env_var, value in stored.items()}
                logger.info("mcp_call_tool: injecting %d stored %s credential(s) for this caller", len(headers), toolset)

        client = None
        try:
            client = McpStreamableHttpClient(MCP_SERVER_URL, headers=headers)
            client.initialize()
            result = client.call_tool(tool_name, arguments)
        except McpError as exc:
            yield self.create_text_message(f"agent-skills call failed: {exc}")
            return
        finally:
            if client:
                client.close()

        content = result.get("content", [])
        if not content:
            yield self.create_json_message(result)
            return

        for item in content:
            item_type = item.get("type")
            if item_type == "text":
                yield self.create_text_message(item.get("text", ""))
            elif item_type in ("image", "video", "audio") and item.get("data"):
                blob = base64.b64decode(item["data"])
                yield self.create_blob_message(
                    blob=blob, meta={"type": item_type, "mime_type": item.get("mimeType")}
                )
            else:
                yield self.create_json_message(item)
