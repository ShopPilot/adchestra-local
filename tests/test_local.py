import json
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
import pytest
from google.ads.googleads.client import GoogleAdsClient
from google.auth.credentials import AnonymousCredentials
from adchestra_local.config import AdchestraConfig, AdsConfig, SafetyConfig
from adchestra_local.runtime import set_default_config, ads_login_customer_id
from adchestra_local.safety.preview import ChangePlan, set_plan_store, get_plan
from adchestra_local.storage import SQLitePlanStore
from adchestra_local.ads import write, client
from adchestra_local.ads.validate_only import ValidateOnlyClient, ValidateOnlyFailure
from adchestra_local.cli import install_claude

@pytest.fixture
def config(tmp_path):
    cfg = AdchestraConfig(ads=AdsConfig(customer_id="1234567890"), safety=SafetyConfig(log_file=str(tmp_path / "audit.jsonl")))
    set_default_config(cfg)
    set_plan_store(SQLitePlanStore(tmp_path / "plans.sqlite"))
    yield cfg
    set_default_config(None)

def test_store_survives_restart_and_expires(tmp_path):
    path = tmp_path / "plans.sqlite"
    store = SQLitePlanStore(path)
    plan = ChangePlan(operation="create_campaign")
    store.store("local", plan)
    reopened = SQLitePlanStore(path)
    assert reopened.get("local", plan.plan_id).operation == "create_campaign"
    plan.created_at = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    reopened.store("local", plan)
    assert reopened.get("local", plan.plan_id) is None

def test_claude_install_preserves_other_servers_and_credentials_stay_out(tmp_path, monkeypatch):
    from adchestra_local import cli
    path = tmp_path / "claude.json"
    path.write_text(json.dumps({"theme":"dark", "mcpServers":{"existing":{"command":"existing"}}}))
    (tmp_path / "token.json").write_text('{"refresh_token":"PRIVATE_TEST_VALUE"}')
    monkeypatch.setattr(cli, "claude_config_path", lambda: path)
    install_claude(tmp_path, True)
    saved = json.loads(path.read_text())
    assert saved["theme"] == "dark"
    assert saved["mcpServers"]["existing"]["command"] == "existing"
    assert "PRIVATE_TEST_VALUE" not in path.read_text()
    assert list(tmp_path.glob("claude-config-before-*.json"))

def test_wrong_account_refused(config):
    with pytest.raises(ValueError): ads_login_customer_id("9999999999")

def draft(config):
    return write.draft_campaign(config, customer_id=config.ads.customer_id, campaign_name="Test", daily_budget=1,
        bidding_strategy="TARGET_SPEND", max_cpc=0.6, geo_target_ids=["2840"], language_ids=["1000"])

def test_google_rejection_never_unlocks_apply(config, monkeypatch):
    plan = draft(config)
    def reject(*a, **kw): raise ValueError("Google rejected this campaign")
    monkeypatch.setattr(write, "_execute_plan", reject)
    assert write.confirm_and_apply(config, plan_id=plan["plan_id"], dry_run=True)["status"] == "DRY_RUN_FAILED"
    assert get_plan(plan["plan_id"]).dry_run_result is None
    assert write.confirm_and_apply(config, plan_id=plan["plan_id"], dry_run=False)["status"] == "DRY_RUN_REQUIRED"

def test_apply_once_after_validation(config, monkeypatch):
    plan = draft(config)
    calls = []
    def execute(*a, validate_only=False):
        calls.append(validate_only)
        return {"validated_calls":1,"skipped_calls":0} if validate_only else {"campaign":"customers/1234567890/campaigns/42"}
    monkeypatch.setattr(write, "_execute_plan", execute)
    assert write.confirm_and_apply(config, plan_id=plan["plan_id"], dry_run=False)["status"] == "DRY_RUN_REQUIRED"
    assert write.confirm_and_apply(config, plan_id=plan["plan_id"], dry_run=True)["status"] == "DRY_RUN_SUCCESS"
    assert write.confirm_and_apply(config, plan_id=plan["plan_id"], dry_run=False)["status"] == "APPLIED"
    assert "error" in write.confirm_and_apply(config, plan_id=plan["plan_id"], dry_run=False)
    assert calls == [True,False]

def test_real_sdk_campaign_is_paused_presence_only_and_validates(config, monkeypatch):
    sdk = GoogleAdsClient(credentials=AnonymousCredentials(), use_proto_plus=True, version=client.GOOGLE_ADS_API_VERSION)
    requests = []
    class Service:
        def mutate(self, request):
            requests.append(request)
            assert request.validate_only is True
            return SimpleNamespace(partial_failure_error=None)
        def campaign_path(self,cid,id): return f"customers/{cid}/campaigns/{id}"
        def campaign_budget_path(self,cid,id): return f"customers/{cid}/campaignBudgets/{id}"
        def ad_group_path(self,cid,id): return f"customers/{cid}/adGroups/{id}"
    class SDK:
        enums=sdk.enums
        def get_type(self, name): return sdk.get_type(name)
        def get_service(self, name): return Service()
    monkeypatch.setattr(client, "get_ads_client", lambda cfg: SDK())
    p = draft(config)
    result = write.confirm_and_apply(config, plan_id=p["plan_id"], dry_run=True)
    assert result["status"] == "DRY_RUN_SUCCESS"
    assert result["checks"] == {"validated_calls":1,"skipped_calls":0}
    campaign=requests[0].mutate_operations[1].campaign_operation.create
    assert campaign.status == sdk.enums.CampaignStatusEnum.PAUSED
    assert campaign.geo_target_type_setting.positive_geo_target_type == sdk.enums.PositiveGeoTargetTypeEnum.PRESENCE
    assert not campaign.network_settings.target_search_network
    assert not campaign.network_settings.target_content_network

def test_validation_refuses_non_validation_write_method():
    class SDK:
        def get_service(self,name): return SimpleNamespace()
    with pytest.raises(ValidateOnlyFailure):
        ValidateOnlyClient(SDK()).get_service("CustomerService").create_customer_client
