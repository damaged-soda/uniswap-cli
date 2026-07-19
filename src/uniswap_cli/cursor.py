from __future__ import annotations

import base64
import json
from typing import Any

from uniswap_cli.errors import invalid_argument

CURSOR_VERSION = 1


def encode_cursor(kind: str, chain_id: int, protocol: str, **state: Any) -> str:
    payload = {
        "v": CURSOR_VERSION,
        "kind": kind,
        "chain_id": chain_id,
        "protocol": protocol,
        "state": state,
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str, *, kind: str, chain_id: int, protocol: str) -> dict[str, Any]:
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(cursor + padding))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise invalid_argument("cursor is malformed") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("state"), dict):
        raise invalid_argument("cursor has an invalid shape")
    expected = (CURSOR_VERSION, kind, chain_id, protocol)
    actual = (
        payload.get("v"),
        payload.get("kind"),
        payload.get("chain_id"),
        payload.get("protocol"),
    )
    if actual != expected:
        raise invalid_argument(
            "cursor does not belong to this query",
            expected_kind=kind,
            expected_chain_id=chain_id,
            expected_protocol=protocol,
        )
    return payload["state"]
