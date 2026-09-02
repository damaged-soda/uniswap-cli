from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Final

from uniswap_cli.errors import UniswapError, invalid_argument

PROTOCOLS: Final[tuple[str, ...]] = ("v2", "v3", "v4")


@dataclass(frozen=True)
class ChainDefinition:
    chain_id: int
    name: str
    aliases: tuple[str, ...]
    native_symbol: str


@dataclass(frozen=True)
class SubgraphDeployment:
    chain_id: int
    protocol: str
    subgraph_id: str
    source_repository: str


@dataclass(frozen=True)
class Endpoint:
    url: str
    label: str
    headers: Mapping[str, str]


CHAINS: Final[tuple[ChainDefinition, ...]] = (
    ChainDefinition(1, "ethereum", ("eth", "mainnet", "ethereum-mainnet"), "ETH"),
    ChainDefinition(10, "optimism", ("op",), "ETH"),
    ChainDefinition(56, "bnb", ("bsc", "bnb-chain"), "BNB"),
    ChainDefinition(130, "unichain", (), "ETH"),
    ChainDefinition(137, "polygon", ("matic",), "POL"),
    ChainDefinition(196, "xlayer", ("x-layer",), "OKB"),
    ChainDefinition(324, "zksync", ("zksync-era",), "ETH"),
    ChainDefinition(480, "worldchain", ("world-chain",), "ETH"),
    ChainDefinition(1_868, "soneium", (), "ETH"),
    ChainDefinition(4_663, "robinhood", ("robinhood-chain", "robinhood-mainnet"), "ETH"),
    ChainDefinition(8_453, "base", (), "ETH"),
    ChainDefinition(42_161, "arbitrum", ("arbitrum-one", "arb"), "ETH"),
    ChainDefinition(42_220, "celo", (), "CELO"),
    ChainDefinition(43_114, "avalanche", ("avax",), "AVAX"),
    ChainDefinition(57_073, "ink", (), "ETH"),
    ChainDefinition(59_144, "linea", (), "ETH"),
    ChainDefinition(81_457, "blast", (), "ETH"),
    ChainDefinition(7_777_777, "zora", (), "ETH"),
)

SUBGRAPH_DEPLOYMENTS: Final[dict[tuple[int, str], SubgraphDeployment]] = {
    (1, "v2"): SubgraphDeployment(
        1,
        "v2",
        "A3Np3RQbaBA6oKJgiwDJeo5T3zrYfGHPWFYayMwtNDum",
        "https://github.com/Uniswap/v2-subgraph",
    ),
    (1, "v3"): SubgraphDeployment(
        1,
        "v3",
        "5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV",
        "https://github.com/Uniswap/v3-subgraph",
    ),
    (1, "v4"): SubgraphDeployment(
        1,
        "v4",
        "DiYPVdygkfjDWhbxGSqAQxwBKmfKnkWQojqeM2rkLb3G",
        "https://github.com/Uniswap/v4-subgraph",
    ),
}


def parse_chain(value: str | int) -> ChainDefinition:
    text = str(value).strip().lower()
    for chain in CHAINS:
        if text == str(chain.chain_id) or text == chain.name or text in chain.aliases:
            return chain
    if text.isdecimal() and int(text) > 0:
        chain_id = int(text)
        return ChainDefinition(chain_id, f"eip155-{chain_id}", (), "UNKNOWN")
    raise invalid_argument(
        "unsupported chain; use `uniswap chains list` to inspect supported chains",
        chain=value,
    )


def parse_protocol(value: str) -> str:
    protocol = value.strip().lower()
    if protocol not in PROTOCOLS:
        raise invalid_argument("protocol must be one of v2, v3, or v4", protocol=value)
    return protocol


def _positive_float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise invalid_argument(f"{name} must be a number", variable=name) from exc
    if value <= 0:
        raise invalid_argument(f"{name} must be greater than zero", variable=name)
    return value


def _nonnegative_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise invalid_argument(f"{name} must be an integer", variable=name) from exc
    if value < 0:
        raise invalid_argument(f"{name} must be non-negative", variable=name)
    return value


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    value = _nonnegative_int(env, name, default)
    if value < 1:
        raise invalid_argument(f"{name} must be at least 1", variable=name)
    return value


