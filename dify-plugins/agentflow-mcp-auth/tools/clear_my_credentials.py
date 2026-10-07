from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from utils import user_credentials
from utils.constants import TOOLSET_FIELDS


class ClearMyCredentials(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        toolset = (tool_parameters.get("toolset") or "").strip().lower()
        if toolset not in TOOLSET_FIELDS:
            yield self.create_text_message(f"Unknown toolset {toolset!r}. Expected one of: {', '.join(TOOLSET_FIELDS)}.")
            return

        user_id = self.runtime.user_id
        if not user_id:
            yield self.create_text_message("Could not identify you as a specific end user for this conversation.")
            return

        user_credentials.clear(self.session.storage, user_id=user_id, toolset=toolset)
        yield self.create_text_message(f"Your stored {toolset.capitalize()} credentials have been deleted.")
