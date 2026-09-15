#!/usr/bin/env python3
"""Automate Bridge MVP for Android/Termux and Automate 1.53.2."""

from __future__ import annotations

import argparse
import hmac
import json
import re
import secrets
import shutil
import struct
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

MAGIC = b"LAFl\x00\x72"
BEGINNING = 1072
APP_START = 1001
EXPRESSION_TRUE = 1058
DELAY = 1046
NOTIFICATION = 1103
TOAST = 1120
TIME_AWAIT = 1169
OUTPUT_DIR = Path.cwd() / "generated"
TOKEN_FILE = Path.cwd() / ".automate_bridge_token"
URL_KEY_FILE = Path.cwd() / ".automate_bridge_url_key"
SHARED_DOWNLOADS = Path.home() / "storage" / "downloads"
PAIR_TOKEN = ""
URL_KEY = ""


def uvarint(value: int) -> bytes:
    if value < 0:
        raise ValueError("Value must be non-negative")
    out = bytearray()
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def sint(value: int) -> bytes:
    encoded = value * 2 if value >= 0 else (-value * 2) - 1
    return uvarint(encoded)


def block(block_type: int, block_id: int, x: int, y: int) -> bytes:
    return sint(block_type) + sint(block_id) + sint(x) + sint(y)


def text(value: str) -> bytes:
    raw = value.encode("utf-8")
    return b"\xd4\x01" + uvarint(len(raw)) + raw


def number(value: float) -> bytes:
    return b"\xd0\x01" + struct.pack(">d", float(value))


def header(highest_id: int, count: int) -> bytes:
    return MAGIC + sint(highest_id) + uvarint(count)


