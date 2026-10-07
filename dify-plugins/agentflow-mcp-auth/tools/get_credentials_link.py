from collections.abc import Generator
from typing import Any
from urllib.parse import urlencode

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from utils import secret_store
from utils.constants import LINK_TTL_SECONDS, TOOLSET_FIELDS


class GetCredentialsLink(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        toolset = (tool_parameters.get("toolset") or "").strip().lower()
        if toolset not in TOOLSET_FIELDS:
            yield self.create_text_message(f"Unknown toolset {toolset!r}. Expected one of: {', '.join(TOOLSET_FIELDS)}.")
            return

        user_id = self.runtime.user_id
        if not user_id:
            yield self.create_text_message(
                "Could not identify you as a specific end user for this conversation, "
                "so a personal credentials link can't be issued here."
            )
            return

        base_url = (self.runtime.credentials.get("credentials_endpoint_url") or "").strip()
        if not base_url:
            yield self.create_text_message(
                "This deployment hasn't finished setup yet: the agentflow MCP Auth Bridge "
                "provider's 'Credentials Endpoint URL' isn't configured. Ask an admin to run "
                "scripts/bootstrap.sh (see docs/CONFIGURATION.md)."
            )
            return

        token = secret_store.sign_link_token(
            self.session.storage, user_id=user_id, toolset=toolset, ttl_seconds=LINK_TTL_SECONDS
        )
        link = f"{base_url}?{urlencode({'token': token})}"
        minutes = LINK_TTL_SECONDS // 60
        yield self.create_text_message(
            f"Open this link yourself to set your own {toolset.capitalize()} credentials "
            f"(valid for {minutes} minutes, usable once you submit it, never sent through this chat):\n\n{link}"
        )
        yield self.create_json_message({"toolset": toolset, "link": link, "expires_in_seconds": LINK_TTL_SECONDS})
