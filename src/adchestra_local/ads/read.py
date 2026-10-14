"""Google Ads read tools — campaign, ad, keyword, and search term performance."""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

from adchestra_local.ads.currency import get_currency_code

if TYPE_CHECKING:
    from adchestra_local.config import AdchestraConfig


def list_accounts(config: AdchestraConfig, *, limit: int = 200) -> dict:
    """List accessible Google Ads accounts, up to *limit* entries.

    The default of 200 covers the vast majority of agency MCCs in a single
    response. Larger MCCs (300+ accounts) can still hit per-response size
    caps or per-tool-call timeouts on some MCP hosts, so the cap is kept —
    when the user genuinely wants the full list, raise *limit* explicitly
    (e.g. ``list_accounts(limit=1000)``) or pass ``customer_id`` directly
    to other tools (``get_campaign_performance``, ``run_gaql``, etc.) to
    query a specific account without enumerating the whole MCC.
    """
    from adchestra_local.ads.gaql import execute_query

    mcc_id = config.ads.login_customer_id
    if mcc_id:
        query = f"""
            SELECT customer_client.id, customer_client.descriptive_name,
                   customer_client.status, customer_client.manager
            FROM customer_client
            LIMIT {int(limit) + 1}
        """
        rows = execute_query(config, mcc_id, query)
    else:
        query = """
            SELECT customer.id, customer.descriptive_name,
                   customer.status, customer.manager
            FROM customer
            LIMIT 1
        """
        rows = execute_query(config, config.ads.customer_id, query)

    truncated = len(rows) > limit
    if truncated:
        rows = rows[:limit]

    result: dict = {"accounts": rows, "total_accounts": len(rows)}
    if truncated:
        result["truncated"] = True
        result["note"] = (
            f"Returned the first {limit} accounts but more exist on this MCC. "
            f"If the user asked to see all of their accounts, call this tool "
            f"again with a much higher limit (e.g. list_accounts(limit=1000)). "
            f"If you only need one specific account, skip listing entirely "
            f"and pass customer_id directly to get_campaign_performance, "
            f"run_gaql, or whichever tool you actually need."
        )
    return result


def get_campaign_performance(
    config: AdchestraConfig,
    *,
    customer_id: str = "",
    date_range_start: str = "",
    date_range_end: str = "",
    compact: bool = False,
) -> dict:
    """Get campaign-level performance metrics for the given date range."""
    from adchestra_local.ads.gaql import execute_query

    start, end = _resolve_date_range(date_range_start, date_range_end)
    date_clause = _date_clause(start, end)

    query = f"""
        SELECT campaign.id, campaign.name, campaign.status,
               campaign.advertising_channel_type, campaign.bidding_strategy_type,
               metrics.impressions, metrics.clicks, metrics.cost_micros,
               metrics.conversions, metrics.conversions_value,
               metrics.ctr, metrics.average_cpc
        FROM campaign
        WHERE campaign.status != 'REMOVED'
          {date_clause}
        ORDER BY metrics.cost_micros DESC
    """

    rows = execute_query(config, customer_id, query)
    currency_code = get_currency_code(config, customer_id)
    _enrich_cost_fields(rows, currency_code)
    period_metadata = _performance_period_metadata(start, end)

    if not compact:
        return {
            "campaigns": rows,
            "total_campaigns": len(rows),
            **period_metadata,
        }

    no_recorded_conversions = [
        r for r in rows
        if (r.get("metrics.cost_micros") or 0) > 0
        and not (r.get("metrics.conversions") or 0)
    ]
    insights: list[str] = []
    if no_recorded_conversions:
        period_cost = round(
            sum(
                (r.get("metrics.cost_micros") or 0)
                for r in no_recorded_conversions
            )
            / 1_000_000,
            2,
        )
        names = ", ".join(
            str(r.get("campaign.name")) for r in no_recorded_conversions[:5]
        )
        insights.append(
            f"{len(no_recorded_conversions)} campaign(s) spent {period_cost} "
            f"{currency_code} and recorded zero conversions from {start} to {end}: "
            f"{names}. Review conversion delay, tracking, traffic relevance, and "
            f"longer history before deciding on action."
        )

    top = rows[:10]
    return {
        "compact": True,
        "total_campaigns": len(rows),
        **period_metadata,
        "totals": _compact_totals(rows, currency_code),
        "by_status": _status_counts(rows, "campaign.status"),
        "by_channel_type": _status_counts(rows, "campaign.advertising_channel_type"),
        "campaigns_top_spend": top,
        "campaigns_with_spend_and_no_recorded_conversions": [
            {
                "name": r.get("campaign.name"),
                "id": r.get("campaign.id"),
                "cost": r.get("metrics.cost"),
                "clicks": r.get("metrics.clicks"),
            }
            for r in no_recorded_conversions[:5]
        ],
        "insights": insights,
        "note": _compact_note(len(top), len(rows), "get_campaign_performance"),
    }


