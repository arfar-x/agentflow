from collections.abc import Mapping

from werkzeug import Request, Response

from dify_plugin import Endpoint

from utils import credentials_form, secret_store, user_credentials
from utils.constants import TOOLSET_FIELDS


class CredentialsPostEndpoint(Endpoint):
    def _invoke(self, r: Request, values: Mapping, settings: Mapping) -> Response:
        token = r.form.get("token", "")
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

        user_id, toolset = verified
        fields = TOOLSET_FIELDS.get(toolset)
        if not fields:
            return Response(
                credentials_form.render_error(f"Unknown toolset {toolset!r}."),
                status=400,
                content_type="text/html",
            )

        values_in = {field.env_var: r.form.get(field.env_var, "") for field in fields}
        try:
            user_credentials.save(self.session.storage, user_id=user_id, toolset=toolset, values=values_in)
        except ValueError as exc:
            html = credentials_form.render_form(toolset=toolset, token=token, fields=fields, error=str(exc))
            return Response(html, status=400, content_type="text/html")

        return Response(credentials_form.render_success(toolset), status=200, content_type="text/html")
