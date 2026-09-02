from __future__ import annotations

import json

import uniswap_cli.cli as cli_module
from uniswap_cli.cli import main


def test_chains_list_is_machine_readable(capsys) -> None:
    assert main(["chains", "list"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "0.1"
    assert payload["data"][0]["chain_id"] == 1
    robinhood = next(row for row in payload["data"] if row["name"] == "robinhood")
    assert robinhood["chain_id"] == 4663
    assert robinhood["rpc_env"] == ["UNISWAP_RPC_URL_4663", "RPC_URL_4663"]


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


def test_robinhood_protocols_list_uses_registered_chain(capsys) -> None:
    assert main(["protocols", "list", "--chain", "robinhood", "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["meta"]["chain_id"] == 4663
    assert payload["meta"]["chain"] == "robinhood"
    assert [row["protocol_version"] for row in payload["data"]] == ["v2", "v3", "v4"]


def test_runtime_invalid_argument_uses_exit_two(capsys) -> None:
    assert (
        main(
            [
                "swaps",
                "list",
                "--pool",
                "0x" + "11" * 20,
                "--from",
                "not-a-time",
            ]
        )
        == 2
    )
    payload = json.loads(capsys.readouterr().err)
    assert payload["error"]["code"] == "INVALID_ARGUMENT"


def test_unexpected_errors_remain_structured(capsys, monkeypatch) -> None:
    async def fail(_args):
        raise ValueError("must not become a traceback")

    monkeypatch.setattr(cli_module, "dispatch", fail)
    assert main(["chains", "list", "--format", "jsonl"]) == 1
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert "\n" not in captured.err.strip()
    payload = json.loads(captured.err)
    assert payload["error"]["code"] == "INTERNAL_ERROR"


def test_doctor_exits_nonzero_when_requested_provider_is_unconfigured(capsys, monkeypatch) -> None:
    monkeypatch.delenv("UNISWAP_RPC_URL_1", raising=False)
    monkeypatch.delenv("RPC_URL_1", raising=False)
    assert main(["doctor", "--provider", "rpc", "--no-archive"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["ok"] is False
