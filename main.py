"""Content Strategy app — decides what content to create, why, for which
site/audience, and hands off a structured brief downstream to article
writing and the future Image/Media app.

Boundaries (see notes "System architecture — Content Strategy app MVP"):
- does NOT publish to WordPress (wp-site-connector's job)
- does NOT generate/edit images (future Image/Media app's job)
- does NOT own technical SEO crawling (SEO Audit Engine's job)

Everything that registers against `ext`/`chat` (chat functions, panels)
lives directly in this file. schemas.py and converters.py are pure leaf
modules imported one-way from here -- nothing imports back from main.py,
which is what the platform's deploy loader requires (it loads main.py by
path, not as a package, so any handler module trying to import `chat`/`ext`
back out of main.py ends up talking to a second, empty copy of this module).
"""
from __future__ import annotations

import calendar as _calendar
from datetime import date as _date

from imperal_sdk import ActionResult, Extension, ChatExtension, ui

from schemas import (
    BuildContentCalendarParams, BuildWriterBriefParams, CreateBriefParams,
    CreateSiteProfileParams, DiscoverOpportunitiesParams,
    GetContentCalendarParams, LinkExternalArticleParams,
    ListBriefsParams, ListOpportunitiesParams, ListQueueParams,
    ListSiteProfilesParams, UpdateQueueStatusParams,
    ArticleBrief, ArticleBriefList,
    ContentCalendarEntry, ContentCalendarEntryList,
    Opportunity, OpportunityList,
    QueueItem, QueueItemList,
    SiteProfile, SiteProfileList,
    ConnectedSite, ConnectedSiteList, ListConnectedSitesParams,
    TopicCluster,
    WriterBrief,
    SiteCompetitorProfile, SiteCompetitorProfileList,
    AddSiteCompetitorParams, ListSiteCompetitorsParams,
)
from converters import (
    guess_intent, cluster_label, priority_score,
    to_opportunity as _to_opportunity,
    to_brief as _to_brief,
    to_calendar_entry as _to_calendar_entry,
    to_queue_item as _to_queue_item,
    to_site_profile as _to_site_profile,
    to_site_competitor as _to_site_competitor,
)

ext = Extension(
    "content-strategy-app",
    version="0.1.0",
    display_name="Content Strategy Hub",
    description=(
        "Plans what to write next for your sites. Discovers content opportunities "
        "from Google Search Console data, clusters them into topics, generates "
        "structured article briefs (with image requirements for downstream "
        "generation), and tracks each idea through an editorial queue from idea "
        "to published."
    ),
    icon="icon.svg",
    actions_explicit=True,
    capabilities=["content_strategy:read", "content_strategy:write"],
)


@ext.health_check
async def health_check(ctx) -> bool:
    """Basic liveness check — confirms the store surface is reachable."""
    await ctx.store.query("site_profiles", limit=1)
    return True


# ──────────────────────────────────────────────────────────────────────────
# Cross-app site discovery for Quick Add -- not just WordPress, on purpose.
# ──────────────────────────────────────────────────────────────────────────
# Registry of app_ids that expose a "list_connected_sites" IPC method
# returning [{"site_id", "name", "url", "status"}, ...]. Any future site
# provider (Shopify, Webflow, a plain-domain connector, ...) is added here
# and Quick Add picks it up automatically -- no panel code changes needed.
SITE_PROVIDER_APP_IDS: list[str] = ["wp-site-connector"]


def _canonical_site_id(row: dict) -> str:
    """Normalise a provider's site identifier to its bare domain.

    Providers name sites their own way -- WordPress Hub uses a slug
    ('g4s-md') while this app keys sites by domain ('g4s.md'). Quick Add must
    speak the domain form, otherwise clicking it would create a DUPLICATE
    profile next to the existing one and 'already added' checks would miss.
    """
    host = (row.get("url") or "").strip().split("://", 1)[-1].split("/", 1)[0].lower()
    if host.startswith("www."):
        host = host[4:]
    return host or (row.get("site_id") or "")


async def fetch_connected_sites(ctx) -> tuple[list[dict], list[dict]]:
    """Pull every connected site from every registered site-provider
    extension via ctx.extensions.call -- direct in-process IPC (no chat
    round-trip, no manual site_id typing).

    Returns (sites, problems). A provider that fails is reported in
    `problems` as {"provider", "reason"} instead of vanishing: the Quick Add
    card then SHOWS why it is empty, so the failure is visible and fixable
    in the UI rather than silently hiding the whole feature.
    """
    sites: list[dict] = []
    problems: list[dict] = []
    for app_id in SITE_PROVIDER_APP_IDS:
        try:
            rows = await ctx.extensions.call(app_id, "list_connected_sites")
        except Exception as exc:  # noqa: BLE001 -- surfaced to the panel, not swallowed
            problems.append({
                "provider": app_id,
                "reason": f"{type(exc).__name__}: {exc}".strip()[:300],
            })
            continue
        for r in rows or []:
            sites.append({
                **r,
                "provider": app_id,
                "provider_site_id": r.get("site_id", ""),
                "site_id": _canonical_site_id(r),
            })
    return sites, problems


chat = ChatExtension(
    ext,
    tool_name="content-strategy-app",
    description=(
        "Content strategy assistant — discovers article opportunities from "
        "search data, builds article briefs, and tracks editorial queue status "
        "per site."
    ),
    system_prompt=(
        "You help the user decide what content to create next for their sites. "
        "You surface opportunities from search data, turn the best ones into "
        "structured briefs, and track their status through an editorial queue. "
        "You do not write full articles, generate images, or publish to "
        "WordPress yourself — you hand off clean briefs for those next steps."
    ),
)


# ──────────────────────────────────────────────────────────────────────────
# Site profiles
# ──────────────────────────────────────────────────────────────────────────

