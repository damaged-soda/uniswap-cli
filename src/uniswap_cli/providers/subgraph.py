from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from uniswap_cli.config import SUBGRAPH_DEPLOYMENTS, Endpoint, Settings
from uniswap_cli.cursor import decode_cursor, encode_cursor
from uniswap_cli.errors import UniswapError, invalid_argument, unsupported
from uniswap_cli.http import JsonHttpClient
from uniswap_cli.models import (
    decimal_string,
    decimal_to_raw,
    exact_decimal_difference,
    exact_decimal_product,
    normalize_address,
    token_model,
    utc_from_timestamp,
)
from uniswap_cli.providers.base import ProviderResult

_META = "_meta { block { number hash } deployment hasIndexingErrors }"
_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_POOL_ID_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")

_POOL_ORDER_FIELDS = {
    "v2": {
        "tvl-usd": "reserveUSD",
        "volume-usd": "volumeUSD",
        "created": "createdAtTimestamp",
        "tx-count": "txCount",
    },
    "v3": {
        "tvl-usd": "totalValueLockedUSD",
        "volume-usd": "volumeUSD",
        "created": "createdAtTimestamp",
        "tx-count": "txCount",
    },
    "v4": {
        "tvl-usd": "totalValueLockedUSD",
        "volume-usd": "volumeUSD",
        "created": "createdAtTimestamp",
        "tx-count": "txCount",
    },
}


def _compact(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item is not None}


def _validate_limit(limit: int) -> None:
    if limit < 1 or limit > 1_000:
        raise invalid_argument("limit must be between 1 and 1000", limit=limit)


def _validate_token_address(value: str, *, field: str = "address") -> str:
    if not _ADDRESS_RE.fullmatch(value):
        raise invalid_argument(f"{field} must be a 20-byte 0x-prefixed address")
    return value.lower()


def _validate_pool_id(value: str, protocol: str) -> str:
    expected = _POOL_ID_RE if protocol == "v4" else _ADDRESS_RE
    if not expected.fullmatch(value):
        label = "32-byte pool ID" if protocol == "v4" else "20-byte pool address"
        raise invalid_argument(f"pool must be a {label}", protocol=protocol)
    return value.lower()


def _page_state(
    cursor: str | None,
    *,
    kind: str,
    chain_id: int,
    protocol: str,
    query: dict[str, Any],
) -> tuple[Any | None, int]:
    if cursor is None:
        return None, 0
    state = decode_cursor(cursor, kind=kind, chain_id=chain_id, protocol=protocol)
    if state.get("query") != query:
        raise invalid_argument("cursor filters do not match this query")
    boundary = state.get("boundary")
    tie_offset = state.get("tie_offset")
    if boundary is None or not isinstance(tie_offset, int) or tie_offset < 1:
        raise invalid_argument("cursor contains an invalid page boundary")
    if tie_offset > 5_000:
        raise UniswapError(
            "SUBGRAPH_TIE_WINDOW_TOO_LARGE",
            "more than 5000 rows share one pagination boundary",
            context={"tie_offset": tie_offset},
        )
    return boundary, tie_offset


def _next_page_state(
    values: list[Any], previous_boundary: Any | None, previous_tie_offset: int
) -> tuple[Any, int]:
    if not values or values[-1] is None:
        raise UniswapError("SUBGRAPH_INVALID_RESPONSE", "page boundary field is missing")
    boundary = values[-1]
    if previous_boundary is not None and str(boundary) == str(previous_boundary):
        return boundary, previous_tie_offset + len(values)
    trailing = 0
    for value in reversed(values):
        if str(value) != str(boundary):
            break
        trailing += 1
    return boundary, trailing


