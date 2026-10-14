"""Tenant configuration passed to the existing Adchestra tool engine."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GA4Config:
    property_id: str = ""

    def __post_init__(self) -> None:
        if self.property_id and not self.property_id.startswith("properties/"):
            self.property_id = f"properties/{self.property_id}"


@dataclass
class AdsConfig:
    developer_token: str = ""
    customer_id: str = ""
    login_customer_id: str = ""


@dataclass
class GscConfig:
    site_url: str = ""


@dataclass
class GtmConfig:
    account_id: str = ""
    container_id: str = ""


@dataclass
class PageSpeedConfig:
    api_key: str = ""


@dataclass
class SafetyConfig:
    max_daily_budget: float = 50.0
    max_bid_increase_pct: int = 100
    require_dry_run: bool = False
    two_phase_apply: bool = True
    log_file: str = ""
    blocked_operations: list[str] = field(default_factory=list)


@dataclass
class AdchestraConfig:
    ga4: GA4Config = field(default_factory=GA4Config)
    ads: AdsConfig = field(default_factory=AdsConfig)
    gsc: GscConfig = field(default_factory=GscConfig)
    gtm: GtmConfig = field(default_factory=GtmConfig)
    pagespeed: PageSpeedConfig = field(default_factory=PageSpeedConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
