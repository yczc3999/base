#!/usr/bin/env python3
"""Re-capture the frozen WP-06 Polygon registry evidence from distinct RPC origins.

Endpoints are read only from ``PM_V2_POLYGON_RPC_URLS`` (or ``--rpc-env``).  The
fixture stores hashes of normalized endpoint origins, never endpoint strings,
paths, credentials, or query parameters. Synthetic receipt/finality conformance
vectors are retained and explicitly labelled; only historical on-chain requests
are sent.

Run from ``serve/``::

    ./.venv/bin/python tests/trading/fixtures/p6_settlement/capture_polygon_registry.py \
      --block 91842167 --require-origins 3
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
DEFAULT_OUTPUT = HERE / "polygon_rpc_golden_v1.json"


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def origin(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("RPC endpoint must be an absolute HTTP(S) URL")
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{parsed.hostname}{port}"


def call(endpoint: str, method: str, params: list[Any]) -> dict[str, Any]:
    payload = canonical_bytes(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    )
    request = Request(
        endpoint,
        data=payload,
        method="POST",
        headers={"content-type": "application/json"},
    )
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310 - explicit capture CLI
            raw = response.read()
    except Exception:
        # Never render the endpoint or provider exception: either may contain a key.
        raise RuntimeError("RPC capture request failed") from None
    try:
        parsed = json.loads(raw)
    except Exception:
        raise RuntimeError("RPC capture returned malformed JSON") from None
    if (
        not isinstance(parsed, dict)
        or parsed.get("jsonrpc") != "2.0"
        or parsed.get("id") != 1
        or ("result" in parsed) == ("error" in parsed)
    ):
        raise RuntimeError("RPC capture returned an invalid JSON-RPC envelope")
    if "error" in parsed:
        error = parsed["error"]
        code = error.get("code") if isinstance(error, dict) else None
        if isinstance(code, bool) or not isinstance(code, int):
            code = "unknown"
        raise RuntimeError(f"RPC capture provider error code={code}")
    return {"result": parsed["result"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--block", type=int, required=True)
    parser.add_argument("--require-origins", type=int, default=3)
    parser.add_argument("--rpc-env", default="PM_V2_POLYGON_RPC_URLS")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.block <= 0 or args.require_origins < 3:
        parser.error("--block must be positive and --require-origins must be >= 3")

    endpoints = tuple(
        value.strip()
        for value in os.environ.get(args.rpc_env, "").split(",")
        if value.strip()
    )
    if len(endpoints) != args.require_origins:
        parser.error(
            f"{args.rpc_env} must contain exactly {args.require_origins} endpoints"
        )
    origins = tuple(origin(endpoint) for endpoint in endpoints)
    if len(set(origins)) != len(origins):
        parser.error("RPC endpoints must have distinct normalized origins")

    fixture = json.loads(args.output.read_text(encoding="utf-8"))
    if fixture.get("snapshot_block_number") != args.block:
        parser.error("--block does not match the frozen fixture snapshot")
    synthetic = set(fixture["source_evidence"]["synthetic_keys"])
    node_names = [f"node-{chr(ord('a') + index)}" for index in range(len(endpoints))]
    fixture["rpc_nodes"] = node_names
    fixture["endpoint_origin_sha256"] = {
        node: hashlib.sha256(origin_value.encode()).hexdigest()
        for node, origin_value in zip(node_names, origins, strict=True)
    }

    for key, wire in fixture["requests"].items():
        if key in synthetic:
            continue
        per_node: dict[str, dict[str, Any]] = {}
        for node, endpoint in zip(node_names, endpoints, strict=True):
            try:
                per_node[node] = call(endpoint, wire["method"], wire["params"])
            except RuntimeError as exc:
                print(f"{node} {key}: {exc}", file=sys.stderr)
                return 2
        if len({canonical_bytes(response) for response in per_node.values()}) != 1:
            print(f"three-origin mismatch for {key}", file=sys.stderr)
            return 3
        fixture["responses"][key] = per_node
        fixture["response_sha256"][key] = {
            node: hashlib.sha256(canonical_bytes(response)).hexdigest()
            for node, response in per_node.items()
        }

    block = fixture["responses"]["eth_getBlockByNumber_snapshot"][node_names[0]]["result"]
    if block.get("number") != hex(args.block):
        print("snapshot response block number mismatch", file=sys.stderr)
        return 4
    fixture["snapshot_block_hash"] = block.get("hash")
    fixture["source_evidence"]["captured_at"] = datetime.now(timezone.utc).isoformat()
    fixture.pop("content_hash", None)
    fixture["content_hash"] = hashlib.sha256(canonical_bytes(fixture)).hexdigest()
    args.output.write_text(
        json.dumps(fixture, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {args.output} content_hash={fixture['content_hash']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