@chat.function(
    "create_site_profile",
    description=(
        "Create or register a managed site profile (domain, brand, target "
        "languages, content categories, default CTA). Needed once per site "
        "before discovering opportunities for it."
    ),
    action_type="write",
    chain_callable=True,
    effects=["create:site_profile"],
    event="created",
    data_model=SiteProfile,
)
async def create_site_profile(ctx, params: CreateSiteProfileParams) -> ActionResult:
    """Register a new managed site profile (idempotent on site_id)."""
    existing = await ctx.store.query("site_profiles", where={"site_id": params.site_id}, limit=1)
    if existing.data:
        return ActionResult.error(
            f"Site profile '{params.site_id}' already exists. Use it directly.",
            retryable=False,
        )
    doc = await ctx.store.create(
        "site_profiles",
        {
            "site_id": params.site_id,
            "domain": params.domain,
            "brand_name": params.brand_name,
            "business_description": params.business_description,
            "target_languages": params.target_languages,
            "content_categories": params.content_categories,
            "cta_default": params.cta_default,
        },
    )
    return ActionResult.success(
        _to_site_profile(doc),
        summary=f"Site profile '{params.site_id}' created.",
        refresh_panels=["queue"],
    )


@chat.function(
    "list_site_profiles",
    description="List all registered site profiles (managed sites).",
    action_type="read",
    data_model=SiteProfileList,
)
async def list_site_profiles(ctx, params: ListSiteProfilesParams) -> ActionResult:
    """Return all registered site profiles."""
    page = await ctx.store.query("site_profiles", limit=params.limit)
    profiles = [_to_site_profile(d) for d in page.data]
    return ActionResult.success(
        SiteProfileList(items=profiles, total=len(profiles)),
        summary=f"{len(profiles)} site profile(s).",
    )


# ──────────────────────────────────────────────────────────────────────────
# Web-level competitor tracking (site-scoped)
# ──────────────────────────────────────────────────────────────────────────

@chat.function(
    "add_site_competitor",
    description=(
        "Track a competitor for a managed site at the web/content level — "
        "which competing pages/sites rank or compete for the same topics, with "
        "observed strengths and weaknesses. Distinct from Brand Strategy Hub's "
        "brand-level competitors: this is about content/SERP rivals for THIS "
        "site specifically."
    ),
    action_type="write",
    chain_callable=True,
    effects=["create:site_competitor"],
    event="site_competitor_added",
    data_model=SiteCompetitorProfile,
)
async def add_site_competitor(ctx, params: AddSiteCompetitorParams) -> ActionResult:
    """Save one site-scoped competitor profile."""
    profile_page = await ctx.store.query("site_profiles", where={"site_id": params.site_id}, limit=1)
    if not profile_page.data:
        return ActionResult.error(
            f"No site profile '{params.site_id}'. Create it first with create_site_profile.",
            retryable=False,
        )
    doc = await ctx.store.create(
        "site_competitors",
        {
            "site_id": params.site_id,
            "name": params.name,
            "url": params.url,
            "competing_topics": params.competing_topics,
            "strengths": params.strengths,
            "weaknesses": params.weaknesses,
            "notes": params.notes,
        },
    )
    return ActionResult.success(
        _to_site_competitor(doc),
        summary=f"Competitor '{params.name}' tracked for site '{params.site_id}'.",
        refresh_panels=["queue"],
    )


@chat.function(
    "list_site_competitors",
    description="List tracked web-level competitors, optionally filtered by site.",
    action_type="read",
    data_model=SiteCompetitorProfileList,
)
async def list_site_competitors(ctx, params: ListSiteCompetitorsParams) -> ActionResult:
    """Return tracked site-scoped competitors."""
    where = {"site_id": params.site_id} if params.site_id else None
    page = await ctx.store.query("site_competitors", where=where, limit=params.limit)
    items = [_to_site_competitor(d) for d in page.data]
    return ActionResult.success(
        SiteCompetitorProfileList(items=items, total=len(items)),
        summary=f"{len(items)} site competitor(s).",
    )


# ──────────────────────────────────────────────────────────────────────────
# Opportunity discovery + clustering
# ──────────────────────────────────────────────────────────────────────────

