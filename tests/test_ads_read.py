"""Tests for read-tool compact mode (issue #45) and audience insights (issue #44)."""

from __future__ import annotations

import pytest

from adchestra_local.ads import read
from adchestra_local.config import AdchestraConfig, AdsConfig


@pytest.fixture
def config() -> AdchestraConfig:
    return AdchestraConfig(ads=AdsConfig(customer_id="123-456-7890"))


@pytest.fixture(autouse=True)
def fixed_currency(monkeypatch):
    monkeypatch.setattr(read, "get_currency_code", lambda *_a, **_k: "EUR")


def _patch_rows(monkeypatch, rows):
    import adchestra_local.ads.gaql as gaql

    monkeypatch.setattr(gaql, "execute_query", lambda *_a, **_k: rows)


def _campaign_row(
    name, *, cost_micros=0, conversions=0, status="ENABLED",
    channel="SEARCH", clicks=0, impressions=0,
):
    return {
        "campaign.id": hash(name) % 10_000,
        "campaign.name": name,
        "campaign.status": status,
        "campaign.advertising_channel_type": channel,
        "metrics.impressions": impressions,
        "metrics.clicks": clicks,
        "metrics.cost_micros": cost_micros,
        "metrics.conversions": conversions,
    }


class TestAudiencePerformanceInsights:
    def test_search_campaigns_trigger_custom_segment_warning(
        self, config, monkeypatch
    ):
        _patch_rows(monkeypatch, [
            {
                "campaign.name": "Brand DE",
                "campaign.advertising_channel_type": "SEARCH",
                "ad_group_criterion.type": "USER_LIST",
                "metrics.cost_micros": 1_000_000,
            },
            {
                "campaign.name": "Display Reach",
                "campaign.advertising_channel_type": "DISPLAY",
                "ad_group_criterion.type": "CUSTOM_AUDIENCE",
                "metrics.cost_micros": 2_000_000,
            },
        ])

        result = read.get_audience_performance(config, customer_id="123")

        warning = [i for i in result["insights"] if "CANNOT be attached" in i]
        assert len(warning) == 1
        assert "Brand DE" in warning[0]
        assert "Display Reach" not in warning[0]

    def test_no_search_campaigns_no_warning(self, config, monkeypatch):
        _patch_rows(monkeypatch, [
            {
                "campaign.name": "Display Reach",
                "campaign.advertising_channel_type": "DISPLAY",
                "metrics.cost_micros": 2_000_000,
            },
        ])

        result = read.get_audience_performance(config, customer_id="123")

        assert not any("CANNOT be attached" in i for i in result["insights"])

    def test_empty_result_mentions_custom_segment_search_incompatibility(
        self, config, monkeypatch
    ):
        _patch_rows(monkeypatch, [])

        result = read.get_audience_performance(config, customer_id="123")

        assert any("custom_audience" in i for i in result["insights"])


class TestCampaignPerformanceCompact:
    def test_default_returns_full_rows_unchanged(self, config, monkeypatch):
        _patch_rows(monkeypatch, [_campaign_row("A", cost_micros=5_000_000)])

        result = read.get_campaign_performance(config, customer_id="123")

        assert "campaigns" in result and "compact" not in result
        assert result["date_range"]["includes_today"] is True
        assert result["date_range"]["end"] == read.date.today().isoformat()

    def test_explicit_historical_range_is_reported_accurately(
        self, config, monkeypatch
    ):
        _patch_rows(monkeypatch, [])

        result = read.get_campaign_performance(
            config,
            customer_id="123",
            date_range_start="2026-07-01",
            date_range_end="2026-07-31",
        )

        assert result["date_range"] == {
            "start": "2026-07-01",
            "end": "2026-07-31",
            "includes_today": False,
        }

    def test_compact_aggregates_and_flags_zero_conversion_spend(
        self, config, monkeypatch
    ):
        rows = [
            _campaign_row(
                f"C{i}", cost_micros=(20 - i) * 1_000_000,
                conversions=(2 if i % 2 else 0), clicks=100, impressions=1000,
            )
            for i in range(20)
        ]
        _patch_rows(monkeypatch, rows)

        result = read.get_campaign_performance(
            config, customer_id="123", compact=True
        )

        assert result["compact"] is True
        assert result["date_range"]["includes_today"] is True
        assert result["total_campaigns"] == 20
        assert len(result["campaigns_top_spend"]) == 10
        assert result["totals"]["cost"] == 210.0
        assert result["totals"]["currency"] == "EUR"
        assert result["by_status"] == {"ENABLED": 20}
        # 10 even-indexed campaigns have no recorded conversions in this period.
        assert any("recorded zero conversions" in i for i in result["insights"])
        assert len(
            result["campaigns_with_spend_and_no_recorded_conversions"]
        ) == 5
        assert result["analysis_context"]["comparison_period_in_result"] is False
        assert "freshness_note" in result["analysis_context"]
        assert "compact=false" in result["note"]


