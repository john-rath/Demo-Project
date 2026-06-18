"""
Local, credential-free asset validation for dd-demo-toolkit.

Encodes the STYLE_GUIDE.md rules as machine checks that run BEFORE any
Datadog API call — catching the deploy-time 400s and "No data" footguns
locally. Importable by the CLI (`dd-demo validate`), the UI server
(in-process), and CI.
"""

from .core import Finding, Severity
from .runner import (
    ALL_RESOURCE_TYPES,
    format_text,
    summarize,
    validate_vertical,
)

__all__ = [
    "Finding",
    "Severity",
    "validate_vertical",
    "summarize",
    "format_text",
    "ALL_RESOURCE_TYPES",
]
