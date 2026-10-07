from collections.abc import Mapping

from werkzeug import Request, Response

from dify_plugin import Endpoint

from utils import credentials_form, secret_store
from utils.constants import TOOLSET_FIELDS


class CredentialsGetEndpoint(Endpoint):
    def _invoke(self, r: Request, values: Mapping, settings: Mapping) -> Response:
        token = r.args.get("token", "")
        verified = secret_store.verify_link_token(self.session.storage, token)
        if not verified:
            return Response(
                credentials_form.render_error(
                    "This link is invalid or has expired. Ask the assistant for a new one "
                    "(\"Get My Credentials Link\")."
                ),
                status=400,
                content_type="text/html",
            )

        _user_id, toolset = verified
        fields = TOOLSET_FIELDS.get(toolset)
        if not fields:
            return Response(
                credentials_form.render_error(f"Unknown toolset {toolset!r}."),
                status=400,
                content_type="text/html",
            )

        html = credentials_form.render_form(toolset=toolset, token=token, fields=fields)
        return Response(html, status=200, content_type="text/html")
