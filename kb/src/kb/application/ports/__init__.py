"""Protocols the use cases depend on. Implementations live in `kb.adapters`."""

from .clock import Clock, SystemClock
from .entry_store import EntryStore
from .knowledge_source import KnowledgeSource, SourceDocument
from .summarizer import Summarizer, SummaryDraft

__all__ = [
    "Clock",
    "SystemClock",
    "EntryStore",
    "KnowledgeSource",
    "SourceDocument",
    "Summarizer",
    "SummaryDraft",
]
