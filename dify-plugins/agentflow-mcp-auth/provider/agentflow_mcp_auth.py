from typing import Any

from dify_plugin import ToolProvider
from dify_plugin.errors.tool import ToolProviderCredentialValidationError


class AgentflowMcpAuthProvider(ToolProvider):
    def _validate_credentials(self, credentials: dict[str, Any]) -> None:
        # credentials_endpoint_url is intentionally optional (see this
        # provider's own yaml) -- it's blank until scripts/bootstrap.sh sets
        # it, and tools work without it except get_credentials_link, which
        # reports that plainly rather than failing here. All that's worth
        # validating up front is that a *non-empty* value at least looks
        # like a URL, to catch a typo'd bootstrap.sh run early.
        url = (credentials.get("credentials_endpoint_url") or "").strip()
        if url and not (url.startswith("http://") or url.startswith("https://")):
            raise ToolProviderCredentialValidationError(
                "credentials_endpoint_url must start with http:// or https://"
            )
