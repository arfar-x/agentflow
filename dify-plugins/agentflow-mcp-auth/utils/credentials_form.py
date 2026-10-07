"""Renders the tiny self-contained HTML form the credentials Endpoint
serves. No external CSS/JS (this is served from an internal plugin
Endpoint, not meant to be pretty) -- just enough to be legible and to post
back to itself.
"""

from __future__ import annotations

from html import escape

from utils.constants import ToolsetField

_PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="robots" content="noindex, nofollow">
<title>agentflow -- {title}</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 32rem; margin: 3rem auto; padding: 0 1rem; color: #1a1a1a; }}
  label {{ display: block; margin-top: 1rem; font-weight: 600; }}
  input {{ width: 100%; padding: 0.5rem; margin-top: 0.25rem; box-sizing: border-box; }}
  button {{ margin-top: 1.5rem; padding: 0.6rem 1.2rem; cursor: pointer; }}
  .notice {{ padding: 0.75rem 1rem; border-radius: 0.25rem; margin-bottom: 1rem; }}
  .error {{ background: #fdecea; color: #611a15; }}
  .success {{ background: #e6f4ea; color: #1e4620; }}
  .hint {{ color: #555; font-size: 0.85em; margin-top: 0.15rem; }}
</style>
</head>
<body>
<h2>{heading}</h2>
{body}
</body>
</html>
"""


def render_error(message: str) -> str:
    return _PAGE.format(
        title="Link error",
        heading="This link isn't usable",
        body=f'<div class="notice error">{escape(message)}</div>',
    )


def render_success(toolset: str) -> str:
    return _PAGE.format(
        title="Saved",
        heading=f"{escape(toolset.capitalize())} credentials saved",
        body=(
            '<div class="notice success">Saved. You can close this tab and go back to your '
            "conversation -- your calls will now use these credentials.</div>"
        ),
    )


def render_form(*, toolset: str, token: str, fields: list[ToolsetField], error: str | None = None) -> str:
    error_html = f'<div class="notice error">{escape(error)}</div>' if error else ""
    field_html = []
    for field in fields:
        input_type = "password" if field.sensitive else "text"
        required_attr = " required" if field.required else ""
        field_html.append(
            f'<label for="{escape(field.env_var)}">{escape(field.label)}</label>'
            f'<input type="{input_type}" id="{escape(field.env_var)}" name="{escape(field.env_var)}"'
            f'{required_attr} autocomplete="off">'
        )
    body = (
        f"{error_html}"
        f'<form method="POST">'
        f'<input type="hidden" name="token" value="{escape(token)}">'
        f"{''.join(field_html)}"
        f'<div class="hint">These are stored only for your own use, encrypted, and never shown to the AI.</div>'
        f'<button type="submit">Save</button>'
        f"</form>"
    )
    return _PAGE.format(title=f"Set {toolset.capitalize()} credentials", heading=f"Set your {toolset.capitalize()} credentials", body=body)
