from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from typing import Any

from uniswap_cli import __version__
from uniswap_cli.config import Settings
from uniswap_cli.errors import UniswapError, invalid_argument
from uniswap_cli.models import error_envelope
from uniswap_cli.service import UniswapService, parse_timestamp

_ENV_HELP = """\
environment:
  UNISWAP_THE_GRAPH_API_KEY          The Graph gateway key for bundled deployments.
  UNISWAP_SUBGRAPH_URL_<id>_<VER>    Custom endpoint, e.g. UNISWAP_SUBGRAPH_URL_1_V3.
  UNISWAP_SUBGRAPH_AUTH_TOKEN_<...>  Optional Bearer token for a custom endpoint.
  UNISWAP_RPC_URL_<chain-id>         Comma-separated RPC fallbacks; RPC_URL_<id> is inherited.
  UNISWAP_DEFAULT_CHAIN              Default chain (default ethereum).
  UNISWAP_DEFAULT_PROTOCOL           Default protocol (default v3).
  UNISWAP_HTTP_TIMEOUT_SECONDS       Per-request timeout (default 20).
  UNISWAP_HTTP_MAX_RETRIES           Retries for network/429/5xx failures (default 3).
  UNISWAP_HTTP_MAX_CONCURRENCY       Per-process outbound concurrency (default 4).
  UNISWAP_RPC_MAX_BLOCK_RANGE        Initial eth_getLogs chunk size (default 2000; adapts down).
  UNISWAP_RPC_MAX_LOG_REQUESTS       Safety cap per command (default 200).

All successful commands write data to stdout. Runtime errors are structured JSON on stderr.
No command signs or broadcasts a transaction.
"""


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        payload = error_envelope(
            {
                "code": "INVALID_ARGUMENT",
                "message": message,
                "retryable": False,
                "context": {},
            }
        )
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
        self.exit(2)


def _subcommands(parser: argparse.ArgumentParser) -> argparse._SubParsersAction[Any]:
    return parser.add_subparsers(dest="command", required=True, parser_class=JsonArgumentParser)


def _add_scope(
    parser: argparse.ArgumentParser,
    *,
    protocol: bool = True,
    provider: bool = True,
) -> None:
    parser.add_argument("--chain", help="Chain name or ID (default from environment).")
    if protocol:
        parser.add_argument("--protocol", choices=["v2", "v3", "v4"], help="Protocol version.")
    if provider:
        parser.add_argument(
            "--provider",
            choices=["auto", "subgraph", "rpc"],
            default="auto",
            help="Upstream provider (default auto).",
        )
    parser.add_argument("--timeout", type=float, help="Override per-request timeout in seconds.")
    parser.add_argument(
        "--format",
        choices=["json", "jsonl", "table"],
        default="json",
        help="Output format (default json).",
    )


