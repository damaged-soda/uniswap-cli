from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProviderResult:
    data: Any
    provider: str
    source_id: str
    indexed_block: int | None = None
    next_cursor: str | None = None
    covered_range: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    extra_meta: dict[str, Any] = field(default_factory=dict)
