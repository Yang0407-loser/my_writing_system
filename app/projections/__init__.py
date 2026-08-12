"""Concrete rebuildable projection adapters for external legacy sinks."""

from .chroma_story import ChromaStoryProjectionAdapter
from .handover import HandoverProjectionAdapter
from .legacy_world import LegacyWorldProjectionAdapter
from .redis_stream import RedisStreamProjectionAdapter
from .task_preview import TaskPreviewProjectionAdapter
from .markdown_export import MarkdownExportProjectionAdapter
from .analytics import AnalyticsProjectionAdapter
from .factory import build_projection_adapters

__all__ = [
    "ChromaStoryProjectionAdapter",
    "HandoverProjectionAdapter",
    "LegacyWorldProjectionAdapter",
    "RedisStreamProjectionAdapter",
    "TaskPreviewProjectionAdapter",
    "MarkdownExportProjectionAdapter",
    "AnalyticsProjectionAdapter",
    "build_projection_adapters",
]