def _block_number(value: str) -> int:
    try:
        number = int(value, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a decimal or 0x-prefixed block number") from exc
    if number < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return number


def _json_object(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"must be valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("must be a JSON object")
    return parsed


def build_parser() -> JsonArgumentParser:
    parser = JsonArgumentParser(
        prog="uniswap",
        description=(
            "Read-only Uniswap data CLI for pools, swaps, historical series, subgraph queries, "
            "and raw EVM logs. CLI + skill only; no MCP or daemon required."
        ),
        epilog=_ENV_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    root = _subcommands(parser)

    chains = root.add_parser("chains", help="Inspect supported chains")
    chains_sub = _subcommands(chains)
    chains_list = chains_sub.add_parser("list", help="List bundled chain support")
    _add_scope(chains_list, protocol=False, provider=False)

    protocols = root.add_parser("protocols", help="Inspect protocol support")
    protocols_sub = _subcommands(protocols)
    protocols_list = protocols_sub.add_parser("list", help="List v2/v3/v4 support")
    _add_scope(protocols_list, protocol=False, provider=False)

    tokens = root.add_parser("tokens", help="Query token aggregates")
    tokens_sub = _subcommands(tokens)
    tokens_get = tokens_sub.add_parser("get", help="Get one token from a subgraph")
    tokens_get.add_argument("--address", required=True, help="Token contract address.")
    _add_scope(tokens_get)

    pools = root.add_parser("pools", help="Discover and inspect pools")
    pools_sub = _subcommands(pools)
    pools_list = pools_sub.add_parser("list", help="List pools ordered by an aggregate")
    pools_list.add_argument("--limit", type=int, default=20)
    pools_list.add_argument("--cursor")
    pools_list.add_argument(
        "--order-by",
        choices=["tvl-usd", "volume-usd", "created", "tx-count"],
        default="tvl-usd",
    )
    pools_list.add_argument("--direction", choices=["asc", "desc"], default="desc")
    pools_list.add_argument("--token0", help="Exact token0 address filter.")
    pools_list.add_argument("--token1", help="Exact token1 address filter.")
    _add_scope(pools_list)
    pools_get = pools_sub.add_parser("get", help="Get one pool by address or v4 pool ID")
    pools_get.add_argument("--pool", required=True, help="Pool address (v2/v3) or pool ID (v4).")
    _add_scope(pools_get)

    swaps = root.add_parser("swaps", help="Query normalized swap history")
    swaps_sub = _subcommands(swaps)
    swaps_list = swaps_sub.add_parser("list", help="List swaps for one pool")
    swaps_list.add_argument("--pool", required=True)
    swaps_list.add_argument("--from", dest="from_time", help="RFC3339 or unix seconds.")
    swaps_list.add_argument("--to", dest="to_time", help="RFC3339 or unix seconds.")
    swaps_list.add_argument("--from-block", type=_block_number)
    swaps_list.add_argument("--to-block", type=_block_number)
    swaps_list.add_argument("--limit", type=int, default=100)
    swaps_list.add_argument("--cursor")
    swaps_list.add_argument("--direction", choices=["asc", "desc"], default="desc")
    _add_scope(swaps_list)
    swaps_reconcile = swaps_sub.add_parser(
        "reconcile", help="Compare subgraph and RPC swaps over a block range"
    )
    swaps_reconcile.add_argument("--pool", required=True)
    swaps_reconcile.add_argument("--from-block", type=_block_number, required=True)
    swaps_reconcile.add_argument("--to-block", type=_block_number, required=True)
    swaps_reconcile.add_argument("--max-swaps", type=int, default=10_000)
    swaps_reconcile.add_argument("--sample-limit", type=int, default=100)
    _add_scope(swaps_reconcile, provider=False)

    series = root.add_parser("series", help="Query pool time series")
    series_sub = _subcommands(series)
    series_get = series_sub.add_parser("get", help="Get one pool metric series")
    series_get.add_argument("--pool", required=True)
    series_get.add_argument(
        "--metric",
        choices=["volume-usd", "tvl-usd", "fees-usd", "tx-count", "ohlcv"],
        required=True,
    )
    series_get.add_argument("--interval", choices=["1h", "1d"], default="1d")
    series_get.add_argument("--from", dest="from_time", help="RFC3339 or unix seconds.")
    series_get.add_argument("--to", dest="to_time", help="RFC3339 or unix seconds.")
    series_get.add_argument("--limit", type=int, default=100)
    series_get.add_argument("--cursor")
    _add_scope(series_get)

    raw = root.add_parser("raw", help="Use explicitly upstream-shaped query surfaces")
    raw_sub = _subcommands(raw)
    raw_graphql = raw_sub.add_parser("graphql", help="Send a raw read-only GraphQL query")
    raw_graphql.add_argument(
        "--query", default="-", help="GraphQL text; '-' reads stdin (default)."
    )
    raw_graphql.add_argument("--variables", type=_json_object, default={})
    _add_scope(raw_graphql, provider=False)
    raw_events = raw_sub.add_parser("events", help="Read raw EVM logs over a bounded block range")
    raw_events.add_argument("--address", required=True)
    raw_events.add_argument(
        "--topic",
        action="append",
        default=[],
        help="Topic by position; repeat flag and use 'null' as a wildcard.",
    )
    raw_events.add_argument("--from-block", type=_block_number, required=True)
    raw_events.add_argument("--to-block", type=_block_number)
    raw_events.add_argument("--limit", type=int, default=1_000)
    raw_events.add_argument("--cursor")
    raw_events.add_argument("--direction", choices=["asc", "desc"], default="asc")
    _add_scope(raw_events, protocol=False, provider=False)

    doctor = root.add_parser("doctor", help="Check credentials, endpoints, chain ID, and indexing")
    doctor.add_argument("--provider", choices=["auto", "subgraph", "rpc"], default="auto")
    doctor.add_argument(
        "--no-archive", action="store_true", help="Skip historical-block capability check."
    )
    _add_scope(doctor, provider=False)
    return parser


def _service(args: argparse.Namespace) -> UniswapService:
    settings = Settings.from_env().with_timeout(getattr(args, "timeout", None))
    return UniswapService(
        settings,
        chain=getattr(args, "chain", None),
        protocol=getattr(args, "protocol", None),
    )


def _read_query(value: str) -> str:
    text = sys.stdin.read() if value == "-" else value
    if not text.strip():
        raise invalid_argument("GraphQL query cannot be empty")
    lowered = text.lower()
    if "mutation" in lowered or "subscription" in lowered:
        raise invalid_argument("raw graphql only accepts read-only query operations")
    return text


async def dispatch(args: argparse.Namespace) -> dict[str, Any]:
    service = _service(args)
    path = tuple(getattr(args, name, None) for name in ("command", "subcommand"))
    # Nested argparse parsers reuse `command`; recover the leaf from an explicit marker.
    action = getattr(args, "action", None)
    if action == "chains-list":
        return service.chains()
    if action == "protocols-list":
        return service.protocols()
    if action == "tokens-get":
        return await service.token(args.address, provider=args.provider)
    if action == "pools-list":
        return await service.pools_list(
            provider=args.provider,
            limit=args.limit,
            cursor=args.cursor,
            order_by=args.order_by,
            direction=args.direction,
            token0=args.token0,
            token1=args.token1,
        )
    if action == "pools-get":
        return await service.pool(args.pool, provider=args.provider)
    if action == "swaps-list":
        return await service.swaps(
            pool_id=args.pool,
            provider=args.provider,
            start_timestamp=parse_timestamp(args.from_time, field="from"),
            end_timestamp=parse_timestamp(args.to_time, field="to"),
            start_block=args.from_block,
            end_block=args.to_block,
            limit=args.limit,
            cursor=args.cursor,
            direction=args.direction,
        )
    if action == "swaps-reconcile":
        return await service.reconcile_swaps(
            pool_id=args.pool,
            start_block=args.from_block,
            end_block=args.to_block,
            max_swaps=args.max_swaps,
            sample_limit=args.sample_limit,
        )
    if action == "series-get":
        return await service.series(
            pool_id=args.pool,
            provider=args.provider,
            metric=args.metric,
            interval=args.interval,
            start_timestamp=parse_timestamp(args.from_time, field="from"),
            end_timestamp=parse_timestamp(args.to_time, field="to"),
            limit=args.limit,
            cursor=args.cursor,
        )
    if action == "raw-graphql":
        return await service.raw_graphql(_read_query(args.query), args.variables)
    if action == "raw-events":
        topics = [None if topic.lower() == "null" else topic for topic in args.topic]
        return await service.raw_events(
            address=args.address,
            topics=topics,
            start_block=args.from_block,
            end_block=args.to_block,
            limit=args.limit,
            cursor=args.cursor,
            direction=args.direction,
        )
    if action == "doctor":
        return await service.doctor(provider=args.provider, check_archive=not args.no_archive)
    raise invalid_argument("unknown command path", path=path)


def _mark_actions(parser: argparse.ArgumentParser) -> None:
    """Attach stable dispatch markers after construction without duplicating parser wiring."""
    root_action = next(
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    )
    mapping = {
        ("chains", "list"): "chains-list",
        ("protocols", "list"): "protocols-list",
        ("tokens", "get"): "tokens-get",
        ("pools", "list"): "pools-list",
        ("pools", "get"): "pools-get",
        ("swaps", "list"): "swaps-list",
        ("swaps", "reconcile"): "swaps-reconcile",
        ("series", "get"): "series-get",
        ("raw", "graphql"): "raw-graphql",
        ("raw", "events"): "raw-events",
    }
    for (parent_name, leaf_name), marker in mapping.items():
        parent = root_action.choices[parent_name]
        child_action = next(
            action for action in parent._actions if isinstance(action, argparse._SubParsersAction)
        )
        child_action.choices[leaf_name].set_defaults(action=marker)
    root_action.choices["doctor"].set_defaults(action="doctor")


def _scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def render_output(payload: dict[str, Any], output_format: str) -> str:
    if output_format == "json":
        return json.dumps(payload, indent=2, ensure_ascii=False)
    data = payload.get("data")
    rows = data if isinstance(data, list) else [data]
    if output_format == "jsonl":
        return "\n".join(
            json.dumps(
                {
                    "schema_version": payload.get("schema_version"),
                    "data": row,
                    "meta": payload.get("meta"),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            for row in rows
        )
    if not rows:
        return ""
    if not all(isinstance(row, dict) for row in rows):
        return "value\n" + "\n".join(_scalar(row) for row in rows)
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    lines = ["\t".join(columns)]
    lines.extend("\t".join(_scalar(row.get(column)) for column in columns) for row in rows)
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    _mark_actions(parser)
    args = parser.parse_args(argv)
    try:
        payload = asyncio.run(dispatch(args))
    except UniswapError as exc:
        print(
            json.dumps(error_envelope(exc.as_dict()), indent=2, ensure_ascii=False), file=sys.stderr
        )
        return 1
    except KeyboardInterrupt:
        error = UniswapError("INTERRUPTED", "operation interrupted")
        print(json.dumps(error_envelope(error.as_dict()), indent=2), file=sys.stderr)
        return 130
    print(render_output(payload, args.format))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through installed entry point
    raise SystemExit(main())
