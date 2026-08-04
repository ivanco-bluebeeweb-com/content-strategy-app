"""Heuristics + store-document -> entity converters for Content Strategy app.

Kept separate from handlers_chat.py so each @chat.function stays about the
business action, not formatting/scoring detail.
"""
from __future__ import annotations

import re

from schemas import ArticleBrief, ContentCalendarEntry, Opportunity, QueueItem, SiteProfile

_INTENT_COMMERCIAL_HINTS = (
    "buy", "price", "cost", "quote", "hire", "service", "company", "near me",
    "купить", "цена", "стоимость", "заказать", "услуги", "компания",
)
_INTENT_NAVIGATIONAL_HINTS = ("login", "sign in", "contact", "address", "hours")

_STOPWORDS = {
    "the", "a", "an", "for", "and", "or", "in", "on", "of", "to",
    "и", "в", "на", "для", "или", "с", "по",
}


def guess_intent(query: str) -> str:
    """Cheap keyword-based search-intent classifier: informational (default),
    commercial, or navigational."""
    q = query.lower()
    if any(h in q for h in _INTENT_NAVIGATIONAL_HINTS):
        return "navigational"
    if any(h in q for h in _INTENT_COMMERCIAL_HINTS):
        return "commercial"
    return "informational"


def cluster_label(query: str) -> str:
    """Cheap heuristic clustering: strip stopwords/numbers, take the first
    two significant tokens as a cluster key. Good enough for MVP grouping;
    not a replacement for a real NLP clustering pass."""
    tokens = re.findall(r"[a-zA-Zа-яА-ЯёЁ]+", query.lower())
    sig = [t for t in tokens if t not in _STOPWORDS and len(t) > 2]
    return " ".join(sig[:2]) if sig else query.lower()[:24]


def priority_score(impressions: int, clicks: int, ctr: float, avg_position: float) -> float:
    """Simple striking-distance-weighted score: reward high impressions with
    low clicks (untapped demand) and positions in the 4-20 'almost ranking'
    band, consistent with how the GSC connector's own striking_distance
    tool frames opportunity."""
    demand = min(impressions, 5000) / 5000.0  # 0..1
    gap = 1.0 - min(ctr, 1.0)  # low ctr = more headroom
    position_bonus = 1.0 if 4 <= avg_position <= 20 else (0.5 if avg_position < 4 else 0.2)
    return round((demand * 0.4 + gap * 0.3 + position_bonus * 0.3) * 100, 1)


def to_opportunity(d) -> Opportunity:
    data = d.data
    return Opportunity(
        id=d.id,
        title=data.get("primary_query", ""),
        site_id=data.get("site_id", ""),
        source=data.get("source", ""),
        primary_query=data.get("primary_query", ""),
        supporting_queries=data.get("supporting_queries", []),
        query_cluster_label=data.get("query_cluster_label", ""),
        intent=data.get("intent", ""),
        impressions=data.get("impressions", 0),
        clicks=data.get("clicks", 0),
        ctr=data.get("ctr", 0.0),
        avg_position=data.get("avg_position", 0.0),
        business_relevance_score=data.get("business_relevance_score", 0.0),
        seo_opportunity_score=data.get("seo_opportunity_score", 0.0),
        total_priority_score=data.get("total_priority_score", 0.0),
        recommended_content_type=data.get("recommended_content_type", "article"),
        recommended_target_url=data.get("recommended_target_url", ""),
        status=data.get("status", "idea"),
    )


def to_brief(d) -> ArticleBrief:
    data = d.data
    return ArticleBrief(
        id=d.id,
        title=data.get("working_title", ""),
        body=data.get("differentiation_notes", ""),
        site_id=data.get("site_id", ""),
        opportunity_id=data.get("opportunity_id", ""),
        working_title=data.get("working_title", ""),
        target_language=data.get("target_language", ""),
        target_audience=data.get("target_audience", ""),
        search_intent=data.get("search_intent", ""),
        primary_query=data.get("primary_query", ""),
        secondary_queries=data.get("secondary_queries", []),
        outline=data.get("outline", []),
        cta_goal=data.get("cta_goal", ""),
        internal_link_targets=data.get("internal_link_targets", []),
        differentiation_notes=data.get("differentiation_notes", ""),
        image_requirements=data.get("image_requirements", []),
        status=data.get("status", "brief_ready"),
    )


def to_queue_item(d) -> QueueItem:
    data = d.data
    return QueueItem(
        id=d.id,
        title=data.get("working_title") or data.get("primary_query") or d.id,
        site_id=data.get("site_id", ""),
        brief_id=data.get("brief_id", ""),
        opportunity_id=data.get("opportunity_id", ""),
        content_type=data.get("content_type", "article"),
        lifecycle_status=data.get("lifecycle_status", "idea"),
        assigned_agent=data.get("assigned_agent", "Webbee"),
        published_url=data.get("published_url", ""),
        scheduled_date=data.get("scheduled_date", ""),
        external_project_id=data.get("external_project_id", ""),
        external_article_id=data.get("external_article_id", ""),
    )


def to_calendar_entry(d) -> ContentCalendarEntry:
    data = d.data
    return ContentCalendarEntry(
        id=d.id,
        title=data.get("working_title") or data.get("primary_query") or d.id,
        site_id=data.get("site_id", ""),
        queue_item_id=d.id,
        scheduled_date=data.get("scheduled_date", ""),
        working_title=data.get("working_title") or data.get("primary_query") or "",
        lifecycle_status=data.get("lifecycle_status", "idea"),
        content_type=data.get("content_type", "article"),
    )


def to_site_profile(d) -> SiteProfile:
    data = d.data
    return SiteProfile(
        id=d.id,
        title=data.get("brand_name") or data.get("domain", d.id),
        site_id=data.get("site_id", ""),
        domain=data.get("domain", ""),
        brand_name=data.get("brand_name", ""),
        business_description=data.get("business_description", ""),
        target_languages=data.get("target_languages", []),
        content_categories=data.get("content_categories", []),
        cta_default=data.get("cta_default", ""),
    )


def to_site_competitor(d) -> "SiteCompetitorProfile":
    from schemas import SiteCompetitorProfile
    data = d.data
    return SiteCompetitorProfile(
        id=d.id,
        title=data.get("name", d.id),
        site_id=data.get("site_id", ""),
        url=data.get("url", ""),
        notes=data.get("notes", ""),
        competing_topics=data.get("competing_topics", []),
        strengths=data.get("strengths", []),
        weaknesses=data.get("weaknesses", []),
    )