def _object_rows(value: Any, *, entity: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise UniswapError(
            "SUBGRAPH_INVALID_RESPONSE",
            f"{entity} collection contains a non-object row",
            context={"entity": entity},
        )
    return value


def _numeric_boundary(value: Any | None) -> Any | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise invalid_argument("cursor contains an invalid numeric boundary")
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as exc:
        raise invalid_argument("cursor contains an invalid numeric boundary") from exc
    if not parsed.is_finite():
        raise invalid_argument("cursor contains an invalid numeric boundary")
    return value


def _timestamp_boundary(value: Any | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise invalid_argument("cursor contains an invalid timestamp boundary")
    return value


def _meta_from_data(data: dict[str, Any]) -> tuple[int | None, dict[str, Any]]:
    raw = data.get("_meta")
    if not isinstance(raw, dict):
        return None, {}
    block = raw.get("block") if isinstance(raw.get("block"), dict) else {}
    number = block.get("number")
    meta = {
        "indexed_block_hash": block.get("hash"),
        "deployment": raw.get("deployment"),
        "has_indexing_errors": raw.get("hasIndexingErrors"),
    }
    return int(number) if number is not None else None, _compact(meta)


def _required_token(raw: Any, *, field: str) -> dict[str, Any]:
    model = token_model(raw if isinstance(raw, dict) else None)
    address = model.get("address") if model else None
    if not isinstance(address, str) or not _ADDRESS_RE.fullmatch(address):
        raise UniswapError(
            "SUBGRAPH_INVALID_RESPONSE",
            f"{field} is missing a valid token address",
            context={"field": field},
        )
    return model


def _required_int(raw: dict[str, Any], field: str) -> int:
    value = raw.get(field)
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise UniswapError(
            "SUBGRAPH_INVALID_RESPONSE",
            f"{field} is missing or invalid",
            context={"field": field},
        ) from exc


def _required_text(raw: dict[str, Any], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value:
        raise UniswapError(
            "SUBGRAPH_INVALID_RESPONSE",
            f"{field} is missing or invalid",
            context={"field": field},
        )
    return value


def _token_with_metrics(raw: dict[str, Any], protocol: str) -> dict[str, Any]:
    result = _required_token(raw, field="token")
    if protocol == "v2":
        result.update(
            _compact(
                {
                    "volume": decimal_string(raw.get("tradeVolume")),
                    "volume_usd": decimal_string(raw.get("tradeVolumeUSD")),
                    "tvl": decimal_string(raw.get("totalLiquidity")),
                    "tvl_usd": decimal_string(raw.get("totalLiquidityUSD")),
                    "tx_count": decimal_string(raw.get("txCount")),
                    "derived_native": decimal_string(raw.get("derivedETH")),
                }
            )
        )
    else:
        result.update(
            _compact(
                {
                    "volume": decimal_string(raw.get("volume")),
                    "volume_usd": decimal_string(raw.get("volumeUSD")),
                    "fees_usd": decimal_string(raw.get("feesUSD")),
                    "tvl": decimal_string(raw.get("totalValueLocked")),
                    "tvl_usd": decimal_string(raw.get("totalValueLockedUSD")),
                    "tx_count": decimal_string(raw.get("txCount")),
                    "pool_count": decimal_string(raw.get("poolCount")),
                    "derived_native": decimal_string(raw.get("derivedETH")),
                }
            )
        )
    return result


def _pool_v2(raw: dict[str, Any]) -> dict[str, Any]:
    pool_id = _required_text(raw, "id").lower()
    if not _ADDRESS_RE.fullmatch(pool_id):
        raise UniswapError("SUBGRAPH_INVALID_RESPONSE", "pool id is not an address")
    return _compact(
        {
            "id": pool_id,
            "pool_address": pool_id,
            "protocol_version": "v2",
            "token0": _required_token(raw.get("token0"), field="token0"),
            "token1": _required_token(raw.get("token1"), field="token1"),
            "fee_tier": "3000",
            "created_at": utc_from_timestamp(raw.get("createdAtTimestamp")),
            "created_at_timestamp": int(raw["createdAtTimestamp"])
            if raw.get("createdAtTimestamp") is not None
            else None,
            "created_at_block": int(raw["createdAtBlockNumber"])
            if raw.get("createdAtBlockNumber") is not None
            else None,
            "reserve0": decimal_string(raw.get("reserve0")),
            "reserve1": decimal_string(raw.get("reserve1")),
            "tvl_usd": decimal_string(raw.get("reserveUSD")),
            "token0_price": decimal_string(raw.get("token0Price")),
            "token1_price": decimal_string(raw.get("token1Price")),
            "volume_token0": decimal_string(raw.get("volumeToken0")),
            "volume_token1": decimal_string(raw.get("volumeToken1")),
            "volume_usd": decimal_string(raw.get("volumeUSD")),
            "tx_count": decimal_string(raw.get("txCount")),
        }
    )


def _pool_v34(raw: dict[str, Any], protocol: str) -> dict[str, Any]:
    pool_id = _required_text(raw, "id").lower()
    expected = _POOL_ID_RE if protocol == "v4" else _ADDRESS_RE
    if not expected.fullmatch(pool_id):
        raise UniswapError("SUBGRAPH_INVALID_RESPONSE", "pool id has an invalid shape")
    return _compact(
        {
            "id": pool_id,
            "pool_address": pool_id if protocol == "v3" else None,
            "protocol_version": protocol,
            "token0": _required_token(raw.get("token0"), field="token0"),
            "token1": _required_token(raw.get("token1"), field="token1"),
            "fee_tier": decimal_string(raw.get("feeTier")),
            "tick_spacing": decimal_string(raw.get("tickSpacing")),
            "hooks": normalize_address(raw.get("hooks")),
            "external_liquidity": raw.get("isExternalLiquidity"),
            "created_at": utc_from_timestamp(raw.get("createdAtTimestamp")),
            "created_at_timestamp": int(raw["createdAtTimestamp"])
            if raw.get("createdAtTimestamp") is not None
            else None,
            "created_at_block": int(raw["createdAtBlockNumber"])
            if raw.get("createdAtBlockNumber") is not None
            else None,
            "liquidity_raw": decimal_string(raw.get("liquidity")),
            "sqrt_price_x96": decimal_string(raw.get("sqrtPrice")),
            "tick": decimal_string(raw.get("tick")),
            "token0_price": decimal_string(raw.get("token0Price")),
            "token1_price": decimal_string(raw.get("token1Price")),
            "tvl_token0": decimal_string(raw.get("totalValueLockedToken0")),
            "tvl_token1": decimal_string(raw.get("totalValueLockedToken1")),
            "tvl_usd": decimal_string(raw.get("totalValueLockedUSD")),
            "volume_token0": decimal_string(raw.get("volumeToken0")),
            "volume_token1": decimal_string(raw.get("volumeToken1")),
            "volume_usd": decimal_string(raw.get("volumeUSD")),
            "fees_usd": decimal_string(raw.get("feesUSD")),
            "tx_count": decimal_string(raw.get("txCount")),
        }
    )


def _signed_v2_amount(raw: dict[str, Any], side: str) -> str:
    amount = exact_decimal_difference(
        raw.get(f"amount{side}In", "0"), raw.get(f"amount{side}Out", "0")
    )
    if amount is None:
        raise UniswapError("SUBGRAPH_INVALID_RESPONSE", "v2 swap amount is invalid")
    return amount


def _swap_model(raw: dict[str, Any], protocol: str) -> dict[str, Any]:
    pool_raw = raw.get("pair") if protocol == "v2" else raw.get("pool")
    pool_raw = pool_raw if isinstance(pool_raw, dict) else {}
    token0_raw = pool_raw.get("token0") or raw.get("token0")
    token1_raw = pool_raw.get("token1") or raw.get("token1")
    token0 = _required_token(token0_raw, field="token0")
    token1 = _required_token(token1_raw, field="token1")
    if protocol == "v2":
        amount0 = _signed_v2_amount(raw, "0")
        amount1 = _signed_v2_amount(raw, "1")
    else:
        amount0 = decimal_string(raw.get("amount0")) or "0"
        amount1 = decimal_string(raw.get("amount1")) or "0"
    tx = raw.get("transaction") if isinstance(raw.get("transaction"), dict) else {}
    timestamp = raw.get("timestamp") or tx.get("timestamp")
    swap_id = _required_text(raw, "id")
    pool_id = normalize_address(pool_raw.get("id"))
    transaction_hash = normalize_address(tx.get("id"))
    if not isinstance(pool_id, str) or not (
        _ADDRESS_RE.fullmatch(pool_id) or _POOL_ID_RE.fullmatch(pool_id)
    ):
        raise UniswapError("SUBGRAPH_INVALID_RESPONSE", "swap pool id is missing or invalid")
    if not isinstance(transaction_hash, str) or not _POOL_ID_RE.fullmatch(transaction_hash):
        raise UniswapError(
            "SUBGRAPH_INVALID_RESPONSE", "swap transaction hash is missing or invalid"
        )
    log_index = _required_int(raw, "logIndex")
    block_number = _required_int(tx, "blockNumber")
    timestamp_value = _required_int({"timestamp": timestamp}, "timestamp")
    amount0_raw = decimal_to_raw(amount0, token0.get("decimals"))
    amount1_raw = decimal_to_raw(amount1, token1.get("decimals"))
    if amount0_raw is None or amount1_raw is None:
        raise UniswapError(
            "SUBGRAPH_INVALID_RESPONSE",
            "swap amount cannot be converted to exact raw units",
            context={"id": swap_id},
        )
    return {
        "id": swap_id,
        "protocol_version": protocol,
        "pool_id": pool_id,
        "transaction_hash": transaction_hash,
        "log_index": log_index,
        "block_number": block_number,
        "timestamp": timestamp_value,
        "datetime": utc_from_timestamp(timestamp_value),
        "sender": normalize_address(raw.get("sender")),
        "recipient": normalize_address(raw.get("to") or raw.get("recipient")),
        "origin": normalize_address(raw.get("from") or raw.get("origin")),
        "token0": token0,
        "token1": token1,
        "amount0": amount0,
        "amount1": amount1,
        "amount0_raw": amount0_raw,
        "amount1_raw": amount1_raw,
        "amount_usd": decimal_string(raw.get("amountUSD")),
        "sqrt_price_x96": decimal_string(raw.get("sqrtPriceX96")),
        "liquidity_raw": None,
        "tick": decimal_string(raw.get("tick")),
    }


class SubgraphProvider:
    def __init__(
        self,
        settings: Settings,
        chain_id: int,
        protocol: str,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.chain_id = chain_id
        self.protocol = protocol
        self.endpoint: Endpoint = settings.subgraph_endpoint(chain_id, protocol)
        self.http = JsonHttpClient(settings, client=http_client)

    async def __aenter__(self) -> SubgraphProvider:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        await self.close()

    async def close(self) -> None:
        await self.http.close()

    async def _query(
        self,
        query: str,
        variables: dict[str, Any] | None,
        *,
        operation: str,
    ) -> tuple[dict[str, Any], int | None, dict[str, Any]]:
        headers = {"content-type": "application/json", **dict(self.endpoint.headers)}
        payload = await self.http.request(
            "POST",
            self.endpoint.url,
            endpoint_label=self.endpoint.label,
            operation=operation,
            headers=headers,
            json_body={"query": query, "variables": variables or {}},
        )
        errors = payload.get("errors") if isinstance(payload, dict) else None
        if errors:
            message = "; ".join(
                str(item.get("message", item)) if isinstance(item, dict) else str(item)
                for item in errors[:3]
            )
            for header_value in self.endpoint.headers.values():
                message = message.replace(header_value, "[redacted]")
                if header_value.lower().startswith("bearer "):
                    message = message.replace(header_value[7:], "[redacted]")
            lower = message.lower()
            if "auth" in lower or "authorization" in lower:
                code = "SUBGRAPH_AUTH_FAILED"
            elif "indexing_error" in lower or "indexing error" in lower:
                code = "SUBGRAPH_INDEXING_ERROR"
            else:
                code = "SUBGRAPH_QUERY_FAILED"
            raise UniswapError(
                code,
                f"{operation} failed: {message}",
                retryable="timeout" in lower,
                context={"endpoint": self.endpoint.label},
            )
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise UniswapError(
                "SUBGRAPH_INVALID_RESPONSE",
                f"{operation} returned no data object",
                context={"endpoint": self.endpoint.label},
            )
        indexed_block, graph_meta = _meta_from_data(data)
        return data, indexed_block, graph_meta

    def _source_id(self) -> str:
        deployment = SUBGRAPH_DEPLOYMENTS.get((self.chain_id, self.protocol))
        return deployment.subgraph_id if deployment else self.endpoint.label

    async def health(self) -> ProviderResult:
        data, indexed_block, graph_meta = await self._query(
            f"query Health {{ {_META} }}", {}, operation="subgraph health"
        )
        return ProviderResult(
            data={
                "ok": not bool(data.get("_meta", {}).get("hasIndexingErrors")),
                "indexed_block": indexed_block,
                **graph_meta,
            },
            provider="subgraph",
            source_id=self._source_id(),
            indexed_block=indexed_block,
            extra_meta=graph_meta,
        )

    async def get_token(self, address: str) -> ProviderResult:
        address = _validate_token_address(address)
        if self.protocol == "v2":
            fields = """
              id symbol name decimals totalSupply tradeVolume tradeVolumeUSD
              untrackedVolumeUSD txCount totalLiquidity derivedETH
            """
        else:
            fields = """
              id symbol name decimals totalSupply volume volumeUSD untrackedVolumeUSD
              feesUSD txCount poolCount totalValueLocked totalValueLockedUSD derivedETH
            """
        query = f"""
        query Token {{
          {_META}
          token(id: "{address}") {{ {fields} }}
        }}
        """
        data, indexed_block, graph_meta = await self._query(query, {}, operation="get token")
        raw = data.get("token")
        if not isinstance(raw, dict):
            raise UniswapError(
                "NOT_FOUND",
                "token not found in the selected subgraph",
                context={"address": address, "protocol": self.protocol},
            )
        return ProviderResult(
            data=_token_with_metrics(raw, self.protocol),
            provider="subgraph",
            source_id=self._source_id(),
            indexed_block=indexed_block,
            extra_meta={**graph_meta, "usd_pricing": "subgraph-derived"},
        )

    def _pool_fields(self) -> str:
        if self.protocol == "v2":
            return """
              id createdAtTimestamp createdAtBlockNumber reserve0 reserve1 reserveUSD
              token0Price token1Price volumeToken0 volumeToken1 volumeUSD txCount
              token0 { id symbol name decimals }
              token1 { id symbol name decimals }
            """
        # The current bundled v4 deployment does not expose this optional field.
        extras = "tickSpacing hooks" if self.protocol == "v4" else ""
        return f"""
          id createdAtTimestamp createdAtBlockNumber feeTier liquidity sqrtPrice tick
          token0Price token1Price volumeToken0 volumeToken1 volumeUSD feesUSD txCount
          totalValueLockedToken0 totalValueLockedToken1 totalValueLockedUSD
          token0 {{ id symbol name decimals }}
          token1 {{ id symbol name decimals }}
          {extras}
        """

    async def list_pools(
        self,
        *,
        limit: int,
        cursor: str | None,
        order_by: str,
        direction: str,
        token0: str | None = None,
        token1: str | None = None,
    ) -> ProviderResult:
        _validate_limit(limit)
        if direction not in {"asc", "desc"}:
            raise invalid_argument("direction must be asc or desc", direction=direction)
        try:
            order_field = _POOL_ORDER_FIELDS[self.protocol][order_by]
        except KeyError as exc:
            raise invalid_argument(
                "order-by must be tvl-usd, volume-usd, created, or tx-count",
                order_by=order_by,
            ) from exc
        entity = "pairs" if self.protocol == "v2" else "pools"
        filter_type = "Pair_filter" if self.protocol == "v2" else "Pool_filter"
        where = _compact(
            {
                "token0": _validate_token_address(token0, field="token0") if token0 else None,
                "token1": _validate_token_address(token1, field="token1") if token1 else None,
            }
        )
        cursor_query = {
            "order_by": order_by,
            "direction": direction,
            "where": where,
        }
        boundary, tie_offset = _page_state(
            cursor,
            kind="pools-list",
            chain_id=self.chain_id,
            protocol=self.protocol,
            query=cursor_query,
        )
        boundary = _numeric_boundary(boundary)
        effective_where = dict(where)
        if boundary is not None:
            comparison = "gte" if direction == "asc" else "lte"
            effective_where[f"{order_field}_{comparison}"] = boundary
        query = f"""
        query Pools($first: Int!, $skip: Int!, $where: {filter_type}!) {{
          {_META}
          {entity}(
            first: $first, skip: $skip, where: $where,
            orderBy: {order_field}, orderDirection: {direction}
          ) {{ {self._pool_fields()} }}
        }}
        """
        data, indexed_block, graph_meta = await self._query(
            query,
            {"first": limit, "skip": tie_offset, "where": effective_where},
            operation="list pools",
        )
        rows = _object_rows(data.get(entity), entity=entity)
        normalized = [
            _pool_v2(item) if self.protocol == "v2" else _pool_v34(item, self.protocol)
            for item in rows
        ]
        next_cursor = None
        if len(rows) == limit:
            next_boundary, next_tie_offset = _next_page_state(
                [item.get(order_field) if isinstance(item, dict) else None for item in rows],
                boundary,
                tie_offset,
            )
            next_cursor = encode_cursor(
                "pools-list",
                self.chain_id,
                self.protocol,
                boundary=next_boundary,
                tie_offset=next_tie_offset,
                query=cursor_query,
            )
        return ProviderResult(
            data=normalized,
            provider="subgraph",
            source_id=self._source_id(),
            indexed_block=indexed_block,
            next_cursor=next_cursor,
            extra_meta={**graph_meta, "usd_pricing": "subgraph-derived"},
        )

    async def get_pool(self, pool_id: str) -> ProviderResult:
        pool_id = _validate_pool_id(pool_id, self.protocol)
        entity = "pair" if self.protocol == "v2" else "pool"
        query = f"""
        query Pool {{
          {_META}
          {entity}(id: "{pool_id}") {{ {self._pool_fields()} }}
        }}
        """
        data, indexed_block, graph_meta = await self._query(query, {}, operation="get pool")
        raw = data.get(entity)
        if not isinstance(raw, dict):
            raise UniswapError(
                "NOT_FOUND",
                "pool not found in the selected subgraph",
                context={"pool_id": pool_id, "protocol": self.protocol},
            )
        model = _pool_v2(raw) if self.protocol == "v2" else _pool_v34(raw, self.protocol)
        return ProviderResult(
            data=model,
            provider="subgraph",
            source_id=self._source_id(),
            indexed_block=indexed_block,
            extra_meta={**graph_meta, "usd_pricing": "subgraph-derived"},
        )

    def _swap_fields(self) -> str:
        if self.protocol == "v2":
            return """
              id timestamp sender from to amount0In amount1In amount0Out amount1Out
              amountUSD logIndex transaction { id blockNumber timestamp }
              pair { id token0 { id symbol name decimals } token1 { id symbol name decimals } }
            """
        recipient = "recipient" if self.protocol == "v3" else ""
        return f"""
          id timestamp sender {recipient} origin amount0 amount1 amountUSD
          sqrtPriceX96 tick logIndex
          transaction {{ id blockNumber timestamp }}
          pool {{ id token0 {{ id symbol name decimals }} token1 {{ id symbol name decimals }} }}
        """

    async def list_swaps(
        self,
        *,
        pool_id: str,
        start_timestamp: int | None,
        end_timestamp: int | None,
        limit: int,
        cursor: str | None,
        direction: str,
    ) -> ProviderResult:
        _validate_limit(limit)
        pool_id = _validate_pool_id(pool_id, self.protocol)
        if direction not in {"asc", "desc"}:
            raise invalid_argument("direction must be asc or desc", direction=direction)
        relation = "pair" if self.protocol == "v2" else "pool"
        where = _compact(
            {
                relation: pool_id,
                "timestamp_gte": start_timestamp,
                "timestamp_lte": end_timestamp,
            }
        )
        cursor_query = {"direction": direction, "where": where}
        boundary, tie_offset = _page_state(
            cursor,
            kind="swaps-list",
            chain_id=self.chain_id,
            protocol=self.protocol,
            query=cursor_query,
        )
        boundary = _timestamp_boundary(boundary)
        effective_where = dict(where)
        if boundary is not None:
            comparison = "gte" if direction == "asc" else "lte"
            effective_where[f"timestamp_{comparison}"] = boundary
        query = f"""
        query Swaps($first: Int!, $skip: Int!, $where: Swap_filter!) {{
          {_META}
          swaps(
            first: $first, skip: $skip, where: $where,
            orderBy: timestamp, orderDirection: {direction}
          ) {{ {self._swap_fields()} }}
        }}
        """
        data, indexed_block, graph_meta = await self._query(
            query,
            {"first": limit, "skip": tie_offset, "where": effective_where},
            operation="list swaps",
        )
        rows = _object_rows(data.get("swaps"), entity="swaps")
        normalized = [_swap_model(item, self.protocol) for item in rows]
        next_cursor = None
        if len(rows) == limit:
            page_timestamps = [_required_int(item, "timestamp") for item in rows]
            next_boundary, next_tie_offset = _next_page_state(page_timestamps, boundary, tie_offset)
            next_cursor = encode_cursor(
                "swaps-list",
                self.chain_id,
                self.protocol,
                boundary=next_boundary,
                tie_offset=next_tie_offset,
                query=cursor_query,
            )
        return ProviderResult(
            data=normalized,
            provider="subgraph",
            source_id=self._source_id(),
            indexed_block=indexed_block,
            next_cursor=next_cursor,
            covered_range=_compact(
                {"from_timestamp": start_timestamp, "to_timestamp": end_timestamp}
            ),
            extra_meta={**graph_meta, "usd_pricing": "subgraph-derived"},
        )

    async def series(
        self,
        *,
        pool_id: str,
        metric: str,
        interval: str,
        start_timestamp: int | None,
        end_timestamp: int | None,
        limit: int,
        cursor: str | None,
    ) -> ProviderResult:
        _validate_limit(limit)
        pool_id = _validate_pool_id(pool_id, self.protocol)
        if interval not in {"1h", "1d"}:
            raise invalid_argument("interval must be 1h or 1d", interval=interval)
        if metric not in {"volume-usd", "tvl-usd", "fees-usd", "tx-count", "ohlcv"}:
            raise invalid_argument(
                "metric must be volume-usd, tvl-usd, fees-usd, tx-count, or ohlcv",
                metric=metric,
            )
        if self.protocol == "v2" and metric == "ohlcv":
            raise unsupported("v2 pool series does not expose pool OHLC fields", metric=metric)
        if self.protocol == "v2":
            if interval == "1h":
                entity, filter_type, time_field = (
                    "pairHourDatas",
                    "PairHourData_filter",
                    "hourStartUnix",
                )
                fields = """
                  id hourStartUnix reserveUSD hourlyVolumeUSD hourlyTxns
                  pair { id }
                """
            else:
                entity, filter_type, time_field = "pairDayDatas", "PairDayData_filter", "date"
                fields = """
                  id date reserveUSD dailyVolumeUSD dailyTxns
                  pairAddress
                """
            relation = "pair" if interval == "1h" else "pairAddress"
        else:
            if interval == "1h":
                entity, filter_type, time_field = (
                    "poolHourDatas",
                    "PoolHourData_filter",
                    "periodStartUnix",
                )
            else:
                entity, filter_type, time_field = "poolDayDatas", "PoolDayData_filter", "date"
            fields = f"""
              id {time_field} liquidity sqrtPrice token0Price token1Price tick tvlUSD
              volumeToken0 volumeToken1 volumeUSD feesUSD txCount open high low close
              pool {{ id }}
            """
            relation = "pool"
        where = _compact(
            {
                relation: pool_id.lower(),
                f"{time_field}_gte": start_timestamp,
                f"{time_field}_lte": end_timestamp,
            }
        )
        cursor_query = {"where": where}
        boundary, tie_offset = _page_state(
            cursor,
            kind=f"series-{interval}-{metric}",
            chain_id=self.chain_id,
            protocol=self.protocol,
            query=cursor_query,
        )
        boundary = _timestamp_boundary(boundary)
        effective_where = dict(where)
        if boundary is not None:
            effective_where[f"{time_field}_gte"] = boundary
        query = f"""
        query Series($first: Int!, $skip: Int!, $where: {filter_type}!) {{
          {_META}
          {entity}(
            first: $first, skip: $skip, where: $where,
            orderBy: {time_field}, orderDirection: asc
          ) {{ {fields} }}
        }}
        """
        data, indexed_block, graph_meta = await self._query(
            query,
            {"first": limit, "skip": tie_offset, "where": effective_where},
            operation="get series",
        )
        raw_rows = _object_rows(data.get(entity), entity=entity)
        rows = [
            self._series_point(item, pool_id=pool_id, metric=metric, interval=interval)
            for item in raw_rows
        ]
        next_cursor = None
        if len(raw_rows) == limit:
            page_timestamps = [_required_int(item, time_field) for item in raw_rows]
            next_boundary, next_tie_offset = _next_page_state(page_timestamps, boundary, tie_offset)
            next_cursor = encode_cursor(
                f"series-{interval}-{metric}",
                self.chain_id,
                self.protocol,
                boundary=next_boundary,
                tie_offset=next_tie_offset,
                query=cursor_query,
            )
        return ProviderResult(
            data=rows,
            provider="subgraph",
            source_id=self._source_id(),
            indexed_block=indexed_block,
            next_cursor=next_cursor,
            covered_range=_compact(
                {"from_timestamp": start_timestamp, "to_timestamp": end_timestamp}
            ),
            extra_meta={**graph_meta, "usd_pricing": "subgraph-derived"},
        )

    def _series_point(
        self,
        raw: dict[str, Any],
        *,
        pool_id: str,
        metric: str,
        interval: str,
    ) -> dict[str, Any]:
        time_field = (
            "hourStartUnix"
            if self.protocol == "v2" and interval == "1h"
            else ("periodStartUnix" if interval == "1h" else "date")
        )
        point_id = _required_text(raw, "id")
        timestamp = _required_int(raw, time_field)
        if self.protocol == "v2":
            volume = raw.get("hourlyVolumeUSD") if interval == "1h" else raw.get("dailyVolumeUSD")
            tx_count = raw.get("hourlyTxns") if interval == "1h" else raw.get("dailyTxns")
            fees = exact_decimal_product(volume or "0", "0.003")
            if fees is None:
                raise UniswapError("SUBGRAPH_INVALID_RESPONSE", "v2 series volume is invalid")
            point = {
                "id": point_id,
                "pool_id": pool_id.lower(),
                "protocol_version": "v2",
                "interval": interval,
                "timestamp": timestamp,
                "datetime": utc_from_timestamp(timestamp),
                "volume_usd": decimal_string(volume),
                "tvl_usd": decimal_string(raw.get("reserveUSD")),
                "fees_usd": decimal_string(fees),
                "fees_usd_method": "derived-volume-times-0.003",
                "tx_count": decimal_string(tx_count),
            }
        else:
            point = {
                "id": point_id,
                "pool_id": pool_id.lower(),
                "protocol_version": self.protocol,
                "interval": interval,
                "timestamp": timestamp,
                "datetime": utc_from_timestamp(timestamp),
                "volume_usd": decimal_string(raw.get("volumeUSD")),
                "tvl_usd": decimal_string(raw.get("tvlUSD")),
                "fees_usd": decimal_string(raw.get("feesUSD")),
                "tx_count": decimal_string(raw.get("txCount")),
                "open": decimal_string(raw.get("open")),
                "high": decimal_string(raw.get("high")),
                "low": decimal_string(raw.get("low")),
                "close": decimal_string(raw.get("close")),
                "price_unit": "token1-per-token0",
                "token0_price": decimal_string(raw.get("token0Price")),
                "token1_price": decimal_string(raw.get("token1Price")),
                "liquidity_raw": decimal_string(raw.get("liquidity")),
                "sqrt_price_x96": decimal_string(raw.get("sqrtPrice")),
                "tick": decimal_string(raw.get("tick")),
            }
        value_map: dict[str, Any] = {
            "volume-usd": point.get("volume_usd"),
            "tvl-usd": point.get("tvl_usd"),
            "fees-usd": point.get("fees_usd"),
            "tx-count": point.get("tx_count"),
            "ohlcv": {key: point.get(key) for key in ("open", "high", "low", "close")},
        }
        result = _compact(point)
        result["metric"] = metric
        result["value"] = value_map[metric]
        return result

    async def raw_graphql(
        self, query: str, variables: dict[str, Any] | None = None
    ) -> ProviderResult:
        data, indexed_block, graph_meta = await self._query(
            query, variables, operation="raw GraphQL query"
        )
        return ProviderResult(
            data=data,
            provider="subgraph",
            source_id=self._source_id(),
            indexed_block=indexed_block,
            extra_meta=graph_meta,
        )
