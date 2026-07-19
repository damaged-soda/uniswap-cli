from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from uniswap_cli.cli import main
from uniswap_cli.config import Settings
from uniswap_cli.providers.subgraph import SubgraphProvider, _pool_v34, _swap_model

SCHEMA_DIR = Path(__file__).parents[1] / "schemas"


def _validator(name: str) -> Draft202012Validator:
    schema = json.loads((SCHEMA_DIR / name).read_text())
    resources = []
    for path in SCHEMA_DIR.glob("*.json"):
        document = json.loads(path.read_text())
        resources.append((document["$id"], Resource.from_contents(document)))
    registry = Registry().with_resources(resources)
    return Draft202012Validator(
        schema,
        registry=registry,
        format_checker=FormatChecker(),
    )


def test_local_response_matches_envelope_schema(capsys) -> None:
    assert main(["chains", "list"]) == 0
    payload = json.loads(capsys.readouterr().out)
    _validator("response-0.1.schema.json").validate(payload)


@pytest.mark.asyncio
async def test_normalized_entities_match_versioned_schemas(load_fixture) -> None:
    pool_raw = load_fixture("subgraph_v3_pools.json")["data"]["pools"][0]
    _validator("pool-0.1.schema.json").validate(_pool_v34(pool_raw, "v3"))

    swap_raw = load_fixture("subgraph_v2_swaps.json")["data"]["swaps"][0]
    _validator("swap-0.1.schema.json").validate(_swap_model(swap_raw, "v2"))

    series_raw = load_fixture("subgraph_v4_series.json")["data"]["poolDayDatas"][0]
    settings = Settings.from_env({"UNISWAP_SUBGRAPH_URL_1_V4": "https://subgraph.test/graphql"})
    provider = SubgraphProvider(settings, 1, "v4")
    point = provider._series_point(
        series_raw,
        pool_id="0x" + "77" * 32,
        metric="ohlcv",
        interval="1d",
    )
    await provider.close()
    _validator("series-point-0.1.schema.json").validate(point)