def get_ad_performance(
    config: AdchestraConfig,
    *,
    customer_id: str = "",
    date_range_start: str = "",
    date_range_end: str = "",
    compact: bool = False,
) -> dict:
    """Get ad-level performance data including headlines, descriptions, and metrics."""
    from adchestra_local.ads.gaql import execute_query

    start, end = _resolve_date_range(date_range_start, date_range_end)
    date_clause = _date_clause(start, end)
    period_metadata = _performance_period_metadata(start, end)

    query = f"""
        SELECT campaign.name, campaign.id, ad_group.name, ad_group.id,
               ad_group_ad.ad.id, ad_group_ad.ad.type,
               ad_group_ad.ad.responsive_search_ad.headlines,
               ad_group_ad.ad.responsive_search_ad.descriptions,
               ad_group_ad.ad.final_urls,
               ad_group_ad.status,
               metrics.impressions, metrics.clicks, metrics.ctr,
               metrics.conversions, metrics.cost_micros
        FROM ad_group_ad
        WHERE ad_group_ad.status != 'REMOVED'
          {date_clause}
        ORDER BY metrics.cost_micros DESC
    """

    rows = execute_query(config, customer_id, query)
    currency_code = get_currency_code(config, customer_id)
    _enrich_cost_fields(rows, currency_code)

    if not compact:
        return {"ads": rows, "total_ads": len(rows), **period_metadata}

    def _asset_count(row: dict, field: str) -> int:
        assets = row.get(field) or []
        return len(assets) if isinstance(assets, list) else 0

    incomplete_rsas: list[dict] = []
    ads_per_group: dict[str, dict] = {}
    for r in rows:
        group_key = str(r.get("ad_group.id"))
        entry = ads_per_group.setdefault(
            group_key, {"name": r.get("ad_group.name"), "enabled_ads": 0}
        )
        if str(r.get("ad_group_ad.status")) == "ENABLED":
            entry["enabled_ads"] += 1

        if str(r.get("ad_group_ad.ad.type")) == "RESPONSIVE_SEARCH_AD":
            headlines = _asset_count(r, "ad_group_ad.ad.responsive_search_ad.headlines")
            descriptions = _asset_count(
                r, "ad_group_ad.ad.responsive_search_ad.descriptions"
            )
            if headlines < 8 or descriptions < 3:
                incomplete_rsas.append({
                    "ad_id": r.get("ad_group_ad.ad.id"),
                    "ad_group": r.get("ad_group.name"),
                    "headlines": headlines,
                    "descriptions": descriptions,
                })

    single_ad_groups = [
        {"ad_group": v["name"], "enabled_ads": v["enabled_ads"]}
        for v in ads_per_group.values()
        if v["enabled_ads"] == 1
    ]

    def _slim(row: dict) -> dict:
        slim = {
            k: v
            for k, v in row.items()
            if k not in (
                "ad_group_ad.ad.responsive_search_ad.headlines",
                "ad_group_ad.ad.responsive_search_ad.descriptions",
                "ad_group_ad.ad.final_urls",
            )
        }
        slim["headline_count"] = _asset_count(
            row, "ad_group_ad.ad.responsive_search_ad.headlines"
        )
        slim["description_count"] = _asset_count(
            row, "ad_group_ad.ad.responsive_search_ad.descriptions"
        )
        return slim

    insights: list[str] = []
    if incomplete_rsas:
        insights.append(
            f"{len(incomplete_rsas)} RSA(s) have fewer than 8 headlines or 3 "
            f"descriptions. This is below Google's asset-coverage guidance, but "
            f"does not by itself prove weak ad performance."
        )
    if single_ad_groups:
        insights.append(
            f"{len(single_ad_groups)} ad group(s) have only one enabled ad — "
            f"this limits ad-level comparison, but does not by itself show a "
            f"performance problem."
        )

    top = [_slim(r) for r in rows[:10]]
    return {
        "compact": True,
        "total_ads": len(rows),
        **period_metadata,
        "totals": _compact_totals(rows, currency_code),
        "by_status": _status_counts(rows, "ad_group_ad.status"),
        "ads_top_spend": top,
        "incomplete_rsas": incomplete_rsas[:10],
        "single_ad_ad_groups": single_ad_groups[:10],
        "insights": insights,
        "note": _compact_note(len(top), len(rows), "get_ad_performance")
        + " Compact rows replace full headline/description lists with counts.",
    }


