"""Heuristics + store-document -> entity converters for Content Strategy app.

Kept separate from handlers_chat.py so each @chat.function stays about the
business action, not formatting/scoring detail.
"""
from __future__ import annotations

import re

from schemas import (
    ArticleBrief, ContentCalendarEntry, Opportunity, QueueItem, SiteProfile,
    SiteCompetitorProfile,
)

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


def decide_image_text_policy(search_intent: str, prohibited_patterns: list | None = None) -> str:
    """Content Strategy's own signal into the image prompt's in-image-text
    instruction -- the actual link this app was missing between "what kind
    of article is this" and "should Media Studio's prompt allow legible
    text baked into the picture".

    Default is 'no_text': a clean photographic/illustrative image, safest
    for most articles and cheapest to get right from an image model.
    'commercial' intent (buy/price/cost/quote-type queries -- see
    guess_intent's own hint list) is the one case where legible in-image
    text (a price tag, a comparison label, a plan name) plausibly helps the
    reader, so it opts into 'allow_text'.

    prohibited_patterns is the approved VBS/Visual Profile's own forbidden-
    pattern list (Brand Strategy Hub); if ANY entry mentions "text", that
    approval-gated brand constraint always wins over the intent heuristic
    and forces 'no_text' -- Content Strategy has no authority to override
    an approved brand guardrail.
    """
    for pattern in (prohibited_patterns or []):
        if "text" in str(pattern).lower():
            return "no_text"
    return "allow_text" if search_intent == "commercial" else "no_text"


def cluster_label(query: str) -> str:
    """Cheap heuristic clustering: strip stopwords/numbers, take the first
    two significant tokens as a cluster key. Good enough for MVP grouping;
    not a replacement for a real NLP clustering pass."""
    tokens = re.findall(r"[a-zA-Zа-яА-ЯёЁ]+", query.lower())
    sig = [t for t in tokens if t not in _STOPWORDS and len(t) > 2]
    return " ".join(sig[:2]) if sig else query.lower()[:24]


_CYRILLIC_RE = re.compile(r"[а-яА-ЯёЁ]")
_LATIN_RE = re.compile(r"[a-zA-Z]")


def detect_language_fallback(title: str, content: str) -> str:
    """Best-effort language guess used ONLY when WordPress/Polylang did not
    report a 'lang' field for a post (run_content_audit's posts_by_language
    was always {"unknown": N} on real bilingual sites before this existed).
    Purely a Cyrillic-vs-Latin script ratio -- deterministic, no fabrication,
    no external calls. Distinguishes ru (Cyrillic) from ro/en (Latin) but
    CANNOT distinguish between two Latin-script languages (e.g. ro vs en) --
    returns 'ru' or 'latin' accordingly, never invents a specific Latin
    language code it cannot actually tell apart."""
    text = f"{title} {content}"
    cyr = len(_CYRILLIC_RE.findall(text))
    lat = len(_LATIN_RE.findall(text))
    if cyr == 0 and lat == 0:
        return "unknown"
    return "ru" if cyr > lat else "latin"


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
        text_policy=data.get("text_policy", "no_text"),
        approved_visual_guidance=data.get("approved_visual_guidance", {}),
        key_action_page_url=data.get("key_action_page_url", ""),
        key_action_page_language=data.get("key_action_page_language", ""),
        key_action_page_reason=data.get("key_action_page_reason", ""),
        external_link_url=data.get("external_link_url", ""),
        external_link_language=data.get("external_link_language", ""),
        external_link_language_priority=data.get("external_link_language_priority", []),
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
        target_language=data.get("target_language", ""),
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
        visual_baseline_state=data.get("visual_baseline_state", "not_attached"),
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
        business_description_i18n=data.get("business_description_i18n", {}),
        target_languages=data.get("target_languages", []),
        content_categories=data.get("content_categories", []),
        cta_default=data.get("cta_default", ""),
        cta_default_i18n=data.get("cta_default_i18n", {}),
        external_sources_i18n=data.get("external_sources_i18n", {}),
        approved_visual_guidance=data.get("approved_visual_guidance", {}),
    )


def to_site_competitor(d) -> SiteCompetitorProfile:
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


# ──────────────────────────────────────────────────────────────────────────
# Content audit + keyword cannibalization heuristics
# ──────────────────────────────────────────────────────────────────────────

_TAG_RE = re.compile(r"<[^>]+>")
_WORD_RE = re.compile(r"[a-zA-Zа-яА-ЯёЁăâîșțĂÂÎȘȚ]+")
_THIN_WORD_THRESHOLD = 300


def strip_html(html: str) -> str:
    """Plain text from WP REST 'rendered' HTML — good enough for word counts
    and keyword-term extraction, not a real HTML sanitizer."""
    if not html:
        return ""
    text = _TAG_RE.sub(" ", html)
    import html as _html
    return _html.unescape(text)


def word_count(html: str) -> int:
    text = strip_html(html)
    return len(_WORD_RE.findall(text))


def top_terms(text_or_html: str, n: int = 12) -> list[str]:
    """Cheap term-frequency signature for one article: strips HTML/stopwords,
    keeps the n most frequent significant tokens (len > 3). Same technique as
    cluster_label, just wider — this is a *signature*, not a real NLP topic
    model, so cannibalization scoring below is a heuristic, not a guarantee."""
    text = strip_html(text_or_html).lower()
    tokens = [t for t in _WORD_RE.findall(text) if t not in _STOPWORDS and len(t) > 3]
    if not tokens:
        return []
    freq: dict[str, int] = {}
    for t in tokens:
        freq[t] = freq.get(t, 0) + 1
    ranked = sorted(freq.items(), key=lambda kv: -kv[1])
    return [t for t, _ in ranked[:n]]


def term_overlap_score(terms_a: list[str], terms_b: list[str]) -> float:
    """Jaccard-style overlap of two term signatures, 0..1."""
    a, b = set(terms_a), set(terms_b)
    if not a or not b:
        return 0.0
    inter = a & b
    union = a | b
    return round(len(inter) / len(union), 3) if union else 0.0


def find_cannibalization_pairs(items: list[dict], min_overlap: float = 0.35) -> list[dict]:
    """Pairwise-compare every existing article's term signature against every
    other and flag pairs above min_overlap as competing for the same topic.
    O(n^2) — fine for a site's article count (dozens-hundreds), not meant
    for a directory with tens of thousands of posts."""
    findings = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b = items[i], items[j]
            score = term_overlap_score(a.get("top_terms", []), b.get("top_terms", []))
            if score >= min_overlap:
                shared = sorted(set(a.get("top_terms", [])) & set(b.get("top_terms", [])))
                findings.append({
                    "shared_terms": shared,
                    "overlap_score": score,
                    "urls": [a.get("link", ""), b.get("link", "")],
                    "titles": [a.get("title", ""), b.get("title", "")],
                    "recommendation": (
                        "merge into one canonical article" if score >= 0.6
                        else "differentiate angle/keyword focus or add internal canonical link"
                    ),
                })
    return findings
