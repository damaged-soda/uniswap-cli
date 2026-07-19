from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

SCHEMA_VERSION = "0.1"


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def utc_from_timestamp(value: int | str | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(int(value), tz=UTC).isoformat().replace("+00:00", "Z")


def normalize_address(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip().lower()
    return text or None


def decimal_string(value: Any) -> str | None:
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return str(value)
    if not number.is_finite():
        return str(number)
    rendered = format(number, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def decimal_to_raw(value: Any, decimals: int | str | None) -> str | None:
    if value is None or decimals is None:
        return None
    try:
        scaled = Decimal(str(value)) * (Decimal(10) ** int(decimals))
    except (InvalidOperation, ValueError, TypeError):
        return None
    integral = scaled.to_integral_value()
    if scaled != integral:
        return None
    return str(int(integral))


def token_model(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    decimals = raw.get("decimals")
    return {
        "address": normalize_address(str(raw.get("id", ""))),
        "symbol": raw.get("symbol"),
        "name": raw.get("name"),
        "decimals": int(decimals) if decimals is not None else None,
    }


def envelope(data: Any, meta: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "data": data, "meta": meta}


def error_envelope(error: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "error": error}
