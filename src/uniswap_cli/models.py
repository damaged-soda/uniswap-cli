from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

SCHEMA_VERSION = "0.1"


def _decimal_components(value: Any) -> tuple[int, int] | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    sign, digits, exponent = number.as_tuple()
    coefficient = int("".join(str(digit) for digit in digits) or "0")
    return (-coefficient if sign and coefficient else coefficient), exponent


def _decimal_from_components(coefficient: int, exponent: int) -> str:
    if coefficient == 0:
        return "0"
    sign = "-" if coefficient < 0 else ""
    digits = str(abs(coefficient))
    if exponent >= 0:
        return sign + digits + ("0" * exponent)
    decimal_places = -exponent
    padded = digits.rjust(decimal_places + 1, "0")
    integer, fraction = padded[:-decimal_places], padded[-decimal_places:].rstrip("0")
    return sign + integer + (f".{fraction}" if fraction else "")


def exact_decimal_difference(left: Any, right: Any) -> str | None:
    left_parts = _decimal_components(left)
    right_parts = _decimal_components(right)
    if left_parts is None or right_parts is None:
        return None
    left_coefficient, left_exponent = left_parts
    right_coefficient, right_exponent = right_parts
    exponent = min(left_exponent, right_exponent)
    coefficient = left_coefficient * (10 ** (left_exponent - exponent))
    coefficient -= right_coefficient * (10 ** (right_exponent - exponent))
    return _decimal_from_components(coefficient, exponent)


def exact_decimal_product(left: Any, right: Any) -> str | None:
    left_parts = _decimal_components(left)
    right_parts = _decimal_components(right)
    if left_parts is None or right_parts is None:
        return None
    return _decimal_from_components(left_parts[0] * right_parts[0], left_parts[1] + right_parts[1])


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
    parts = _decimal_components(value)
    try:
        decimal_places = int(decimals)
    except (ValueError, TypeError):
        return None
    if parts is None or decimal_places < 0:
        return None
    coefficient, exponent = parts
    scaled_exponent = exponent + decimal_places
    if scaled_exponent >= 0:
        raw = coefficient * (10**scaled_exponent)
    else:
        sign = -1 if coefficient < 0 else 1
        raw, remainder = divmod(abs(coefficient), 10 ** (-scaled_exponent))
        if remainder:
            return None
        raw *= sign
    return str(raw)


def token_model(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    decimals = raw.get("decimals")
    address = normalize_address(str(raw.get("id", "")))
    if address is None:
        return None
    return {
        "address": address,
        "symbol": raw.get("symbol"),
        "name": raw.get("name"),
        "decimals": int(decimals) if decimals is not None else None,
    }


def envelope(data: Any, meta: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "data": data, "meta": meta}


def error_envelope(error: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "error": error}