def get_keyword_performance(
    config: AdchestraConfig,
    *,
    customer_id: str = "",
    date_range_start: str = "",
    date_range_end: str = "",
    compact: bool = False,
) -> dict:
    """Get keyword metrics including quality scores and competitive data."""
    from adchestra_local.ads.gaql import execute_query

    start, end = _resolve_date_range(date_range_start, date_range_end)
    date_clause = _date_clause(start, end)
    period_metadata = _performance_period_metadata(start, end)

    query = f"""
        SELECT campaign.name, ad_group.name,
               ad_group_criterion.keyword.text,
               ad_group_criterion.keyword.match_type,
               ad_group_criterion.quality_info.quality_score,
               metrics.impressions, metrics.clicks, metrics.ctr,
               metrics.average_cpc, metrics.cost_micros,
               metrics.conversions
        FROM keyword_view
        WHERE ad_group_criterion.status != 'REMOVED'
          {date_clause}
        ORDER BY metrics.cost_micros DESC
    """

    rows = execute_query(config, customer_id, query)
    currency_code = get_currency_code(config, customer_id)
    _enrich_cost_fields(rows, currency_code)

    if not compact:
        return {"keywords": rows, "total_keywords": len(rows), **period_metadata}

    qs_field = "ad_group_criterion.quality_info.quality_score"
    low_quality = [
        r for r in rows
        if r.get(qs_field) is not None and (r.get(qs_field) or 0) < 5
    ]
    no_recorded_conversions = [
        r for r in rows
        if (r.get("metrics.cost_micros") or 0) > 0
        and not (r.get("metrics.conversions") or 0)
    ]

    def _kw(r: dict) -> dict:
        return {
            "keyword": r.get("ad_group_criterion.keyword.text"),
            "match_type": r.get("ad_group_criterion.keyword.match_type"),
            "quality_score": r.get(qs_field),
            "cost": r.get("metrics.cost"),
            "clicks": r.get("metrics.clicks"),
            "conversions": r.get("metrics.conversions"),
        }

    insights: list[str] = []
    if low_quality:
        insights.append(
            f"{len(low_quality)} keyword(s) have Google quality score below 5. "
            f"Review ad relevance and landing pages; Quality Score alone does not "
            f"establish profitability."
        )
    if no_recorded_conversions:
        period_cost = round(
            sum((r.get("metrics.cost_micros") or 0) for r in no_recorded_conversions)
            / 1_000_000,
            2,
        )
        insights.append(
            f"{len(no_recorded_conversions)} keyword(s) spent {period_cost} "
            f"{currency_code} and recorded zero conversions from {start} to {end}. "
            f"Treat them as review candidates until longer history, conversion "
            f"delay, and the marketer's goal are considered."
        )

    top = rows[:10]
    return {
        "compact": True,
        "total_keywords": len(rows),
        **period_metadata,
        "totals": _compact_totals(rows, currency_code),
        "by_match_type": _status_counts(
            rows, "ad_group_criterion.keyword.match_type"
        ),
        "keywords_top_spend": top,
        "low_quality_score": [_kw(r) for r in low_quality[:10]],
        "keywords_with_spend_and_no_recorded_conversions": [
            _kw(r) for r in no_recorded_conversions[:10]
        ],
        "insights": insights,
        "note": _compact_note(len(top), len(rows), "get_keyword_performance"),
    }


