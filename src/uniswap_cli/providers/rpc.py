from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx

from uniswap_cli.config import Endpoint, Settings
from uniswap_cli.cursor import decode_cursor, encode_cursor
from uniswap_cli.errors import UniswapError, invalid_argument, unsupported
from uniswap_cli.http import JsonHttpClient
from uniswap_cli.models import normalize_address, utc_from_timestamp
from uniswap_cli.providers.base import ProviderResult

V2_SWAP_TOPIC = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
V3_SWAP_TOPIC = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"
V4_SWAP_TOPIC = "0x40e9cecb9f5f1f1c5b9c97dec2917b7ee92e57ba5563708daca94dd84ad7112f"
V4_POOL_MANAGER = "0x000000000004444c5dc75cb358380d2e3de08a90"

_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_TOPIC_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
_HEX_RE = re.compile(r"^0x[0-9a-fA-F]+$")
_RANGE_LIMIT_RE = re.compile(r"up to (?:a )?([0-9,]+) block range", re.I)

_TOKEN0_SELECTOR = "0x0dfe1681"
_TOKEN1_SELECTOR = "0xd21220a7"
_DECIMALS_SELECTOR = "0x313ce567"


def _hex_quantity(value: int) -> str:
    if value < 0:
        raise invalid_argument("block number cannot be negative", block=value)
    return hex(value)


def _hex_int(value: str | None, *, field: str) -> int:
    if not isinstance(value, str) or not _HEX_RE.fullmatch(value):
        raise UniswapError(
            "RPC_INVALID_RESPONSE",
            f"RPC returned an invalid {field}",
            context={"field": field},
        )
    return int(value, 16)


def _validate_address(value: str, *, field: str = "address") -> str:
    if not _ADDRESS_RE.fullmatch(value):
        raise invalid_argument(f"{field} must be a 20-byte 0x-prefixed address", **{field: value})
    return value.lower()


def _validate_topic(value: str) -> str:
    if not _TOPIC_RE.fullmatch(value):
        raise invalid_argument("topic must be a 32-byte 0x-prefixed value", topic=value)
    return value.lower()


def _address_from_topic(topic: str) -> str:
    return "0x" + _validate_topic(topic)[-40:]


def _words(data: str) -> list[str]:
    if not isinstance(data, str) or not data.startswith("0x"):
        raise UniswapError("RPC_INVALID_RESPONSE", "event data is not hex")
    body = data[2:]
    if len(body) % 64:
        raise UniswapError("RPC_INVALID_RESPONSE", "event data is not ABI word-aligned")
    return [body[index : index + 64] for index in range(0, len(body), 64)]


def _uint(word: str) -> int:
    return int(word, 16)


def _signed(word: str) -> int:
    value = int(word, 16)
    return value - (1 << 256) if value >= 1 << 255 else value


def _human_amount(raw: int, decimals: int) -> str:
    if decimals < 0:
        raise UniswapError(
            "RPC_INVALID_RESPONSE",
            "token decimals cannot be negative",
            context={"decimals": decimals},
        )
    sign = "-" if raw < 0 else ""
    digits = str(abs(raw))
    if decimals == 0:
        return sign + digits
    padded = digits.rjust(decimals + 1, "0")
    integer, fraction = padded[:-decimals], padded[-decimals:].rstrip("0")
    return sign + integer + (f".{fraction}" if fraction else "")


