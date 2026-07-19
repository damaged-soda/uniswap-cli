from __future__ import annotations

import json

from uniswap_cli.cli import main


def test_chains_list_is_machine_readable(capsys) -> None:
    assert main(["chains", "list"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "0.1"
    assert payload["data"][0]["chain_id"] == 1


def test_argument_errors_are_structured_json(capsys) -> None:
    try:
        main(["pools", "get"])
    except SystemExit as exc:
        assert exc.code == 2
    payload = json.loads(capsys.readouterr().err)
    assert payload["error"]["code"] == "INVALID_ARGUMENT"


def test_missing_subgraph_key_is_explicit_and_secret_safe(capsys, monkeypatch) -> None:
    monkeypatch.delenv("UNISWAP_THE_GRAPH_API_KEY", raising=False)
    monkeypatch.delenv("UNISWAP_SUBGRAPH_URL_1_V3", raising=False)
    assert (
        main(
            [
                "pools",
                "get",
                "--pool",
                "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640",
            ]
        )
        == 1
    )
    payload = json.loads(capsys.readouterr().err)
    assert payload["error"]["code"] == "SUBGRAPH_AUTH_MISSING"


def test_table_output(capsys) -> None:
    assert main(["protocols", "list", "--format", "table"]) == 0
    output = capsys.readouterr().out
    assert "protocol_version" in output
    assert "v3" in output