def get_search_terms(
    config: AdchestraConfig,
    *,
    customer_id: str = "",
    date_range_start: str = "",
    date_range_end: str = "",
    compact: bool = False,
) -> dict:
    """Get search terms report — what users actually typed before clicking ads."""
    from adchestra_local.ads.gaql import execute_query

    start, end = _resolve_date_range(date_range_start, date_range_end)
    period_metadata = _performance_period_metadata(start, end)

    query = f"""
        SELECT search_term_view.search_term,
               campaign.name, ad_group.name,
               metrics.impressions, metrics.clicks,
               metrics.cost_micros, metrics.conversions
        FROM search_term_view
        WHERE segments.date BETWEEN '{start}' AND '{end}'
        ORDER BY metrics.clicks DESC
        LIMIT 200
    """

    rows = execute_query(config, customer_id, query)
    currency_code = get_currency_code(config, customer_id)
    _enrich_cost_fields(rows, currency_code)

    if not compact:
        return {
            "search_terms": rows,
            "total_search_terms": len(rows),
            **period_metadata,
        }

    def _term(r: dict) -> dict:
        return {
            "search_term": r.get("search_term_view.search_term"),
            "campaign": r.get("campaign.name"),
            "clicks": r.get("metrics.clicks"),
            "cost": r.get("metrics.cost"),
            "conversions": r.get("metrics.conversions"),
        }

    review_candidates = sorted(
        (
            r for r in rows
            if (r.get("metrics.clicks") or 0) >= 5
            and not (r.get("metrics.conversions") or 0)
        ),
        key=lambda r: r.get("metrics.cost_micros") or 0,
        reverse=True,
    )
    converters = sorted(
        (r for r in rows if (r.get("metrics.conversions") or 0) > 0),
        key=lambda r: r.get("metrics.conversions") or 0,
        reverse=True,
    )

    insights: list[str] = []
    if review_candidates:
        period_cost = round(
            sum(
                (r.get("metrics.cost_micros") or 0) for r in review_candidates
            )
            / 1_000_000,
            2,
        )
        insights.append(
            f"{len(review_candidates)} search term(s) had 5+ clicks, cost "
            f"{period_cost} {currency_code}, and recorded zero conversions from "
            f"{start} to {end}. Review intent, longer history, and conversion delay "
            f"before adding negatives."
        )
    if converters:
        insights.append(
            f"{len(converters)} search term(s) converted — check whether the top "
            f"converters exist as exact-match keywords yet."
        )

    top = rows[:10]
    return {
        "compact": True,
        "total_search_terms": len(rows),
        **period_metadata,
        "totals": _compact_totals(rows, currency_code),
        "search_terms_top_clicks": top,
        "search_terms_for_review": [_term(r) for r in review_candidates[:10]],
        "top_converters": [_term(r) for r in converters[:5]],
        "insights": insights,
        "note": _compact_note(len(top), len(rows), "get_search_terms"),
    }


def get_negative_keywords(
    config: AdchestraConfig,
    *,
    customer_id: str = "",
    campaign_id: str = "",
) -> dict:
    """List negative keywords for a campaign or all campaigns."""
    from adchestra_local.ads.gaql import execute_query

    campaign_filter = ""
    if campaign_id:
        campaign_filter = f"AND campaign.id = {campaign_id}"

    query = f"""
        SELECT campaign.id, campaign.name,
               campaign_criterion.keyword.text,
               campaign_criterion.keyword.match_type,
               campaign_criterion.negative,
               campaign_criterion.criterion_id
        FROM campaign_criterion
        WHERE campaign_criterion.negative = TRUE
          AND campaign_criterion.status != 'REMOVED'
          {campaign_filter}
        ORDER BY campaign.name
    """

    rows = execute_query(config, customer_id, query)
    for row in rows:
        cid = row.get("campaign.id")
        crit_id = row.get("campaign_criterion.criterion_id")
        if cid and crit_id:
            row["resource_id"] = f"{cid}~{crit_id}"
    return {"negative_keywords": rows, "total_negative_keywords": len(rows)}


def get_negative_keyword_lists(
    config: AdchestraConfig,
    *,
    customer_id: str = "",
) -> dict:
    """List all shared negative keyword lists (SharedSets) in the account.

    Returns each list's ID, name, status, and keyword count. Use this before
    calling propose_negative_keyword_list to check whether a suitable list
    already exists and only needs attaching to a new campaign.
    """
    from adchestra_local.ads.gaql import execute_query

    query = """
        SELECT shared_set.id, shared_set.name, shared_set.status,
               shared_set.member_count, shared_set.resource_name
        FROM shared_set
        WHERE shared_set.type = 'NEGATIVE_KEYWORDS'
          AND shared_set.status != 'REMOVED'
        ORDER BY shared_set.name
    """

    rows = execute_query(config, customer_id, query)
    return {"negative_keyword_lists": rows, "total_lists": len(rows)}


