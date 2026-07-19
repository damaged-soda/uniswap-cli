from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from uniswap_cli.config import (
    CHAINS,
    PROTOCOLS,
    SUBGRAPH_DEPLOYMENTS,
    Settings,
    parse_chain,
    parse_protocol,
)
from uniswap_cli.errors import UniswapError, invalid_argument, unsupported
from uniswap_cli.models import envelope, utc_now
from uniswap_cli.providers.base import ProviderResult
from uniswap_cli.providers.rpc import RpcProvider
from uniswap_cli.providers.subgraph import SubgraphProvider
from uniswap_cli.reconcile import reconcile_swap_rows


def parse_timestamp(value: str | int | None, *, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        if value < 0:
            raise invalid_argument(f"{field} cannot be negative", **{field: value})
        return value
    text = value.strip()
    if not text:
        raise invalid_argument(f"{field} cannot be empty")
    if text.isdigit():
        return int(text)
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise invalid_argument(
            f"{field} must be RFC3339 or unix seconds", **{field: value}
        ) from exc
    if parsed.tzinfo is None:
        raise invalid_argument(f"{field} must include a timezone", **{field: value})
    timestamp = int(parsed.astimezone(UTC).timestamp())
    if timestamp < 0:
        raise invalid_argument(f"{field} cannot be negative", **{field: value})
    return timestamp


def _local_result(data: Any, *, source_id: str) -> ProviderResult:
    return ProviderResult(data=data, provider="local", source_id=source_id)


class UniswapService:
    def __init__(
        self,
        settings: Settings,
        *,
        chain: str | int | None = None,
        protocol: str | None = None,
    ) -> None:
        self.settings = settings
        self.chain = parse_chain(chain or settings.default_chain)
        self.protocol = parse_protocol(protocol or settings.default_protocol)

    def render(self, result: ProviderResult) -> dict[str, Any]:
        meta = {
            "chain_id": self.chain.chain_id,
            "chain": self.chain.name,
            "protocol_version": self.protocol,
            "provider": result.provider,
            "source_id": result.source_id,
            "queried_at": utc_now(),
            "indexed_block": result.indexed_block,
            "range": result.covered_range,
            "next_cursor": result.next_cursor,
            "warnings": result.warnings,
            **result.extra_meta,
        }
        return envelope(result.data, meta)

    def chains(self) -> dict[str, Any]:
        rows = []
        for chain in CHAINS:
            protocols = [
                protocol
                for protocol in PROTOCOLS
                if (chain.chain_id, protocol) in SUBGRAPH_DEPLOYMENTS
            ]
            rows.append(
                {
                    "chain_id": chain.chain_id,
                    "name": chain.name,
                    "aliases": list(chain.aliases),
                    "native_symbol": chain.native_symbol,
                    "subgraph_protocols": protocols,
                    "rpc_env": [
                        f"UNISWAP_RPC_URL_{chain.chain_id}",
                        f"RPC_URL_{chain.chain_id}",
                    ],
                    "custom_subgraph_env": [
                        f"UNISWAP_SUBGRAPH_URL_{chain.chain_id}_V2",
                        f"UNISWAP_SUBGRAPH_URL_{chain.chain_id}_V3",
                        f"UNISWAP_SUBGRAPH_URL_{chain.chain_id}_V4",
                    ],
                }
            )
        result = _local_result(rows, source_id="bundled-chain-registry")
        result.extra_meta["protocol_version"] = None
        return self.render(result)

    def protocols(self) -> dict[str, Any]:
        rows = []
        for protocol in PROTOCOLS:
            deployment = SUBGRAPH_DEPLOYMENTS.get((self.chain.chain_id, protocol))
            rows.append(
                {
                    "protocol_version": protocol,
                    "subgraph_supported": deployment is not None,
                    "subgraph_id": deployment.subgraph_id if deployment else None,
                    "source_repository": deployment.source_repository if deployment else None,
                    "normalized_rpc_swaps": protocol in {"v2", "v3"},
                }
            )
        result = _local_result(rows, source_id="bundled-protocol-registry")
        result.extra_meta["protocol_version"] = None
        return self.render(result)

    async def token(self, address: str, *, provider: str) -> dict[str, Any]:
        self._require_provider(provider, allowed={"auto", "subgraph"})
        async with SubgraphProvider(self.settings, self.chain.chain_id, self.protocol) as subgraph:
            return self.render(await subgraph.get_token(address))

    async def pools_list(
        self,
        *,
        provider: str,
        limit: int,
        cursor: str | None,
        order_by: str,
        direction: str,
        token0: str | None,
        token1: str | None,
    ) -> dict[str, Any]:
        self._require_provider(provider, allowed={"auto", "subgraph"})
        async with SubgraphProvider(self.settings, self.chain.chain_id, self.protocol) as subgraph:
            result = await subgraph.list_pools(
                limit=limit,
                cursor=cursor,
                order_by=order_by,
                direction=direction,
                token0=token0,
                token1=token1,
            )
            return self.render(result)

    async def pool(self, pool_id: str, *, provider: str) -> dict[str, Any]:
        self._require_provider(provider, allowed={"auto", "subgraph"})
        async with SubgraphProvider(self.settings, self.chain.chain_id, self.protocol) as subgraph:
            return self.render(await subgraph.get_pool(pool_id))

    async def swaps(
        self,
        *,
        pool_id: str,
        provider: str,
        start_timestamp: int | None,
        end_timestamp: int | None,
        start_block: int | None,
        end_block: int | None,
        limit: int,
        cursor: str | None,
        direction: str,
    ) -> dict[str, Any]:
        self._require_provider(provider, allowed={"auto", "subgraph", "rpc"})
        has_times = start_timestamp is not None or end_timestamp is not None
        has_blocks = start_block is not None or end_block is not None
        if has_times and has_blocks:
            raise invalid_argument("time and block ranges cannot be combined")
        if (
            start_timestamp is not None
            and end_timestamp is not None
            and start_timestamp > end_timestamp
        ):
            raise invalid_argument("--from must not exceed --to")

        selected = provider
        fallback_warning: str | None = None
        if selected == "auto":
            selected = "rpc" if has_blocks else "subgraph"
            if selected == "subgraph":
                try:
                    self.settings.subgraph_endpoint(self.chain.chain_id, self.protocol)
                except UniswapError as exc:
                    if exc.code not in {"SUBGRAPH_AUTH_MISSING", "SUBGRAPH_UNAVAILABLE"}:
                        raise
                    if not (start_timestamp is not None and end_timestamp is not None):
                        raise UniswapError(
                            "NO_USABLE_PROVIDER",
                            "subgraph is not configured; RPC fallback requires both "
                            "--from and --to",
                            context={"subgraph_error": exc.code},
                        ) from exc
                    self.settings.rpc_endpoints(self.chain.chain_id)
                    selected = "rpc"
                    fallback_warning = (
                        "auto-selected RPC because subgraph configuration is unavailable"
                    )

        if selected == "subgraph":
            if has_blocks:
                raise unsupported(
                    "subgraph swap queries accept time ranges, not event block ranges; "
                    "use --provider rpc"
                )
            async with SubgraphProvider(
                self.settings, self.chain.chain_id, self.protocol
            ) as subgraph:
                return self.render(
                    await subgraph.list_swaps(
                        pool_id=pool_id,
                        start_timestamp=start_timestamp,
                        end_timestamp=end_timestamp,
                        limit=limit,
                        cursor=cursor,
                        direction=direction,
                    )
                )

        async with RpcProvider(self.settings, self.chain.chain_id) as rpc:
            if has_times:
                if start_timestamp is None or end_timestamp is None:
                    raise invalid_argument("RPC time queries require both --from and --to")
                start_block, end_block = await asyncio.gather(
                    rpc.block_at_or_after(start_timestamp),
                    rpc.block_at_or_before(end_timestamp),
                )
            else:
                if start_block is None:
                    raise invalid_argument(
                        "RPC swap queries require --from-block or a bounded time range"
                    )
                if end_block is None:
                    end_block = await rpc.block_number()
            assert start_block is not None and end_block is not None
            result = await rpc.list_swaps(
                protocol=self.protocol,
                pool_id=pool_id,
                start_block=start_block,
                end_block=end_block,
                limit=limit,
                cursor=cursor,
                direction=direction,
            )
            if fallback_warning:
                result.warnings.append(fallback_warning)
            return self.render(result)

    async def series(
        self,
        *,
        pool_id: str,
        provider: str,
        metric: str,
        interval: str,
        start_timestamp: int | None,
        end_timestamp: int | None,
        limit: int,
        cursor: str | None,
    ) -> dict[str, Any]:
        self._require_provider(provider, allowed={"auto", "subgraph"})
        if (
            start_timestamp is not None
            and end_timestamp is not None
            and start_timestamp > end_timestamp
        ):
            raise invalid_argument("--from must not exceed --to")
        async with SubgraphProvider(self.settings, self.chain.chain_id, self.protocol) as subgraph:
            result = await subgraph.series(
                pool_id=pool_id,
                metric=metric,
                interval=interval,
                start_timestamp=start_timestamp,
                end_timestamp=end_timestamp,
                limit=limit,
                cursor=cursor,
            )
            return self.render(result)

    async def reconcile_swaps(
        self,
        *,
        pool_id: str,
        start_block: int,
        end_block: int,
        max_swaps: int,
        sample_limit: int,
    ) -> dict[str, Any]:
        if self.protocol not in {"v2", "v3"}:
            raise unsupported("swap reconciliation currently supports v2 and v3")
        if start_block > end_block:
            raise invalid_argument("--from-block must not exceed --to-block")
        if max_swaps < 1 or max_swaps > 10_000:
            raise invalid_argument("max-swaps must be between 1 and 10000")
        if sample_limit < 1 or sample_limit > 1_000:
            raise invalid_argument("sample-limit must be between 1 and 1000")

        subgraph = SubgraphProvider(self.settings, self.chain.chain_id, self.protocol)
        try:
            rpc = RpcProvider(self.settings, self.chain.chain_id)
        except Exception:
            await subgraph.close()
            raise
        try:
            start_info, end_info = await asyncio.gather(
                rpc.block(start_block), rpc.block(end_block)
            )
            subgraph_rows: list[dict[str, Any]] = []
            subgraph_cursor: str | None = None
            subgraph_source = ""
            subgraph_indexed_block: int | None = None
            subgraph_page_size = min(max_swaps + 1, 1_000)
            while True:
                page = await subgraph.list_swaps(
                    pool_id=pool_id,
                    start_timestamp=start_info["timestamp"],
                    end_timestamp=end_info["timestamp"],
                    limit=subgraph_page_size,
                    cursor=subgraph_cursor,
                    direction="asc",
                )
                subgraph_source = page.source_id
                subgraph_indexed_block = page.indexed_block
                subgraph_rows.extend(
                    row
                    for row in page.data
                    if isinstance(row, dict)
                    and isinstance(row.get("block_number"), int)
                    and start_block <= row["block_number"] <= end_block
                )
                subgraph_cursor = page.next_cursor
                if len(subgraph_rows) > max_swaps:
                    raise UniswapError(
                        "RESULT_LIMIT_EXCEEDED",
                        "subgraph reconciliation rows exceed --max-swaps",
                        context={"max_swaps": max_swaps},
                    )
                if subgraph_cursor is None:
                    break

            rpc_page = await rpc.list_swaps(
                protocol=self.protocol,
                pool_id=pool_id,
                start_block=start_block,
                end_block=end_block,
                limit=max_swaps,
                cursor=None,
                direction="asc",
            )
            if rpc_page.next_cursor is not None:
                raise UniswapError(
                    "RESULT_LIMIT_EXCEEDED",
                    "RPC reconciliation rows exceed --max-swaps",
                    context={"max_swaps": max_swaps},
                )
            comparison = reconcile_swap_rows(
                subgraph_rows,
                [row for row in rpc_page.data if isinstance(row, dict)],
                sample_limit=sample_limit,
            )
            result = ProviderResult(
                data={
                    "pool_id": pool_id.lower(),
                    "protocol_version": self.protocol,
                    "from_block": start_block,
                    "to_block": end_block,
                    **comparison,
                },
                provider="composite",
                source_id=f"subgraph:{subgraph_source}+{rpc_page.source_id}",
                indexed_block=subgraph_indexed_block,
                covered_range={"from_block": start_block, "to_block": end_block},
                warnings=[
                    "A mismatch may reflect subgraph indexing lag; inspect indexed_block "
                    "before concluding."
                ]
                if not comparison["complete_match"]
                else [],
                extra_meta={
                    "subgraph_indexed_block": subgraph_indexed_block,
                    "rpc_head_for_query": rpc_page.indexed_block,
                },
            )
            return self.render(result)
        finally:
            await asyncio.gather(subgraph.close(), rpc.close())

    async def raw_graphql(self, query: str, variables: dict[str, Any] | None) -> dict[str, Any]:
        async with SubgraphProvider(self.settings, self.chain.chain_id, self.protocol) as subgraph:
            return self.render(await subgraph.raw_graphql(query, variables))

    async def raw_events(
        self,
        *,
        address: str,
        topics: list[str | None],
        start_block: int,
        end_block: int | None,
        limit: int,
        cursor: str | None,
        direction: str,
    ) -> dict[str, Any]:
        async with RpcProvider(self.settings, self.chain.chain_id) as rpc:
            if end_block is None:
                end_block = await rpc.block_number()
            result = await rpc.raw_events(
                address=address,
                topics=topics,
                start_block=start_block,
                end_block=end_block,
                limit=limit,
                cursor=cursor,
                direction=direction,
            )
            return self.render(result)

    async def doctor(self, *, provider: str, check_archive: bool) -> dict[str, Any]:
        self._require_provider(provider, allowed={"auto", "subgraph", "rpc"})
        checks: list[dict[str, Any]] = []
        tasks: list[tuple[str, Any]] = []
        if provider in {"auto", "subgraph"}:
            try:
                subgraph = SubgraphProvider(self.settings, self.chain.chain_id, self.protocol)
            except UniswapError as exc:
                checks.append(
                    {
                        "provider": "subgraph",
                        "configured": False,
                        "ok": False,
                        "error": exc.as_dict(),
                    }
                )
            else:
                tasks.append(("subgraph", self._doctor_subgraph(subgraph)))
        if provider in {"auto", "rpc"}:
            try:
                rpc = RpcProvider(self.settings, self.chain.chain_id)
            except UniswapError as exc:
                checks.append(
                    {
                        "provider": "rpc",
                        "configured": False,
                        "ok": False,
                        "error": exc.as_dict(),
                    }
                )
            else:
                tasks.append(("rpc", self._doctor_rpc(rpc, check_archive=check_archive)))
        if tasks:
            results = await asyncio.gather(*(task for _, task in tasks))
            checks.extend(results)
        result = _local_result(
            {
                "ok": any(check.get("ok") for check in checks),
                "degraded": any(not check.get("ok") for check in checks),
                "checks": checks,
            },
            source_id="doctor",
        )
        return self.render(result)

    @staticmethod
    async def _doctor_subgraph(provider: SubgraphProvider) -> dict[str, Any]:
        try:
            result = await provider.health()
        except UniswapError as exc:
            return {
                "provider": "subgraph",
                "configured": True,
                "ok": False,
                "error": exc.as_dict(),
            }
        finally:
            await provider.close()
        return {
            "provider": "subgraph",
            "configured": True,
            "ok": bool(result.data.get("ok")),
            "source_id": result.source_id,
            "indexed_block": result.indexed_block,
            "details": result.data,
        }

    @staticmethod
    async def _doctor_rpc(provider: RpcProvider, *, check_archive: bool) -> dict[str, Any]:
        try:
            result = await provider.health(check_archive=check_archive)
        except UniswapError as exc:
            return {
                "provider": "rpc",
                "configured": True,
                "ok": False,
                "error": exc.as_dict(),
            }
        finally:
            await provider.close()
        return {
            "provider": "rpc",
            "configured": True,
            "ok": bool(result.data.get("ok")),
            "source_id": result.source_id,
            "indexed_block": result.indexed_block,
            "details": result.data,
        }

    @staticmethod
    def _require_provider(provider: str, *, allowed: set[str]) -> None:
        if provider not in allowed:
            raise unsupported(
                f"provider {provider!r} is unavailable for this command",
                provider=provider,
                allowed=sorted(allowed),
            )