def require_text(params: dict, key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{key}' must be non-empty text")
    if len(value.encode("utf-8")) > 4096:
        raise ValueError(f"'{key}' is too long")
    return value


def parse_time(value: object) -> float:
    if isinstance(value, (int, float)):
        seconds = float(value)
    elif isinstance(value, str) and re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        hour, minute = map(int, value.split(":"))
        seconds = hour * 3600 + minute * 60
    else:
        raise ValueError("'time' must be HH:MM or seconds after midnight")
    if not 0 <= seconds < 86400:
        raise ValueError("'time' must be within one day")
    return seconds


def flow_toast(params: dict) -> bytes:
    message = require_text(params, "message")
    return (
        header(2, 2)
        + block(BEGINNING, 1, 0, 0)
        + block(TOAST, 2, 0, 6)
        + b"\x00\x20\x00" + text(message) + (b"\x00" * 6) + b"\x03"
    )


def flow_two_toasts(params: dict) -> bytes:
    first = require_text(params, "first")
    second = require_text(params, "second")
    return (
        header(3, 3)
        + block(BEGINNING, 1, 0, 0)
        + block(TOAST, 2, 5, 8)
        + block(TOAST, 3, -3, 9)
        + b"\x00\x20\x00" + text(second) + b"\x00\x07" + text(first)
        + (b"\x00" * 6) + b"\x03\x05"
    )


def flow_delay_toast(params: dict) -> bytes:
    message = require_text(params, "message")
    seconds = float(params.get("seconds", 0))
    if not 0 <= seconds <= 86400:
        raise ValueError("'seconds' must be from 0 to 86400")
    return (
        header(3, 3)
        + block(BEGINNING, 1, 0, 0)
        + block(DELAY, 2, 0, 6)
        + block(TOAST, 3, 8, 2)
        + b"\x00\x20\x00" + text(message) + b"\x00\x20\x02\x00"
        + number(seconds) + (b"\x00" * 5) + b"\x03\x05"
    )


def flow_notification(params: dict) -> bytes:
    title = require_text(params, "title")
    message = require_text(params, "message")
    return (
        header(2, 2)
        + block(BEGINNING, 1, 0, 0)
        + block(NOTIFICATION, 2, 0, 6)
        + b"\x00\x00\x20\x00" + text(title) + text(message)
        + (b"\x00" * 24) + b"\x03"
    )


def flow_app_start(params: dict) -> bytes:
    package = require_text(params, "package")
    if not re.fullmatch(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+", package):
        raise ValueError("'package' is not a valid Android package name")
    return (
        header(2, 2)
        + block(BEGINNING, 1, 0, 0)
        + block(APP_START, 2, 0, 6)
        + b"\x00" + text(package) + (b"\x00" * 14) + b"\x03"
    )


def flow_condition_demo(params: dict) -> bytes:
    yes = require_text(params, "yes")
    no = require_text(params, "no")
    return (
        header(4, 4)
        + block(BEGINNING, 1, 0, 0)
        + block(EXPRESSION_TRUE, 2, 0, 6)
        + block(TOAST, 3, 0, 12)
        + b"\x00\x20\x00" + text(yes) + b"\x00"
        + block(TOAST, 4, 3, 18)
        + b"\x00\x07" + text(no) + b"\x00\xf4\x01"
        + number(1) + number(1) + (b"\x00" * 5) + b"\x03\x05\x0b"
    )


def flow_time_toast(params: dict) -> bytes:
    message = require_text(params, "message")
    seconds = parse_time(params.get("time"))
    return (
        header(3, 3)
        + block(BEGINNING, 1, 0, 0)
        + block(TIME_AWAIT, 2, 0, 6)
        + block(TOAST, 3, 0, 12)
        + b"\x00\x20\x00" + text(message) + b"\x00\x20\x04\x00\x00"
        + number(seconds) + (b"\x00" * 9) + b"\x03\x05"
    )


RECIPES = {
    "toast": flow_toast,
    "two_toasts": flow_two_toasts,
    "delay_toast": flow_delay_toast,
    "notification": flow_notification,
    "app_start": flow_app_start,
    "condition_demo": flow_condition_demo,
    "time_toast": flow_time_toast,
}


EXAMPLES = {
    "toast": {"name": "hello", "recipe": "toast", "params": {"message": "Hello from Bridge"}},
    "two_toasts": {"name": "two-steps", "recipe": "two_toasts", "params": {"first": "Step 1", "second": "Step 2"}},
    "delay_toast": {"name": "short-delay", "recipe": "delay_toast", "params": {"seconds": 2, "message": "Done"}},
    "notification": {"name": "study-alert", "recipe": "notification", "params": {"title": "Possible1", "message": "Study session starts now"}},
    "app_start": {"name": "open-calculator", "recipe": "app_start", "params": {"package": "com.miui.calculator"}},
    "condition_demo": {"name": "condition-test", "recipe": "condition_demo", "params": {"yes": "YES", "no": "NO"}},
    "time_toast": {"name": "study-reminder", "recipe": "time_toast", "params": {"time": "18:00", "message": "Time to study"}},
}


def safe_name(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("'name' must be text")
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip(".-")
    if not name:
        raise ValueError("'name' must contain letters or numbers")
    return name[:80] + ".flo"


def generate(spec: dict) -> tuple[str, bytes]:
    if not isinstance(spec, dict):
        raise ValueError("FlowSpec must be a JSON object")
    recipe = spec.get("recipe")
    if recipe not in RECIPES:
        raise ValueError("Unknown recipe. Supported: " + ", ".join(RECIPES))
    params = spec.get("params", {})
    if not isinstance(params, dict):
        raise ValueError("'params' must be an object")
    return safe_name(spec.get("name", recipe)), RECIPES[recipe](params)


def load_secret(path: Path) -> str:
    if path.is_file():
        token = path.read_text(encoding="utf-8").strip()
        if len(token) >= 24:
            return token
    token = secrets.token_urlsafe(32)
    path.write_text(token + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return token


def connection_path() -> str:
    return "/connect/" + URL_KEY + "/mcp"


def open_in_automate(target: Path) -> dict:
    if not SHARED_DOWNLOADS.is_dir():
        return {
            "launch_requested": False,
            "copied_to_downloads": False,
            "error": "Shared Downloads is unavailable. Run termux-setup-storage once and allow storage access.",
        }
    shared_target = SHARED_DOWNLOADS / target.name
    shutil.copyfile(target, shared_target)
    am_command = shutil.which("am")
    if not am_command and Path("/system/bin/am").is_file():
        am_command = "/system/bin/am"
    if not am_command:
        return {
            "launch_requested": False,
            "copied_to_downloads": True,
            "shared_path": str(shared_target),
            "error": "Android activity manager command was not found.",
        }
    result = subprocess.run(
        [
            am_command,
            "start",
            "-a",
            "android.intent.action.VIEW",
            "-d",
            shared_target.resolve().as_uri(),
            "-t",
            "application/octet-stream",
            "-f",
            "0x10000001",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=5,
        check=False,
        text=True,
    )
    response = {
        "launch_requested": result.returncode == 0,
        "copied_to_downloads": True,
        "shared_path": str(shared_target),
    }
    if result.returncode != 0:
        response["error"] = (result.stderr or result.stdout or "Android rejected the launch request").strip()[:300]
    return response


def mcp_tools() -> list[dict]:
    return [
        {
            "name": "bridge_status",
            "title": "Check Automate Bridge",
            "description": "Check whether the Android Automate Bridge is online.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
        },
        {
            "name": "list_recipes",
            "title": "List flow recipes",
            "description": "List native Automate flow recipes and example FlowSpecs supported by the phone.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
        },
        {
            "name": "list_flows",
            "title": "List generated flows",
            "description": "List .flo files already generated on the Android phone.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
        },
        {
            "name": "create_flow",
            "title": "Create an Automate flow",
            "description": "Generate a native .flo file on the paired Android phone from a supported recipe.",
            "inputSchema": {
                "type": "object",
                "required": ["name", "recipe", "params"],
                "properties": {
                    "name": {"type": "string", "description": "Safe filename without .flo"},
                    "recipe": {"type": "string", "enum": list(RECIPES)},
                    "params": {"type": "object", "description": "Recipe parameters returned by list_recipes"},
                },
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
        },
        {
            "name": "open_flow",
            "title": "Open a flow in Automate",
            "description": "Copy one generated .flo file to shared Downloads and request its import in Automate on the paired Android phone.",
            "inputSchema": {
                "type": "object",
                "required": ["filename"],
                "properties": {"filename": {"type": "string", "description": "Filename returned by create_flow or list_flows"}},
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
        },
    ]


def mcp_tool_call(name: str, arguments: object) -> dict:
    args = arguments if isinstance(arguments, dict) else {}
    if name == "bridge_status":
        data = {"online": True, "automate_version": "1.53.2", "recipe_count": len(RECIPES)}
    elif name == "list_recipes":
        data = {"recipes": EXAMPLES}
    elif name == "list_flows":
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        data = {"flows": [{"filename": p.name, "bytes": p.stat().st_size} for p in sorted(OUTPUT_DIR.glob("*.flo"))]}
    elif name == "create_flow":
        filename, payload = generate(args)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        target = OUTPUT_DIR / filename
        target.write_bytes(payload)
        data = {"created": True, "filename": filename, "bytes": len(payload), "opened": False}
    elif name == "open_flow":
        requested = args.get("filename")
        if not isinstance(requested, str) or Path(requested).name != requested or not requested.endswith(".flo"):
            raise ValueError("Invalid flow filename")
        target = OUTPUT_DIR / requested
        if not target.is_file():
            raise ValueError("Flow not found on phone")
        data = {"filename": requested, **open_in_automate(target)}
    else:
        raise ValueError("Unknown tool")
    return {
        "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
        "structuredContent": data,
        "isError": False,
    }


HTML = """<!doctype html><html><head><meta name=viewport content='width=device-width,initial-scale=1'>
<title>Automate Bridge</title><style>
:root{font-family:system-ui;color:#18211d;background:#eef6f0}body{margin:0;padding:20px}.card{max-width:720px;margin:auto;background:white;border:1px solid #cad9cf;border-radius:24px;padding:22px;box-shadow:0 16px 44px #163d2420}h1{margin:0 0 4px}p{color:#526259}select,textarea,button{width:100%;box-sizing:border-box;font:inherit;border-radius:14px}select,textarea{border:1px solid #b9c9bf;padding:12px;background:#fbfdfb}textarea{height:280px;font-family:monospace;margin:12px 0;resize:vertical}button{border:0;padding:14px;background:#176b3a;color:white;font-weight:700}button:active{transform:scale(.99)}#status{white-space:pre-wrap;margin-top:14px;padding:12px;border-radius:12px;background:#eef6f0}a{color:#176b3a;font-weight:700}
</style></head><body><main class=card><h1>Automate Bridge</h1><p>FlowSpec JSON → native Automate .flo</p>
<select id=recipe></select><textarea id=spec spellcheck=false></textarea><button id=generate>Generate & open in Automate</button><div id=status>Ready.</div></main>
<script>
const examples=__EXAMPLES__;const select=document.querySelector('#recipe'),area=document.querySelector('#spec'),status=document.querySelector('#status');
Object.keys(examples).forEach(function(k){select.add(new Option(k.split('_').join(' '),k));});function load(){area.value=JSON.stringify(examples[select.value],null,2)}select.onchange=load;load();
document.querySelector('#generate').onclick=async()=>{status.textContent='Generating…';try{const body=JSON.parse(area.value);body.open=true;const r=await fetch('/api/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const j=await r.json();if(!r.ok)throw Error(j.error);status.innerHTML=`Created ${j.filename} (${j.bytes} bytes)<br><a href="${j.download}">Download flow</a><br>${j.launch_requested?'Automate import requested.':'Tap Download if Automate did not open.'}`;}catch(e){status.textContent='Error: '+e.message}};
</script></body></html>""".replace("__EXAMPLES__", json.dumps(EXAMPLES, ensure_ascii=False))


class Handler(BaseHTTPRequestHandler):
    def send_bytes(self, status: int, content_type: str, data: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, status: int, value: dict) -> None:
        self.send_bytes(status, "application/json; charset=utf-8", json.dumps(value).encode())

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self.send_bytes(200, "text/html; charset=utf-8", HTML.encode())
        elif path == "/health":
            self.send_json(200, {"ok": True, "recipes": list(RECIPES)})
        elif path.startswith("/files/"):
            filename = Path(unquote(path.removeprefix("/files/"))).name
            target = OUTPUT_DIR / filename
            if not target.is_file() or target.suffix != ".flo":
                self.send_json(404, {"error": "Flow not found"})
                return
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_json(404, {"error": "Not found"})

    def authorized_for_mcp(self) -> bool:
        supplied = self.headers.get("Authorization", "")
        expected = "Bearer " + PAIR_TOKEN
        return hmac.compare_digest(supplied, expected)

    def handle_mcp(self, request: object) -> None:
        if not isinstance(request, dict):
            self.send_json(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}})
            return
        request_id = request.get("id")
        method = request.get("method")
        if method == "notifications/initialized":
            self.send_bytes(202, "application/json", b"")
            return
        try:
            if method == "initialize":
                requested = request.get("params", {}).get("protocolVersion") if isinstance(request.get("params"), dict) else None
                result = {
                    "protocolVersion": requested or "2025-06-18",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "automate-bridge", "version": "0.2.3"},
                    "instructions": "Create only recipes listed by list_recipes. Create a flow first, report its filename, then call open_flow only when the user asks to import/open it. Say that Android launch was requested only when launch_requested is true; never claim the user completed the import.",
                }
            elif method == "tools/list":
                result = {"tools": mcp_tools()}
            elif method == "tools/call":
                params = request.get("params")
                if not isinstance(params, dict) or not isinstance(params.get("name"), str):
                    raise ValueError("Missing tool name")
                result = mcp_tool_call(params["name"], params.get("arguments", {}))
            elif method == "ping":
                result = {}
            else:
                self.send_json(200, {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}})
                return
            self.send_json(200, {"jsonrpc": "2.0", "id": request_id, "result": result})
        except (ValueError, TypeError) as exc:
            self.send_json(200, {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": str(exc)}})

    def do_mcp_post(self, require_bearer: bool = True) -> None:
        if require_bearer and not self.authorized_for_mcp():
            self.send_response(401)
            self.send_header("WWW-Authenticate", "Bearer")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 65536:
                raise ValueError("Invalid request size")
            self.handle_mcp(json.loads(self.rfile.read(length)))
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/mcp":
            self.do_mcp_post()
            return
        if path != "/api/generate":
            self.send_json(404, {"error": "Not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 65536:
                raise ValueError("Request must be between 1 byte and 64 KB")
            spec = json.loads(self.rfile.read(length))
            filename, data = generate(spec)
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            target = OUTPUT_DIR / filename
            target.write_bytes(data)
            launch = {"launch_requested": False, "copied_to_downloads": False}
            if spec.get("open") is True:
                launch = open_in_automate(target)
            self.send_json(200, {"ok": True, "filename": filename, "bytes": len(data), "download": "/files/" + filename, **launch})
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self.send_json(400, {"error": str(exc)})
        except Exception as exc:
            self.send_json(500, {"error": f"Generation failed: {exc}"})

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")


class McpOnlyHandler(Handler):
    def do_GET(self) -> None:
        self.send_json(404, {"error": "MCP endpoint requires POST /mcp"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/mcp":
            self.do_mcp_post()
        elif hmac.compare_digest(path, connection_path()):
            self.do_mcp_post(require_bearer=False)
        else:
            self.send_json(404, {"error": "Not found"})


def self_test() -> None:
    for key, spec in EXAMPLES.items():
        name, data = generate(spec)
        assert name.endswith(".flo") and data.startswith(MAGIC) and len(data) > 17, key
    assert flow_toast({"message": "A"}).hex() == "4c41466c00720402e010020000c01104000c002000d401014100000000000003"
    assert len(mcp_tools()) == 5
    assert mcp_tool_call("bridge_status", {})["structuredContent"]["online"] is True
    print(f"PASS: {len(EXAMPLES)} recipes and exact Toast-A reference")


def main() -> None:
    global PAIR_TOKEN, URL_KEY
    parser = argparse.ArgumentParser(description="Local FlowSpec to Automate .flo bridge")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--mcp-port", type=int, default=8788)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    PAIR_TOKEN = load_secret(TOKEN_FILE)
    URL_KEY = load_secret(URL_KEY_FILE)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    mcp_server = ThreadingHTTPServer(("127.0.0.1", args.mcp_port), McpOnlyHandler)
    mcp_thread = threading.Thread(target=mcp_server.serve_forever, daemon=True)
    mcp_thread.start()
    print(f"Automate Bridge running at http://{args.host}:{args.port}")
    print(f"MCP-only endpoint: http://127.0.0.1:{args.mcp_port}/mcp")
    print(f"Pairing token: {PAIR_TOKEN}")
    print(f"ChatGPT secret path: {connection_path()}")
    print("Keep both secrets private")
    print("Press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped")
    finally:
        mcp_server.shutdown()
        mcp_server.server_close()
        server.server_close()


if __name__ == "__main__":
    main()