class RpcProvider:
    def __init__(
        self,
        settings: Settings,
        chain_id: int,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.chain_id = chain_id
        self.endpoints: tuple[Endpoint, ...] = settings.rpc_endpoints(chain_id)
        self.http = JsonHttpClient(settings, client=http_client)
        self._request_id = 0
        self._block_cache: dict[int, dict[str, Any]] = {}
        self._used_endpoint_labels: set[str] = set()

    async def __aenter__(self) -> RpcProvider:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        await self.close()

    async def close(self) -> None:
        await self.http.close()

    async def _rpc(self, method: str, params: list[Any]) -> Any:
        self._request_id += 1
        last_error: UniswapError | None = None
        for endpoint in self.endpoints:
            try:
                payload = await self.http.request(
                    "POST",
                    endpoint.url,
                    endpoint_label=endpoint.label,
                    operation=method,
                    headers={"content-type": "application/json"},
                    json_body={
                        "jsonrpc": "2.0",
                        "id": self._request_id,
                        "method": method,
                        "params": params,
                    },
                )
            except UniswapError as exc:
                last_error = exc
                continue
            if not isinstance(payload, dict):
                last_error = UniswapError(
                    "RPC_INVALID_RESPONSE",
                    f"{method} returned a non-object response",
                    context={"endpoint": endpoint.label},
                )
                continue
            error = payload.get("error")
            if error:
                if isinstance(error, dict):
                    message = str(error.get("message", error))
                    rpc_code = error.get("code")
                else:
                    message = str(error)
                    rpc_code = None
                last_error = UniswapError(
                    "RPC_ERROR",
                    f"{method} failed: {message}",
                    retryable=False,
                    context={
                        "endpoint": endpoint.label,
                        "rpc_code": rpc_code,
                        "upstream_message": message,
                    },
                )
                continue
            if "result" not in payload:
                last_error = UniswapError(
                    "RPC_INVALID_RESPONSE",
                    f"{method} response has no result",
                    context={"endpoint": endpoint.label},
                )
                continue
            self._used_endpoint_labels.add(endpoint.label)
            return payload["result"]
        if last_error is not None:
            raise last_error
        raise UniswapError("RPC_UNAVAILABLE", f"no RPC endpoint accepted {method}")

    def _source_id(self) -> str:
        labels = sorted(self._used_endpoint_labels)
        if not labels:
            return self.endpoints[0].label
        if len(labels) == 1:
            return labels[0]
        return "rpc-composite:" + ",".join(labels)

    async def chain_id_value(self) -> int:
        return _hex_int(await self._rpc("eth_chainId", []), field="chainId")

    async def block_number(self) -> int:
        return _hex_int(await self._rpc("eth_blockNumber", []), field="blockNumber")

    async def block(self, number: int) -> dict[str, Any]:
        if number in self._block_cache:
            return self._block_cache[number]
        raw = await self._rpc("eth_getBlockByNumber", [_hex_quantity(number), False])
        if not isinstance(raw, dict):
            raise UniswapError("NOT_FOUND", "block not found", context={"block_number": number})
        result = {
            "number": _hex_int(raw.get("number"), field="block.number"),
            "timestamp": _hex_int(raw.get("timestamp"), field="block.timestamp"),
            "hash": raw.get("hash"),
        }
        self._block_cache[number] = result
        return result

    async def health(self, *, check_archive: bool = True) -> ProviderResult:
        actual_chain, head = await asyncio.gather(self.chain_id_value(), self.block_number())
        if actual_chain != self.chain_id:
            raise UniswapError(
                "RPC_CHAIN_MISMATCH",
                "RPC endpoint is connected to the wrong chain",
                context={"expected_chain_id": self.chain_id, "actual_chain_id": actual_chain},
            )
        latest = await self.block(head)
        archive: dict[str, Any] = {"checked": False}
        if check_archive and self.chain_id == 1:
            archive_block = 12_369_621
            historical = await self.block(archive_block)
            archive = {
                "checked": True,
                "ok": historical["number"] == archive_block,
                "sample_block": archive_block,
            }
        return ProviderResult(
            data={
                "ok": True,
                "chain_id": actual_chain,
                "head_block": head,
                "head_timestamp": latest["timestamp"],
                "head_datetime": utc_from_timestamp(latest["timestamp"]),
                "archive": archive,
            },
            provider="rpc",
            source_id=self._source_id(),
            indexed_block=head,
        )

    async def block_at_or_after(self, timestamp: int) -> int:
        if timestamp < 0:
            raise invalid_argument("timestamp cannot be negative", timestamp=timestamp)
        head = await self.block_number()
        latest = await self.block(head)
        if timestamp > latest["timestamp"]:
            return head + 1
        low, high = 0, head
        while low < high:
            mid = (low + high) // 2
            block = await self.block(mid)
            if block["timestamp"] < timestamp:
                low = mid + 1
            else:
                high = mid
        return low

    async def block_at_or_before(self, timestamp: int) -> int:
        first_after = await self.block_at_or_after(timestamp)
        head = await self.block_number()
        if first_after > head:
            return head
        candidate = await self.block(first_after)
        if candidate["timestamp"] <= timestamp:
            return first_after
        return max(first_after - 1, 0)

    @staticmethod
    def _range_limit_from_error(error: UniswapError) -> int | None:
        message = " ".join(
            str(value)
            for key, value in error.context.items()
            if key in {"upstream_message", "message"}
        )
        if not message:
            message = error.message
        match = _RANGE_LIMIT_RE.search(message)
        if not match:
            return None
        return int(match.group(1).replace(",", ""))

    async def _log_page(
        self,
        *,
        address: str,
        topics: list[str | None],
        start_block: int,
        end_block: int,
    ) -> list[dict[str, Any]]:
        result = await self._rpc(
            "eth_getLogs",
            [
                {
                    "address": address,
                    "fromBlock": _hex_quantity(start_block),
                    "toBlock": _hex_quantity(end_block),
                    "topics": topics,
                }
            ],
        )
        if not isinstance(result, list) or not all(isinstance(item, dict) for item in result):
            raise UniswapError("RPC_INVALID_RESPONSE", "eth_getLogs returned a non-list result")
        return result

    async def _collect_logs(
        self,
        *,
        kind: str,
        protocol: str,
        address: str,
        topics: list[str | None],
        start_block: int,
        end_block: int,
        limit: int,
        cursor: str | None,
        direction: str,
    ) -> tuple[list[dict[str, Any]], str | None, int]:
        if start_block > end_block:
            raise invalid_argument(
                "from-block must not exceed to-block",
                from_block=start_block,
                to_block=end_block,
            )
        if limit < 1 or limit > 10_000:
            raise invalid_argument("limit must be between 1 and 10000", limit=limit)
        if direction not in {"asc", "desc"}:
            raise invalid_argument("direction must be asc or desc", direction=direction)

        cursor_block: int | None = None
        cursor_log_index: int | None = None
        cursor_query = {
            "address": address,
            "topics": topics,
            "start_block": start_block,
            "end_block": end_block,
            "direction": direction,
        }
        if cursor:
            state = decode_cursor(cursor, kind=kind, chain_id=self.chain_id, protocol=protocol)
            if state.get("query") != cursor_query:
                raise invalid_argument("cursor filters do not match this query")
            cursor_block = state.get("block")
            cursor_log_index = state.get("log_index")
            if not isinstance(cursor_block, int) or not isinstance(cursor_log_index, int):
                raise invalid_argument("cursor contains an invalid log position")

        chunk_size = self.settings.rpc_max_block_range
        current = (
            max(start_block, cursor_block if cursor_block is not None else start_block)
            if direction == "asc"
            else min(end_block, cursor_block if cursor_block is not None else end_block)
        )
        collected: list[dict[str, Any]] = []
        requests = 0
        exhausted = False

        while start_block <= current <= end_block and len(collected) <= limit:
            if requests >= self.settings.rpc_max_log_requests:
                raise UniswapError(
                    "RPC_RANGE_TOO_LARGE",
                    "log query exceeded UNISWAP_RPC_MAX_LOG_REQUESTS",
                    context={
                        "max_requests": self.settings.rpc_max_log_requests,
                        "from_block": start_block,
                        "to_block": end_block,
                    },
                )
            if direction == "asc":
                page_start, page_end = current, min(end_block, current + chunk_size - 1)
            else:
                page_start, page_end = max(start_block, current - chunk_size + 1), current
            try:
                page = await self._log_page(
                    address=address,
                    topics=topics,
                    start_block=page_start,
                    end_block=page_end,
                )
                requests += 1
            except UniswapError as exc:
                requests += 1
                discovered = self._range_limit_from_error(exc)
                if discovered is not None and discovered < chunk_size:
                    chunk_size = discovered
                    continue
                if page_start < page_end and exc.code in {"RPC_ERROR", "UPSTREAM_HTTP_ERROR"}:
                    chunk_size = max((page_end - page_start + 1) // 2, 1)
                    continue
                raise

            page.sort(
                key=lambda item: (
                    _hex_int(item.get("blockNumber"), field="log.blockNumber"),
                    _hex_int(item.get("logIndex"), field="log.logIndex"),
                ),
                reverse=direction == "desc",
            )
            if cursor_block is not None and cursor_log_index is not None:
                cursor_key = (cursor_block, cursor_log_index)
                filtered: list[dict[str, Any]] = []
                for item in page:
                    item_key = (
                        _hex_int(item.get("blockNumber"), field="log.blockNumber"),
                        _hex_int(item.get("logIndex"), field="log.logIndex"),
                    )
                    if (direction == "asc" and item_key > cursor_key) or (
                        direction == "desc" and item_key < cursor_key
                    ):
                        filtered.append(item)
                page = filtered
                cursor_block = cursor_log_index = None
            collected.extend(page)
            if direction == "asc":
                if page_end >= end_block:
                    exhausted = True
                    break
                current = page_end + 1
            else:
                if page_start <= start_block:
                    exhausted = True
                    break
                current = page_start - 1

        returned = collected[:limit]
        next_cursor = None
        if returned and (len(collected) > limit or not exhausted):
            last = returned[-1]
            next_cursor = encode_cursor(
                kind,
                self.chain_id,
                protocol,
                block=_hex_int(last.get("blockNumber"), field="log.blockNumber"),
                log_index=_hex_int(last.get("logIndex"), field="log.logIndex"),
                query=cursor_query,
            )
        return returned, next_cursor, requests

    async def raw_events(
        self,
        *,
        address: str,
        topics: list[str | None],
        start_block: int,
        end_block: int,
        limit: int,
        cursor: str | None,
        direction: str,
    ) -> ProviderResult:
        address = _validate_address(address)
        normalized_topics = [None if topic is None else _validate_topic(topic) for topic in topics]
        head = await self.block_number()
        if end_block > head:
            raise UniswapError(
                "RPC_BLOCK_NOT_AVAILABLE",
                "to-block is above the current RPC head",
                context={"to_block": end_block, "head_block": head},
            )
        logs, next_cursor, requests = await self._collect_logs(
            kind="raw-events",
            protocol="raw",
            address=address,
            topics=normalized_topics,
            start_block=start_block,
            end_block=end_block,
            limit=limit,
            cursor=cursor,
            direction=direction,
        )
        data = [
            {
                "address": normalize_address(item.get("address")),
                "block_number": _hex_int(item.get("blockNumber"), field="log.blockNumber"),
                "block_hash": item.get("blockHash"),
                "transaction_hash": item.get("transactionHash"),
                "transaction_index": _hex_int(
                    item.get("transactionIndex"), field="log.transactionIndex"
                ),
                "log_index": _hex_int(item.get("logIndex"), field="log.logIndex"),
                "removed": bool(item.get("removed", False)),
                "topics": item.get("topics", []),
                "data": item.get("data"),
            }
            for item in logs
        ]
        return ProviderResult(
            data=data,
            provider="rpc",
            source_id=self._source_id(),
            indexed_block=head,
            next_cursor=next_cursor,
            covered_range={"from_block": start_block, "to_block": end_block},
            warnings=["result is paginated; follow next_cursor for complete coverage"]
            if next_cursor
            else [],
            extra_meta={
                "rpc_log_requests": requests,
                "range_complete": next_cursor is None,
                "provider_head_block": head,
                "data_kind": "raw-events",
                "protocol_version": None,
            },
        )

    async def _eth_call(self, to: str, data: str) -> str:
        result = await self._rpc("eth_call", [{"to": to, "data": data}, "latest"])
        if not isinstance(result, str) or not result.startswith("0x"):
            raise UniswapError("RPC_INVALID_RESPONSE", "eth_call returned invalid hex")
        return result

    async def _pool_tokens(self, pool: str) -> tuple[dict[str, Any], dict[str, Any]]:
        token0_word, token1_word = await asyncio.gather(
            self._eth_call(pool, _TOKEN0_SELECTOR),
            self._eth_call(pool, _TOKEN1_SELECTOR),
        )
        if len(token0_word) != 66 or len(token1_word) != 66:
            raise UniswapError("RPC_INVALID_RESPONSE", "pool token call returned invalid ABI data")
        token0 = _validate_address("0x" + token0_word[-40:], field="token0")
        token1 = _validate_address("0x" + token1_word[-40:], field="token1")
        decimals0_word, decimals1_word = await asyncio.gather(
            self._eth_call(token0, _DECIMALS_SELECTOR),
            self._eth_call(token1, _DECIMALS_SELECTOR),
        )
        if len(decimals0_word) != 66 or len(decimals1_word) != 66:
            raise UniswapError(
                "RPC_INVALID_RESPONSE", "token decimals call returned invalid ABI data"
            )
        decimals0 = _hex_int(decimals0_word, field="token0.decimals")
        decimals1 = _hex_int(decimals1_word, field="token1.decimals")
        if decimals0 > 255 or decimals1 > 255:
            raise UniswapError(
                "RPC_INVALID_RESPONSE",
                "token decimals exceed uint8",
                context={"decimals0": decimals0, "decimals1": decimals1},
            )
        return (
            {"address": token0, "symbol": None, "name": None, "decimals": decimals0},
            {"address": token1, "symbol": None, "name": None, "decimals": decimals1},
        )

    async def list_swaps(
        self,
        *,
        protocol: str,
        pool_id: str,
        start_block: int,
        end_block: int,
        limit: int,
        cursor: str | None,
        direction: str,
    ) -> ProviderResult:
        if protocol not in {"v2", "v3"}:
            raise unsupported(
                "normalized RPC swaps currently support v2 and v3; "
                "use raw events or subgraph for v4",
                protocol=protocol,
            )
        pool = _validate_address(pool_id, field="pool")
        topic = V2_SWAP_TOPIC if protocol == "v2" else V3_SWAP_TOPIC
        head = await self.block_number()
        if end_block > head:
            raise UniswapError(
                "RPC_BLOCK_NOT_AVAILABLE",
                "to-block is above the current RPC head",
                context={"to_block": end_block, "head_block": head},
            )
        logs, next_cursor, requests = await self._collect_logs(
            kind="swaps-rpc",
            protocol=protocol,
            address=pool,
            topics=[topic],
            start_block=start_block,
            end_block=end_block,
            limit=limit,
            cursor=cursor,
            direction=direction,
        )
        token0, token1 = await self._pool_tokens(pool)
        removed_count = sum(bool(item.get("removed", False)) for item in logs)
        logs = [item for item in logs if not bool(item.get("removed", False))]
        block_numbers = {
            _hex_int(item.get("blockNumber"), field="log.blockNumber") for item in logs
        }
        await asyncio.gather(*(self.block(number) for number in block_numbers))
        data = [self._decode_swap(item, protocol, pool, token0, token1) for item in logs]
        return ProviderResult(
            data=data,
            provider="rpc",
            source_id=self._source_id(),
            indexed_block=head,
            next_cursor=next_cursor,
            covered_range={"from_block": start_block, "to_block": end_block},
            warnings=[
                "RPC-derived swaps do not include USD valuation or token symbols",
                *(
                    ["result is paginated; follow next_cursor for complete coverage"]
                    if next_cursor
                    else []
                ),
                *([f"ignored {removed_count} removed log(s)"] if removed_count else []),
            ],
            extra_meta={
                "rpc_log_requests": requests,
                "range_complete": next_cursor is None,
                "provider_head_block": head,
            },
        )

    def _decode_swap(
        self,
        raw: dict[str, Any],
        protocol: str,
        pool: str,
        token0: dict[str, Any],
        token1: dict[str, Any],
    ) -> dict[str, Any]:
        topics = raw.get("topics")
        if not isinstance(topics, list) or len(topics) < 3:
            raise UniswapError("RPC_INVALID_RESPONSE", "swap event has too few topics")
        words = _words(raw.get("data"))
        if protocol == "v2":
            if len(words) != 4:
                raise UniswapError("RPC_INVALID_RESPONSE", "v2 swap event has invalid data")
            amount0_raw = _uint(words[0]) - _uint(words[2])
            amount1_raw = _uint(words[1]) - _uint(words[3])
            sqrt_price = liquidity = tick = None
        else:
            if len(words) != 5:
                raise UniswapError("RPC_INVALID_RESPONSE", "v3 swap event has invalid data")
            amount0_raw = _signed(words[0])
            amount1_raw = _signed(words[1])
            sqrt_price = _uint(words[2])
            liquidity = _uint(words[3])
            tick = _signed(words[4])
        block_number = _hex_int(raw.get("blockNumber"), field="log.blockNumber")
        log_index = _hex_int(raw.get("logIndex"), field="log.logIndex")
        block = self._block_cache[block_number]
        tx_hash = normalize_address(raw.get("transactionHash"))
        return {
            "id": f"{tx_hash}#{log_index}",
            "protocol_version": protocol,
            "pool_id": pool,
            "transaction_hash": tx_hash,
            "log_index": log_index,
            "block_number": block_number,
            "timestamp": block["timestamp"],
            "datetime": utc_from_timestamp(block["timestamp"]),
            "sender": _address_from_topic(topics[1]),
            "recipient": _address_from_topic(topics[2]),
            "origin": None,
            "token0": token0,
            "token1": token1,
            "amount0": _human_amount(amount0_raw, token0["decimals"]),
            "amount1": _human_amount(amount1_raw, token1["decimals"]),
            "amount0_raw": str(amount0_raw),
            "amount1_raw": str(amount1_raw),
            "amount_usd": None,
            "sqrt_price_x96": str(sqrt_price) if sqrt_price is not None else None,
            "liquidity_raw": str(liquidity) if liquidity is not None else None,
            "tick": str(tick) if tick is not None else None,
        }