def get_negative_keyword_list_keywords(
    config: AdchestraConfig,
    *,
    customer_id: str = "",
    shared_set_id: str = "",
) -> dict:
    """List the keywords inside a shared negative keyword list.

    shared_set_id: the numeric ID from get_negative_keyword_lists
    (shared_set.id field).
    """
    from adchestra_local.ads.gaql import execute_query

    if not shared_set_id:
        return {"error": "shared_set_id is required"}
    if not shared_set_id.isdigit():
        return {"error": "shared_set_id must be a numeric ID"}

    query = f"""
        SELECT shared_criterion.criterion_id,
               shared_criterion.keyword.text,
               shared_criterion.keyword.match_type,
               shared_criterion.type,
               shared_set.id, shared_set.name
        FROM shared_criterion
        WHERE shared_set.id = {shared_set_id}
        ORDER BY shared_criterion.keyword.text
    """

    rows = execute_query(config, customer_id, query)
    for row in rows:
        ssid = row.get("shared_set.id")
        crit_id = row.get("shared_criterion.criterion_id")
        if ssid and crit_id:
            row["resource_id"] = f"{ssid}~{crit_id}"
    return {
        "keywords": rows,
        "total_keywords": len(rows),
        "shared_set_id": shared_set_id,
    }


def get_negative_keyword_list_campaigns(
    config: AdchestraConfig,
    *,
    customer_id: str = "",
    shared_set_id: str = "",
) -> dict:
    """List which campaigns a shared negative keyword list is attached to.

    shared_set_id: the numeric ID from get_negative_keyword_lists
    (shared_set.id field). Omit to return all list-to-campaign attachments.
    """
    from adchestra_local.ads.gaql import execute_query

    shared_set_filter = ""
    if shared_set_id:
        if not shared_set_id.isdigit():
            return {"error": "shared_set_id must be a numeric ID"}
        shared_set_filter = f"AND shared_set.id = {shared_set_id}"

    query = f"""
        SELECT campaign.id, campaign.name, campaign.status,
               shared_set.id, shared_set.name
        FROM campaign_shared_set
        WHERE campaign_shared_set.status != 'REMOVED'
          {shared_set_filter}
        ORDER BY shared_set.name, campaign.name
    """

    rows = execute_query(config, customer_id, query)
    return {"attachments": rows, "total_attachments": len(rows)}


