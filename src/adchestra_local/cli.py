"""Install, connect and run Adchestra Local."""
import argparse
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timezone
from google_auth_oauthlib.flow import InstalledAppFlow
from adchestra_local.auth import SCOPE, connection_folder, private_json


def account_id(value):
    value = value.replace("-", "").strip()
    if len(value) != 10 or not value.isdigit():
        raise argparse.ArgumentTypeError("Use a ten-digit Google Ads customer ID.")
    return value


def claude_config_path():
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
    if sys.platform == "win32":
        return Path(os.environ["APPDATA"]) / "Claude/claude_desktop_config.json"
    raise RuntimeError("Automatic Claude Desktop setup supports macOS and Windows.")


def install_claude(folder, apply=False):
    if not (folder / "token.json").exists():
        raise RuntimeError("Run adchestra-local setup first.")
    path = claude_config_path()
    raw = path.read_text() if path.exists() else "{}"
    config = json.loads(raw)
    servers = config.setdefault("mcpServers", {})
    entry = {"command": sys.executable, "args": ["-m", "adchestra_local", "serve"],
             "env": {"ADCHESTRA_LOCAL_HOME": str(folder)}}
    if "adchestra-local" in servers and servers["adchestra-local"] != entry:
        raise RuntimeError("A different adchestra-local entry exists. Review it before replacing it.")
    print("Claude config:", path)
    if not apply:
        print(json.dumps(entry, indent=2))
        print("Preview only. Add --apply to save.")
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    private_json(folder / f"claude-config-before-{stamp}.json", json.loads(raw))
    servers["adchestra-local"] = entry
    private_json(path, config)
    print("Saved. Other connections preserved. Fully quit and reopen Claude.")


def authorize(client_data, folder, no_browser=False):
    flow = InstalledAppFlow.from_client_config(client_data, [SCOPE])
    credentials = flow.run_local_server(
        host="localhost", port=0, open_browser=not no_browser,
        prompt="consent", access_type="offline", timeout_seconds=600,
        success_message="Connected. Return to Adchestra Local.",
    )
    if not credentials.refresh_token:
        raise RuntimeError("Google did not issue a refresh token. Retry authorization.")
    private_json(folder / "token.json", json.loads(credentials.to_json()))
    private_json(folder / "client.json", client_data)


def main():
    parser = argparse.ArgumentParser(prog="adchestra-local")
    sub = parser.add_subparsers(dest="command", required=True)
    setup = sub.add_parser("setup", help="Connect your Google Ads account")
    setup.add_argument("--client", required=True, type=Path)
    setup.add_argument("--account", required=True, type=account_id)
    setup.add_argument("--manager", default="", type=account_id)
    setup.add_argument("--max-daily-budget", type=float, default=50)
    setup.add_argument("--no-browser", action="store_true")
    reconnect = sub.add_parser("reconnect", help="Renew Google authorization")
    reconnect.add_argument("--no-browser", action="store_true")
    install = sub.add_parser("install-claude", help="Add the local server to Claude Desktop")
    install.add_argument("--apply", action="store_true")
    sub.add_parser("doctor", help="Check the live account connection")
    sub.add_parser("serve", help="Run the local MCP server")
    args = parser.parse_args()
    folder = connection_folder()
    try:
        if args.command == "serve":
            from adchestra_local.server import serve
            serve()
        elif args.command == "install-claude":
            install_claude(folder, args.apply)
        elif args.command == "setup":
            data = json.loads(args.client.expanduser().read_text())
            if "installed" not in data:
                raise ValueError("Download a Desktop app OAuth client, not a Web client.")
            if not 0 < args.max_daily_budget <= 100000:
                raise ValueError("Set a positive daily budget limit in your account currency.")
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            if (folder / "token.json").exists():
                raise ValueError("A connection already exists. Use reconnect to renew it.")
            authorize(data, folder, args.no_browser)
            private_json(folder / "settings.json", {"account_id": args.account,
                "manager_id": args.manager, "project_id": data["installed"].get("project_id", ""),
                "max_daily_budget": args.max_daily_budget})
            print("Google Ads connected. Run adchestra-local install-claude --apply.")
        elif args.command == "reconnect":
            data = json.loads((folder / "client.json").read_text())
            authorize(data, folder, args.no_browser)
            print("Google authorization renewed. Restart Claude.")
        elif args.command == "doctor":
            from adchestra_local.server import configure
            from adchestra_local.ads.gaql import run_gaql
            config = configure(folder)
            result = run_gaql(config, query="SELECT customer.id, customer.descriptive_name, customer.currency_code FROM customer", customer_id=config.ads.customer_id)
            if result.get("error"):
                raise RuntimeError(result["error"])
            print(json.dumps(result, indent=2))
    except (Exception, KeyboardInterrupt) as exc:
        print(f"Error: {exc}" if str(exc) else "Canceled.", file=sys.stderr)
        raise SystemExit(1)
