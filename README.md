# Automate Bridge

Automate Bridge lets ChatGPT create native `.flo` files on an Android phone and open them in [Automate](https://llamalab.com/automate/).

The bridge runs locally in Termux, exposes a small MCP server, and converts validated FlowSpec JSON into Automate 1.53.2 flow files. It does not require root.

> [!IMPORTANT]
> This is an experimental project. The `.flo` format is not a documented public API, and the compiler has currently been verified only with Automate 1.53.2 on Android 10.

## Features

- Native, editable Automate `.flo` generation
- Local browser UI for creating flows manually
- Remote MCP tools for ChatGPT Developer Mode
- Secret connection URL and bearer-token authentication
- Automatic copy to shared Downloads before importing
- No Python packages beyond the standard library
- Built-in compiler self-test

## Supported recipes

| Recipe | Parameters | Result |
| --- | --- | --- |
| `toast` | `message` | Show a toast message |
| `two_toasts` | `first`, `second` | Show two sequential toast messages |
| `delay_toast` | `seconds`, `message` | Wait, then show a toast |
| `notification` | `title`, `message` | Post a notification |
| `app_start` | `package` | Launch an Android app |
| `condition_demo` | `yes`, `no` | Demonstrate a true/false branch |
| `time_toast` | `time`, `message` | Wait until `HH:MM`, then show a toast |

## Requirements

- Android phone with Automate 1.53.2 installed
- Termux
- Python 3
- `cloudflared` for ChatGPT access
- ChatGPT account with Developer Mode access

## Phone setup

### 1. Install dependencies

Open Termux and run:

```sh
pkg update
pkg install python git cloudflared
termux-setup-storage
```

Allow the Android storage permission when requested.

### 2. Download the project

```sh
git clone https://github.com/Possible1Official/Automate-Plugin.git
cd Automate-Plugin
```

You can also download the repository ZIP from GitHub and extract it in Termux.

### 3. Test and start the bridge

```sh
python automate_bridge.py --self-test
python automate_bridge.py
```

A successful self-test prints:

```text
PASS: 7 recipes and exact Toast-A reference
```

Keep this Termux session running. The bridge exposes:

- Local UI: `http://127.0.0.1:8787`
- Private MCP port: `http://127.0.0.1:8788`

The first start creates two private files:

- `.automate_bridge_token`
- `.automate_bridge_url_key`

Never publish, share, or screenshot their contents.

## Connect ChatGPT

### 1. Start an HTTPS tunnel

Open a second Termux session:

```sh
cloudflared tunnel --url http://127.0.0.1:8788
```

The command stays active while the tunnel is running. Copy the generated `https://...trycloudflare.com` hostname.

### 2. Build the private connection URL

The bridge terminal prints a line similar to:

```text
ChatGPT secret path: /connect/RANDOM_PRIVATE_KEY/mcp
```

Append that path to the Cloudflare hostname:

```text
https://your-tunnel.trycloudflare.com/connect/RANDOM_PRIVATE_KEY/mcp
```

Treat this complete URL like a password.

### 3. Add the MCP server in ChatGPT

In ChatGPT on the web:

1. Open **Settings → Security and login** and enable **Developer mode**.
2. Open **Plugins**, select **+**, and create a Developer Mode connection.
3. Name it `Automate Bridge`.
4. Paste the complete private connection URL.
5. Select **No Authentication**. Authentication is provided by the high-entropy key inside the URL.
6. Review the five discovered tools and create the connection.

The available tools are:

- `bridge_status`
- `list_recipes`
- `list_flows`
- `create_flow`
- `open_flow`

### 4. Test the connection

Optionally test the public tunnel from a third Termux session without displaying the bearer token:

```sh
python test_remote_mcp.py https://your-tunnel.trycloudflare.com
```

Then select Automate Bridge in ChatGPT's Developer Mode tool and try:

```text
Create a toast flow named hello-chatgpt with the message "Hello from ChatGPT", then open it in Automate.
```

Automate Bridge copies the generated file to Android's shared Downloads directory before sending the import intent. Review the flow in Automate and approve its permissions before running it.

## FlowSpec example

```json
{
  "name": "study-reminder",
  "recipe": "time_toast",
  "params": {
    "time": "18:00",
    "message": "Time to study"
  }
}
```

Generated files are stored in `generated/` and copied to shared Downloads when `open_flow` is called.

## Local HTTP API

Health check:

```text
GET http://127.0.0.1:8787/health
```

Generate a flow:

```text
POST http://127.0.0.1:8787/api/generate
Content-Type: application/json
```

Send a FlowSpec as the JSON body. Add `"open": true` at the root to request the Automate import screen.

## Troubleshooting

### `Failed to read flow`

Run `termux-setup-storage`, allow storage permission, and restart the bridge. Version 0.2.3 copies flows to shared Downloads before opening them.

### Automate does not appear

On MIUI, open the Termux app permissions and allow background pop-up windows or **Display over other apps**. Set its battery mode to **No restrictions**.

### ChatGPT cannot connect

- Confirm both the bridge and `cloudflared` are still running.
- Quick Tunnel URLs change after `cloudflared` restarts. Update the ChatGPT connection with the new hostname.
- Preserve the complete secret path printed by the bridge.

### Recipe selector is empty

Restart the bridge after updating the script, then refresh the local browser page to clear the older UI.

## Security

- Port `8787` binds to localhost and should never be exposed publicly.
- Tunnel only port `8788`.
- The normal `/mcp` endpoint requires a bearer token.
- The ChatGPT connection path contains a separate high-entropy secret.
- Never commit `.automate_bridge_token`, `.automate_bridge_url_key`, or generated flows.
- Stop `cloudflared` when remote access is not needed.
- Inspect every generated flow before granting permissions or running it.

If a secret is exposed, stop the bridge, delete only the affected secret file, restart the bridge to generate a new value, and update the ChatGPT connection.

## Current limitations

- Verified only with Automate 1.53.2 and Android 10
- Supports the listed recipes, not arbitrary Automate blocks yet
- Cloudflare Quick Tunnels are temporary development tunnels
- Android still requires the user to confirm importing a flow

## Project files

- `automate_bridge.py` — FlowSpec compiler, local UI, and MCP server
- `test_remote_mcp.py` — authenticated public-tunnel test

## How it works

1. ChatGPT calls an MCP tool through the HTTPS tunnel.
2. The phone validates the secret connection path.
3. The bridge compiles a supported FlowSpec into `.flo` bytes.
4. The file is stored locally and copied to shared Downloads.
5. Android opens Automate's import screen for user review.

Contributions that add carefully verified block mappings, tests, or Android compatibility improvements are welcome.
