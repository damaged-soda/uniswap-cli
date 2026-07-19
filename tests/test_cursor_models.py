from __future__ import annotations

import pytest

from uniswap_cli.cursor import decode_cursor, encode_cursor
from uniswap_cli.errors import UniswapError
from uniswap_cli.models import (
    decimal_string,
    decimal_to_raw,
    exact_decimal_difference,
    exact_decimal_product,
)


def test_cursor_round_trip_and_scope() -> None:
    cursor = encode_cursor("swaps-list", 1, "v3", offset=20)
    assert decode_cursor(cursor, kind="swaps-list", chain_id=1, protocol="v3") == {"offset": 20}
    with pytest.raises(UniswapError):
        decode_cursor(cursor, kind="pools-list", chain_id=1, protocol="v3")


def test_decimal_rendering_and_raw_conversion() -> None:
    assert decimal_string("10.5000") == "10.5"
    assert decimal_to_raw("10.5", 6) == "10500000"
    assert decimal_to_raw("0.0000001", 6) is None
    assert (
        decimal_to_raw("-500000000000.123456789012345678", 18) == "-500000000000123456789012345678"
    )
    assert (
        exact_decimal_difference("500000000000.123456789012345678", "0")
        == "500000000000.123456789012345678"
    )
    assert (
        exact_decimal_product("1234.5678901234567890123456789012", "0.003")
        == "3.7037036703703703670370370367036"
    )
