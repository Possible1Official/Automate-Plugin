#!/usr/bin/env python3
"""Test an Automate Bridge through its public HTTPS tunnel."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


TOKEN_FILE = Path.cwd() / ".automate_bridge_token"


def rpc(endpoint: str, token: str, request_id: int, method: str, params: dict | None = None) -> dict:
    payload = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        payload["params"] = params
    request = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python test_remote_mcp.py https://your-tunnel.trycloudflare.com")
    endpoint = sys.argv[1].rstrip("/") + "/mcp"
    if not TOKEN_FILE.is_file():
        raise SystemExit("Missing .automate_bridge_token; run this from the bridge directory.")
    token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    if len(token) < 24:
        raise SystemExit("The local pairing token is invalid.")

    try:
        initialized = rpc(
            endpoint,
            token,
            1,
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "automate-bridge-self-test", "version": "1.0"},
            },
        )
        tools = rpc(endpoint, token, 2, "tools/list", {})
    except HTTPError as exc:
        raise SystemExit(f"FAIL: tunnel returned HTTP {exc.code}") from exc
    except URLError as exc:
        raise SystemExit(f"FAIL: could not reach tunnel: {exc.reason}") from exc

    server = initialized.get("result", {}).get("serverInfo", {}).get("name")
    tool_names = [item.get("name") for item in tools.get("result", {}).get("tools", [])]
    expected = {"bridge_status", "list_recipes", "list_flows", "create_flow", "open_flow"}
    if server != "automate-bridge" or set(tool_names) != expected:
        raise SystemExit(f"FAIL: unexpected MCP response (server={server!r}, tools={tool_names!r})")
    print("PASS: public tunnel reached the authenticated Automate Bridge")
    print("Tools: " + ", ".join(tool_names))


if __name__ == "__main__":
    main()
