"""Turning pydantic's validation errors into something an operator can act on.

A sync run reads hundreds of documents from systems nobody in this repo
controls. When one of them can't become an entry, the log line has to say which
document and which field -- "3 validation errors for Entry" in a container log
is not an answer anyone can use.
"""

from __future__ import annotations

from pydantic import ValidationError


def describe(error: ValidationError) -> tuple[str, ...]:
    """Every problem in one validation failure, as readable sentences.

    All of them, not just the first: one malformed source should produce one
    complete report rather than one field fixed per run.
    """
    described: list[str] = []
    for detail in error.errors():
        location = ".".join(str(part) for part in detail["loc"]) or "<model>"
        described.append(f"{location}: {detail['msg']}")
    return tuple(described)


def describe_for(subject: str, error: ValidationError) -> str:
    """One log-ready line naming the document that failed."""
    return f"{subject}: " + "; ".join(describe(error))