@chat.function(
    "discover_opportunities",
    description=(
        "Turn query signals (fetch these first from the Google Search "
        "Console connector's top_queries or striking_distance tools, or from "
        "SEO Audit Engine findings) into scored content opportunities for a "
        "site, clustered by topic. Creates queue items in 'idea' status for "
        "each new opportunity. "
        "ASSISTANT POLICY (not enforced by this tool's params): before calling "
        "this, ask the user in chat how many articles/opportunities they want "
        "surfaced this round — never assume the 'limit' default silently. If "
        "the user's answer is ambiguous (e.g. mixed-language phrasing, unclear "
        "number, or could mean per-language rather than total — this platform "
        "is multilingual), ask a clarifying follow-up instead of guessing."
    ),
    action_type="write",
    chain_callable=True,
    effects=["create:opportunity", "create:queue_item"],
    event="opportunities_discovered",
    data_model=OpportunityList,
)
async def discover_opportunities(ctx, params: DiscoverOpportunitiesParams) -> ActionResult:
    """Score and cluster incoming query signals into new opportunities +
    queue items for one site, skipping queries already tracked."""
    if not params.queries:
        return ActionResult.error(
            "No query signals provided. Fetch queries from the Google Search "
            "Console connector (top_queries or striking_distance) or SEO Audit "
            "Engine findings first, then pass them in as 'queries'.",
            retryable=False,
        )

    profile_page = await ctx.store.query("site_profiles", where={"site_id": params.site_id}, limit=1)
    if not profile_page.data:
        return ActionResult.error(
            f"Site profile '{params.site_id}' not found. Create it first with "
            f"create_site_profile.",
            retryable=False,
        )

    # existing primary queries for this site — avoid duplicate opportunities
    existing_page = await ctx.store.query("opportunities", limit=500)
    existing_queries = {
        d.data.get("primary_query", "").lower()
        for d in existing_page.data
        if d.data.get("site_id") == params.site_id
    }

    created: list[Opportunity] = []
    for sig in params.queries[: params.limit]:
        if sig.query.lower() in existing_queries:
            continue
        intent = guess_intent(sig.query)
        label = cluster_label(sig.query)
        score = priority_score(sig.impressions, sig.clicks, sig.ctr, sig.avg_position)
        doc = await ctx.store.create(
            "opportunities",
            {
                "site_id": params.site_id,
                "source": sig.source,
                "primary_query": sig.query,
                "supporting_queries": [],
                "query_cluster_label": label,
                "intent": intent,
                "impressions": sig.impressions,
                "clicks": sig.clicks,
                "ctr": sig.ctr,
                "avg_position": sig.avg_position,
                "business_relevance_score": 0.0,
                "seo_opportunity_score": score,
                "total_priority_score": score,
                "recommended_content_type": "article",
                "recommended_target_url": "",
                "status": "idea",
            },
        )
        await ctx.store.create(
            "queue_items",
            {
                "site_id": params.site_id,
                "brief_id": "",
                "opportunity_id": doc.id,
                "content_type": "article",
                "lifecycle_status": "idea",
                "assigned_agent": "Webbee",
                "published_url": "",
                "primary_query": sig.query,
            },
        )
        created.append(_to_opportunity(doc))

    created.sort(key=lambda o: o.total_priority_score, reverse=True)
    return ActionResult.success(
        OpportunityList(items=created, total=len(created)),
        summary=(
            f"Found {len(created)} new opportunity(ies) for {params.site_id} "
            f"({len(params.queries) - len(created)} skipped as duplicates)."
        ),
        refresh_panels=["queue"],
    )


@chat.function(
    "list_opportunities",
    description="List content opportunities, optionally filtered by site and status.",
    action_type="read",
    data_model=OpportunityList,
)
async def list_opportunities(ctx, params: ListOpportunitiesParams) -> ActionResult:
    """Return opportunities, optionally filtered by site and/or status."""
    page = await ctx.store.query("opportunities", order_by="-created_at", limit=200)
    items = [d for d in page.data]
    if params.site_id:
        items = [d for d in items if d.data.get("site_id") == params.site_id]
    if params.status:
        items = [d for d in items if d.data.get("status") == params.status]
    items = items[: params.limit]
    opps = [_to_opportunity(d) for d in items]
    return ActionResult.success(
        OpportunityList(items=opps, total=len(opps)),
        summary=f"{len(opps)} opportunity(ies).",
    )


# ──────────────────────────────────────────────────────────────────────────
# Brief generation
# ──────────────────────────────────────────────────────────────────────────

@chat.function(
    "create_brief",
    description=(
        "Generate a structured article brief from an existing opportunity: "
        "title direction, search intent, outline skeleton, CTA goal, and "
        "image requirement placeholders for the downstream image pipeline. "
        "Moves the linked queue item to 'brief_ready'."
    ),
    action_type="write",
    chain_callable=True,
    effects=["create:article_brief", "update:queue_item"],
    event="created",
    data_model=ArticleBrief,
)
async def create_brief(ctx, params: CreateBriefParams) -> ActionResult:
    """Build a structured article brief from an existing opportunity and
    advance its linked queue item to brief_ready. Supports one brief per
    language per opportunity — pass target_language explicitly to create
    a second (third, ...) brief for the same opportunity in another language,
    which also gets its own queue item so Editorial Queue tracks each
    language's article separately."""
    opp_doc = await ctx.store.get("opportunities", params.opportunity_id)
    if not opp_doc:
        return ActionResult.error(
            f"Opportunity '{params.opportunity_id}' not found.", retryable=False
        )
    opp = opp_doc.data
    profile_page = await ctx.store.query("site_profiles", where={"site_id": opp.get("site_id", "")}, limit=1)
    profile = profile_page.data[0].data if profile_page.data else {}

    target_language = params.target_language or (profile.get("target_languages") or ["en"])[0]

    # one brief per (opportunity, language) — refuse a silent duplicate
    existing_briefs = await ctx.store.query("article_briefs", limit=500)
    for d in existing_briefs.data:
        if (d.data.get("opportunity_id") == params.opportunity_id
                and d.data.get("target_language") == target_language):
            return ActionResult.error(
                f"A brief already exists for this opportunity in '{target_language}' "
                f"(brief {d.id}). Use a different target_language or that brief directly.",
                retryable=False,
            )

    outline = [
        f"H1: {opp.get('primary_query', '').capitalize()}",
        "Intro — why this matters for the reader",
        "Main section(s) covering supporting queries",
        "Practical/actionable guidance",
        f"CTA: {profile.get('cta_default', 'contact us')}",
    ]
    image_requirements = [
        "featured: hero image representing the primary topic",
        "inline_1: supporting visual for the main section",
    ]

    brief_doc = await ctx.store.create(
        "article_briefs",
        {
            "site_id": opp.get("site_id", ""),
            "opportunity_id": params.opportunity_id,
            "working_title": opp.get("primary_query", "").capitalize(),
            "target_language": target_language,
            "target_audience": profile.get("business_description", ""),
            "search_intent": opp.get("intent", "informational"),
            "primary_query": opp.get("primary_query", ""),
            "secondary_queries": opp.get("supporting_queries", []),
            "outline": outline,
            "cta_goal": profile.get("cta_default", ""),
            "internal_link_targets": [],
            "differentiation_notes": "",
            "image_requirements": image_requirements,
            "status": "brief_ready",
        },
    )

    await ctx.store.update("opportunities", params.opportunity_id, {"status": "brief_ready"})

    # find the queue item for this (opportunity, language); if this is the
    # first brief for the opportunity there's already a language-less queue
    # item from discover_opportunities — claim it. Otherwise (a second
    # language for the same opportunity) create a fresh queue item so each
    # language's article is tracked as its own row.
    q_page = await ctx.store.query("queue_items", limit=500)
    claimed = False
    for d in q_page.data:
        if (d.data.get("opportunity_id") == params.opportunity_id
                and not d.data.get("brief_id")
                and d.data.get("target_language", "") in ("", target_language)):
            await ctx.store.update(
                "queue_items", d.id,
                {"lifecycle_status": "brief_ready", "brief_id": brief_doc.id, "target_language": target_language},
            )
            claimed = True
            break
    if not claimed:
        await ctx.store.create(
            "queue_items",
            {
                "site_id": opp.get("site_id", ""),
                "brief_id": brief_doc.id,
                "opportunity_id": params.opportunity_id,
                "target_language": target_language,
                "content_type": "article",
                "lifecycle_status": "brief_ready",
                "assigned_agent": "Webbee",
                "published_url": "",
                "primary_query": opp.get("primary_query", ""),
            },
        )

    return ActionResult.success(
        _to_brief(brief_doc),
        summary=f"Brief created ({target_language}): {brief_doc.data['working_title']}",
        refresh_panels=["queue"],
    )


