"""Heuristics + store-document -> entity converters for Content Strategy app.

Kept separate from handlers_chat.py so each @chat.function stays about the
business action, not formatting/scoring detail.
"""
from __future__ import annotations

import re

from schemas import (
    ArticleBrief, ContentCalendarEntry, Opportunity, QueueItem, SiteProfile,
    SiteCompetitorProfile, OutreachTarget,
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


# ──────────────────────────────────────────────────────────────────────────
# Funnel-stage classification (TOFU/MOFU/BOFU) -- deliberately separate from
# guess_intent(): intent (informational/commercial/navigational) answers
# "what is the searcher looking for", funnel_stage answers "how close is
# this searcher to buying". A commercial-intent query like "climatizare
# birou pret" is still often MOFU (comparing options) rather than BOFU
# (ready to commit) -- collapsing the two into one field previously forced
# a false choice at brief time. Every classification carries an explicit,
# auditable reason string -- never a silent guess.
# ──────────────────────────────────────────────────────────────────────────

_BOFU_HINTS = (
    # ready-to-buy: guarantees, ordering, quotes, reviews of who to hire,
    # explicit "best company" comparisons aimed at a purchase decision
    "guarantee", "warranty", "order", "buy now", "quote", "review", "reviews",
    "best company", "hire",
    "гарантия", "гарантией", "заказать", "заявка", "отзывы", "лучшая компания",
    "рассчитать стоимость", "калькулятор",
    "garanție", "garantie", "comandă", "comanda", "recenzii", "cea mai bună",
    "calculator", "deviz",
)
_MOFU_HINTS = (
    "price", "cost", "compare", "comparison", "vs", "how to choose",
    "which is better",
    "цена", "стоимость", "сравнение", "как выбрать", "какой лучше", "контракт",
    "pret", "preț", "compara", "cum să alegi", "care este mai bun", "contract",
)


def guess_funnel_stage(query: str) -> tuple[str, str]:
    """Classify a query into tofu (awareness -- broad/symptom/what-is
    questions), mofu (consideration -- comparing options, pricing research)
    or bofu (decision -- ready to order/hire, wants guarantees/reviews/a
    quote right now). Returns (stage, reason) so the assignment is always
    auditable, never a silent guess.

    This is intentionally a THIRD axis from guess_intent(): a query can be
    'commercial' intent (has a price/buy word) yet still be MOFU rather
    than BOFU if it reads as comparison-shopping rather than a ready-to-
    commit signal (an explicit "guarantee/order/reviews of who's best"
    phrase). BOFU hints are checked first since they are the more specific
    (and rarer) signal; MOFU hints are the broader comparison-shopping net;
    anything left is TOFU by default -- awareness-stage content casts the
    widest net and should never be silently reclassified as further down
    the funnel just because it mentions a price word.
    """
    q = query.lower()
    for hint in _BOFU_HINTS:
        if hint in q:
            return "bofu", f"Contains a ready-to-buy signal ('{hint}') -- guarantee/order/quote/reviews language."
    for hint in _MOFU_HINTS:
        if hint in q:
            return "mofu", f"Contains a comparison/consideration signal ('{hint}') -- price research or option comparison."
    return "tofu", "No purchase-decision or comparison-shopping signal found -- treated as awareness-stage."


def decide_image_text_policy(
    search_intent: str, prohibited_patterns: list | None = None, candidate_text: str = "",
) -> tuple[str, str]:
    """Content Strategy's own signal into the image prompt's in-image-text
    instruction -- the actual link this app was missing between "what kind
    of article is this" and "should Media Studio's prompt allow legible
    text baked into the picture, and with WHAT text".

    Returns (text_policy, image_text) -- always one of two explicit shapes,
    never a vague "maybe":
    - ("no_text", "") -- a clean photographic/illustrative image, safest
      for most articles and the default for everything below.
    - ("allow_text", "<exact words>") -- ONLY when there is real wording to
      hand the image model. Media Studio's own contract (see its
      MEDIA_INVALID_TEXT_POLICY validation) rejects 'allow_text' without
      actual text, on purpose: an image prompt can never just say "some
      text is fine", it must say either "no text" or the specific text.

    'commercial' intent (buy/price/cost/quote-type queries -- see
    guess_intent's own hint list) is the one case where legible in-image
    text (a price tag, a comparison label, a CTA phrase) plausibly helps
    the reader -- but ONLY if `candidate_text` (the brief's own CTA goal,
    passed in by the caller) is actually non-empty. Commercial intent with
    no real candidate text stays 'no_text' rather than inventing wording.

    prohibited_patterns is the approved VBS/Visual Profile's own forbidden-
    pattern list (Brand Strategy Hub); if ANY entry mentions "text", that
    approval-gated brand constraint always wins over the intent heuristic
    and forces ('no_text', '') -- Content Strategy has no authority to
    override an approved brand guardrail.
    """
    for pattern in (prohibited_patterns or []):
        if "text" in str(pattern).lower():
            return "no_text", ""
    text = candidate_text.strip()
    if search_intent == "commercial" and text:
        return "allow_text", text
    return "no_text", ""


def _stem(token: str) -> str:
    """Trim a long token to an 8-char prefix so near-synonymous word forms
    (e.g. ro 'recuperator' / 'recuperatoare', ru declensions) collapse to the
    same cluster key. Cheap and deterministic -- not real morphological
    stemming, but real enough to stop obvious variants of the same word
    from splitting into separate clusters (see task #1894). Short tokens
    (<=8 chars) are already specific enough and left untouched."""
    return token[:8] if len(token) > 8 else token


def cluster_label(query: str) -> str:
    """Cheap heuristic clustering: strip stopwords/numbers, stem the first
    two significant tokens, and use them as a cluster key so query variants
    of the same topic (different word endings, added connector words like
    'de'/'of') collapse into ONE cluster instead of one per literal string.
    Good enough for MVP grouping; not a replacement for a real NLP
    clustering pass."""
    tokens = re.findall(r"[a-zA-Zа-яА-ЯёЁ]+", query.lower())
    sig = [_stem(t) for t in tokens if t not in _STOPWORDS and len(t) > 2]
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
        funnel_stage=data.get("funnel_stage", ""),
        funnel_stage_reason=data.get("funnel_stage_reason", ""),
        impressions=data.get("impressions", 0),
        clicks=data.get("clicks", 0),
        ctr=data.get("ctr", 0.0),
        avg_position=data.get("avg_position", 0.0),
        business_relevance_score=data.get("business_relevance_score", 0.0),
        seo_opportunity_score=data.get("seo_opportunity_score", 0.0),
        total_priority_score=data.get("total_priority_score", 0.0),
        recommended_content_type=data.get("recommended_content_type", "article"),
        recommended_target_url=data.get("recommended_target_url", ""),
        strategic_rationale=data.get("strategic_rationale", ""),
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
        funnel_stage=data.get("funnel_stage", ""),
        primary_query=data.get("primary_query", ""),
        secondary_queries=data.get("secondary_queries", []),
        outline=data.get("outline", []),
        cta_goal=data.get("cta_goal", ""),
        internal_link_targets=data.get("internal_link_targets", []),
        differentiation_notes=data.get("differentiation_notes", ""),
        image_requirements=data.get("image_requirements", []),
        text_policy=data.get("text_policy", "no_text"),
        image_text=data.get("image_text", ""),
        approved_visual_guidance=data.get("approved_visual_guidance", {}),
        key_action_page_url=data.get("key_action_page_url", ""),
        key_action_page_language=data.get("key_action_page_language", ""),
        key_action_page_reason=data.get("key_action_page_reason", ""),
        external_link_url=data.get("external_link_url", ""),
        external_link_language=data.get("external_link_language", ""),
        external_link_language_priority=data.get("external_link_language_priority", []),
        author_id=data.get("author_id", ""),
        author_name=data.get("author_name", ""),
        author_bio=data.get("author_bio", ""),
        author_credentials=data.get("author_credentials", []),
        resolved_category=data.get("resolved_category", ""),
        resolved_category_id=data.get("resolved_category_id", 0),
        category_resolution_reason=data.get("category_resolution_reason", ""),
        brand_readiness_checked=data.get("brand_readiness_checked", False),
        brand_id=data.get("brand_id", ""),
        status=data.get("status", "brief_ready"),
    )