def get_recommendations(
    config: AdchestraConfig,
    *,
    customer_id: str = "",
    recommendation_types: list[str] | None = None,
    campaign_id: str = "",
) -> dict:
    """Retrieve Google-generated recommendations with estimated impact.

    Uses the service directly (not ``execute_query``) because
    ``recommendation.impact`` sub-fields are not individually selectable
    in GAQL — the impact object must be extracted from the proto.
    """
    from adchestra_local.ads.client import (
        get_ads_client,
        normalize_customer_id,
        routed_ads_config,
    )

    customer_id = customer_id or config.ads.customer_id
    client = get_ads_client(routed_ads_config(config, customer_id))
    service = client.get_service("GoogleAdsService")
    cid = normalize_customer_id(customer_id)

    type_filter = ""
    if recommendation_types:
        types_str = ", ".join(f"'{t}'" for t in recommendation_types)
        type_filter = f"AND recommendation.type IN ({types_str})"

    query = f"""
        SELECT recommendation.resource_name,
               recommendation.type,
               recommendation.campaign,
               recommendation.ad_group,
               recommendation.dismissed,
               recommendation.impact
        FROM recommendation
        WHERE recommendation.dismissed = FALSE
          {type_filter}
    """

    rows: list[dict] = []
    for row in service.search(customer_id=cid, query=query):
        rec = row.recommendation
        rec_type = rec.type_.name if hasattr(rec.type_, "name") else str(rec.type_)

        impact = rec.impact
        base = impact.base_metrics
        pot = impact.potential_metrics

        base_impressions = _round_metric(getattr(base, "impressions", 0))
        base_clicks = _round_metric(getattr(base, "clicks", 0))
        base_cost_micros = getattr(base, "cost_micros", 0) or 0
        base_conversions = _round_metric(getattr(base, "conversions", 0))

        pot_impressions = _round_metric(getattr(pot, "impressions", 0))
        pot_clicks = _round_metric(getattr(pot, "clicks", 0))
        pot_cost_micros = getattr(pot, "cost_micros", 0) or 0
        pot_conversions = _round_metric(getattr(pot, "conversions", 0))

        entry = {
            "recommendation.type": rec_type,
            "recommendation.campaign": rec.campaign,
            "recommendation.ad_group": rec.ad_group or "",
            "recommendation.dismissed": rec.dismissed,
            "impact.base": {
                "impressions": base_impressions,
                "clicks": base_clicks,
                "cost_micros": base_cost_micros,
                "cost": round(base_cost_micros / 1_000_000, 2),
                "conversions": base_conversions,
            },
            "impact.potential": {
                "impressions": pot_impressions,
                "clicks": pot_clicks,
                "cost_micros": pot_cost_micros,
                "cost": round(pot_cost_micros / 1_000_000, 2),
                "conversions": pot_conversions,
            },
            "estimated_improvement": {
                "impressions": _improvement(base_impressions, pot_impressions),
                "clicks": _improvement(base_clicks, pot_clicks),
                "cost": _improvement(
                    round(base_cost_micros / 1_000_000, 2),
                    round(pot_cost_micros / 1_000_000, 2),
                ),
                "conversions": _improvement(base_conversions, pot_conversions),
            },
        }
        rows.append(entry)

    if campaign_id:
        rows = [
            r for r in rows
            if str(campaign_id) in str(r.get("recommendation.campaign", ""))
        ]

    type_counts: dict[str, int] = {}
    for row in rows:
        rtype = row.get("recommendation.type", "UNKNOWN")
        type_counts[rtype] = type_counts.get(rtype, 0) + 1

    _BUDGET_TYPES = {
        "CAMPAIGN_BUDGET", "MOVE_UNUSED_BUDGET",
        "FORECASTING_CAMPAIGN_BUDGET", "MARGINAL_ROI_CAMPAIGN_BUDGET",
    }

    insights: list[str] = []
    if not rows:
        insights.append("No active recommendations found.")
    else:
        insights.append(
            f"{len(rows)} active recommendation(s) across {len(type_counts)} type(s): "
            f"{dict(sorted(type_counts.items(), key=lambda x: -x[1]))}"
        )

        budget_recs = [r for r in rows if r.get("recommendation.type") in _BUDGET_TYPES]
        if budget_recs:
            insights.append(
                f"{len(budget_recs)} recommendation(s) are budget-related. "
                f"They may increase or redistribute spend. Compare Google's "
                f"estimate with actual performance, goals, and acceptable spend."
            )

        high_impact = [
            r for r in rows
            if (r.get("estimated_improvement", {}).get("conversions", 0) or 0) > 1
        ]
        if high_impact:
            types = set(r.get("recommendation.type") for r in high_impact)
            insights.append(
                f"Google estimates >1 additional conversion for {len(high_impact)} "
                f"recommendation(s): types {types}. This is a projection, not a "
                f"guarantee; evaluate projected spend and CPA before acting."
            )

    return {
        "recommendations": rows,
        "total_recommendations": len(rows),
        "by_type": type_counts,
        "insights": insights,
    }


def get_audience_performance(
    config: AdchestraConfig,
    *,
    customer_id: str = "",
    date_range_start: str = "",
    date_range_end: str = "",
    campaign_id: str = "",
) -> dict:
    """Get audience segment performance metrics (remarketing, in-market, affinity, demographics)."""
    from adchestra_local.ads.gaql import execute_query

    start, end = _resolve_date_range(date_range_start, date_range_end)
    date_clause = _date_clause(start, end)
    period_metadata = _performance_period_metadata(start, end)

    campaign_filter = ""
    if campaign_id:
        campaign_filter = f"AND campaign.id = {campaign_id}"

    query = f"""
        SELECT campaign.id, campaign.name,
               campaign.advertising_channel_type,
               ad_group.id, ad_group.name,
               ad_group_criterion.display_name,
               ad_group_criterion.type,
               metrics.impressions, metrics.clicks, metrics.cost_micros,
               metrics.conversions, metrics.ctr, metrics.average_cpc
        FROM ad_group_audience_view
        WHERE campaign.status != 'REMOVED'
          {date_clause}
          {campaign_filter}
        ORDER BY metrics.cost_micros DESC
        LIMIT 200
    """

    rows = execute_query(config, customer_id, query)
    currency_code = get_currency_code(config, customer_id)
    _enrich_cost_fields(rows, currency_code)

    insights: list[str] = []
    if not rows:
        insights.append(
            "No audience performance data found. This account's campaigns may not "
            "have explicit audience targeting (remarketing lists, in-market segments, "
            "demographics). PMax audience targeting is automatic and does not appear "
            "in this report. Also note: custom segments (custom_audience) can never "
            "be attached to Search campaigns — an empty result for a Search campaign "
            "is expected, not missing data."
        )

    search_campaigns = sorted({
        str(r.get("campaign.name") or r.get("campaign.id") or "")
        for r in rows
        if str(r.get("campaign.advertising_channel_type") or "") == "SEARCH"
    })
    if search_campaigns:
        shown = ", ".join(search_campaigns[:5])
        insights.append(
            f"{len(search_campaigns)} of these campaigns are SEARCH campaigns "
            f"({shown}{', …' if len(search_campaigns) > 5 else ''}). Custom "
            "segments (custom_audience) CANNOT be attached to Search campaigns — "
            "they only work in Display, Video, Demand Gen, and as PMax signals. "
            "Do not propose custom-segment targeting for these campaigns; use "
            "remarketing lists, in-market, or affinity segments instead (see the "
            "audience compatibility matrix in the orchestration rules)."
        )

    return {
        "audiences": rows,
        "total_audiences": len(rows),
        "insights": insights,
        **period_metadata,
    }