@dataclass(frozen=True)
class Settings:
    env: Mapping[str, str]
    timeout_seconds: float
    max_retries: int
    retry_backoff_seconds: float
    max_concurrency: int
    rpc_max_block_range: int
    rpc_max_log_requests: int
    default_chain: str
    default_protocol: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        source = dict(os.environ if env is None else env)
        return cls(
            env=source,
            timeout_seconds=_positive_float(source, "UNISWAP_HTTP_TIMEOUT_SECONDS", 20.0),
            max_retries=_nonnegative_int(source, "UNISWAP_HTTP_MAX_RETRIES", 3),
            retry_backoff_seconds=_positive_float(
                source, "UNISWAP_HTTP_RETRY_BACKOFF_SECONDS", 0.2
            ),
            max_concurrency=_positive_int(source, "UNISWAP_HTTP_MAX_CONCURRENCY", 4),
            rpc_max_block_range=_positive_int(source, "UNISWAP_RPC_MAX_BLOCK_RANGE", 2_000),
            rpc_max_log_requests=_positive_int(source, "UNISWAP_RPC_MAX_LOG_REQUESTS", 200),
            default_chain=source.get("UNISWAP_DEFAULT_CHAIN", "ethereum"),
            default_protocol=parse_protocol(source.get("UNISWAP_DEFAULT_PROTOCOL", "v3")),
        )

    def with_timeout(self, timeout_seconds: float | None) -> Settings:
        if timeout_seconds is None:
            return self
        if timeout_seconds <= 0:
            raise invalid_argument("--timeout must be greater than zero")
        return replace(self, timeout_seconds=timeout_seconds)

    def subgraph_endpoint(self, chain_id: int, protocol: str) -> Endpoint:
        protocol = parse_protocol(protocol)
        suffix = f"{chain_id}_{protocol.upper()}"
        custom_url = self.env.get(f"UNISWAP_SUBGRAPH_URL_{suffix}", "").strip()
        token = self.env.get(f"UNISWAP_SUBGRAPH_AUTH_TOKEN_{suffix}", "").strip()
        if custom_url:
            headers = {"authorization": f"Bearer {token}"} if token else {}
            return Endpoint(custom_url, f"custom:{chain_id}:{protocol}", headers)

        deployment = SUBGRAPH_DEPLOYMENTS.get((chain_id, protocol))
        if deployment is None:
            raise UniswapError(
                "SUBGRAPH_UNAVAILABLE",
                "no bundled subgraph deployment for this chain and protocol",
                context={"chain_id": chain_id, "protocol": protocol},
            )
        api_key = self.env.get("UNISWAP_THE_GRAPH_API_KEY", "").strip()
        if not api_key:
            raise UniswapError(
                "SUBGRAPH_AUTH_MISSING",
                "UNISWAP_THE_GRAPH_API_KEY is required for the bundled The Graph deployment; "
                f"alternatively set UNISWAP_SUBGRAPH_URL_{suffix}",
                context={"chain_id": chain_id, "protocol": protocol},
            )
        url = f"https://gateway.thegraph.com/api/subgraphs/id/{deployment.subgraph_id}"
        return Endpoint(
            url,
            f"the-graph:{deployment.subgraph_id}",
            {"authorization": f"Bearer {api_key}"},
        )

    def rpc_endpoints(self, chain_id: int) -> tuple[Endpoint, ...]:
        specific = self.env.get(f"UNISWAP_RPC_URL_{chain_id}", "").strip()
        inherited = self.env.get(f"RPC_URL_{chain_id}", "").strip()
        raw = specific or inherited
        if not raw:
            raise UniswapError(
                "RPC_CONFIG_MISSING",
                f"set UNISWAP_RPC_URL_{chain_id} or RPC_URL_{chain_id}",
                context={"chain_id": chain_id},
            )
        urls = tuple(value.strip() for value in raw.split(",") if value.strip())
        if not urls:
            raise UniswapError(
                "RPC_CONFIG_MISSING",
                "RPC endpoint list is empty",
                context={"chain_id": chain_id},
            )
        return tuple(
            Endpoint(url, f"rpc:{chain_id}:{index + 1}", {}) for index, url in enumerate(urls)
        )
