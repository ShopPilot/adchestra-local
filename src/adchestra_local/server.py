"""Google Ads tools for Claude Desktop."""
import functools
import inspect
import typing
from fastmcp import FastMCP
from filelock import FileLock
from mcp.types import ToolAnnotations
from adchestra_local.ads import read, write, gaql, forecast
from adchestra_local.config import AdchestraConfig, AdsConfig, SafetyConfig
from adchestra_local.auth import connection_folder, set_connection_folder
from adchestra_local.runtime import set_default_config, current_config, ads_login_customer_id
from adchestra_local.safety.preview import set_plan_store
from adchestra_local.storage import SQLitePlanStore
import json

mcp = FastMCP("Adchestra Local", instructions=(
    "Use live account data. Never invent search volumes, bids or conversions. "
    "Every change starts with a draft. Show the preview, validate it with "
    "confirm_and_apply(dry_run=true), and wait for the user's explicit approval "
    "before confirm_and_apply(dry_run=false). New campaigns are paused. "
    "TARGET_SPEND means Maximize Clicks. Keyword Planner needs Basic API access."
))

TOOLS = {
    read: ["list_accounts", "get_campaign_performance", "get_ad_performance", "get_keyword_performance", "get_search_terms", "get_negative_keywords"],
    gaql: ["run_gaql"],
    forecast: ["discover_keywords", "estimate_budget"],
    write: ["draft_campaign", "draft_ad_group", "draft_responsive_search_ad", "draft_keywords", "add_negative_keywords", "add_negative_locations", "update_campaign", "update_ad_group", "pause_entity", "enable_entity", "confirm_and_apply"],
}
_folder = None

def register(module, name):
    function = getattr(module, name)
    module.AdchestraConfig = AdchestraConfig
    hints = typing.get_type_hints(function)
    signature = inspect.signature(function)
    parameters = [p.replace(annotation=hints.get(p.name, p.annotation)) for p in signature.parameters.values() if p.name != "config"]

    @functools.wraps(function)
    def tool(**kwargs):
        config = current_config()
        if "customer_id" in signature.parameters:
            kwargs["customer_id"] = kwargs.get("customer_id") or config.ads.customer_id
            ads_login_customer_id(kwargs["customer_id"])
        try:
            if name == "confirm_and_apply":
                with FileLock(str(_folder / "apply.lock"), timeout=60):
                    return function(config, **kwargs)
            return function(config, **kwargs)
        except Exception as exc:
            if name in {"discover_keywords", "estimate_budget"} and "403" in str(exc):
                return {"error": "Keyword Planner requires Basic Google Ads API access. Complete brand verification and upgrade the same Cloud project."}
            return {"error": str(exc), "tool": name}

    tool.__signature__ = signature.replace(parameters=parameters, return_annotation=dict)
    tool.__annotations__ = {p.name: p.annotation for p in parameters} | {"return": dict}
    tool.__doc__ = function.__doc__.split("\n\n")[0] if function.__doc__ else name.replace("_", " ")
    mcp.tool(name=name, annotations=ToolAnnotations(readOnlyHint=module != write, destructiveHint=name == "enable_entity"))(tool)

for module, names in TOOLS.items():
    for name in names:
        register(module, name)

def configure(folder=None):
    global _folder
    _folder = folder or connection_folder()
    settings = json.loads((_folder / "settings.json").read_text())
    set_connection_folder(_folder)
    config = AdchestraConfig(
        ads=AdsConfig(customer_id=settings["account_id"], login_customer_id=settings.get("manager_id", "")),
        safety=SafetyConfig(max_daily_budget=settings.get("max_daily_budget", 50), require_dry_run=False, two_phase_apply=True, log_file=str(_folder / "audit.jsonl")),
    )
    set_default_config(config)
    set_plan_store(SQLitePlanStore(_folder / "plans.sqlite"))
    return config

def serve():
    configure()
    mcp.run(transport="stdio", show_banner=False)
