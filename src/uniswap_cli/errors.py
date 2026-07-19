from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

_SECRET_KEY_RE = re.compile(
    r"(?:api.?key|access.?token|auth(?:orization)?|bearer|secret|credential|password)", re.I
)
_URL_RE = re.compile(r"https?://[^\s\"']+", re.I)


def safe_endpoint(value: str) -> str:
    """Keep only scheme and host so path-embedded API keys cannot leak."""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "[redacted-endpoint]"
    if parsed.scheme in {"http", "https"} and parsed.hostname:
        host = parsed.hostname
        if ":" in host:
            host = f"[{host}]"
        try:
            port = parsed.port
        except ValueError:
            return "[redacted-endpoint]"
        authority = f"{host}:{port}" if port is not None else host
        return f"{parsed.scheme}://{authority}/***"
    return "[redacted-endpoint]"


def redact_text(value: str) -> str:
    return _URL_RE.sub(lambda match: safe_endpoint(match.group(0)), value)


def sanitize_context(value: Any, *, key: str | None = None) -> Any:
    if key and _SECRET_KEY_RE.search(key):
        return "[redacted]"
    if isinstance(value, Mapping):
        return {str(k): sanitize_context(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_context(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(str(value))


@dataclass(eq=False)
class UniswapError(RuntimeError):
    code: str
    message: str
    retryable: bool = False
    context: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.message = redact_text(self.message)
        self.context = sanitize_context(self.context)
        super().__init__(self.message)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "context": self.context,
        }


def invalid_argument(message: str, **context: Any) -> UniswapError:
    return UniswapError("INVALID_ARGUMENT", message, context=context)


def unsupported(message: str, **context: Any) -> UniswapError:
    return UniswapError("UNSUPPORTED", message, context=context)
