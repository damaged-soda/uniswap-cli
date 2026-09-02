from __future__ import annotations

import pytest

from uniswap_cli.config import Settings, parse_chain
from uniswap_cli.errors import UniswapError, redact_text, sanitize_context


def settings(env: dict[str, str]) -> Settings:
    return Settings.from_env(env)


def test_bundled_subgraph_requires_key() -> None:
    with pytest.raises(UniswapError) as caught:
        settings({}).subgraph_endpoint(1, "v3")
    assert caught.value.code == "SUBGRAPH_AUTH_MISSING"


def test_bundled_subgraph_key_uses_bearer_header_not_url() -> None:
    endpoint = settings({"UNISWAP_THE_GRAPH_API_KEY": "super-secret"}).subgraph_endpoint(1, "v3")
    assert "super-secret" not in endpoint.url
    assert "super-secret" not in endpoint.label
    assert endpoint.headers == {"authorization": "Bearer super-secret"}
    assert endpoint.label.startswith("the-graph:")


def test_custom_subgraph_bearer_token() -> None:
    endpoint = settings(
        {
            "UNISWAP_SUBGRAPH_URL_1_V3": "https://example.test/graphql",
            "UNISWAP_SUBGRAPH_AUTH_TOKEN_1_V3": "token-value",
        }
    ).subgraph_endpoint(1, "v3")
    assert endpoint.url == "https://example.test/graphql"
    assert endpoint.headers == {"authorization": "Bearer token-value"}


def test_rpc_inherits_existing_namespace_variable() -> None:
    endpoints = settings(
        {"RPC_URL_1": "https://first.test/key, https://second.test/key"}
    ).rpc_endpoints(1)
    assert [endpoint.label for endpoint in endpoints] == ["rpc:1:1", "rpc:1:2"]


def test_secret_redaction_removes_url_paths_and_secret_fields() -> None:
    assert (
        redact_text("failed https://host.test/v3/secret-key?q=1") == "failed https://host.test/***"
    )
    assert sanitize_context({"endpoint": "https://host.test/path/key", "api_key": "secret"}) == {
        "endpoint": "https://host.test/***",
        "api_key": "[redacted]",
    }
    assert redact_text("failed https://user:password@host.test/key") == (
        "failed https://host.test/***"
    )
    assert sanitize_context({"token0": "0x" + "11" * 20}) == {"token0": "0x" + "11" * 20}


def test_chain_aliases() -> None:
    assert parse_chain("eth").chain_id == 1
    assert parse_chain("1").name == "ethereum"
    assert parse_chain("robinhood").chain_id == 4663
    assert parse_chain("robinhood-chain").name == "robinhood"
    assert parse_chain("4663").name == "robinhood"