def get_demographic_targeting(
    config: AdchestraConfig,
    *,
    customer_id: str = "",
    ad_group_id: str = "",
    campaign_id: str = "",
) -> dict:
    """List current demographic targeting (AGE_RANGE, GENDER, PARENTAL_STATUS, INCOME_RANGE).

    Provide either `ad_group_id` or `campaign_id`. Returns criteria with their
    `criterion_id` (needed to remove a criterion), the demographic value, and
    whether the criterion is negative (excluded) or positive (narrowed targeting).

    By default, Google Ads serves ads to all demographic segments — a criterion
    only appears here when the user has explicitly added an exclusion or
    positive targeting refinement.
    """
    from adchestra_local.ads.gaql import execute_query

    if not ad_group_id and not campaign_id:
        return {
            "error": "Provide either ad_group_id or campaign_id",
        }
    if ad_group_id and campaign_id:
        return {
            "error": "Provide only one of ad_group_id or campaign_id, not both",
        }
    if (ad_group_id or campaign_id) and not (ad_group_id or campaign_id).isdigit():
        return {
            "error": "ad_group_id / campaign_id must be a numeric Google Ads ID",
        }

    demographic_types = (
        "'AGE_RANGE', 'GENDER', 'PARENTAL_STATUS', 'INCOME_RANGE'"
    )

    if ad_group_id:
        query = f"""
            SELECT ad_group_criterion.criterion_id,
                   ad_group_criterion.type,
                   ad_group_criterion.negative,
                   ad_group_criterion.status,
                   ad_group_criterion.age_range.type,
                   ad_group_criterion.gender.type,
                   ad_group_criterion.parental_status.type,
                   ad_group_criterion.income_range.type,
                   ad_group.id, ad_group.name,
                   campaign.id, campaign.name
            FROM ad_group_criterion
            WHERE ad_group.id = {ad_group_id}
              AND ad_group_criterion.type IN ({demographic_types})
            ORDER BY ad_group_criterion.type
        """
        level = "ad_group"
    else:
        query = f"""
            SELECT campaign_criterion.criterion_id,
                   campaign_criterion.type,
                   campaign_criterion.negative,
                   campaign_criterion.status,
                   campaign_criterion.age_range.type,
                   campaign_criterion.gender.type,
                   campaign_criterion.parental_status.type,
                   campaign_criterion.income_range.type,
                   campaign.id, campaign.name
            FROM campaign_criterion
            WHERE campaign.id = {campaign_id}
              AND campaign_criterion.type IN ({demographic_types})
            ORDER BY campaign_criterion.type
        """
        level = "campaign"

    rows = execute_query(config, customer_id, query)

    # Surface the composite resource ID for each criterion so the AI can pass
    # it straight to remove_entity (which expects 'parentId~criterionId').
    for row in rows:
        criterion_id = row.get(f"{level}_criterion.criterion_id")
        if criterion_id is None:
            continue
        if level == "ad_group":
            parent_id = row.get("ad_group.id")
        else:
            parent_id = row.get("campaign.id")
        if parent_id is not None:
            row["remove_id"] = f"{parent_id}~{criterion_id}"

    insights: list[str] = []
    if not rows:
        insights.append(
            f"No demographic criteria found on this {level}. By default, Google "
            f"Ads serves ads to all age/gender/parental/income segments — "
            f"criteria only appear here once you actively exclude or narrow them."
        )
    else:
        negatives = sum(
            1 for r in rows
            if r.get(f"{level}_criterion.negative") is True
        )
        if negatives:
            insights.append(
                f"{negatives} demographic exclusion(s) active on this {level}. "
                f"Excluded segments will not see ads."
            )

    return {
        "level": level,
        "criteria": rows,
        "total_criteria": len(rows),
        "insights": insights,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _round_metric(value: object) -> float:
    """Round an API metric to 4 decimal places, collapsing near-zero to 0."""
    v = float(value or 0)
    r = round(v, 4)
    return 0.0 if abs(r) < 0.0001 else r


def _improvement(base: float, potential: float) -> float | None:
    """Compute estimated improvement, returning None when Google has no estimate.

    The API returns 0 for potential when it doesn't have a projection for that
    metric. Naively subtracting would produce a misleading negative number.
    """
    if potential == 0 and base != 0:
        return None
    return round(potential - base, 4)


def _date_clause(start: str, end: str) -> str:
    """Build a GAQL date WHERE fragment."""
    if start and end:
        return f"AND segments.date BETWEEN '{start}' AND '{end}'"
    start, end = _resolve_date_range(start, end)
    return f"AND segments.date BETWEEN '{start}' AND '{end}'"


def _resolve_date_range(start: str, end: str) -> tuple[str, str]:
    """Resolve the default rolling 30-day range, including the current day."""
    if start and end:
        return start, end
    today = date.today()
    return (today - timedelta(days=29)).isoformat(), today.isoformat()


def _performance_period_metadata(start: str, end: str) -> dict:
    """Describe exactly what time and context a performance result contains."""
    includes_today = end == date.today().isoformat()
    analysis_context: dict = {
        "scope": "selected_period_only",
        "comparison_period_in_result": False,
        "recent_account_changes_in_result": False,
        "business_context_in_result": False,
    }
    if includes_today:
        analysis_context["freshness_note"] = (
            "This range includes the current date. Today's data may be incomplete, "
            "and recent conversions may still be recorded later."
        )
    return {
        "date_range": {
            "start": start,
            "end": end,
            "includes_today": includes_today,
        },
        "analysis_context": analysis_context,
    }


def _compact_totals(rows: list[dict], currency_code: str) -> dict:
    """Deterministic account-level aggregates over enriched metric rows."""
    cost = sum((r.get("metrics.cost_micros") or 0) for r in rows) / 1_000_000
    clicks = sum((r.get("metrics.clicks") or 0) for r in rows)
    impressions = sum((r.get("metrics.impressions") or 0) for r in rows)
    conversions = sum((r.get("metrics.conversions") or 0) for r in rows)
    totals: dict = {
        "cost": round(cost, 2),
        "clicks": clicks,
        "impressions": impressions,
        "conversions": round(conversions, 1),
        "currency": currency_code,
    }
    if conversions > 0:
        totals["cpa"] = round(cost / conversions, 2)
    if impressions > 0:
        totals["ctr_pct"] = round(clicks / impressions * 100, 2)
    return totals


def _compact_note(shown: int, total: int, tool_name: str) -> str:
    return (
        f"Compact mode: aggregates plus the top {shown} of {total} rows. "
        f"Call {tool_name} with compact=false when you need every row "
        f"(e.g. before proposing changes to a specific low-spend entity)."
    )


def _status_counts(rows: list[dict], field: str) -> dict:
    counts: dict[str, int] = {}
    for r in rows:
        status = str(r.get(field) or "UNKNOWN")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _enrich_cost_fields(rows: list[dict], currency_code: str = "EUR") -> None:
    """Add human-readable cost and CPA fields computed from cost_micros."""
    for row in rows:
        cost_micros = row.get("metrics.cost_micros", 0) or 0
        row["metrics.cost"] = round(cost_micros / 1_000_000, 2)

        conversions = row.get("metrics.conversions", 0) or 0
        if conversions > 0:
            row["metrics.cpa"] = round(cost_micros / 1_000_000 / conversions, 2)

        avg_cpc_micros = row.get("metrics.average_cpc", 0) or 0
        if avg_cpc_micros:
            row["metrics.average_cpc_amount"] = round(avg_cpc_micros / 1_000_000, 2)

        row["metrics.currency"] = currency_code