class TestKeywordPerformanceCompact:
    def test_compact_surfaces_low_qs_and_zero_conv(self, config, monkeypatch):
        rows = [
            {
                "ad_group_criterion.keyword.text": f"kw{i}",
                "ad_group_criterion.keyword.match_type": "EXACT" if i % 2 else "PHRASE",
                "ad_group_criterion.quality_info.quality_score": 3 if i < 4 else 8,
                "metrics.cost_micros": (30 - i) * 1_000_000,
                "metrics.clicks": 10,
                "metrics.impressions": 100,
                "metrics.conversions": 0 if i < 6 else 1,
            }
            for i in range(30)
        ]
        _patch_rows(monkeypatch, rows)

        result = read.get_keyword_performance(
            config, customer_id="123", compact=True
        )

        assert result["compact"] is True
        assert result["total_keywords"] == 30
        assert len(result["keywords_top_spend"]) == 10
        assert len(result["low_quality_score"]) == 4
        assert all(k["quality_score"] == 3 for k in result["low_quality_score"])
        assert len(
            result["keywords_with_spend_and_no_recorded_conversions"]
        ) == 6
        assert result["by_match_type"] == {"PHRASE": 15, "EXACT": 15}
        assert any("quality score" in i for i in result["insights"])

    def test_compact_ignores_missing_quality_scores(self, config, monkeypatch):
        _patch_rows(monkeypatch, [
            {
                "ad_group_criterion.keyword.text": "kw",
                "ad_group_criterion.keyword.match_type": "EXACT",
                "ad_group_criterion.quality_info.quality_score": None,
                "metrics.cost_micros": 1_000_000,
                "metrics.conversions": 1,
            }
        ])

        result = read.get_keyword_performance(
            config, customer_id="123", compact=True
        )

        assert result["low_quality_score"] == []


class TestSearchTermsCompact:
    def test_compact_ranks_review_candidates_and_converters(
        self, config, monkeypatch
    ):
        rows = [
            # Review candidates: 5+ clicks, zero recorded conversions.
            {"search_term_view.search_term": "cheap junk", "campaign.name": "C",
             "metrics.clicks": 9, "metrics.cost_micros": 3_000_000,
             "metrics.conversions": 0},
            {"search_term_view.search_term": "expensive junk", "campaign.name": "C",
             "metrics.clicks": 5, "metrics.cost_micros": 9_000_000,
             "metrics.conversions": 0},
            # Below click threshold — not included in the review list.
            {"search_term_view.search_term": "rare miss", "campaign.name": "C",
             "metrics.clicks": 2, "metrics.cost_micros": 1_000_000,
             "metrics.conversions": 0},
            # converter
            {"search_term_view.search_term": "buy adchestra", "campaign.name": "C",
             "metrics.clicks": 20, "metrics.cost_micros": 4_000_000,
             "metrics.conversions": 3},
        ]
        _patch_rows(monkeypatch, rows)

        result = read.get_search_terms(config, customer_id="123", compact=True)

        assert result["compact"] is True
        review_terms = [
            row["search_term"] for row in result["search_terms_for_review"]
        ]
        assert review_terms == ["expensive junk", "cheap junk"]
        assert result["top_converters"][0]["search_term"] == "buy adchestra"
        assert any("before adding negatives" in i for i in result["insights"])


class TestAdPerformanceCompact:
    def _ad_row(self, ad_id, group, *, headlines=10, descriptions=4,
                status="ENABLED", ad_type="RESPONSIVE_SEARCH_AD", cost=1):
        return {
            "campaign.name": "C",
            "ad_group.id": group,
            "ad_group.name": f"AG {group}",
            "ad_group_ad.ad.id": ad_id,
            "ad_group_ad.ad.type": ad_type,
            "ad_group_ad.status": status,
            "ad_group_ad.ad.responsive_search_ad.headlines": [
                {"text": f"H{i}"} for i in range(headlines)
            ],
            "ad_group_ad.ad.responsive_search_ad.descriptions": [
                {"text": f"D{i}"} for i in range(descriptions)
            ],
            "ad_group_ad.ad.final_urls": ["https://example.com"],
            "metrics.cost_micros": cost * 1_000_000,
            "metrics.clicks": 5,
            "metrics.impressions": 50,
            "metrics.conversions": 0,
        }

    def test_compact_strips_asset_lists_and_finds_thin_spots(
        self, config, monkeypatch
    ):
        rows = [
            self._ad_row(1, "g1", headlines=10, descriptions=4, cost=9),
            self._ad_row(2, "g1", headlines=4, descriptions=2, cost=5),  # thin RSA
            self._ad_row(3, "g2", headlines=12, descriptions=4, cost=2),  # single-ad group
        ]
        _patch_rows(monkeypatch, rows)

        result = read.get_ad_performance(config, customer_id="123", compact=True)

        assert result["compact"] is True
        top = result["ads_top_spend"][0]
        assert "ad_group_ad.ad.responsive_search_ad.headlines" not in top
        assert top["headline_count"] == 10
        assert top["description_count"] == 4

        assert len(result["incomplete_rsas"]) == 1
        assert result["incomplete_rsas"][0]["ad_id"] == 2

        single = [g["ad_group"] for g in result["single_ad_ad_groups"]]
        assert single == ["AG g2"]
        assert any("asset-coverage guidance" in i for i in result["insights"])
        assert any("one enabled ad" in i for i in result["insights"])
        assert any("limits ad-level comparison" in i for i in result["insights"])


class TestPerformancePeriodMetadata:
    @pytest.mark.parametrize(
        "tool",
        [
            read.get_ad_performance,
            read.get_keyword_performance,
            read.get_search_terms,
            read.get_audience_performance,
        ],
    )
    def test_time_based_read_tools_report_exact_scope(
        self, tool, config, monkeypatch
    ):
        _patch_rows(monkeypatch, [])

        result = tool(
            config,
            customer_id="123",
            date_range_start="2026-07-01",
            date_range_end="2026-07-31",
        )

        assert result["date_range"] == {
            "start": "2026-07-01",
            "end": "2026-07-31",
            "includes_today": False,
        }
        assert result["analysis_context"] == {
            "scope": "selected_period_only",
            "comparison_period_in_result": False,
            "recent_account_changes_in_result": False,
            "business_context_in_result": False,
        }