# ──────────────────────────────────────────────────────────────────────────
# Monthly content calendar — publication grid
# ──────────────────────────────────────────────────────────────────────────

@chat.function(
    "build_content_calendar",
    description=(
        "Build a monthly content calendar for a site: assigns scheduled_date "
        "slots (on the given weekdays, posts_per_week per week) across the "
        "given month to queue items, filling the publication grid. Picks the "
        "highest-priority unscheduled brief_ready+ items automatically if "
        "queue_item_ids is empty."
    ),
    action_type="write",
    chain_callable=True,
    effects=["update:queue_item"],
    event="scheduled",
    data_model=ContentCalendarEntryList,
)
async def build_content_calendar(ctx, params: BuildContentCalendarParams) -> ActionResult:
    """Assign scheduled_date slots across a calendar month to queue items."""
    days_in_month = _calendar.monthrange(params.year, params.month)[1]
    slot_dates = [
        _date(params.year, params.month, d)
        for d in range(1, days_in_month + 1)
        if _date(params.year, params.month, d).isoweekday() in params.weekdays
    ]
    if not slot_dates:
        return ActionResult.error(
            "No publication slots match the given weekdays for that month.", retryable=False)

    if params.queue_item_ids:
        docs = []
        for qid in params.queue_item_ids:
            doc = await ctx.store.get("queue_items", qid)
            if doc:
                docs.append(doc)
    else:
        page = await ctx.store.query("queue_items", where={"site_id": params.site_id}, limit=500)
        candidates = [
            d for d in page.data
            if d.data.get("lifecycle_status") in ("brief_ready", "draft_requested", "draft_ready", "approved")
            and not d.data.get("scheduled_date")
        ]

        # priority order: pull each candidate's linked opportunity score, default 0
        async def _score(d):
            opp_id = d.data.get("opportunity_id", "")
            if not opp_id:
                return 0.0
            opp_doc = await ctx.store.get("opportunities", opp_id)
            return opp_doc.data.get("total_priority_score", 0.0) if opp_doc else 0.0

        scored = [(await _score(d), d) for d in candidates]
        scored.sort(key=lambda t: t[0], reverse=True)
        docs = [d for _, d in scored]

    slots_needed = params.posts_per_week * (len(slot_dates) // 7 + (1 if len(slot_dates) % 7 else 0))
    # simplest fair distribution: walk slot_dates in order, cycling once posts_per_week per week is reached
    scheduled = []
    week_counts: dict[int, int] = {}
    slot_idx = 0
    for doc in docs:
        placed = False
        while slot_idx < len(slot_dates):
            d = slot_dates[slot_idx]
            week_no = d.isocalendar()[1]
            if week_counts.get(week_no, 0) < params.posts_per_week:
                week_counts[week_no] = week_counts.get(week_no, 0) + 1
                await ctx.store.update("queue_items", doc.id, {"scheduled_date": d.isoformat()})
                doc.data["scheduled_date"] = d.isoformat()
                scheduled.append(_to_calendar_entry(doc))
                slot_idx += 1
                placed = True
                break
            slot_idx += 1
        if not placed:
            break  # ran out of slots for this month

    return ActionResult.success(
        ContentCalendarEntryList(items=scheduled, total=len(scheduled)),
        summary=f"Scheduled {len(scheduled)} item(s) across {params.year}-{params.month:02d}.",
        refresh_panels=["queue"],
    )


@chat.function(
    "get_content_calendar",
    description=(
        "Read the publication grid: queue items that already have a "
        "scheduled_date, optionally filtered by site/year/month. Use this "
        "to see what is due to publish and when."
    ),
    action_type="read",
    data_model=ContentCalendarEntryList,
)
async def get_content_calendar(ctx, params: GetContentCalendarParams) -> ActionResult:
    """List queue items that have a scheduled_date, as calendar entries."""
    page = await ctx.store.query("queue_items", order_by="-created_at", limit=500)
    items = [d for d in page.data if d.data.get("scheduled_date")]
    if params.site_id:
        items = [d for d in items if d.data.get("site_id") == params.site_id]
    if params.year:
        prefix = f"{params.year:04d}-"
        items = [d for d in items if d.data.get("scheduled_date", "").startswith(prefix)]
    if params.month:
        tag = f"-{params.month:02d}-"
        items = [d for d in items if tag in d.data.get("scheduled_date", "")]
    items.sort(key=lambda d: d.data.get("scheduled_date", ""))
    entries = [_to_calendar_entry(d) for d in items]
    return ActionResult.success(
        ContentCalendarEntryList(items=entries, total=len(entries)),
        summary=f"{len(entries)} scheduled item(s).",
    )


# ──────────────────────────────────────────────────────────────────────────
# Article Writer pipeline linkage — pass-through handoff, no direct IPC
# ──────────────────────────────────────────────────────────────────────────

@chat.function(
    "link_external_article",
    description=(
        "Record the Article Writer (imperal-article-writer-extension) "
        "project_id/article_id once you have created them there for this "
        "queue item, so this app's queue keeps a two-way reference between "
        "the brief and the actual article being written."
    ),
    action_type="write",
    chain_callable=True,
    effects=["update:queue_item"],
    event="linked",
    id_projection="queue_item_id",
    data_model=QueueItem,
)
async def link_external_article(ctx, params: LinkExternalArticleParams) -> ActionResult:
    """Store Article Writer's project_id/article_id against a queue item."""
    doc = await ctx.store.get("queue_items", params.queue_item_id)
    if not doc:
        return ActionResult.error(f"Queue item '{params.queue_item_id}' not found.", retryable=False)
    update = {}
    if params.external_project_id:
        update["external_project_id"] = params.external_project_id
    if params.external_article_id:
        update["external_article_id"] = params.external_article_id
    if not update:
        return ActionResult.error("Provide external_project_id and/or external_article_id.", retryable=False)
    await ctx.store.update("queue_items", params.queue_item_id, update)
    doc.data.update(update)
    return ActionResult.success(
        _to_queue_item(doc),
        summary="Linked to Article Writer.",
        refresh_panels=["queue"],
    )


@chat.function(
    "build_writer_brief",
    description=(
        "Assemble an existing article brief into the exact shape Article "
        "Writer (imperal-article-writer-extension) needs for "
        "create_project/create_article/generate_article — title, keyword, "
        "outline, audience, CTA. Pass the returned fields straight into "
        "that app's tools; there is no direct extension-to-extension call, "
        "so Webbee relays this data in the same chat turn."
    ),
    action_type="read",
    data_model=WriterBrief,
)
async def build_writer_brief(ctx, params: BuildWriterBriefParams) -> ActionResult:
    """Read one article brief and reshape it for Article Writer's tool inputs."""
    brief_doc = await ctx.store.get("article_briefs", params.brief_id)
    if not brief_doc:
        return ActionResult.error(f"Brief '{params.brief_id}' not found.", retryable=False)
    brief = brief_doc.data

    profile_page = await ctx.store.query("site_profiles", where={"site_id": brief.get("site_id", "")}, limit=1)
    profile = profile_page.data[0].data if profile_page.data else {}

    q_page = await ctx.store.query("queue_items", limit=500)
    queue_item_id = ""
    for d in q_page.data:
        if d.data.get("brief_id") == params.brief_id:
            queue_item_id = d.id
            break

    body_lines = [
        f"# {brief.get('working_title', '')}",
        "",
        f"Target audience: {brief.get('target_audience', '')}",
        f"Search intent: {brief.get('search_intent', '')}",
        "",
        "## Outline",
    ] + [f"- {line}" for line in brief.get("outline", [])] + [
        "",
        f"CTA goal: {brief.get('cta_goal', '')}",
    ]

    payload = WriterBrief(
        id=brief_doc.id,
        title=brief.get("working_title", ""),
        body="\n".join(body_lines),
        site_id=brief.get("site_id", ""),
        queue_item_id=queue_item_id,
        brief_id=params.brief_id,
        suggested_project_name=profile.get("brand_name") or brief.get("site_id", ""),
        site_url=profile.get("domain", ""),
        target_keyword=brief.get("primary_query", ""),
        working_title=brief.get("working_title", ""),
        target_audience=brief.get("target_audience", ""),
        secondary_queries=brief.get("secondary_queries", []),
        outline=brief.get("outline", []),
        cta_goal=brief.get("cta_goal", ""),
        internal_link_targets=brief.get("internal_link_targets", []),
        differentiation_notes=brief.get("differentiation_notes", ""),
    )
    return ActionResult.success(payload, summary=f"Writer brief assembled for '{payload.working_title}'.")


@chat.function(
    "list_briefs",
    description="List article briefs, optionally filtered by site.",
    action_type="read",
    data_model=ArticleBriefList,
)
async def list_briefs(ctx, params: ListBriefsParams) -> ActionResult:
    """Return article briefs, optionally filtered by site."""
    page = await ctx.store.query("article_briefs", order_by="-created_at", limit=200)
    items = list(page.data)
    if params.site_id:
        items = [d for d in items if d.data.get("site_id") == params.site_id]
    items = items[: params.limit]
    briefs = [_to_brief(d) for d in items]
    return ActionResult.success(
        ArticleBriefList(items=briefs, total=len(briefs)),
        summary=f"{len(briefs)} brief(s).",
    )


# ──────────────────────────────────────────────────────────────────────────
# Editorial queue
# ──────────────────────────────────────────────────────────────────────────

@chat.function(
    "list_queue",
    description=(
        "List the editorial queue — opportunities and briefs tracked through "
        "their lifecycle from idea to published. Optionally filter by site "
        "and/or lifecycle status."
    ),
    action_type="read",
    data_model=QueueItemList,
)
async def list_queue(ctx, params: ListQueueParams) -> ActionResult:
    """Return editorial queue items, optionally filtered by site and/or
    lifecycle status."""
    page = await ctx.store.query("queue_items", order_by="-created_at", limit=500)
    items = list(page.data)
    if params.site_id:
        items = [d for d in items if d.data.get("site_id") == params.site_id]
    if params.lifecycle_status:
        items = [d for d in items if d.data.get("lifecycle_status") == params.lifecycle_status]
    items = items[: params.limit]
    queue = [_to_queue_item(d) for d in items]
    return ActionResult.success(
        QueueItemList(items=queue, total=len(queue)),
        summary=f"{len(queue)} queue item(s).",
    )


@chat.function(
    "update_queue_status",
    description=(
        "Move a queue item to a new lifecycle status "
        "(idea|brief_ready|draft_requested|draft_ready|approved|published). "
        "Set published_url when marking as published."
    ),
    action_type="write",
    chain_callable=True,
    effects=["update:queue_item"],
    event="updated",
    id_projection="queue_item_id",
    data_model=QueueItem,
)
async def update_queue_status(ctx, params: UpdateQueueStatusParams) -> ActionResult:
    """Move one queue item to a new lifecycle status."""
    doc = await ctx.store.get("queue_items", params.queue_item_id)
    if not doc:
        return ActionResult.error(f"Queue item '{params.queue_item_id}' not found.", retryable=False)
    update = {"lifecycle_status": params.lifecycle_status}
    if params.published_url:
        update["published_url"] = params.published_url
    await ctx.store.update("queue_items", params.queue_item_id, update)
    doc.data.update(update)
    return ActionResult.success(
        _to_queue_item(doc),
        summary=f"Queue item moved to '{params.lifecycle_status}'.",
        refresh_panels=["queue"],
    )



_SITES_CACHE_MARKER = "quick_add_sites"  # value of the "kind" field identifying the one cache row


async def _cache_connected_sites(ctx, sites: list[dict], problems: list[dict]) -> None:
    """Persist the last-known-good Quick Add source list to this app's own
    store. Needed because a real-user chat/tool call to list_connected_sites
    reaches the target extension with a normal, populated user context, while
    the SAME ctx.extensions.call made from inside a *panel render* has been
    observed to reach it with an empty user context (kernel-side — a
    ContextFactory.create_child gap during panel rendering, not something
    fixable from an extension's own code). Caching what the working call path
    already proved lets the panel show real data without depending on the
    panel-render call path at all.

    Looked up by a "kind" marker via query(where=...), NOT a fixed doc id:
    store.create always server-assigns its own id (a caller-supplied id is
    not honoured), so a fixed-id get() would never find the row it wrote.
    """
    payload = {"kind": _SITES_CACHE_MARKER, "sites": sites, "problems": problems}
    page = await ctx.store.query("connected_sites_cache", where={"kind": _SITES_CACHE_MARKER}, limit=1)
    if page.data:
        await ctx.store.update("connected_sites_cache", page.data[0].id, payload)
    else:
        await ctx.store.create("connected_sites_cache", payload)


async def _read_cached_connected_sites(ctx) -> tuple[list[dict], list[dict], bool]:
    """Read the cached Quick Add source list. Returns (sites, problems, has_cache)."""
    page = await ctx.store.query("connected_sites_cache", where={"kind": _SITES_CACHE_MARKER}, limit=1)
    if not page.data:
        return [], [], False
    doc = page.data[0]
    return doc.data.get("sites", []), doc.data.get("problems", []), True


@chat.function(
    "list_connected_sites",
    description=(
        "List the sites already connected in other apps (WordPress Hub today, "
        "any future site provider) that Quick Add offers as one-click site "
        "profile candidates, flagging which ones are already registered here. "
        "Also the diagnostic for an empty Quick Add list: it reports whether "
        "a provider could not be reached and why."
    ),
    action_type="read",
    data_model=ConnectedSiteList,
)
async def list_connected_sites(ctx, params: ListConnectedSitesParams) -> ActionResult:
    """Read connected sites from every registered site-provider extension,
    and cache the result so the panel can show it reliably (see
    _cache_connected_sites)."""
    sites, problems = await fetch_connected_sites(ctx)
    await _cache_connected_sites(ctx, sites, problems)

    page = await ctx.store.query("site_profiles", limit=500)
    existing_site_ids = {
        d.data.get("site_id") for d in page.data if d.data.get("site_id")
    }

    items = [
        ConnectedSite(
            id=s.get("site_id", ""),
            title=s.get("name") or s.get("site_id", ""),
            kind="connected_site",
            site_id=s.get("site_id", ""),
            url=s.get("url", ""),
            status=s.get("status", ""),
            provider=s.get("provider", ""),
            already_tracked=s.get("site_id") in existing_site_ids,
        )
        for s in sites[: max(1, min(params.limit, 100))]
    ]

    if problems:
        detail = "; ".join(f"{p['provider']}: {p['reason']}" for p in problems)
        return ActionResult.success(
            ConnectedSiteList(items=items),
            summary=(
                f"{len(items)} connected site(s) readable. "
                f"Could not read from — {detail}"
            ),
            refresh_panels=["sources"],
        )

    fresh = sum(1 for i in items if not i.already_tracked)
    return ActionResult.success(
        ConnectedSiteList(items=items),
        summary=(
            f"{len(items)} connected site(s); {fresh} without a site profile yet."
        ),
        refresh_panels=["sources"],
    )


_STATUS_COLOR = {
    "idea": "gray",
    "brief_ready": "blue",
    "draft_requested": "yellow",
    "draft_ready": "yellow",
    "approved": "green",
    "published": "green",
}

_STATUS_LABEL = {
    "idea": "Idea",
    "brief_ready": "Brief ready",
    "draft_requested": "Draft requested",
    "draft_ready": "Draft ready",
    "approved": "Approved",
    "published": "Published",
}


async def _fetch_queue(ctx, site_id: str = "") -> list:
    page = await ctx.store.query("queue_items", order_by="-created_at", limit=500)
    items = list(page.data)
    if site_id:
        items = [d for d in items if d.data.get("site_id") == site_id]
    return items


@ext.panel(
    "queue",
    slot="left",
    title="Editorial Queue",
    icon="📋",
    default_width=300,
    min_width=240,
    max_width=460,
)
async def queue_panel(ctx, site_id: str = "", **kwargs) -> object:
    docs = await _fetch_queue(ctx, site_id)

    site_page = await ctx.store.query("site_profiles", limit=50)
    site_options = [{"value": "", "label": "All sites"}] + [
        {"value": d.data.get("site_id", d.id), "label": d.data.get("brand_name") or d.data.get("site_id", d.id)}
        for d in site_page.data
    ]

    filter_row = ui.Select(
        options=site_options,
        value=site_id,
        placeholder="Filter by site",
        param_name="site_id",
        on_change=ui.Call("__panel__queue"),
    )

    # Explicit button to open the Sites panel (right slot). The right slot's
    # own auto-population at session init is not guaranteed the way the left
    # slot's is, so Quick Add there could otherwise be reachable only by luck
    # of panel-discovery timing. This button makes it reachable with one
    # deliberate click, every time, from a panel that IS always on screen.
    sites_button = ui.Button(
        "🌐 Sites — manage & Quick Add", variant="secondary", size="sm", full_width=True,
        on_click=ui.Call("__panel__sources"),
    )

    if not docs:
        body = ui.Stack(
            direction="v",
            gap=3,
            children=[
                sites_button,
                filter_row,
                ui.Empty(
                    message="No queue items yet — discover opportunities from chat to get started.",
                    icon="📋",
                ),
            ],
        )
        return body

    items = []
    for d in docs:
        data = d.data
        status = data.get("lifecycle_status", "idea")
        items.append(
            ui.ListItem(
                id=d.id,
                title=data.get("primary_query") or data.get("working_title") or d.id,
                subtitle=data.get("site_id", ""),
                badge=ui.Badge(_STATUS_LABEL.get(status, status), color=_STATUS_COLOR.get(status, "gray")),
                on_click=ui.Call("__panel__brief", queue_item_id=d.id),
            )
        )

    root = ui.Stack(
        direction="v",
        gap=3,
        children=[
            sites_button,
            filter_row,
            ui.List(items=items, searchable=True),
        ],
    )
    return root


def _outline_markdown(outline: list) -> str:
    if not outline:
        return "_No outline yet._"
    return "\n".join(f"- {line}" for line in outline)


def _image_reqs_markdown(reqs: list) -> str:
    if not reqs:
        return "_No image requirements yet._"
    return "\n".join(f"- {r}" for r in reqs)


@ext.panel(
    "brief",
    slot="center",
    title="Opportunity / Brief",
    icon="📝",
    center_overlay=True,
)
async def brief_panel(ctx, queue_item_id: str = "", **kwargs) -> object:
    if not queue_item_id:
        return ui.Empty(message="Select a queue item to see its detail.", icon="📝")

    q_doc = await ctx.store.get("queue_items", queue_item_id)
    if not q_doc:
        return ui.Error(message="Queue item not found — it may have been deleted.")
    q = q_doc.data

    opp = {}
    if q.get("opportunity_id"):
        opp_doc = await ctx.store.get("opportunities", q["opportunity_id"])
        if opp_doc:
            opp = opp_doc.data

    brief = {}
    if q.get("brief_id"):
        brief_doc = await ctx.store.get("article_briefs", q["brief_id"])
        if brief_doc:
            brief = brief_doc.data

    status = q.get("lifecycle_status", "idea")
    title = brief.get("working_title") or opp.get("primary_query") or q.get("primary_query") or "Untitled"

    header = ui.Header(title, level=2, subtitle=f"{q.get('site_id', '')} · {_STATUS_LABEL.get(status, status)}")

    kv_items = [
        {"key": "Status", "value": _STATUS_LABEL.get(status, status)},
        {"key": "Source", "value": opp.get("source", "—")},
        {"key": "Intent", "value": opp.get("intent") or brief.get("search_intent", "—")},
        {"key": "Priority score", "value": str(opp.get("total_priority_score", "—"))},
        {"key": "Impressions", "value": str(opp.get("impressions", "—"))},
        {"key": "Avg. position", "value": str(opp.get("avg_position", "—"))},
    ]
    overview_card = ui.Card(title="Overview", content=ui.KeyValue(columns=2, items=kv_items))

    sections = [header, overview_card]

    if brief:
        sections.append(
            ui.Card(
                title="Outline",
                content=ui.Markdown(content=_outline_markdown(brief.get("outline", []))),
            )
        )
        sections.append(
            ui.Card(
                title="Image requirements",
                subtitle="Handed off to the Image / Media app",
                content=ui.Markdown(content=_image_reqs_markdown(brief.get("image_requirements", []))),
            )
        )
        cta = brief.get("cta_goal", "")
        if cta:
            sections.append(ui.Text(f"CTA goal: {cta}", variant="caption"))
    else:
        sections.append(
            ui.Card(
                title="Brief",
                content=ui.Text(
                    "No brief yet. Ask Webbee to generate one for this opportunity "
                    "(create_brief)."
                ),
            )
        )

    # Lifecycle status transitions — simple linear buttons, always available
    # so the user (or Webbee on their behalf) can move an item forward or
    # correct a status without needing chat.
    transitions = []
    order = ["idea", "brief_ready", "draft_requested", "draft_ready", "approved", "published"]
    if status in order:
        idx = order.index(status)
        if idx + 1 < len(order):
            next_status = order[idx + 1]
            transitions.append(
                ui.Button(
                    f"Move to {_STATUS_LABEL[next_status]}",
                    variant="primary",
                    on_click=ui.Call("update_queue_status", queue_item_id=queue_item_id, lifecycle_status=next_status),
                )
            )
    sections.append(ui.Row(gap=2, children=transitions or [ui.Text("Published — end of lifecycle.", variant="caption")]))

    return ui.Stack(direction="v", gap=4, children=sections)


def _quick_add_block(connected_sites: list[dict], existing_site_ids: set[str],
                     problems: list[dict] | None = None, has_cache: bool = True) -> object:
    """Quick Add: one real button per connected site not yet registered as a
    site profile here, pre-filling create_site_profile's site_id/domain/
    brand_name via ui.Call so a profile can be started in one click from
    whatever is already connected in WordPress Hub (or any future site
    provider in SITE_PROVIDER_APP_IDS) -- no retyping the domain, no chat.

    ALWAYS returns a card, never None: if there is nothing to offer, the card
    says WHY (no provider reachable / nothing connected / all already added /
    not loaded yet) and carries a Refresh button that runs the REAL read
    (list_connected_sites) and re-renders. A silently missing card is
    unfixable from the UI, which is exactly the failure mode this replaces.
    """
    problems = problems or []
    candidates = [s for s in connected_sites if s.get("site_id") not in existing_site_ids]

    refresh = ui.Button(
        "Refresh", variant="secondary", size="sm", icon="RefreshCw",
        on_click=ui.Call("list_connected_sites"),
    )

    if not has_cache and not connected_sites and not problems:
        return ui.Card(
            title="Quick Add — from connected sites",
            content=ui.Stack(direction="v", gap=2, children=[
                ui.Text(
                    "Not loaded yet — click Refresh to pull sites connected "
                    "in WordPress Hub (or any future site provider).",
                    variant="caption",
                ),
                refresh,
            ]),
        )

    if candidates:
        body: list = [
            ui.Text(
                f"{len(candidates)} connected site(s) without a site profile yet — "
                "click one to register it.",
                variant="caption",
            ),
            ui.Stack(direction="h", gap=1, wrap=True, children=[
                ui.Button(
                    s.get("name") or s["site_id"],
                    variant="secondary", size="sm", icon="Plus",
                    on_click=ui.Call(
                        "create_site_profile",
                        site_id=s["site_id"],
                        domain=(s.get("url", "").replace("https://", "").replace("http://", "").rstrip("/")
                                or s["site_id"]),
                        brand_name=s.get("name") or "",
                    ),
                )
                for s in candidates
            ]),
        ]
    elif problems:
        body = [
            ui.Text(
                "Could not read connected sites from: "
                + ", ".join(p["provider"] for p in problems),
                variant="body",
            ),
            ui.Text(problems[0]["reason"], variant="caption"),
        ]
    elif connected_sites:
        body = [ui.Text(
            "Every connected site already has a site profile.",
            variant="caption",
        )]
    else:
        body = [ui.Text(
            "No sites connected yet — connect one in WordPress Hub and it "
            "will appear here.",
            variant="caption",
        )]

    return ui.Card(
        title="Quick Add — from connected sites",
        content=ui.Stack(direction="v", gap=2, children=body + [refresh]),
    )


@ext.panel(
    "sources",
    slot="right",
    title="Sites",
    icon="🌐",
    default_width=280,
    min_width=220,
    max_width=420,
)
async def sources_panel(ctx, **kwargs) -> object:
    """List of managed site profiles. Always carries its own 'New site'
    ui.Form so the very first (and every subsequent) site profile can be
    created directly from the panel -- no chat message required. Also
    offers Quick Add buttons for any site already connected elsewhere
    (WordPress Hub today, more providers later via SITE_PROVIDER_APP_IDS)."""
    page = await ctx.store.query("site_profiles", limit=50)
    existing_site_ids = {d.data.get("site_id") for d in page.data if d.data.get("site_id")}
    # Read from cache, NOT a live ctx.extensions.call here: a panel render
    # runs in a context where inter-extension IPC has been observed to fail
    # (empty user context downstream), while the same call from a real
    # chat/tool invocation (list_connected_sites itself) works and refreshes
    # this cache. See _cache_connected_sites for the full explanation.
    connected_sites, site_problems, has_cache = await _read_cached_connected_sites(ctx)
    quick_add = _quick_add_block(connected_sites, existing_site_ids, site_problems, has_cache)

    new_site_form = ui.Card(
        title="New site",
        content=ui.Form(
            action="create_site_profile",
            submit_label="Create site profile",
            children=[
                ui.Input(param_name="site_id", placeholder="Site id, e.g. g4s.md"),
                ui.Input(param_name="domain", placeholder="Domain, e.g. g4s.md"),
                ui.Input(param_name="brand_name", placeholder="Brand name (optional)"),
                ui.TextArea(param_name="business_description",
                            placeholder="What the business does (optional)", rows=2),
                ui.TagInput(param_name="target_languages",
                            placeholder="Add a language code and press Enter, e.g. ru"),
                ui.TagInput(param_name="content_categories",
                            placeholder="Add a content category and press Enter"),
                ui.Input(param_name="cta_default", placeholder="Default CTA (optional)"),
            ],
        ),
    )

    if not page.data:
        return ui.Stack(
            direction="v", gap=3,
            children=[
                ui.Empty(
                    message="No site profiles yet — create one below.",
                    icon="🌐",
                ),
                quick_add,
                new_site_form,
            ],
        )

    cards = []
    for d in page.data:
        data = d.data
        cards.append(
            ui.Card(
                title=data.get("brand_name") or data.get("site_id", d.id),
                subtitle=data.get("domain", ""),
                content=ui.KeyValue(
                    columns=1,
                    items=[
                        {"key": "Languages", "value": ", ".join(data.get("target_languages", [])) or "—"},
                        {"key": "Categories", "value": ", ".join(data.get("content_categories", [])) or "—"},
                        {"key": "Default CTA", "value": data.get("cta_default", "—")},
                    ],
                ),
            )
        )

    return ui.Stack(
        direction="v", gap=3,
        children=cards + [quick_add, new_site_form],
    )