def to_content_author(d) -> "ContentAuthor":
    from schemas import ContentAuthor
    data = d.data
    return ContentAuthor(
        id=d.id,
        title=data.get("name", ""),
        site_id=data.get("site_id", ""),
        name=data.get("name", ""),
        bio=data.get("bio", ""),
        credentials=data.get("credentials", []),
        expertise_areas=data.get("expertise_areas", []),
        author_page_url=data.get("author_page_url", ""),
        same_as=data.get("same_as", []),
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
        fact_checked=data.get("fact_checked", False),
        fact_checked_by=data.get("fact_checked_by", ""),
        fact_checked_at=data.get("fact_checked_at", ""),
        edited_by=data.get("edited_by", ""),
        edited_at=data.get("edited_at", ""),
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
        requires_named_author=data.get("requires_named_author", False),
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


def to_outreach_target(d) -> OutreachTarget:
    data = d.data
    return OutreachTarget(
        id=d.id,
        title=data.get("target_domain", d.id),
        site_id=data.get("site_id", ""),
        target_domain=data.get("target_domain", ""),
        target_url=data.get("target_url", ""),
        tactic=data.get("tactic", "guest_post"),
        linked_article_url=data.get("linked_article_url", ""),
        contact_name=data.get("contact_name", ""),
        contact_email=data.get("contact_email", ""),
        status=data.get("status", "prospected"),
        acquired_url=data.get("acquired_url", ""),
        notes=data.get("notes", ""),
        status_history=data.get("status_history", []),
        created_at=data.get("created_at", ""),
        updated_at=data.get("updated_at", ""),
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


def resolve_category(topic_terms: list[str], categories: list[dict]) -> tuple[str, int, str]:
    """Deterministically resolve a WordPress category for a new article from
    the site's REAL existing category tree -- never a hardcoded/generic
    default like "Blog". Used by create_brief's mandatory category-resolution
    step (every article brief must call this, never skip straight to a
    default name).

    ``categories`` is WordPress Hub's list_post_categories_full IPC payload:
    [{"id", "name", "slug", "parent_id", "count"}, ...]. ``topic_terms`` is
    the brief's own top_terms signature (primary_query + supporting_queries).

    Scoring: token-overlap between the topic's terms and each category's own
    name/slug tokens (same technique as term_overlap_score elsewhere in this
    module). A category with zero posts (count=0) is still eligible but
    scored slightly lower than an equally-matching populated one, since an
    empty category is more likely orphaned/never-really-used taxonomy (the
    exact 'Blog' category bug this replaces).

    Returns (category_name, category_id, reason):
      - category_id > 0 -> an existing real category matched with a real
        content overlap; category_name is its exact existing name.
      - category_id == 0 -> no existing category scored above zero overlap;
        category_name is a new name derived from the topic itself (never
        "Blog"/"Uncategorized"/empty), for the caller to create.
    """
    topic_set = set(t.lower() for t in topic_terms if t)
    if not categories:
        fallback = " ".join(topic_terms[:2]).title() or "General"
        return fallback, 0, "No categories exist yet on this site -- proposing a new one from the article's own topic terms."

    best = None  # (tiebreak, overlap, category_dict)
    for cat in categories:
        name = str(cat.get("name", ""))
        slug = str(cat.get("slug", ""))
        cat_tokens = set(re.findall(r"[a-zA-Za-\u044f\u0410-\u042f\u0451\u0401]+", (name + " " + slug).lower()))
        cat_tokens = {t for t in cat_tokens if t not in _STOPWORDS and len(t) > 2}
        if not cat_tokens or not topic_set:
            overlap = 0.0
        else:
            inter = topic_set & cat_tokens
            union = topic_set | cat_tokens
            overlap = len(inter) / len(union) if union else 0.0
        tiebreak = overlap + (0.001 if int(cat.get("count", 0) or 0) > 0 else 0.0)
        if best is None or tiebreak > best[0]:
            best = (tiebreak, overlap, cat)

    if best and best[1] > 0.0:
        _, overlap, cat = best
        return (
            str(cat.get("name", "")),
            int(cat.get("id", 0) or 0),
            f"Matched existing category '{cat.get('name', '')}' by real topic-term overlap ({overlap:.2f}); "
            f"chosen from the site's own category tree, not a default.",
        )

    fallback = " ".join(topic_terms[:2]).title() or "General"
    return (
        fallback, 0,
        f"No existing category overlapped this topic's terms ({len(categories)} categories checked) -- "
        f"proposing a new category '{fallback}' derived from the article's own topic, not a generic default.",
    )


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


# ──────────────────────────────────────────────────────────────────────────
# Strategic topic generation -- the "beyond existing opportunities" engine.
#
# discover_opportunities only ever scores/clusters query signals the caller
# already fetched from GSC/SEO Audit -- it cannot surface a topic nobody has
# searched for YET, or a gap the site's own declared categories imply but no
# query signal happens to cover this month. generate_strategic_candidates is
# the deliberate second engine: it reasons from the site's OWN declared
# content_categories (what this business says it does), cross-referenced
# against topic terms that are ALREADY covered (existing opportunities +
# published content, both passed in by the caller) so it never proposes a
# duplicate, and produces new candidate topics using a fixed library of
# funnel-stage question PATTERNS (awareness/comparison/decision) applied to
# each uncovered category. This is a deterministic, auditable heuristic --
# not a language model call -- but it is a genuinely different reasoning
# step from clustering: pattern x category, minus what's covered, gives
# topics that never appeared in any query signal at all.
# ──────────────────────────────────────────────────────────────────────────

_STRATEGIC_PATTERNS = {
    "tofu": [
        {"ro": "Ce este {cat} și când ai nevoie de el", "ru": "Что такое {cat} и когда это нужно", "en": "What is {cat} and when do you need it"},
        {"ro": "Semne că ai nevoie de {cat}", "ru": "Признаки того, что вам нужен {cat}", "en": "Signs you need {cat}"},
        {"ro": "Greșeli frecvente legate de {cat}", "ru": "Частые ошибки с {cat}", "en": "Common mistakes with {cat}"},
    ],
    "mofu": [
        {"ro": "Cum alegi {cat} potrivit pentru afacerea ta", "ru": "Как выбрать {cat} для вашего бизнеса", "en": "How to choose the right {cat} for your business"},
        {"ro": "{cat}: ce influențează prețul", "ru": "{cat}: от чего зависит цена", "en": "{cat}: what drives the price"},
        {"ro": "{cat} vs alternative — comparație", "ru": "{cat} против альтернатив — сравнение", "en": "{cat} vs alternatives — comparison"},
    ],
    "bofu": [
        {"ro": "Ofertă și deviz pentru {cat}", "ru": "Предложение и расчёт стоимости на {cat}", "en": "Quote and estimate for {cat}"},
        {"ro": "Garanție și mentenanță pentru {cat}", "ru": "Гарантия и обслуживание {cat}", "en": "Warranty and maintenance for {cat}"},
        {"ro": "De ce să alegi echipa noastră pentru {cat}", "ru": "Почему стоит выбрать нашу команду для {cat}", "en": "Why choose our team for {cat}"},
    ],
}

_FUNNEL_ORDER = ("tofu", "mofu", "bofu")


def generate_strategic_candidates(
    content_categories: list[str],
    covered_terms: set[str],
    language: str = "ro",
    per_category: int = 3,
    balance_funnel: bool = True,
    funnel_focus: str = "",
) -> list[dict]:
    """Generate NEW candidate topics beyond any existing opportunity/query
    signal, by applying a fixed library of funnel-stage question patterns to
    each of the site's own declared content_categories, skipping any
    category already well-covered by covered_terms (token overlap check
    against the category's own words -- same heuristic as resolve_category).

    Returns a list of dicts: {title, funnel_stage, funnel_stage_reason,
    category, strategic_rationale} -- ready to become Opportunity rows.
    Deterministic and side-effect free so it is fully unit-testable without
    IPC; the caller (generate_strategic_topics) is responsible for actually
    persisting these and cross-checking exact-duplicate titles against
    existing opportunities.

    balance_funnel=True (default) rotates tofu/mofu/bofu evenly across the
    categories in order, so a multi-category site gets a spread of funnel
    stages rather than all-tofu or all-bofu -- the same "beyond existing"
    goal this whole engine exists for would be defeated if it only ever
    generated awareness-stage filler.

    funnel_focus (optional: 'tofu'|'mofu'|'bofu') overrides the rotation and
    forces EVERY generated candidate to that single stage -- e.g. when a
    site's TOFU/MOFU coverage already comes from real query signals and the
    deliberate gap to fill is specifically bottom-of-funnel (ready-to-buy)
    content, one per category, instead of guessing at a proxy topic.
    """
    lang_key = language.lower()[:2]
    lang_key = lang_key if lang_key in ("ro", "ru") else "en"
    candidates: list[dict] = []
    stage_cycle_idx = 0
    for cat in content_categories:
        cat_clean = cat.strip()
        if not cat_clean:
            continue
        cat_tokens = {t for t in re.findall(r"[a-zA-Zа-яА-ЯёЁăâîșțĂÂÎȘȚ]+", cat_clean.lower()) if t not in _STOPWORDS and len(t) > 2}
        if cat_tokens and covered_terms and len(cat_tokens & covered_terms) / max(len(cat_tokens), 1) >= 0.75:
            # this category's own vocabulary is already heavily represented
            # in existing opportunities/content -- skip generating more
            # near-duplicate topics for it rather than pad the queue.
            continue
        if funnel_focus in ("tofu", "mofu", "bofu"):
            stages_for_cat = [funnel_focus] * per_category
        else:
            stages_for_cat = (
                [_FUNNEL_ORDER[(stage_cycle_idx + i) % 3] for i in range(per_category)]
                if balance_funnel else ["tofu"] * per_category
            )
        stage_cycle_idx += 1
        for stage in stages_for_cat:
            patterns = _STRATEGIC_PATTERNS[stage]
            pattern = patterns[len([c for c in candidates if c["funnel_stage"] == stage]) % len(patterns)]
            title = pattern.get(lang_key, pattern["en"]).format(cat=cat_clean)
            candidates.append({
                "title": title,
                "funnel_stage": stage,
                "funnel_stage_reason": f"Generated from a {stage.upper()} question pattern applied to declared category '{cat_clean}', not copied from any existing query signal.",
                "category": cat_clean,
                "strategic_rationale": (
                    f"Site declares '{cat_clean}' as a content category but no existing opportunity/content "
                    f"covers a {stage.upper()}-stage angle for it -- this fills that gap intentionally, "
                    f"expanding coverage beyond queries already seen in Search Console."
                ),
            })
    return candidates
