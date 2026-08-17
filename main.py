"""Content Strategy app — decides what content to create, why, for which
site/audience, and hands off a structured brief downstream to article
writing and the future Image/Media app.

Boundaries (see notes "System architecture — Content Strategy app MVP"):
- does NOT publish to WordPress (wordpress-hub's job)
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
    BuildContentCalendarParams, BuildMediaBriefHandoffParams, BuildWriterBriefParams, CreateBriefParams,
    RefreshBriefVisualGuidanceParams,
    UpdateBriefTitleParams,
    CreateSiteProfileParams, DiscoverOpportunitiesParams,
    DiscoverOpportunitiesFromSearchConsoleParams, QuerySignal,
    GenerateStrategicTopicsParams,
    GetContentCalendarParams, LinkExternalArticleParams,
    ListBriefsParams, ListOpportunitiesParams, ListQueueParams,
    ListSiteProfilesParams, UpdateQueueStatusParams, UpdateSiteProfileParams,
    RecordEditorialSignoffParams,
    ArticleBrief, ArticleBriefList, MediaBriefHandoff,
    ContentCalendarEntry, ContentCalendarEntryList,
    Opportunity, OpportunityList,
    QueueItem, QueueItemList,
    SiteProfile, SiteProfileList,
    ConnectedSite, ConnectedSiteList, ListConnectedSitesParams,
    TopicCluster,
    WriterBrief,
    SiteCompetitorProfile, SiteCompetitorProfileList,
    AddSiteCompetitorParams, ListSiteCompetitorsParams,
    ContentAuditReport, RunContentAuditParams, GetContentAuditParams,
    ContentPerformanceSignal, TrackContentDecayParams, DecayingContentItem,
    RecordKpiSnapshotParams, KpiSnapshot, KpiTrendDelta, KpiDashboardReport, GetKpiDashboardParams,
    CreateOutreachTargetParams, UpdateOutreachStatusParams, OutreachTarget, OutreachTargetList,
    ListOutreachTargetsParams, LinkBuildingReport, GetLinkBuildingReportParams,
    ContentDecayReport, GetContentDecayParams,
    CannibalizationFinding, CannibalizationFindingList, CheckCannibalizationParams,
    ResolveCannibalizationParams,
    PurgePipelineDataParams, PurgeSitePipelineDataParams, PurgeResult,
    ContentAuthor, ContentAuthorList, CreateContentAuthorParams, ListContentAuthorsParams,
)
from link_policy import language_priority, resolve_external_source, resolve_key_action_page
from converters import (
    guess_intent, guess_funnel_stage, cluster_label, priority_score,
    decide_image_text_policy as _decide_image_text_policy,
    detect_language_fallback as _detect_language_fallback,
    to_opportunity as _to_opportunity,
    to_brief as _to_brief,
    to_calendar_entry as _to_calendar_entry,
    to_queue_item as _to_queue_item,
    to_site_profile as _to_site_profile,
    to_site_competitor as _to_site_competitor,
    to_outreach_target as _to_outreach_target,
    to_content_author as _to_content_author,
    word_count as _word_count,
    top_terms as _top_terms,
    term_overlap_score as _term_overlap_score,
    find_cannibalization_pairs as _find_cannibalization_pairs,
    resolve_category as _resolve_category,
    generate_strategic_candidates as _generate_strategic_candidates,
)
import time as _time

ext = Extension(
    "content-strategy-app",
    version="0.7.0",
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


@ext.expose("register_project", action_type="write")
async def expose_register_project(ctx, site_id: str = "", domain: str = "",
                                   name: str = "", **kwargs) -> dict:
    """Inter-extension IPC surface (ctx.extensions.call) for Sites Registry:
    called automatically whenever a site is registered there (manually,
    via WordPress Hub's connect, or via a registry sync/backfill), so it
    shows up here as an existing site profile without the user re-adding
    it by hand.

    Idempotent by design -- a site_id that already has a profile is left
    completely untouched (never overwritten by a bare domain-only fan-out
    call); only a genuinely new site_id gets a fresh, minimal profile.
    Returns a plain dict (never surfaced to the LLM/user directly).
    """
    sid = (site_id or domain).strip()
    if not sid:
        return {"ok": False, "error": "site_id or domain is required.", "retryable": False}
    existing = await ctx.store.query("site_profiles", where={"site_id": sid}, limit=1)
    if existing.data:
        return {"ok": True, "site_id": sid, "created": False}
    await ctx.store.create(
        "site_profiles",
        {
            "site_id": sid,
            "domain": domain or sid,
            "brand_name": name or sid,
            "business_description": "",
            "business_description_i18n": {},
            "target_languages": [],
            "content_categories": [],
            "cta_default": "",
            "cta_default_i18n": {},
            "external_sources_i18n": {},
            "approved_visual_guidance": {},
            "requires_named_author": False,
        },
    )
    return {"ok": True, "site_id": sid, "created": True}


# ──────────────────────────────────────────────────────────────────────────
# Cross-app site discovery for Quick Add -- not just WordPress, on purpose.
# ──────────────────────────────────────────────────────────────────────────
# Registry of app_ids that expose a "list_connected_sites" IPC method
# returning [{"site_id", "name", "url", "status"}, ...]. Any future site
# provider (Shopify, Webflow, a plain-domain connector, ...) is added here
# and Quick Add picks it up automatically -- no panel code changes needed.
SITE_PROVIDER_APP_IDS: list[str] = ["wordpress-hub"]

# Marketplace display_name for each provider app_id -- shown under a
# connected site's bare domain in Quick Add so it's clear WHERE that site
# comes from, using the same name the user sees when browsing the
# Marketplace (confirmed via marketplace.list_my_installed), not the raw
# app_id slug.
PROVIDER_DISPLAY_NAMES: dict[str, str] = {
    "wordpress-hub": "WordPress Hub",
    "sites-registry": "Sites Registry",
}


def provider_display_name(app_id: str) -> str:
    return PROVIDER_DISPLAY_NAMES.get(app_id, app_id)


def _bare_domain(url: str) -> str:
    """Strip a leading scheme (https://, http://) for display -- Quick Add
    shows the domain a human recognises, not the URL a browser needs."""
    return (url or "").strip().split("://", 1)[-1].rstrip("/") or url


def _display_domain(value: str) -> str:
    """Reduce any URL/domain/site_id string down to a bare 'example.com'
    form: no scheme, no leading www., no path/query/fragment, no port,
    no trailing slash. Used for the sidebar project list, which must show
    only the clean domain -- nothing else."""
    host = (value or "").strip()
    host = host.split("://", 1)[-1]  # drop scheme
    host = host.split("/", 1)[0]  # drop path
    host = host.split("?", 1)[0].split("#", 1)[0]  # drop query/fragment (belt & suspenders)
    host = host.split(":", 1)[0]  # drop port
    if host.startswith("www."):
        host = host[4:]
    return host or (value or "")


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


async def _resolve_wp_site_id(ctx, site_id: str) -> str:
    """Translate this app's domain-form site_id ('climtec.md') into the WP
    Site Connector's own slug-form id ('climtec-md') before any IPC call.

    Without this, run_content_audit/check_keyword_cannibalization silently
    got zero posts back for every real site: list_posts_full does an exact
    match against WP Site Connector's own record id, which is never the
    bare domain. Falls back to the naive dot-to-hyphen slug (matching
    wp_client.site_id_from_url's own scheme) if the connector lookup fails,
    so a transient IPC error degrades gracefully instead of hard-failing.
    """
    try:
        rows = await ctx.extensions.call("wordpress-hub", "list_connected_sites")
    except Exception:
        rows = []
    for r in rows or []:
        if _canonical_site_id(r) == site_id:
            return r.get("site_id", site_id)
    return site_id.strip().lower().replace(".", "-")


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

def _normalize_approved_visual_guidance(guidance: dict[str, object]) -> dict[str, object]:
    """Keep only the non-personal, approved Brand-handoff contract.

    Content Strategy cannot independently verify Brand tenant ACL/audit records, so
    it fail-closes on the handoff's immutable approval basis instead of storing an
    arbitrary visual prompt or any personal/asset data.
    """
    if not guidance:
        return {}
    required = ("profile_id", "profile_revision", "vbs_id", "vbs_revision", "snapshot_hash")
    missing = [field for field in required if not guidance.get(field)]
    if missing:
        raise ValueError("Approved visual guidance is missing required approval basis: " + ", ".join(missing))
    return {
        "profile_id": str(guidance["profile_id"]),
        "profile_revision": int(guidance["profile_revision"]),
        "vbs_id": str(guidance["vbs_id"]),
        "vbs_revision": int(guidance["vbs_revision"]),
        "snapshot_hash": str(guidance["snapshot_hash"]),
        "visual_intent": str(guidance.get("visual_intent", "")),
        "style_direction": str(guidance.get("style_direction") or guidance.get("art_direction", "")),
        "prohibited_patterns": list(guidance.get("prohibited_patterns", [])),
        "provider_policy": "third_party_only_unless_technical_failure",
        "generation_boundary": "No generation is performed by this handoff.",
    }


_APPROVED_VISUAL_BASIS_FIELDS = ("profile_id", "profile_revision", "vbs_id", "vbs_revision", "snapshot_hash")


def _approved_visual_baseline_is_current(
    brief_guidance: dict[str, object], current_guidance: dict[str, object]
) -> bool:
    """Compare the immutable approved-baseline basis; missing data fails closed."""
    return bool(brief_guidance and current_guidance) and all(
        brief_guidance.get(field) and current_guidance.get(field)
        and brief_guidance[field] == current_guidance[field]
        for field in _APPROVED_VISUAL_BASIS_FIELDS
    )


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
    try:
        approved_visual_guidance = _normalize_approved_visual_guidance(params.approved_visual_guidance)
    except (TypeError, ValueError) as exc:
        return ActionResult.error(str(exc), retryable=False)
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
            "business_description_i18n": params.business_description_i18n,
            "target_languages": params.target_languages,
            "content_categories": params.content_categories,
            "cta_default": params.cta_default,
            "cta_default_i18n": params.cta_default_i18n,
            "external_sources_i18n": params.external_sources_i18n,
            "approved_visual_guidance": approved_visual_guidance,
            "requires_named_author": params.requires_named_author,
        },
    )
    return ActionResult.success(
        _to_site_profile(doc),
        summary=f"Site profile '{params.site_id}' created.",
        refresh_panels=["sources", "queue", "brief"],
    )


@chat.function(
    "update_site_profile",
    description=(
        "Update selected fields of an existing site profile -- domain, brand, "
        "business description (incl. per-language overrides), target "
        "languages, content categories, or default CTA (incl. per-language "
        "overrides). Only given fields change. Use this to refresh a profile "
        "after re-running Brand Strategy Hub's build_content_strategy_handoff "
        "-- create_site_profile refuses to run again once a site_id exists."
    ),
    action_type="write",
    chain_callable=True,
    effects=["update:site_profile"],
    event="updated",
    data_model=SiteProfile,
)
async def update_site_profile(ctx, params: UpdateSiteProfileParams) -> ActionResult:
    """Patch an existing site profile with only the given fields."""
    existing = await ctx.store.query("site_profiles", where={"site_id": params.site_id}, limit=1)
    if not existing.data:
        return ActionResult.error(
            f"Site profile '{params.site_id}' not found. Create it first with create_site_profile.",
            retryable=False,
        )
    doc_id = existing.data[0].id
    updates = {}
    for field in ("brand_name", "business_description", "cta_default"):
        value = getattr(params, field)
        if value is not None:
            updates[field] = value
    for field in ("requires_named_author",):
        value = getattr(params, field)
        if value is not None:
            updates[field] = value
    for field in ("business_description_i18n", "target_languages", "content_categories", "cta_default_i18n", "external_sources_i18n", "approved_visual_guidance"):
        value = getattr(params, field)
        if value is not None:
            updates[field] = value
    if "approved_visual_guidance" in updates:
        try:
            updates["approved_visual_guidance"] = _normalize_approved_visual_guidance(updates["approved_visual_guidance"])
        except (TypeError, ValueError) as exc:
            return ActionResult.error(str(exc), retryable=False)
    if not updates:
        return ActionResult.error("No fields given to update.", retryable=False)
    updated = await ctx.store.update("site_profiles", doc_id, updates)
    return ActionResult.success(
        _to_site_profile(updated),
        summary=f"Site profile '{params.site_id}' updated.",
        refresh_panels=["sources"],
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
# Phase 6: active link-building / outreach workflow
#
# Concrete, named outreach targets with a real lifecycle status -- turns
# link-building from passively-observed DataForSEO backlink data into
# trackable human work. This app never sends outreach emails itself and
# never contacts DataForSEO/any connector directly -- statuses are
# recorded here by a human (or by Webbee acting on the human's behalf)
# after the outreach actually happened.
# ──────────────────────────────────────────────────────────────────────────

_OUTREACH_STATUSES = ("prospected", "contacted", "replied", "link_acquired", "declined", "no_response")


@chat.function(
    "add_link_building_target",
    description=(
        "Add a concrete link-building/outreach target for a site -- a named domain (and "
        "optionally a specific page) to pursue for a backlink or mention, with the tactic "
        "being used (guest_post, broken_link, resource_page, linkable_asset, digital_pr, "
        "mention_upgrade, other). Starts at status 'prospected'. Use update_outreach_status "
        "to move it forward as real outreach happens."
    ),
    action_type="write",
    chain_callable=True,
    effects=["create:outreach_target"],
    event="outreach_target_created",
    data_model=OutreachTarget,
)
async def add_link_building_target(ctx, params: CreateOutreachTargetParams) -> ActionResult:
    """Register one outreach target at status 'prospected'."""
    profile_page = await ctx.store.query("site_profiles", where={"site_id": params.site_id}, limit=1)
    if not profile_page.data:
        return ActionResult.error(
            f"No site profile '{params.site_id}'. Create it first with create_site_profile.",
            retryable=False,
        )
    now = ctx.time.now().isoformat() if hasattr(ctx, "time") and hasattr(ctx.time, "now") else ""
    payload = {
        "site_id": params.site_id,
        "target_domain": params.target_domain,
        "target_url": params.target_url,
        "tactic": params.tactic,
        "linked_article_url": params.linked_article_url,
        "contact_name": params.contact_name,
        "contact_email": params.contact_email,
        "status": "prospected",
        "acquired_url": "",
        "notes": params.notes,
        "status_history": [{"status": "prospected", "at": now}],
        "created_at": now,
        "updated_at": now,
    }
    doc = await ctx.store.create("outreach_targets", payload)
    return ActionResult.success(
        _to_outreach_target(doc),
        summary=f"Outreach target '{params.target_domain}' added for '{params.site_id}' (prospected).",
        refresh_panels=["queue"],
    )


@chat.function(
    "update_outreach_status",
    description=(
        "Move a link-building outreach target to a new status: prospected|contacted|replied|"
        "link_acquired|declined|no_response. Pass acquired_url when moving to 'link_acquired' "
        "to record where the backlink actually landed."
    ),
    action_type="write",
    chain_callable=True,
    effects=["update:outreach_target"],
    event="outreach_status_updated",
    data_model=OutreachTarget,
)
async def update_outreach_status(ctx, params: UpdateOutreachStatusParams) -> ActionResult:
    """Update an outreach target's lifecycle status, keeping status_history."""
    if params.status not in _OUTREACH_STATUSES:
        return ActionResult.error(
            f"Invalid status '{params.status}'. Must be one of: {', '.join(_OUTREACH_STATUSES)}.",
            retryable=False,
        )
    doc = await ctx.store.get("outreach_targets", params.outreach_id)
    if not doc:
        return ActionResult.error(f"Outreach target '{params.outreach_id}' not found.", retryable=False)
    now = ctx.time.now().isoformat() if hasattr(ctx, "time") and hasattr(ctx.time, "now") else ""
    history = list(doc.data.get("status_history", []))
    history.append({"status": params.status, "at": now})
    update = {"status": params.status, "status_history": history, "updated_at": now}
    if params.acquired_url:
        update["acquired_url"] = params.acquired_url
    if params.notes:
        update["notes"] = params.notes
    await ctx.store.update("outreach_targets", params.outreach_id, update)
    updated = await ctx.store.get("outreach_targets", params.outreach_id)
    return ActionResult.success(
        _to_outreach_target(updated),
        summary=f"Outreach target moved to '{params.status}'.",
        refresh_panels=["queue"],
    )


@chat.function(
    "list_link_building_targets",
    description="List link-building/outreach targets, optionally filtered by site and/or status.",
    action_type="read",
    data_model=OutreachTargetList,
)
async def list_link_building_targets(ctx, params: ListOutreachTargetsParams) -> ActionResult:
    """Return tracked outreach targets."""
    where = {}
    if params.site_id:
        where["site_id"] = params.site_id
    if params.status:
        where["status"] = params.status
    page = await ctx.store.query("outreach_targets", where=where or None, limit=params.limit)
    items = [_to_outreach_target(d) for d in page.data]
    return ActionResult.success(
        OutreachTargetList(items=items, total=len(items)),
        summary=f"{len(items)} outreach target(s).",
    )


@chat.function(
    "get_link_building_report",
    description=(
        "Read a summary of a site's link-building pipeline: counts by status, conversion "
        "rate (link_acquired / total non-prospected), and the list of currently active "
        "(contacted/replied) targets that need a follow-up."
    ),
    action_type="read",
    data_model=LinkBuildingReport,
)
async def get_link_building_report(ctx, params: GetLinkBuildingReportParams) -> ActionResult:
    """Aggregate outreach targets for one site into a pipeline report."""
    page = await ctx.store.query("outreach_targets", where={"site_id": params.site_id}, limit=500)
    if not page.data:
        return ActionResult.error(
            f"No outreach targets found for '{params.site_id}' yet. Add one with add_link_building_target.",
            retryable=False,
        )
    by_status: dict[str, int] = {}
    needs_doing: list[str] = []
    for d in page.data:
        status = d.data.get("status", "prospected")
        by_status[status] = by_status.get(status, 0) + 1
        if status in ("contacted", "replied"):
            needs_doing.append(f"Follow up: {d.data.get('target_domain', d.id)} ({status})")
    total = len(page.data)
    acquired = by_status.get("link_acquired", 0)
    outreached = total - by_status.get("prospected", 0)
    # reply_rate_pct: of everyone actually contacted, how many responded in any way
    # (replied/link_acquired/declined all count as a reply; no_response does not).
    responded = by_status.get("replied", 0) + by_status.get("link_acquired", 0) + by_status.get("declined", 0)
    reply_rate_pct = round(responded / outreached * 100.0, 1) if outreached else 0.0
    acquisition_rate_pct = round(acquired / total * 100.0, 1) if total else 0.0
    if not needs_doing:
        needs_doing.append("No pending follow-ups -- every non-prospected target has a final outcome.")
    report = LinkBuildingReport(
        id=params.site_id, title=f"Link building — {params.site_id}",
        site_id=params.site_id, total_targets=total, by_status=by_status,
        links_acquired=acquired, reply_rate_pct=reply_rate_pct,
        acquisition_rate_pct=acquisition_rate_pct, needs_doing=needs_doing,
    )
    return ActionResult.success(report, summary=f"{total} outreach target(s), {acquired} link(s) acquired.")


# ──────────────────────────────────────────────────────────────────────────
# E-E-A-T: named authors with real, declared expertise
#
# create_brief refuses to run for a site with requires_named_author=true
# unless author_id resolves to a real ContentAuthor for that site (see the
# gate inside create_brief below). This is what stops YMYL content from
# ever going out unattributed.
# ──────────────────────────────────────────────────────────────────────────

@chat.function(
    "create_content_author",
    description=(
        "Register a real, named content author for a site — name, bio, "
        "credentials, and expertise areas establishing genuine E-E-A-T "
        "(Experience, Expertise, Authoritativeness, Trust). This is not a "
        "generated persona: enter a real person's real credentials. Required "
        "before create_brief for any site with requires_named_author=true."
    ),
    action_type="write",
    chain_callable=True,
    effects=["create:content_author"],
    event="content_author_created",
    data_model=ContentAuthor,
)
async def create_content_author(ctx, params: CreateContentAuthorParams) -> ActionResult:
    """Save one real, named author profile for a site."""
    profile_page = await ctx.store.query("site_profiles", where={"site_id": params.site_id}, limit=1)
    if not profile_page.data:
        return ActionResult.error(
            f"No site profile '{params.site_id}'. Create it first with create_site_profile.",
            retryable=False,
        )
    doc = await ctx.store.create(
        "content_authors",
        {
            "site_id": params.site_id,
            "name": params.name,
            "bio": params.bio,
            "credentials": params.credentials,
            "expertise_areas": params.expertise_areas,
            "author_page_url": params.author_page_url,
            "same_as": params.same_as,
        },
    )
    return ActionResult.success(
        _to_content_author(doc),
        summary=f"Author '{params.name}' registered for site '{params.site_id}'.",
        refresh_panels=["sources"],
    )


@chat.function(
    "list_content_authors",
    description="List registered content authors, optionally filtered by site.",
    action_type="read",
    data_model=ContentAuthorList,
)
async def list_content_authors(ctx, params: ListContentAuthorsParams) -> ActionResult:
    """Return registered authors, optionally filtered by site."""
    where = {"site_id": params.site_id} if params.site_id else None
    page = await ctx.store.query("content_authors", where=where, limit=params.limit)
    items = [_to_content_author(d) for d in page.data]
    return ActionResult.success(
        ContentAuthorList(items=items, total=len(items)),
        summary=f"{len(items)} content author(s).",
    )


# ──────────────────────────────────────────────────────────────────────────
# Mandatory pre-strategy content audit + keyword cannibalization
#
# discover_opportunities REFUSES to run for a site until run_content_audit
# has been run for it at least once (see the gate inside discover_opportunities
# below) -- new topics must never be picked blind to what already exists.
# ──────────────────────────────────────────────────────────────────────────

@chat.function(
    "run_content_audit",
    description=(
        "Deep audit of a site's EXISTING content -- MANDATORY before any new "
        "content strategy work for that site (discover_opportunities refuses "
        "to run without one). Pulls every live post from WordPress Hub (via "
        "inter-extension IPC, not chat), measures word count (flags thin "
        "content under 300 words), checks for a missing excerpt, extracts a "
        "cheap keyword signature per article, and cross-checks every pair for "
        "keyword cannibalization -- two+ articles competing for the same topic "
        "and splitting ranking signal instead of reinforcing one page. Always "
        "produces an explicit 'needs_doing' list, even when nothing is wrong, "
        "so the audit result is visible and actionable in the panel, never a "
        "silent pass."
    ),
    action_type="write",
    chain_callable=True,
    effects=["create:content_audit"],
    event="content_audit_completed",
    data_model=ContentAuditReport,
)
async def run_content_audit(ctx, params: RunContentAuditParams) -> ActionResult:
    """Fetch every existing post for a site via WP Site Connector IPC,
    score it, cross-check for cannibalization, and persist the report."""
    profile_page = await ctx.store.query("site_profiles", where={"site_id": params.site_id}, limit=1)
    if not profile_page.data:
        return ActionResult.error(
            f"Site profile '{params.site_id}' not found. Create it first with create_site_profile.",
            retryable=False,
        )

    wp_site_id = await _resolve_wp_site_id(ctx, params.site_id)
    try:
        raw_posts = await ctx.extensions.call(
            "wordpress-hub", "list_posts_full", site_id=wp_site_id, limit=500,
        )
    except Exception as exc:  # noqa: BLE001 -- surfaced, not swallowed
        return ActionResult.error(
            f"Could not read existing posts from WordPress Hub for '{params.site_id}': "
            f"{type(exc).__name__}: {exc}. Make sure the site is connected there first.",
            retryable=True,
        )

    items = []
    posts_by_lang: dict[str, int] = {}
    thin_urls = []
    missing_excerpt = 0
    covered = set()
    for p in raw_posts or []:
        wc = _word_count(p.get("content", ""))
        terms = _top_terms((p.get("title", "") + " " + p.get("content", "")))
        lang = p.get("lang") or _detect_language_fallback(p.get("title", ""), p.get("content", ""))
        posts_by_lang[lang] = posts_by_lang.get(lang, 0) + 1
        is_thin = wc < 300
        if is_thin:
            thin_urls.append(p.get("link", p.get("slug", "")))
        if not (p.get("excerpt") or "").strip():
            missing_excerpt += 1
        covered.update(terms[:3])
        items.append({
            "id": p.get("id"), "title": p.get("title", ""), "link": p.get("link", ""),
            "slug": p.get("slug", ""), "word_count": wc, "is_thin": is_thin,
            "lang": lang, "top_terms": terms,
        })

    pairs = _find_cannibalization_pairs(items)
    for pair in pairs:
        await ctx.store.create("cannibalization_findings", {
            "site_id": params.site_id, "status": "open", "chosen_action": "", "resolved_at": "", **pair,
        })

    needs_doing: list[str] = []
    if thin_urls:
        needs_doing.append(f"{len(thin_urls)} thin article(s) under 300 words need expanding: "
                           + ", ".join(thin_urls[:5]) + (" ..." if len(thin_urls) > 5 else ""))
    if missing_excerpt:
        needs_doing.append(f"{missing_excerpt} article(s) have no excerpt set")
    if pairs:
        needs_doing.append(f"{len(pairs)} keyword-cannibalization pair(s) found -- "
                           "merge, differentiate, or canonicalize before adding a new article on the same topic")
    if not items:
        needs_doing.append("No existing posts found -- confirm the site is connected and has published content "
                           "before treating this as a clean slate")
    if not needs_doing:
        needs_doing.append("No gaps found in this pass -- safe to proceed to discover_opportunities")

    audited_at = ctx.time.now().isoformat() if hasattr(ctx, "time") and hasattr(ctx.time, "now") else ""

    existing_audit = await ctx.store.query("content_audits", where={"site_id": params.site_id}, limit=1)
    payload = {
        "site_id": params.site_id, "total_posts": len(items), "posts_by_language": posts_by_lang,
        "thin_content_count": len(thin_urls), "thin_content_urls": thin_urls,
        "missing_excerpt_count": missing_excerpt, "cannibalization_pairs_found": len(pairs),
        "covered_topics": sorted(covered), "needs_doing": needs_doing, "audited_at": audited_at,
    }
    if existing_audit.data:
        await ctx.store.update("content_audits", existing_audit.data[0].id, payload)
    else:
        await ctx.store.create("content_audits", payload)

    # Persist each individual item too (not just the aggregate report) so
    # create_brief can look up existing posts on overlapping topics and
    # populate internal_link_targets -- previously that field was always []
    # because no per-post record survived past this function's local scope.
    old_items_page = await ctx.store.query("existing_content_items", where={"site_id": params.site_id}, limit=500)
    for old in old_items_page.data:
        await ctx.store.delete("existing_content_items", old.id)
    for it in items:
        await ctx.store.create("existing_content_items", {"site_id": params.site_id, **it})

    report = ContentAuditReport(id=params.site_id, title=f"Content audit — {params.site_id}", **payload)
    return ActionResult.success(
        report,
        summary=f"Audited {len(items)} post(s) for '{params.site_id}': "
                f"{len(thin_urls)} thin, {missing_excerpt} missing excerpt, {len(pairs)} cannibalization pair(s).",
        refresh_panels=["queue", "sources"],
    )


@chat.function(
    "get_content_audit",
    description="Read the most recent content audit for a site (run_content_audit's saved result).",
    action_type="read",
    data_model=ContentAuditReport,
)
async def get_content_audit(ctx, params: GetContentAuditParams) -> ActionResult:
    """Read back the most recently saved content-audit report for a site."""
    page = await ctx.store.query("content_audits", where={"site_id": params.site_id}, limit=1)
    if not page.data:
        return ActionResult.error(
            f"No content audit found for '{params.site_id}' yet. Run run_content_audit first.",
            retryable=False,
        )
    d = page.data[0]
    report = ContentAuditReport(id=d.id, title=f"Content audit — {params.site_id}", **d.data)
    return ActionResult.success(report, summary=f"Audit for '{params.site_id}' from {d.data.get('audited_at', '?')}.")


# ──────────────────────────────────────────────────────────────────────────
# Content decay tracking
#
# Nothing calls a search-performance connector directly from here -- exactly
# like discover_opportunities' QuerySignal, the caller fetches fresh
# GSC/DataForSEO numbers first and passes them in as ContentPerformanceSignal.
# Each call diffs the new reading against the PREVIOUS one stored for that
# URL, so decay only becomes visible after at least two periodic calls.
# ──────────────────────────────────────────────────────────────────────────

@chat.function(
    "track_content_decay",
    description=(
        "Compare current search-performance signals (clicks/impressions/position "
        "for published URLs, typically from Google Search Console's top_queries "
        "aggregated by page, or DataForSEO's rank tracking) against each URL's "
        "own PREVIOUS reading to surface which existing articles are losing "
        "traffic or ranking and need refreshing -- not just which new topics "
        "to write. Call this periodically (e.g. monthly) per site; the first "
        "call for a URL only sets its baseline."
    ),
    action_type="write",
    chain_callable=True,
    effects=["create:content_decay_report"],
    event="content_decay_tracked",
    data_model=ContentDecayReport,
)
async def track_content_decay(ctx, params: TrackContentDecayParams) -> ActionResult:
    """Diff each given URL's current signal against its last stored reading."""
    profile_page = await ctx.store.query("site_profiles", where={"site_id": params.site_id}, limit=1)
    if not profile_page.data:
        return ActionResult.error(
            f"Site profile '{params.site_id}' not found. Create it first with create_site_profile.",
            retryable=False,
        )

    title_by_url = {}
    existing_items_page = await ctx.store.query("existing_content_items", where={"site_id": params.site_id}, limit=500)
    for d in existing_items_page.data:
        link = d.data.get("link", "")
        if link:
            title_by_url[link] = d.data.get("title", d.data.get("slug", ""))

    decaying_items: list[DecayingContentItem] = []
    decaying_count = 0
    improving_count = 0
    new_count = 0

    for sig in params.signals:
        prior_page = await ctx.store.query(
            "content_decay_readings", where={"site_id": params.site_id, "url": sig.url}, limit=1,
        )
        prior = prior_page.data[0].data if prior_page.data else None

        if prior is None:
            verdict = "new"
            new_count += 1
            item = DecayingContentItem(
                url=sig.url, title=title_by_url.get(sig.url, ""),
                previous_clicks=0, current_clicks=sig.clicks, click_change_pct=0.0,
                previous_position=0.0, current_position=sig.avg_position, position_change=0.0,
                verdict=verdict, recommendation="Baseline recorded — decay will show from the next check.",
            )
        else:
            prev_clicks = prior.get("clicks", 0)
            prev_position = prior.get("avg_position", 0.0)
            click_change_pct = (
                ((sig.clicks - prev_clicks) / prev_clicks * 100.0) if prev_clicks > 0
                else (100.0 if sig.clicks > 0 else 0.0)
            )
            position_change = sig.avg_position - prev_position if prev_position > 0 else 0.0
            if click_change_pct <= -20.0 or (prev_position > 0 and position_change >= 3.0):
                verdict = "decaying"
                decaying_count += 1
                recommendation = (
                    "Refresh: update facts/data, expand thin sections, re-check internal links and CTA -- "
                    "this article is losing clicks and/or ranking."
                )
            elif click_change_pct >= 20.0 or (prev_position > 0 and position_change <= -3.0):
                verdict = "improving"
                improving_count += 1
                recommendation = "Improving — no action needed; consider building on this topic with a cluster article."
            else:
                verdict = "stable"
                recommendation = ""
            item = DecayingContentItem(
                url=sig.url, title=title_by_url.get(sig.url, ""),
                previous_clicks=prev_clicks, current_clicks=sig.clicks, click_change_pct=round(click_change_pct, 1),
                previous_position=prev_position, current_position=sig.avg_position, position_change=round(position_change, 1),
                verdict=verdict, recommendation=recommendation,
            )

        if verdict == "decaying":
            decaying_items.append(item)

        reading_payload = {
            "site_id": params.site_id, "url": sig.url, "clicks": sig.clicks,
            "impressions": sig.impressions, "avg_position": sig.avg_position,
            "source": sig.source,
        }
        if prior_page.data:
            await ctx.store.update("content_decay_readings", prior_page.data[0].id, reading_payload)
        else:
            await ctx.store.create("content_decay_readings", reading_payload)

    needs_doing: list[str] = []
    if decaying_items:
        needs_doing.append(
            f"{len(decaying_items)} article(s) are decaying (clicks down 20%+ or position dropped 3+ places): "
            + ", ".join(i.url for i in decaying_items[:5]) + (" ..." if len(decaying_items) > 5 else "")
        )
    if new_count and not decaying_items and not improving_count:
        needs_doing.append(f"{new_count} URL(s) had no prior reading -- baseline set, check again next period for a trend.")
    if not params.signals:
        needs_doing.append("No performance signals given -- pass GSC top_queries-by-page or DataForSEO rank data.")
    if not needs_doing:
        needs_doing.append("No decaying content this pass.")

    checked_at = ctx.time.now().isoformat() if hasattr(ctx, "time") and hasattr(ctx.time, "now") else ""

    payload = {
        "site_id": params.site_id, "tracked_count": len(params.signals),
        "decaying_count": decaying_count, "improving_count": improving_count, "new_count": new_count,
        "decaying_items": [item.model_dump() for item in decaying_items],
        "needs_doing": needs_doing, "checked_at": checked_at,
    }
    existing_report = await ctx.store.query("content_decay_reports", where={"site_id": params.site_id}, limit=1)
    if existing_report.data:
        await ctx.store.update("content_decay_reports", existing_report.data[0].id, payload)
    else:
        await ctx.store.create("content_decay_reports", payload)

    report = ContentDecayReport(id=params.site_id, title=f"Content decay — {params.site_id}", **payload)
    return ActionResult.success(
        report,
        summary=f"Tracked {len(params.signals)} URL(s) for '{params.site_id}': "
                f"{decaying_count} decaying, {improving_count} improving, {new_count} new baseline(s).",
        refresh_panels=["queue", "sources"],
    )


@chat.function(
    "get_content_decay",
    description="Read the most recent content-decay report for a site (track_content_decay's saved result).",
    action_type="read",
    data_model=ContentDecayReport,
)
async def get_content_decay(ctx, params: GetContentDecayParams) -> ActionResult:
    """Read back the most recently saved content-decay report for a site."""
    page = await ctx.store.query("content_decay_reports", where={"site_id": params.site_id}, limit=1)
    if not page.data:
        return ActionResult.error(
            f"No content decay report found for '{params.site_id}' yet. Run track_content_decay first.",
            retryable=False,
        )
    d = page.data[0]
    report = ContentDecayReport(id=d.id, title=f"Content decay — {params.site_id}", **d.data)
    return ActionResult.success(report, summary=f"Decay report for '{params.site_id}' from {d.data.get('checked_at', '?')}.")


# ──────────────────────────────────────────────────────────────────────────
# Phase 5: unified KPI dashboard
#
# Same non-generative pattern as track_content_decay: this app never calls
# GA4/GSC/DataForSEO connectors itself. The caller fetches each connector's
# own numbers first and records one combined snapshot per period here.
# ──────────────────────────────────────────────────────────────────────────

@chat.function(
    "record_kpi_snapshot",
    description=(
        "Record one periodic combined KPI snapshot for a site -- GA4 sessions/users/"
        "conversions, Search Console clicks/impressions/position, and DataForSEO rank "
        "tracking/backlinks, all already fetched by the caller from those connectors "
        "(this app does not call them itself). Call this once per period (e.g. monthly) "
        "per site; get_kpi_dashboard then shows the latest snapshot plus its trend "
        "against the previous one."
    ),
    action_type="write",
    chain_callable=True,
    effects=["create:kpi_snapshot"],
    event="kpi_snapshot_recorded",
    data_model=KpiSnapshot,
)
async def record_kpi_snapshot(ctx, params: RecordKpiSnapshotParams) -> ActionResult:
    """Store one periodic KPI snapshot, then refresh the site's dashboard report."""
    profile_page = await ctx.store.query("site_profiles", where={"site_id": params.site_id}, limit=1)
    if not profile_page.data:
        return ActionResult.error(
            f"Site profile '{params.site_id}' not found. Create it first with create_site_profile.",
            retryable=False,
        )

    payload = {
        "site_id": params.site_id,
        "period_label": params.period_label,
        "ga4_sessions": params.ga4_sessions,
        "ga4_users": params.ga4_users,
        "ga4_conversions": params.ga4_conversions,
        "gsc_clicks": params.gsc_clicks,
        "gsc_impressions": params.gsc_impressions,
        "gsc_avg_position": params.gsc_avg_position,
        "dataforseo_avg_rank": params.dataforseo_avg_rank,
        "dataforseo_keywords_top10": params.dataforseo_keywords_top10,
        "referring_domains": params.referring_domains,
        "notes": params.notes,
        "recorded_at": ctx.time.now().isoformat() if hasattr(ctx, "time") and hasattr(ctx.time, "now") else "",
    }
    existing = await ctx.store.query(
        "kpi_snapshots", where={"site_id": params.site_id, "period_label": params.period_label}, limit=1,
    )
    if existing.data:
        await ctx.store.update("kpi_snapshots", existing.data[0].id, payload)
        snap_id = existing.data[0].id
    else:
        created = await ctx.store.create("kpi_snapshots", payload)
        snap_id = created.id

    await _rebuild_kpi_dashboard(ctx, params.site_id)

    snapshot = KpiSnapshot(id=snap_id, title=f"KPI {params.period_label} — {params.site_id}", **payload)
    return ActionResult.success(
        snapshot,
        summary=f"Recorded KPI snapshot for '{params.site_id}' / {params.period_label}.",
    )


async def _rebuild_kpi_dashboard(ctx, site_id: str) -> None:
    """Recompute and persist the dashboard report from all stored snapshots."""
    page = await ctx.store.query("kpi_snapshots", where={"site_id": site_id}, limit=200)
    rows = sorted(page.data, key=lambda d: d.data.get("period_label", ""))
    if not rows:
        return

    latest = rows[-1].data
    history_periods = [r.data.get("period_label", "") for r in rows[::-1]][:12]

    trend: list[KpiTrendDelta] = []
    needs_doing: list[str] = []
    if len(rows) >= 2:
        prev = rows[-2].data
        metrics = [
            ("GA4 sessions", "ga4_sessions"), ("GA4 users", "ga4_users"),
            ("GA4 conversions", "ga4_conversions"), ("GSC clicks", "gsc_clicks"),
            ("GSC impressions", "gsc_impressions"), ("GSC avg position", "gsc_avg_position"),
            ("DataForSEO avg rank", "dataforseo_avg_rank"),
            ("DataForSEO keywords top10", "dataforseo_keywords_top10"),
            ("Referring domains", "referring_domains"),
        ]
        for label, key in metrics:
            prev_v = float(prev.get(key, 0) or 0)
            cur_v = float(latest.get(key, 0) or 0)
            change_pct = ((cur_v - prev_v) / prev_v * 100.0) if prev_v else (100.0 if cur_v else 0.0)
            trend.append(KpiTrendDelta(metric=label, previous=prev_v, current=cur_v, change_pct=round(change_pct, 1)))
        if latest.get("gsc_clicks", 0) < prev.get("gsc_clicks", 0):
            needs_doing.append("GSC clicks dropped period-over-period — check track_content_decay for which URLs are losing traffic.")
        if latest.get("ga4_conversions", 0) < prev.get("ga4_conversions", 0):
            needs_doing.append("GA4 conversions dropped period-over-period — review recent published content and CTAs.")
        if latest.get("dataforseo_avg_rank", 0) > prev.get("dataforseo_avg_rank", 0) and prev.get("dataforseo_avg_rank", 0):
            needs_doing.append("Average tracked keyword rank got worse — check DataForSEO Connector's keyword history for specifics.")
    else:
        needs_doing.append("Only one snapshot recorded so far — trend will appear once a second period is recorded.")

    report_payload = {
        "site_id": site_id,
        "latest_period": latest.get("period_label", ""),
        "latest": latest,
        "trend": [t.model_dump() for t in trend],
        "history_periods": history_periods,
        "needs_doing": needs_doing,
    }
    existing_report = await ctx.store.query("kpi_dashboards", where={"site_id": site_id}, limit=1)
    if existing_report.data:
        await ctx.store.update("kpi_dashboards", existing_report.data[0].id, report_payload)
    else:
        await ctx.store.create("kpi_dashboards", report_payload)


@chat.function(
    "get_kpi_dashboard",
    description=(
        "Read the unified KPI dashboard for a site: its latest recorded snapshot "
        "(GA4 + Search Console + DataForSEO combined) plus the trend against the "
        "previous period, in one place instead of three separate apps. "
        "Requires at least one record_kpi_snapshot call for the site."
    ),
    action_type="read",
    data_model=KpiDashboardReport,
)
async def get_kpi_dashboard(ctx, params: GetKpiDashboardParams) -> ActionResult:
    """Read back the most recently built KPI dashboard report for a site."""
    page = await ctx.store.query("kpi_dashboards", where={"site_id": params.site_id}, limit=1)
    if not page.data:
        return ActionResult.error(
            f"No KPI dashboard found for '{params.site_id}' yet. Run record_kpi_snapshot first.",
            retryable=False,
        )
    d = page.data[0]
    report = KpiDashboardReport(id=d.id, title=f"KPI dashboard — {params.site_id}", **d.data)
    return ActionResult.success(report, summary=f"KPI dashboard for '{params.site_id}', latest period {d.data.get('latest_period', '?')}.")


@chat.function(
    "check_keyword_cannibalization",
    description=(
        "Check for keyword cannibalization among a site's existing articles -- optionally "
        "including one NEW candidate keyword/topic, so a duplicate topic is caught BEFORE "
        "a new article is written, not after it's published and already splitting ranking "
        "signal with an old one. Requires a content audit to already exist for the site."
    ),
    action_type="read",
    data_model=CannibalizationFindingList,
)
async def check_keyword_cannibalization(ctx, params: CheckCannibalizationParams) -> ActionResult:
    """Re-score the saved audit's articles for cannibalization, optionally
    also scoring one new candidate keyword against every existing article."""
    page = await ctx.store.query("cannibalization_findings", where={"site_id": params.site_id}, limit=200)
    findings = [
        CannibalizationFinding(id=d.id, title=", ".join(d.data.get("titles", [])[:2]), **d.data)
        for d in page.data
    ]
    if params.candidate_keyword:
        audit_page = await ctx.store.query("content_audits", where={"site_id": params.site_id}, limit=1)
        if not audit_page.data:
            return ActionResult.error(
                f"No content audit found for '{params.site_id}' yet. Run run_content_audit first.",
                retryable=False,
            )
        candidate_terms = _top_terms(params.candidate_keyword, n=8)
        wp_site_id = await _resolve_wp_site_id(ctx, params.site_id)
        try:
            raw_posts = await ctx.extensions.call(
                "wordpress-hub", "list_posts_full", site_id=wp_site_id, limit=500,
            )
        except Exception:
            raw_posts = []
        for p in raw_posts or []:
            terms = _top_terms((p.get("title", "") + " " + p.get("content", "")))
            score = _term_overlap_score(candidate_terms, terms)
            if score >= 0.3:
                findings.append(CannibalizationFinding(
                    id=f"candidate-{p.get('id')}", title=p.get("title", ""),
                    site_id=params.site_id, shared_terms=list(set(candidate_terms) & set(terms)),
                    overlap_score=score, urls=[p.get("link", "")], titles=[p.get("title", "")],
                    recommendation="differentiate -- an existing article already covers this topic closely",
                ))

    return ActionResult.success(
        CannibalizationFindingList(items=findings, total=len(findings)),
        summary=f"{len(findings)} cannibalization finding(s) for '{params.site_id}'.",
    )


_CANNIBALIZATION_ACTIONS = {"merge", "differentiate", "canonicalize", "dismiss"}


@chat.function(
    "resolve_cannibalization_finding",
    description=(
        "Record the user's choice for ONE cannibalization finding. The system "
        "already decides and shows its own recommendation (merge/differentiate/"
        "canonicalize) from the overlap score when the finding is surfaced -- "
        "this function only records which option the user picked in response "
        "(including 'dismiss' if they judge it a false positive). Content Writer "
        "is never invoked automatically for this: an author is not required just "
        "to resolve a cannibalization decision."
    ),
    action_type="write",
    effects=["update:cannibalization_finding"],
    event="cannibalization_resolved",
    data_model=CannibalizationFinding,
)
async def resolve_cannibalization_finding(ctx, params: ResolveCannibalizationParams) -> ActionResult:
    """Mark one cannibalization_findings row resolved with the user's chosen
    action. Any of the four offered options is valid -- the system's own
    recommendation is only ever a suggestion in the UI, never enforced."""
    action = params.action.strip().lower()
    if action not in _CANNIBALIZATION_ACTIONS:
        return ActionResult.error(
            f"Invalid action '{params.action}'. Must be one of: {', '.join(sorted(_CANNIBALIZATION_ACTIONS))}.",
            retryable=False,
        )
    doc = await ctx.store.get("cannibalization_findings", params.finding_id)
    if not doc:
        return ActionResult.error(f"Cannibalization finding '{params.finding_id}' not found.", retryable=False)

    now = ctx.time.now().isoformat() if hasattr(ctx, "time") and hasattr(ctx.time, "now") else ""
    status = "dismissed" if action == "dismiss" else "resolved"
    update = {"status": status, "chosen_action": action, "resolved_at": now}
    await ctx.store.update("cannibalization_findings", params.finding_id, update)
    updated = await ctx.store.get("cannibalization_findings", params.finding_id)
    return ActionResult.success(
        CannibalizationFinding(id=updated.id, title=", ".join(updated.data.get("titles", [])[:2]), **updated.data),
        summary=f"Cannibalization finding marked '{status}' ({action}).",
        refresh_panels=["sources"],
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

    # MANDATORY GATE: never plan new content blind to what already exists.
    # discover_opportunities must not run before a content audit exists for
    # this site — the audit is what surfaces thin/duplicate/cannibalizing
    # existing articles BEFORE new topics get picked, which is the whole
    # point of auditing first.
    audit_page = await ctx.store.query("content_audits", where={"site_id": params.site_id}, limit=1)
    if not audit_page.data:
        return ActionResult.error(
            f"No content audit found for '{params.site_id}' yet. Run run_content_audit "
            f"first -- discovering new opportunities blind to the site's existing content "
            f"risks duplicating topics and creating keyword cannibalization.",
            retryable=False,
            code="CONTENT_AUDIT_REQUIRED",
        )

    # existing primary + supporting queries for this site — avoid duplicate
    # opportunities and avoid re-clustering a query already folded into one
    existing_page = await ctx.store.query("opportunities", limit=500)
    existing_queries: set[str] = set()
    for d in existing_page.data:
        if d.data.get("site_id") != params.site_id:
            continue
        existing_queries.add(d.data.get("primary_query", "").lower())
        existing_queries.update(q.lower() for q in d.data.get("supporting_queries", []))

    site_content_categories = {
        c.lower() for c in profile_page.data[0].data.get("content_categories", [])
    }

    # CLUSTERING: group incoming signals by cluster_label BEFORE creating
    # opportunities, so synonymous queries (e.g. "recuperator caldura" /
    # "recuperatoare caldura" / "recuperator de caldura") collapse into ONE
    # opportunity instead of one-opportunity-per-query. Within each cluster,
    # the highest-scoring signal becomes primary_query and the rest become
    # supporting_queries; impressions/clicks are summed across the cluster
    # so the priority score reflects the topic's real combined demand.
    clusters: dict[str, list] = {}
    skipped_duplicates = 0
    for sig in params.queries[: params.limit]:
        if sig.query.lower() in existing_queries:
            skipped_duplicates += 1
            continue
        label = cluster_label(sig.query)
        clusters.setdefault(label, []).append(sig)

    created: list[Opportunity] = []
    for label, sigs in clusters.items():
        sigs_scored = [
            (sig, priority_score(sig.impressions, sig.clicks, sig.ctr, sig.avg_position))
            for sig in sigs
        ]
        sigs_scored.sort(key=lambda pair: pair[1], reverse=True)
        primary_sig, _ = sigs_scored[0]
        supporting = [sig.query for sig, _ in sigs_scored[1:]]

        total_impressions = sum(sig.impressions for sig in sigs)
        total_clicks = sum(sig.clicks for sig in sigs)
        # weighted avg position/ctr by impressions, falling back to the
        # primary signal's own numbers when impressions are all zero
        if total_impressions > 0:
            avg_position = sum(sig.avg_position * sig.impressions for sig in sigs) / total_impressions
            ctr = total_clicks / total_impressions
        else:
            avg_position = primary_sig.avg_position
            ctr = primary_sig.ctr

        intent = guess_intent(primary_sig.query)
        funnel_stage, funnel_stage_reason = guess_funnel_stage(primary_sig.query)
        score = priority_score(total_impressions, total_clicks, ctr, avg_position)

        # business relevance: does this topic overlap with the site's own
        # declared content categories? Tokenizes both sides (categories are
        # often multi-word phrases like "eficiență energetică HVAC" -- a
        # single-token substring check against that whole phrase would
        # almost never match, silently keeping this at 0.0). Cheap
        # token-overlap heuristic -- not real semantic matching, but no
        # longer hardcoded 0.0 and no longer defeated by phrase-vs-token
        # length mismatch.
        cluster_tokens = set(label.split())
        category_tokens: set[str] = set()
        for cat in site_content_categories:
            category_tokens.update(cat.split())
        relevance = 0.0
        if category_tokens and cluster_tokens:
            hits = len(cluster_tokens & category_tokens)
            relevance = round(min(hits / max(len(cluster_tokens), 1), 1.0) * 100, 1)

        total_score = round(score * 0.7 + relevance * 0.3, 1)

        doc = await ctx.store.create(
            "opportunities",
            {
                "site_id": params.site_id,
                "source": primary_sig.source,
                "primary_query": primary_sig.query,
                "supporting_queries": supporting,
                "query_cluster_label": label,
                "intent": intent,
                "funnel_stage": funnel_stage,
                "funnel_stage_reason": funnel_stage_reason,
                "impressions": total_impressions,
                "clicks": total_clicks,
                "ctr": round(ctr, 4),
                "avg_position": round(avg_position, 1),
                "business_relevance_score": relevance,
                "seo_opportunity_score": score,
                "total_priority_score": total_score,
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
                "primary_query": primary_sig.query,
            },
        )
        created.append(_to_opportunity(doc))

    created.sort(key=lambda o: o.total_priority_score, reverse=True)
    return ActionResult.success(
        OpportunityList(items=created, total=len(created)),
        summary=(
            f"Found {len(created)} new opportunity(ies) for {params.site_id} "
            f"clustered from {len(params.queries) - skipped_duplicates} query signal(s) "
            f"({skipped_duplicates} skipped as duplicates)."
        ),
        refresh_panels=["queue"],
    )


def _normalize_gsc_host(site_url: str) -> str:
    """'sc-domain:climtec.md' or 'https://climtec.md/' -> 'climtec.md'."""
    s = (site_url or "").strip().lower()
    if s.startswith("sc-domain:"):
        host = s.split(":", 1)[1]
    else:
        host = s.split("://", 1)[-1].split("/", 1)[0]
    return host[4:] if host.startswith("www.") else host


async def _resolve_gsc_site_url(ctx, domain: str) -> str:
    """Find this domain's own verified Search Console property (sc-domain:
    or URL-prefix form) via IPC, falling back to the sc-domain: guess so a
    transient connector error degrades gracefully instead of hard-failing."""
    target = domain.strip().lower()
    if target.startswith("www."):
        target = target[4:]
    try:
        sites = await ctx.extensions.call("google-search-console-connector", "list_sites")
        rows = (sites or {}).get("sites", []) if isinstance(sites, dict) else (sites or [])
    except Exception:
        rows = []
    matches = [r for r in rows or [] if _normalize_gsc_host(r.get("site_url", "")) == target]
    # Prefer the sc-domain: property (covers every scheme/subdomain) over a
    # single URL-prefix property when both are verified for the same host.
    for r in matches:
        if (r.get("site_url") or "").startswith("sc-domain:"):
            return r["site_url"]
    if matches:
        return matches[0]["site_url"]
    return f"sc-domain:{target}"


@chat.function(
    "discover_opportunities_from_search_console",
    description=(
        "Panel convenience wrapper for discover_opportunities: fetches this site's "
        "own Search Console top_queries via inter-extension IPC (no manual JSON "
        "copy-paste needed) and feeds them straight into the same scoring, "
        "clustering, and content-audit gate as discover_opportunities. Use this "
        "from a button; use discover_opportunities directly when you already have "
        "query signals in hand (e.g. from striking_distance or SEO Audit Engine)."
    ),
    action_type="write",
    chain_callable=True,
    effects=["create:opportunity", "create:queue_item"],
    event="opportunities_discovered",
    data_model=OpportunityList,
)
async def discover_opportunities_from_search_console(
    ctx, params: DiscoverOpportunitiesFromSearchConsoleParams,
) -> ActionResult:
    """Resolve the site's GSC property, pull top_queries, map to QuerySignal,
    and delegate to discover_opportunities for the actual scoring/gate logic."""
    profile_page = await ctx.store.query("site_profiles", where={"site_id": params.site_id}, limit=1)
    if not profile_page.data:
        return ActionResult.error(
            f"Site profile '{params.site_id}' not found. Create it first with create_site_profile.",
            retryable=False,
        )
    domain = profile_page.data[0].data.get("domain", params.site_id)

    site_url = await _resolve_gsc_site_url(ctx, domain)
    try:
        gsc_result = await ctx.extensions.call(
            "google-search-console-connector", "top_queries",
            site_url=site_url, limit=params.limit,
        )
    except Exception as exc:  # noqa: BLE001 -- surface the real IPC failure, don't silently return zero signals
        return ActionResult.error(
            f"Could not fetch Search Console queries for '{domain}' ({site_url}): "
            f"{type(exc).__name__}: {exc}. Connect/verify this property in the Google "
            f"Search Console connector first.",
            retryable=True,
        )
    rows = (gsc_result or {}).get("rows", []) if isinstance(gsc_result, dict) else (gsc_result or [])
    if not rows:
        return ActionResult.error(
            f"Search Console returned no query rows for '{domain}' ({site_url}). "
            f"Nothing to discover from yet.",
            retryable=False,
        )
    signals = [
        QuerySignal(
            query=row.get("query", ""),
            impressions=int(row.get("impressions", 0) or 0),
            clicks=int(row.get("clicks", 0) or 0),
            ctr=float(row.get("ctr", 0.0) or 0.0) / 100.0,  # GSC returns ctr as a percentage (0-100)
            avg_position=float(row.get("position", 0.0) or 0.0),
            source="gsc",
        )
        for row in rows
        if (row.get("query") or "").strip()
    ]
    return await discover_opportunities(
        ctx, DiscoverOpportunitiesParams(site_id=params.site_id, queries=signals, limit=params.limit),
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


@chat.function(
    "generate_strategic_topics",
    description=(
        "Generate NEW content opportunities beyond any existing query signal, "
        "by reasoning over the site's own declared content_categories, what's "
        "already covered by existing opportunities/published content, and a "
        "fixed library of funnel-stage (tofu/mofu/bofu) question patterns. "
        "This is the deliberate second discovery engine -- discover_opportunities "
        "only scores/clusters query signals the caller already fetched from GSC/ "
        "SEO Audit, so it can never surface a topic nobody has searched for yet, "
        "or fill a gap the site's own declared categories imply. Requires a "
        "content audit (same gate as discover_opportunities) so it never proposes "
        "a topic that duplicates existing content."
    ),
    action_type="write",
    chain_callable=True,
    effects=["create:opportunity", "create:queue_item"],
    event="strategic_topics_generated",
    data_model=OpportunityList,
)
async def generate_strategic_topics(ctx, params: GenerateStrategicTopicsParams) -> ActionResult:
    """Reason beyond existing opportunities: turn the site's own declared
    content_categories into new candidate topics, deliberately balanced
    across tofu/mofu/bofu, skipping categories/terms already covered."""
    profile_page = await ctx.store.query("site_profiles", where={"site_id": params.site_id}, limit=1)
    if not profile_page.data:
        return ActionResult.error(
            f"Site profile '{params.site_id}' not found. Create it first with create_site_profile.",
            retryable=False,
        )
    profile = profile_page.data[0].data

    # Same mandatory gate as discover_opportunities: never plan new content
    # (even self-generated topics) blind to what already exists on the site.
    audit_page = await ctx.store.query("content_audits", where={"site_id": params.site_id}, limit=1)
    if not audit_page.data:
        return ActionResult.error(
            f"No content audit found for '{params.site_id}' yet. Run run_content_audit "
            f"first -- generating strategic topics blind to the site's existing content "
            f"risks duplicating or cannibalizing what's already published.",
            retryable=False,
        )

    content_categories = profile.get("content_categories", [])
    if not content_categories:
        return ActionResult.error(
            f"Site profile '{params.site_id}' has no content_categories declared. "
            f"Set them via update_site_profile first -- this engine reasons from the "
            f"site's own declared categories, it cannot invent what the business does.",
            retryable=False,
        )

    language = params.language or (profile.get("target_languages") or ["ro"])[0]

    # Build the covered-terms set from BOTH existing opportunities (any
    # source) and already-published content (from the audit's existing
    # content items) so a strategic topic never duplicates a query already
    # tracked or an article already live.
    covered_terms: set[str] = set()
    existing_opps_page = await ctx.store.query("opportunities", where={"site_id": params.site_id}, limit=500)
    for d in existing_opps_page.data:
        for q in [d.data.get("primary_query", ""), d.data.get("query_cluster_label", "")] + d.data.get("supporting_queries", []):
            covered_terms.update(t.lower() for t in q.split() if len(t) > 2)
    existing_content_page = await ctx.store.query("existing_content_items", where={"site_id": params.site_id}, limit=500)
    for d in existing_content_page.data:
        covered_terms.update(t.lower() for t in d.data.get("top_terms", []))

    candidates = _generate_strategic_candidates(
        content_categories=content_categories,
        covered_terms=covered_terms,
        language=language,
        per_category=max(1, min(params.per_category, 10)),
    )
    candidates = candidates[: max(1, min(params.limit, 100))]

    if not candidates:
        return ActionResult.success(
            OpportunityList(items=[], total=0),
            summary=(
                f"No new strategic topics generated for '{params.site_id}' -- every "
                f"declared content category already looks well-covered by existing "
                f"opportunities/content."
            ),
        )

    created = []
    for cand in candidates:
        doc = await ctx.store.create(
            "opportunities",
            {
                "site_id": params.site_id,
                "source": "strategic_gap_analysis",
                "primary_query": cand["title"],
                "supporting_queries": [],
                "query_cluster_label": cluster_label(cand["title"]),
                "intent": "commercial" if cand["funnel_stage"] == "bofu" else (
                    "informational" if cand["funnel_stage"] == "tofu" else "commercial"
                ),
                "funnel_stage": cand["funnel_stage"],
                "funnel_stage_reason": cand["funnel_stage_reason"],
                "impressions": 0,
                "clicks": 0,
                "ctr": 0.0,
                "avg_position": 0.0,
                "business_relevance_score": 100.0,  # generated directly from the site's own declared categories
                "seo_opportunity_score": 0.0,  # no query signal yet -- this is a proactive gap-fill, not a ranking-driven pick
                "total_priority_score": 50.0,  # neutral mid-priority: real but unproven demand, scheduled deliberately rather than reactively
                "recommended_content_type": "article",
                "recommended_target_url": "",
                "strategic_rationale": cand["strategic_rationale"],
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
                "primary_query": cand["title"],
            },
        )
        created.append(_to_opportunity(await ctx.store.get("opportunities", doc.id)))

    stage_counts = {"tofu": 0, "mofu": 0, "bofu": 0}
    for c in candidates:
        stage_counts[c["funnel_stage"]] = stage_counts.get(c["funnel_stage"], 0) + 1

    return ActionResult.success(
        OpportunityList(items=created, total=len(created)),
        summary=(
            f"{len(created)} new strategic topic(s) generated for '{params.site_id}' "
            f"beyond existing signals (tofu={stage_counts['tofu']}, "
            f"mofu={stage_counts['mofu']}, bofu={stage_counts['bofu']})."
        ),
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
    if not profile:
        return ActionResult.error(
            f"Site profile for '{opp.get('site_id', '')}' is missing. Create or restore it before creating a brief.",
            retryable=False,
        )

    # MANDATORY GATE: whenever a user asks for an article, drafting must be
    # grounded in the REAL state of Brand Strategy Hub's data for this site
    # -- not guessed off a bare site profile. create_brief always calls
    # Brand Strategy Hub's check_brand_readiness IPC surface before it is
    # allowed to build a brief; a missing/incomplete brand blocks the brief
    # the same way run_content_audit's own mandatory gate blocks
    # discover_opportunities. A downstream IPC failure is surfaced as a
    # retryable error rather than silently treated as "brand is fine".
    site_id = opp.get("site_id", "")
    try:
        readiness = await ctx.extensions.call(
            "brand-strategy-hub", "check_brand_readiness", site_id=site_id,
        )
    except Exception as exc:  # noqa: BLE001 -- a dependency failure must not silently bypass this gate
        return ActionResult.error(
            f"BRAND_READINESS_CHECK_FAILED: could not verify Brand Strategy Hub data for "
            f"'{site_id}' ({type(exc).__name__}: {exc}). This brief cannot be created blind "
            f"-- retry once Brand Strategy Hub is reachable.",
            retryable=True,
            code="BRAND_READINESS_CHECK_FAILED",
        )
    readiness = readiness or {}
    if not readiness.get("brand_exists"):
        return ActionResult.error(
            f"BRAND_PROFILE_REQUIRED: no Brand Strategy Hub brand profile exists for '{site_id}'. "
            f"Create one there (create_brand_profile) with real mission/value_proposition/tone_of_voice "
            f"before drafting content for this site -- an article without a real brand behind it is a guess.",
            retryable=False,
            code="BRAND_PROFILE_REQUIRED",
        )
    if not readiness.get("positioning_complete"):
        missing = ", ".join(readiness.get("missing_positioning_fields", []) or ["positioning fields"])
        return ActionResult.error(
            f"BRAND_POSITIONING_INCOMPLETE: the brand profile for '{site_id}' is missing: {missing}. "
            f"Fill these in via Brand Strategy Hub's update_brand_profile before drafting -- this brief "
            f"would otherwise be written without knowing what the brand actually stands for.",
            retryable=False,
            code="BRAND_POSITIONING_INCOMPLETE",
        )
    brand_readiness_warnings: list[str] = list(readiness.get("reasons", []) or [])

    target_language = params.target_language or (profile.get("target_languages") or ["en"])[0]
    lang_key = target_language.lower()[:2]
    site_languages = list(profile.get("target_languages") or [lang_key])

    # Required pipeline link policy. Pages come fresh from WordPress Hub,
    # source URLs come only from the verified per-site registry. Neither URL
    # is invented; absence blocks the brief before Article Writer can draft.
    wp_site_id = await _resolve_wp_site_id(ctx, opp.get("site_id", ""))
    try:
        action_pages = await ctx.extensions.call(
            "wordpress-hub", "list_pages_full", site_id=wp_site_id, limit=500,
        )
    except Exception as exc:  # noqa: BLE001 -- a dependency failure must not silently bypass a quality gate
        return ActionResult.error(
            f"Cannot resolve a key action page for '{opp.get('site_id', '')}': WordPress Hub page inventory failed ({type(exc).__name__}: {exc}).",
            retryable=True,
        )
    # WordPress's REST API never actually exposes Polylang's per-page
    # language on /wp/v2/pages (same gap run_content_audit already works
    # around for posts) -- every page comes back with lang="". Apply the
    # same deterministic script-only fallback here, or resolve_key_action_page
    # would reject every real action page on every site for a language
    # mismatch that was never real.
    for page in action_pages or []:
        if not (page.get("lang") or "").strip():
            page["lang"] = _detect_language_fallback(page.get("title", ""), page.get("content", ""))
    action_page, action_language_priority = resolve_key_action_page(
        action_pages or [], lang_key, site_languages,
    )
    if not action_page:
        return ActionResult.error(
            "KEY_ACTION_PAGE_REQUIRED: no real published action page (contact, consultation, quote, request, order) was found for "
            f"language priority {action_language_priority}. Add/publish the page in WordPress Hub, then create the brief again.",
            retryable=False,
        )
    external_link_url, external_link_language, external_language_priority = resolve_external_source(
        profile.get("external_sources_i18n", {}), lang_key, site_languages,
    )
    if not external_link_url:
        return ActionResult.error(
            "EXTERNAL_SOURCE_REQUIRED: no verified external source URL is configured for "
            f"language priority {external_language_priority}. Add a source to site profile external_sources_i18n, then create the brief again.",
            retryable=False,
        )

    # E-E-A-T gate: a site marked requires_named_author must never get a
    # brief without a real, registered author attached. Even for sites that
    # don't require it, a given author_id must resolve to a real author
    # record for THIS site -- never silently ignored.
    author_id = ""
    author_name = ""
    author_bio = ""
    author_credentials: list[str] = []
    if params.author_id:
        author_doc = await ctx.store.get("content_authors", params.author_id)
        if not author_doc or author_doc.data.get("site_id") != opp.get("site_id", ""):
            return ActionResult.error(
                f"Author '{params.author_id}' not found for site '{opp.get('site_id', '')}'. "
                f"Use list_content_authors or register one with create_content_author.",
                retryable=False,
            )
        author_id = author_doc.id
        author_name = author_doc.data.get("name", "")
        author_bio = author_doc.data.get("bio", "")
        author_credentials = author_doc.data.get("credentials", [])
    elif profile.get("requires_named_author"):
        return ActionResult.error(
            "NAMED_AUTHOR_REQUIRED: this site is marked requires_named_author=true "
            "(E-E-A-T gate) -- create_brief needs a real author_id. Register one with "
            "create_content_author, then pass its id in author_id.",
            retryable=False,
            code="NAMED_AUTHOR_REQUIRED",
        )

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

    # BRIEF TITLE LANGUAGE: an Opportunity's primary_query is language-neutral
    # storage -- it is literally whatever text the query/topic was first
    # discovered in (e.g. Russian GSC data), with no per-language variants.
    # create_brief supports one brief per language for the SAME opportunity,
    # so reusing that stored text as-is for every language would silently
    # give a Romanian brief a Russian title (exactly the bug reported: a RU
    # brief and its RO sibling both titled in Russian). Require the caller
    # to pass the REAL query text for this language (target_query) whenever
    # the stored primary_query's script doesn't match target_language --
    # inventing a translation here would be fabricating brand/content copy,
    # not fixing a bug, so we gate instead of guessing.
    brief_title_source = params.target_query.strip() or opp.get("primary_query", "")
    if not params.target_query.strip():
        query_script = _detect_language_fallback(opp.get("primary_query", ""), "")
        script_matches = (
            query_script == "unknown"
            or (lang_key == "ru" and query_script == "ru")
            or (lang_key != "ru" and query_script == "latin")
        )
        if not script_matches:
            return ActionResult.error(
                f"TARGET_QUERY_REQUIRED: this opportunity's stored query "
                f"('{opp.get('primary_query', '')}') is not written in "
                f"'{target_language}'. Pass target_query with the real "
                f"query/title text for '{target_language}' (e.g. from that "
                f"language's own Search Console data) -- reusing the stored "
                f"text as-is would give this brief a title in the wrong language.",
                retryable=False,
                code="TARGET_QUERY_REQUIRED",
            )

    # Outline skeleton labels are OUR OWN scaffolding text (not brand copy),
    # so they can be honestly localized by target_language. site_profile's
    # business_description/cta_default are single unlocalized strings (see
    # schemas.SiteProfile) — target_audience/cta_goal below still fall back
    # to that single string because we have no per-language brand copy to
    # draw from; inventing a translation here would be fabricating content,
    # not fixing a bug. A real fix needs per-language fields on SiteProfile.
    _OUTLINE_LABELS = {
        "ro": {
            "intro": "Introducere — de ce este important pentru cititor",
            "main": "Secțiune(i) principală(e) care acoperă întrebările secundare",
            "practical": "Recomandări practice / acționabile",
            "cta": "CTA",
        },
        "ru": {
            "intro": "Введение — почему это важно для читателя",
            "main": "Основной(е) раздел(ы), охватывающий(е) сопутствующие запросы",
            "practical": "Практические / применимые рекомендации",
            "cta": "Призыв к действию",
        },
        "en": {
            "intro": "Intro — why this matters for the reader",
            "main": "Main section(s) covering supporting queries",
            "practical": "Practical/actionable guidance",
            "cta": "CTA",
        },
    }
    labels = _OUTLINE_LABELS.get(lang_key, _OUTLINE_LABELS["en"])
    # localized CTA copy: prefer the per-language override (cta_default_i18n)
    # for THIS target_language, falling back to the single unlocalized
    # cta_default string. Computed once here and reused below for cta_goal
    # so the outline's own CTA line and the brief's cta_goal field never
    # disagree (previously the outline always used the unlocalized
    # cta_default even when target_language differed).
    localized_cta = (
        profile.get("cta_default_i18n", {}).get(target_language)
        or profile.get("cta_default", "contact us")
    )
    outline = [
        f"H1: {brief_title_source.capitalize()}",
        labels["intro"],
        labels["main"],
        labels["practical"],
        f"{labels['cta']}: {localized_cta}",
    ]
    image_requirements = [
        "featured: hero image representing the primary topic",
        "inline_1: supporting visual for the main section",
    ]

    # internal_link_targets: find existing published posts (from the last
    # run_content_audit) on an overlapping topic, in the SAME language as
    # this brief, so the generated article has somewhere real to link
    # internally -- previously this was always [] even when overlapping
    # posts existed, because nothing ever populated it.
    #
    # Language matching must account for detect_language_fallback's script-only
    # output: it can only tell "ru" (Cyrillic) from "latin" (any Latin-script
    # language, e.g. ro/en) -- it never emits an ISO code like "ro". A strict
    # equality check against target_language ("ro") therefore NEVER matched
    # "latin", so Latin-script briefs got zero internal links even when
    # matching posts existed. Any non-Cyrillic target_language accepts "latin".
    lang_matches = {target_language, lang_key, ""}
    if lang_key != "ru":
        lang_matches.add("latin")
    opp_terms = _top_terms(
        opp.get("primary_query", "") + " " + " ".join(opp.get("supporting_queries", []))
    )
    internal_link_targets: list[str] = []
    if opp_terms:
        existing_items_page = await ctx.store.query(
            "existing_content_items", where={"site_id": opp.get("site_id", "")}, limit=500
        )
        scored = []
        for d in existing_items_page.data:
            item = d.data
            if item.get("lang") not in lang_matches:
                continue
            overlap = _term_overlap_score(opp_terms, item.get("top_terms", []))
            if overlap > 0:
                scored.append((overlap, item.get("link", "")))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        internal_link_targets = [link for _, link in scored[:3] if link]

    # MANDATORY AUTOMATED CATEGORY RESOLUTION: every brief must go through
    # this step -- never a hardcoded/generic default (e.g. the previous
    # 'Blog' guess that turned out to be an empty, unrelated category on a
    # real site). Reads the site's REAL WordPress category tree via IPC and
    # scores it against this brief's own topic-term signature. An IPC
    # failure is non-fatal here (unlike the brand gate above) because a
    # brief without WordPress reachable can still be reviewed/edited before
    # publish -- it just carries an honest "could not resolve" reason
    # instead of a fabricated category name.
    try:
        wp_categories = await ctx.extensions.call(
            "wordpress-hub", "list_post_categories_full", site_id=wp_site_id, lang=lang_key,
        )
    except Exception as exc:  # noqa: BLE001 -- degrade to an honest unresolved state, never fabricate a category
        wp_categories = None
        category_resolution_reason = (
            f"Could not read WordPress's real category tree ({type(exc).__name__}: {exc}) -- "
            f"category left unresolved; resolve manually before publishing."
        )
        resolved_category, resolved_category_id = "", 0
    if wp_categories is not None:
        resolved_category, resolved_category_id, category_resolution_reason = _resolve_category(
            opp_terms or [brief_title_source], wp_categories or []
        )

    text_policy, image_text = _decide_image_text_policy(
        opp.get("intent", "informational"),
        profile.get("approved_visual_guidance", {}).get("prohibited_patterns", []),
        candidate_text=localized_cta,
    )

    brief_doc = await ctx.store.create(
        "article_briefs",
        {
            "site_id": opp.get("site_id", ""),
            "opportunity_id": params.opportunity_id,
            "working_title": brief_title_source.capitalize(),
            "target_language": target_language,
            "target_audience": (
                profile.get("business_description_i18n", {}).get(target_language)
                or profile.get("business_description", "")
            ),
            "search_intent": opp.get("intent", "informational"),
            "funnel_stage": opp.get("funnel_stage", ""),
            "primary_query": opp.get("primary_query", ""),
            "secondary_queries": opp.get("supporting_queries", []),
            "outline": outline,
            "cta_goal": localized_cta,
            "internal_link_targets": internal_link_targets,
            "key_action_page_url": action_page.get("link", ""),
            "key_action_page_language": action_page.get("lang", "") or action_language_priority[0],
            "key_action_page_reason": "Highest-ranked published action page resolved from title, slug and content using the article-language-first fallback order.",
            "external_link_url": external_link_url,
            "external_link_language": external_link_language,
            "external_link_language_priority": external_language_priority,
            "differentiation_notes": "",
            "author_id": author_id,
            "author_name": author_name,
            "author_bio": author_bio,
            "author_credentials": author_credentials,
            "image_requirements": image_requirements,
            "text_policy": text_policy,
            "image_text": image_text,
            "approved_visual_guidance": profile.get("approved_visual_guidance", {}),
            "resolved_category": resolved_category,
            "resolved_category_id": resolved_category_id,
            "category_resolution_reason": category_resolution_reason,
            "brand_readiness_checked": True,
            "brand_id": readiness.get("brand_id", ""),
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
                scheduled.append(await _calendar_entry_with_visual_baseline(ctx, doc))
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


async def _calendar_entry_with_visual_baseline(ctx, doc) -> ContentCalendarEntry:
    """Return calendar metadata without copying approved guidance into the queue."""
    entry = _to_calendar_entry(doc)
    brief_id = doc.data.get("brief_id", "")
    if not brief_id:
        return entry
    brief_doc = await ctx.store.get("article_briefs", brief_id)
    if not brief_doc or not brief_doc.data.get("approved_visual_guidance"):
        return entry
    site_page = await ctx.store.query(
        "site_profiles", where={"site_id": doc.data.get("site_id", "")}, limit=1
    )
    current_guidance = site_page.data[0].data.get("approved_visual_guidance", {}) if site_page.data else {}
    brief_guidance = brief_doc.data["approved_visual_guidance"]
    entry.visual_baseline_state = (
        "current" if _approved_visual_baseline_is_current(brief_guidance, current_guidance) else "stale"
    )
    return entry


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
    entries = [await _calendar_entry_with_visual_baseline(ctx, d) for d in items]
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
    "refresh_brief_visual_guidance",
    description=(
        "Refresh an existing article brief with the current approved visual guidance from its Site Profile. "
        "Copies read-only guidance only; it does not create a media package or generate images."
    ),
    action_type="write",
    effects=["update:article_brief"],
    event="content-strategy-app.refresh_brief_visual_guidance",
    data_model=ArticleBrief,
)
async def refresh_brief_visual_guidance(ctx, params: RefreshBriefVisualGuidanceParams) -> ActionResult[ArticleBrief]:
    """Copy the current approved Site Profile baseline into one existing brief."""
    brief_doc = await ctx.store.get("article_briefs", params.brief_id)
    if not brief_doc:
        return ActionResult.error(f"Brief '{params.brief_id}' not found.", retryable=False)
    profile_page = await ctx.store.query(
        "site_profiles", where={"site_id": brief_doc.data.get("site_id", "")}, limit=1
    )
    if not profile_page.data:
        return ActionResult.error("Site Profile for this brief was not found.", retryable=False)
    try:
        guidance = _normalize_approved_visual_guidance(
            profile_page.data[0].data.get("approved_visual_guidance", {})
        )
    except (TypeError, ValueError) as exc:
        return ActionResult.error(str(exc), retryable=False)
    if not guidance:
        return ActionResult.error(
            "Site Profile has no approved visual guidance to refresh from.", retryable=False
        )
    await ctx.store.update("article_briefs", brief_doc.id, {"approved_visual_guidance": guidance})
    brief_doc.data["approved_visual_guidance"] = guidance
    return ActionResult.success(
        _to_brief(brief_doc),
        summary="Brief visual guidance refreshed from the current approved Site Profile baseline.",
        refresh_panels=["queue"],
    )


@chat.function(
    "update_brief_title",
    description=(
        "Fix an existing brief's working_title in place — for the known bug where a brief "
        "created for a SECOND language of the same opportunity kept the FIRST language's title "
        "(e.g. a Romanian brief still titled in Russian) because the opportunity's own stored "
        "query text was reused as-is. Pass the real, correctly-translated title for this brief's "
        "own target_language — never invent a translation."
    ),
    action_type="write",
    effects=["update:article_brief"],
    event="content-strategy-app.update_brief_title",
    data_model=ArticleBrief,
)
async def update_brief_title(ctx, params: UpdateBriefTitleParams) -> ActionResult[ArticleBrief]:
    """Overwrite one brief's working_title without touching its body/outline."""
    brief_doc = await ctx.store.get("article_briefs", params.brief_id)
    if not brief_doc:
        return ActionResult.error(f"Brief '{params.brief_id}' not found.", retryable=False)
    new_title = params.working_title.strip()
    if not new_title:
        return ActionResult.error("working_title cannot be empty.", retryable=False)
    await ctx.store.update("article_briefs", brief_doc.id, {"working_title": new_title})
    brief_doc.data["working_title"] = new_title
    return ActionResult.success(
        _to_brief(brief_doc),
        summary=f"Brief title updated to '{new_title}'.",
        refresh_panels=["queue"],
    )


@chat.function(
    "build_media_brief_handoff",
    description=(
        "Build a read-only Media Studio create_media_brief payload from an article brief and its approved visual guidance. "
        "It does not create a media package or generate images."
    ),
    action_type="read",
    data_model=MediaBriefHandoff,
)
async def build_media_brief_handoff(ctx, params: BuildMediaBriefHandoffParams) -> ActionResult[MediaBriefHandoff]:
    """Assemble a safe, non-generative Media Studio draft-brief payload."""
    brief_doc = await ctx.store.get("article_briefs", params.brief_id)
    if not brief_doc:
        return ActionResult.error(f"Brief '{params.brief_id}' not found.", retryable=False)
    brief = brief_doc.data
    profile_page = await ctx.store.query("site_profiles", where={"site_id": brief.get("site_id", "")}, limit=1)
    site = profile_page.data[0].data if profile_page.data else {}
    guidance = brief.get("approved_visual_guidance", {})
    if not guidance:
        return ActionResult.error(
            "Brief has no approved visual guidance; Media Studio handoff is unavailable.",
            retryable=False,
        )
    try:
        guidance = _normalize_approved_visual_guidance(guidance)
        current_guidance = _normalize_approved_visual_guidance(site.get("approved_visual_guidance", {}))
    except (TypeError, ValueError) as exc:
        return ActionResult.error(str(exc), retryable=False)
    if not current_guidance:
        return ActionResult.error(
            "Site Profile no longer has approved visual guidance; Media Studio handoff is unavailable.",
            retryable=False,
        )
    if not _approved_visual_baseline_is_current(guidance, current_guidance):
        return ActionResult.error(
            "Brief visual guidance is stale versus the current approved Site Profile baseline. Rebuild the brief before creating a Media Studio handoff.",
            retryable=False,
        )
    prohibited = guidance.get("prohibited_patterns", [])
    style_parts = [guidance.get("style_direction", "")]
    if prohibited:
        style_parts.append("Avoid: " + "; ".join(prohibited))
    text_policy, image_text = _decide_image_text_policy(
        brief.get("search_intent", ""), prohibited, candidate_text=brief.get("cta_goal", ""),
    )
    # SYSTEM-LEVEL GUARANTEE (2026-08-18): Subject+Action and Environment/Context
    # must never be left for Media Studio's engine to guess, even when this
    # brief's own `summary`-equivalent context is thin. Built ONLY from fields
    # `create_brief` guarantees are real, non-generic text -- never invented
    # here:
    #   - primary_query: the opportunity's real discovered search signal,
    #     never blank (comes straight from live GSC/query data).
    #   - resolved_category: `resolve_category`'s own contract guarantees a
    #     real existing category OR an honestly-derived new name -- NEVER a
    #     blank/generic "Blog" placeholder (see converters.resolve_category).
    #   - target_audience: usually real (site's business description), but
    #     IS allowed to be blank on a thin profile, so it's an optional
    #     enrichment here, never a load-bearing requirement.
    # visual_environment prefers the approved Visual Profile's own
    # visual_intent (real, human-approved scene guidance) when present; if a
    # brand has none set, this is left "" ON PURPOSE rather than fabricated --
    # Media Studio's prompt engine guarantees (via its own generic fallback)
    # that Environment is still never left blank in the final prompt either
    # way. Two independent layers, so a missing upstream context is never a
    # visible failure.
    visual_subject = (
        f"{brief.get('primary_query', '')} — a real, correctly installed "
        f"scene representative of '{brief.get('resolved_category', '')}'"
    ).strip(" —")
    if brief.get("target_audience", ""):
        visual_subject = f"{visual_subject}, relevant to {brief.get('target_audience', '')}"
    visual_environment = guidance.get("visual_intent", "").strip()
    handoff = MediaBriefHandoff(
        id=f"media-handoff-{brief_doc.id}",
        title=f"Media brief handoff: {brief.get('working_title', '')}",
        site=site.get("domain") or brief.get("site_id", ""),
        article_title=brief.get("working_title", ""),
        native_title=brief.get("working_title", ""),
        summary=(
            f"Article topic: {brief.get('primary_query', '')}. "
            f"Audience: {brief.get('target_audience', '')}. "
            f"Approved visual intent: {guidance.get('visual_intent', '')}."
        ).strip(),
        lang=brief.get("target_language", ""),
        inline_count=len(brief.get("image_requirements", [])) - 1 if brief.get("image_requirements") else 2,
        model="auto",
        style_direction=" ".join(part for part in style_parts if part),
        text_policy=text_policy,
        image_text=image_text,
        visual_subject=visual_subject,
        visual_environment=visual_environment,
        source_brief_id=brief_doc.id,
        approved_visual_profile_id=guidance["profile_id"],
        approved_visual_profile_revision=guidance["profile_revision"],
        approved_vbs_id=guidance["vbs_id"],
        approved_vbs_revision=guidance["vbs_revision"],
        approved_snapshot_hash=guidance["snapshot_hash"],
    )
    return ActionResult.success(handoff, "Read-only Media Studio draft-brief payload is ready; no media package or assets were created.")


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
        "",
        "## Mandatory link policy",
        "- Include at least one natural internal link from the approved targets below.",
        f"- Include this verified external source: {brief.get('external_link_url', '')} (source language: {brief.get('external_link_language', '')}; required priority: {brief.get('external_link_language_priority', [])}).",
        "- End the article with a CTA that is a markdown link to the resolved key action page; use a natural, language-appropriate anchor that expresses the CTA goal.",
        f"- Resolved key action page: {brief.get('key_action_page_url', '')} ({brief.get('key_action_page_reason', '')})",
    ]
    if brief.get("author_name"):
        author_lines = [
            "",
            "## Author (E-E-A-T attribution)",
            f"- Byline: {brief.get('author_name', '')}",
        ]
        if brief.get("author_bio"):
            author_lines.append(f"- Bio: {brief.get('author_bio', '')}")
        if brief.get("author_credentials"):
            author_lines.append(f"- Credentials: {', '.join(brief.get('author_credentials', []))}")
        body_lines += author_lines
    visual_guidance = brief.get("approved_visual_guidance", {})
    current_guidance = profile.get("approved_visual_guidance", {})
    visual_guidance_is_current = _approved_visual_baseline_is_current(
        visual_guidance, current_guidance
    )
    if visual_guidance_is_current:
        body_lines += [
            "",
            "## Approved visual guidance (non-generative)",
            f"- Visual intent: {visual_guidance.get('visual_intent', '')}",
            f"- Style direction: {visual_guidance.get('style_direction', '')}",
            f"- Prohibited patterns: {', '.join(visual_guidance.get('prohibited_patterns', [])) or 'None recorded'}",
            "- Do not generate media from this brief. If handed to Media Studio later, use third-party providers first; Magnific only after other providers technically fail.",
        ]
    elif visual_guidance:
        body_lines += [
            "",
            "## Visual guidance status",
            "- The brief's approved visual baseline is stale and was intentionally omitted. Refresh the brief from the current Site Profile before passing visual guidance downstream.",
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
        key_action_page_url=brief.get("key_action_page_url", ""),
        key_action_page_language=brief.get("key_action_page_language", ""),
        key_action_page_reason=brief.get("key_action_page_reason", ""),
        external_link_url=brief.get("external_link_url", ""),
        external_link_language=brief.get("external_link_language", ""),
        external_link_language_priority=brief.get("external_link_language_priority", []),
        differentiation_notes=brief.get("differentiation_notes", ""),
        author_name=brief.get("author_name", ""),
        author_bio=brief.get("author_bio", ""),
        author_credentials=brief.get("author_credentials", []),
        approved_visual_guidance=visual_guidance if visual_guidance_is_current else {},
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
    # Editorial integrity gate: an article must never reach "published" without
    # a named human having fact-checked it first -- mirrors the E-E-A-T
    # author gate in create_brief. This is deliberately about status alone;
    # it never blocks earlier lifecycle steps (idea/brief_ready/draft_*).
    if params.lifecycle_status == "published" and not doc.data.get("fact_checked"):
        return ActionResult.error(
            "FACT_CHECK_REQUIRED: this queue item has not been fact-checked by a "
            "named human yet. Call record_editorial_signoff with fact_checked_by "
            "before marking it published.",
            retryable=False,
        )
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


@chat.function(
    "record_editorial_signoff",
    description=(
        "Record that a real, named human has fact-checked and/or edited a "
        "queue item's draft. fact_checked_by is required before "
        "update_queue_status can move that item to 'published' -- this is "
        "the editorial-lifecycle counterpart to the E-E-A-T author gate on "
        "create_brief. Pass only the field(s) being recorded now."
    ),
    action_type="write",
    chain_callable=True,
    effects=["update:queue_item"],
    event="editorial_signoff_recorded",
    id_projection="queue_item_id",
    data_model=QueueItem,
)
async def record_editorial_signoff(ctx, params: RecordEditorialSignoffParams) -> ActionResult:
    """Stamp fact-check and/or edit sign-off onto a queue item."""
    doc = await ctx.store.get("queue_items", params.queue_item_id)
    if not doc:
        return ActionResult.error(f"Queue item '{params.queue_item_id}' not found.", retryable=False)
    if not params.fact_checked_by and not params.edited_by:
        return ActionResult.error(
            "Give at least one of fact_checked_by or edited_by.", retryable=False,
        )
    now = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())
    update = {}
    if params.fact_checked_by:
        update["fact_checked"] = True
        update["fact_checked_by"] = params.fact_checked_by
        update["fact_checked_at"] = now
    if params.edited_by:
        update["edited_by"] = params.edited_by
        update["edited_at"] = now
    await ctx.store.update("queue_items", params.queue_item_id, update)
    doc.data.update(update)
    return ActionResult.success(
        _to_queue_item(doc),
        summary=f"Editorial sign-off recorded for queue item '{params.queue_item_id}'.",
        refresh_panels=["queue"],
    )


# ──────────────────────────────────────────────────────────────────────────
# Full pipeline wipe -- keeps only registered site profiles (connected
# sites), removes every piece of downstream working content: opportunities,
# briefs, queue items, tracked competitors, content audits, and
# cannibalization findings. Irreversible -- gated by an explicit
# confirm_wipe flag so it can never fire from a misread instruction.
# ──────────────────────────────────────────────────────────────────────────

@chat.function(
    "purge_pipeline_data",
    description=(
        "Wipe ALL working pipeline data for every site -- opportunities, "
        "article briefs, editorial queue items, tracked site competitors, "
        "content audits, and cannibalization findings. Site profiles "
        "themselves (the connected sites) are NEVER touched -- only the "
        "downstream content-strategy work built on top of them. "
        "Irreversible. Requires confirm_wipe=true."
    ),
    action_type="destructive",
    chain_callable=True,
    effects=[
        "delete:opportunity", "delete:article_brief", "delete:queue_item",
        "delete:site_competitor", "delete:content_audit",
        "delete:cannibalization_finding",
    ],
    event="pipeline_purged",
    data_model=PurgeResult,
)
async def purge_pipeline_data(ctx, params: PurgePipelineDataParams) -> ActionResult:
    """Delete every pipeline working record, keeping only site profiles."""
    if not params.confirm_wipe:
        return ActionResult.error(
            "Refusing to wipe pipeline data without confirm_wipe=true. "
            "This deletes ALL opportunities, briefs, queue items, tracked "
            "competitors, content audits, and cannibalization findings -- "
            "irreversibly. Re-call with confirm_wipe=true to proceed.",
            retryable=True,
        )

    collections = [
        "opportunities", "article_briefs", "queue_items",
        "site_competitors", "content_audits", "cannibalization_findings",
        "existing_content_items", "content_authors",
        "content_decay_readings", "content_decay_reports",
        "kpi_snapshots", "kpi_dashboards", "outreach_targets",
    ]
    removed = {}
    for coll in collections:
        page = await ctx.store.query(coll, limit=1000)
        count = 0
        for doc in page.data:
            await ctx.store.delete(coll, doc.id)
            count += 1
        removed[coll] = count

    profiles_page = await ctx.store.query("site_profiles", limit=200)
    kept_site_ids = [d.data.get("site_id", d.id) for d in profiles_page.data]

    result = PurgeResult(
        id="pipeline-purge",
        title="Pipeline data purge",
        opportunities_removed=removed["opportunities"],
        briefs_removed=removed["article_briefs"],
        queue_items_removed=removed["queue_items"],
        competitors_removed=removed["site_competitors"],
        content_audits_removed=removed["content_audits"],
        cannibalization_findings_removed=removed["cannibalization_findings"],
        authors_removed=removed["content_authors"],
        decay_readings_removed=removed["content_decay_readings"] + removed["content_decay_reports"],
        kpi_snapshots_removed=removed["kpi_snapshots"] + removed["kpi_dashboards"],
        outreach_targets_removed=removed["outreach_targets"],
        kept_site_ids=kept_site_ids,
    )
    total = sum(removed.values())
    return ActionResult.success(
        result,
        summary=(
            f"Purged {total} pipeline record(s) "
            f"({removed['opportunities']} opportunities, {removed['article_briefs']} briefs, "
            f"{removed['queue_items']} queue items, {removed['site_competitors']} competitors, "
            f"{removed['content_audits']} content audits, "
            f"{removed['cannibalization_findings']} cannibalization findings, "
            f"{removed['content_authors']} authors, "
            f"{removed['content_decay_readings'] + removed['content_decay_reports']} decay records, "
            f"{removed['kpi_snapshots'] + removed['kpi_dashboards']} KPI records, "
            f"{removed['outreach_targets']} outreach targets). "
            f"Kept {len(kept_site_ids)} connected site(s): {', '.join(kept_site_ids) or '—'}."
        ),
        refresh_panels=["queue", "sources"],
    )


# ──────────────────────────────────────────────────────────────────────────
# Site-scoped pipeline wipe -- same contract as purge_pipeline_data above,
# but restricted to ONE site's own records. Added because a full-portfolio
# wipe is too blunt for the common real request "redo this ONE site's SEO
# pipeline from scratch" (e.g. climtec.md) without touching every other
# managed site (e.g. g4s.md) in the same call. Irreversible -- gated by the
# same explicit confirm_wipe flag.
# ──────────────────────────────────────────────────────────────────────────

@chat.function(
    "purge_site_pipeline_data",
    description=(
        "Wipe ALL working pipeline data for ONE site -- its opportunities, "
        "article briefs, editorial queue items, tracked competitors, content "
        "audits, and cannibalization findings. Every OTHER site's data is "
        "untouched. The site profile itself is NEVER removed. "
        "Irreversible. Requires confirm_wipe=true."
    ),
    action_type="destructive",
    chain_callable=True,
    effects=[
        "delete:opportunity", "delete:article_brief", "delete:queue_item",
        "delete:site_competitor", "delete:content_audit",
        "delete:cannibalization_finding",
    ],
    event="site_pipeline_purged",
    data_model=PurgeResult,
)
async def purge_site_pipeline_data(ctx, params: PurgeSitePipelineDataParams) -> ActionResult:
    """Delete every pipeline working record for ONE site, keeping its site profile."""
    if not params.confirm_wipe:
        return ActionResult.error(
            "Refusing to wipe pipeline data without confirm_wipe=true. "
            f"This deletes ALL opportunities, briefs, queue items, tracked "
            f"competitors, content audits, and cannibalization findings for "
            f"site '{params.site_id}' only -- irreversibly. Re-call with "
            f"confirm_wipe=true to proceed.",
            retryable=True,
        )

    profile_page = await ctx.store.query("site_profiles", where={"site_id": params.site_id}, limit=1)
    if not profile_page.data:
        return ActionResult.error(f"Site '{params.site_id}' not found in list_site_profiles.", retryable=False)

    collections = [
        "opportunities", "article_briefs", "queue_items",
        "site_competitors", "content_audits", "cannibalization_findings",
        "existing_content_items", "content_authors",
        "content_decay_readings", "content_decay_reports",
        "kpi_snapshots", "kpi_dashboards", "outreach_targets",
    ]
    removed = {}
    for coll in collections:
        page = await ctx.store.query(coll, where={"site_id": params.site_id}, limit=1000)
        count = 0
        for doc in page.data:
            await ctx.store.delete(coll, doc.id)
            count += 1
        removed[coll] = count

    result = PurgeResult(
        id=f"site-pipeline-purge-{params.site_id}",
        title=f"Pipeline data purge: {params.site_id}",
        opportunities_removed=removed["opportunities"],
        briefs_removed=removed["article_briefs"],
        queue_items_removed=removed["queue_items"],
        competitors_removed=removed["site_competitors"],
        content_audits_removed=removed["content_audits"],
        cannibalization_findings_removed=removed["cannibalization_findings"],
        authors_removed=removed["content_authors"],
        decay_readings_removed=removed["content_decay_readings"] + removed["content_decay_reports"],
        kpi_snapshots_removed=removed["kpi_snapshots"] + removed["kpi_dashboards"],
        outreach_targets_removed=removed["outreach_targets"],
        kept_site_ids=[params.site_id],
    )
    total = sum(removed.values())
    return ActionResult.success(
        result,
        summary=(
            f"Purged {total} pipeline record(s) for site '{params.site_id}' "
            f"({removed['opportunities']} opportunities, {removed['article_briefs']} briefs, "
            f"{removed['queue_items']} queue items, {removed['site_competitors']} competitors, "
            f"{removed['content_audits']} content audits, "
            f"{removed['cannibalization_findings']} cannibalization findings, "
            f"{removed['content_authors']} authors, "
            f"{removed['content_decay_readings'] + removed['content_decay_reports']} decay records, "
            f"{removed['kpi_snapshots'] + removed['kpi_dashboards']} KPI records, "
            f"{removed['outreach_targets']} outreach targets). "
            f"Site profile '{params.site_id}' itself was kept untouched."
        ),
        refresh_panels=["queue", "sources"],
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


@ext.schedule("csa_connected_sites_refresh", "10 * * * *")
async def csa_connected_sites_refresh(ctx) -> None:
    """Keeps the Quick Add connected-sites cache warm with NO button and NO
    chat message -- the whole point of this tick.

    WHY A SEPARATE SCHEDULE INSTEAD OF A BUTTON. `ctx.extensions.call` made
    from inside a *panel render* has been observed to reach the target
    extension with an empty user context (kernel-side gap, see
    _cache_connected_sites's own comment) -- so the panel can never refresh
    itself live. A real `@ext.schedule` tick is, like `list_connected_sites`
    itself, a normal call path with a populated context, so it is the
    correct place to do this automatically instead of asking a human to
    click Refresh every time a site gets connected or disconnected
    elsewhere.
    """
    sites, problems = await fetch_connected_sites(ctx)
    await _cache_connected_sites(ctx, sites, problems)


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


async def _projects_section(ctx, site_id: str, show_add_project: str) -> ui.UINode:
    """'Projects' = the site profiles already registered (our connected
    sites). Shown as a clickable list at the top of the sidebar so a
    project is always one click away -- no separate navigation needed.
    Clicking a project routes the center panel to that project's brief
    catalogue (brief_panel(site_id=...)). 'Add new project' opens a
    ui.Dialog (per explicit request) instead of the plain inline Form the
    Sites panel already offers -- Input children carrying param_name are
    merged into on_confirm's call params the same way ui.Form merges its
    own children, so the dialog needs no separate submit button."""
    site_page = await ctx.store.query("site_profiles", limit=50)
    profiles = list(site_page.data)

    briefs_page = await ctx.store.query("article_briefs", limit=1000)
    brief_count_by_site: dict[str, int] = {}
    for d in briefs_page.data:
        sid = d.data.get("site_id", "")
        brief_count_by_site[sid] = brief_count_by_site.get(sid, 0) + 1

    project_items = [
        ui.ListItem(
            id=d.data.get("site_id", d.id),
            title=_display_domain(d.data.get("domain") or d.data.get("site_id", d.id)),
            meta=f"{brief_count_by_site.get(d.data.get('site_id', ''), 0)} briefs",
            selected=(d.data.get("site_id") == site_id and bool(site_id)),
            on_click=ui.Call("__panel__brief", site_id=d.data.get("site_id", d.id)),
        )
        for d in profiles
    ]

    add_project_button = ui.Button(
        "Add new project", variant="primary", size="lg", full_width=True,
        icon="Plus",
        on_click=ui.Call("__panel__queue", site_id=site_id, show_add_project="1"),
    )

    children: list[ui.UINode] = [add_project_button]
    if project_items:
        children.append(ui.List(items=project_items, searchable=False))
    else:
        children.append(ui.Empty(message="No projects yet — add one to get started.", icon="🗂️"))

    if show_add_project:
        children.append(
            ui.Dialog(
                title="Add new project",
                content=ui.Stack(
                    direction="v", gap=2,
                    children=[
                        ui.Input(param_name="site_id", placeholder="Site id, e.g. g4s.md"),
                        ui.Input(param_name="domain", placeholder="Domain, e.g. g4s.md"),
                        ui.Input(param_name="brand_name", placeholder="Brand name (optional)"),
                    ],
                ),
                confirm_label="Create project",
                cancel_label="Cancel",
                on_confirm=ui.Call("create_site_profile"),
            )
        )

    # No ui.Card wrapper here on purpose -- left-sidebar blocks in this app
    # follow the platform's own sidebar UX rule: no padded/filled/bordered
    # "card" container, just a plain Stack, separated from the next sidebar
    # block by a Divider (see queue_panel) rather than a box. No section
    # header here either, per explicit request -- the Add-project CTA and
    # the project list speak for themselves without a "Projects" label.
    return ui.Stack(
        direction="v", gap=2,
        children=children,
    )


@ext.panel(
    "queue",
    slot="left",
    title="Editorial Queue",
    icon="📋",
    default_width=300,
    min_width=240,
    max_width=460,
)
async def queue_panel(ctx, site_id: str = "", show_add_project: str = "", **kwargs) -> object:
    """Left sidebar: just the Projects section (no header, no search, no
    Sites/Quick Add button -- all removed per explicit request).

    No queue/briefs listing here on purpose (removed per explicit request) --
    a project's own briefs are already one click away via _briefs_catalog_view
    (clicking a project routes to brief_panel(site_id=...)), so a second,
    duplicate, searchable list of the same items in the sidebar was pure
    clutter. Same goes for the 'Filter by site' Select it depended on --
    with no queue list left to filter, the control had no purpose either.

    The Sites panel (Quick Add, site management) is still reachable via the
    right-slot 'sources' panel through normal panel navigation -- it is just
    no longer duplicated as a button here.
    """
    return await _projects_section(ctx, site_id, show_add_project)


def _outline_markdown(outline: list) -> str:
    if not outline:
        return "_No outline yet._"
    return "\n".join(f"- {line}" for line in outline)


def _image_reqs_markdown(reqs: list) -> str:
    if not reqs:
        return "_No image requirements yet._"
    return "\n".join(f"- {r}" for r in reqs)


_BRIEF_STATUS_ORDER = ["idea", "brief_ready", "draft_requested", "draft_ready", "approved", "published"]


def _brief_status_breakdown(rows: list[dict]) -> str:
    """'3 brief ready, 1 approved' -- a live summary of every state a
    brief in this project can be in, mirroring Media Hub's own
    _status_breakdown pattern for its packages catalogue."""
    if not rows:
        return "No briefs yet."
    counts: dict[str, int] = {}
    for row in rows:
        status = row.get("status") or "idea"
        counts[status] = counts.get(status, 0) + 1
    parts = [f"{counts.pop(s)} {_STATUS_LABEL.get(s, s)}" for s in _BRIEF_STATUS_ORDER if s in counts]
    parts += [f"{n} {_STATUS_LABEL.get(s, s)}" for s, n in counts.items()]
    return ", ".join(parts)


async def _create_brief_form(ctx, site_id: str, profile: dict) -> ui.UINode:
    """The 'Create new brief' ui.Dialog revealed above the briefs list.
    Carries every field create_brief actually accepts (see
    CreateBriefParams): which opportunity to turn into a brief, the
    target language, the real target_query text for that language
    (required whenever the opportunity's own query script won't match
    the chosen language -- create_brief itself gates and errors clearly
    if it's needed and missing), and the named author when this site
    requires one for E-E-A-T. Only opportunities NOT already
    brief_ready/published for every one of this site's target languages
    are worth offering, but we keep the list simple and let
    create_brief's own one-per-language duplicate check do the
    enforcement -- this form must not invent or hide real state.

    Uses ui.Dialog (per explicit request), the same pattern
    _projects_section already uses for 'Add new project': field children
    carrying param_name are merged into on_confirm's call params, so the
    dialog needs no separate submit button of its own."""
    # Sort by -created_at at the store level (the same order_by every other
    # opportunities query in this file already uses -- see list_opportunities)
    # and re-sort by priority score in Python instead. An unindexed/unusual
    # order_by key at the store layer is a plausible cause of the reported
    # 'Create new brief' hang: this keeps the query itself on the one
    # order_by value already proven to work everywhere else in this app.
    opp_page = await ctx.store.query(
        "opportunities", where={"site_id": site_id}, order_by="-created_at", limit=100
    )
    sorted_opps = sorted(
        opp_page.data, key=lambda d: d.data.get("total_priority_score", 0), reverse=True
    )
    opportunity_options = [
        {
            "value": d.id,
            "label": f"{d.data.get('primary_query', '(no query)')} "
                     f"(score {d.data.get('total_priority_score', 0)}, {d.data.get('status', 'idea')})",
        }
        for d in sorted_opps
    ]

    target_languages = list(profile.get("target_languages") or ["en"])
    language_options = [{"value": lang, "label": lang} for lang in target_languages]

    requires_named_author = bool(profile.get("requires_named_author"))
    author_page = await ctx.store.query("content_authors", where={"site_id": site_id}, limit=100)
    author_options = [{"value": d.id, "label": d.data.get("name", "(unnamed)")} for d in author_page.data]

    fields: list[ui.UINode] = []
    if not opportunity_options:
        fields.append(ui.Alert(
            title="No opportunities yet",
            message="Discover opportunities for this project first (Discover from Search Console), "
                    "then come back here to turn one into a brief.",
            type="warning",
        ))
    else:
        fields.append(ui.Select(param_name="opportunity_id", options=opportunity_options,
                                 placeholder="Opportunity to turn into a brief"))
    if language_options:
        fields.append(ui.Select(param_name="target_language", options=language_options,
                                 placeholder="Target language"))
    fields.append(ui.Input(
        param_name="target_query",
        placeholder="Real query/title text in the target language "
                    "(required if it differs in script from the opportunity's own query)",
    ))
    if requires_named_author:
        if author_options:
            fields.append(ui.Select(param_name="author_id", options=author_options,
                                     placeholder="Author (required — this site requires a named author)"))
        else:
            fields.append(ui.Alert(
                title="No content author registered",
                message="This site requires a named author for E-E-A-T. Register one with "
                        "create_content_author before creating a brief.",
                type="warning",
            ))
    else:
        fields.append(ui.Select(param_name="author_id", options=author_options,
                                 placeholder="Author (optional, recommended for E-E-A-T)"))

    return ui.Dialog(
        title="Create new brief",
        content=ui.Stack(direction="v", gap=2, children=fields),
        confirm_label="Create brief",
        cancel_label="Cancel",
        on_confirm=ui.Call("create_brief"),
    )


_PROJECT_TAB_ORDER = [
    ("strategy", "Overview"),
    ("plan", "Content Plan"),
    ("briefs", "Content Briefs"),
]


async def _content_strategy_tab_view(ctx, site_id: str, profile: dict) -> ui.UINode:
    """'Overview' tab: the site-level strategic picture -- the mandatory
    content audit, content-decay tracking, approved visual guidance handed
    down from Brand Strategy Hub, tracked competitors, named authors
    (E-E-A-T), and any open keyword-cannibalization findings. Mirrors what
    sources_panel already shows per site card in the right sidebar, just
    surfaced here too so it's visible without leaving the project you're
    already looking at. Rendered as a ui.Accordion (one collapsible
    section per topic) so a project with a lot of history stays scannable
    instead of one long scroll."""
    audit_page = await ctx.store.query("content_audits", where={"site_id": site_id}, limit=1)
    audit_children: list[ui.UINode] = []
    if audit_page.data:
        audit = audit_page.data[0].data
        audit_children.append(ui.KeyValue(columns=1, items=[
            {"key": "Total posts", "value": str(audit.get("total_posts", 0))},
            {"key": "Thin content", "value": str(audit.get("thin_content_count", 0))},
            {"key": "Cannibalizing pairs", "value": str(audit.get("cannibalization_pairs_found", 0))},
            {"key": "Audited", "value": audit.get("audited_at", "?")},
        ]))
        needs_doing = audit.get("needs_doing", [])
        if needs_doing:
            audit_children.append(ui.Text("Needs doing:", variant="caption"))
            audit_children.append(ui.List(items=[
                ui.ListItem(id=f"audit-needs-{i}", title=item) for i, item in enumerate(needs_doing)
            ]))
    else:
        audit_children.append(ui.Text("⚠️ Never run — required before new opportunities.", variant="caption"))
    audit_children.append(ui.Button(
        "🔎 Run content audit" if not audit_page.data else "🔎 Re-run content audit",
        variant="secondary", size="sm",
        on_click=ui.Call("run_content_audit", site_id=site_id),
    ))

    decay_page = await ctx.store.query("content_decay_reports", where={"site_id": site_id}, limit=1)
    decay_children: list[ui.UINode] = []
    if decay_page.data:
        decay = decay_page.data[0].data
        decay_children.append(ui.KeyValue(columns=1, items=[
            {"key": "Decaying", "value": str(decay.get("decaying_count", 0))},
            {"key": "Improving", "value": str(decay.get("improving_count", 0))},
            {"key": "New baseline", "value": str(decay.get("new_count", 0))},
            {"key": "Checked", "value": decay.get("checked_at", "?")},
        ]))
        decaying_items = decay.get("decaying_items", [])
        if decaying_items:
            decay_children.append(ui.Text("Needs refreshing:", variant="caption"))
            decay_children.append(ui.List(items=[
                ui.ListItem(id=f"decay-{i}", title=item.get("url", "")) for i, item in enumerate(decaying_items[:10])
            ]))
    else:
        decay_children.append(ui.Text(
            "No content decay report yet — run track_content_decay with fresh GSC/DataForSEO numbers.",
            variant="caption",
        ))

    visual_guidance = profile.get("approved_visual_guidance", {})
    visual_children: list[ui.UINode] = []
    if visual_guidance:
        visual_children.append(ui.KeyValue(columns=1, items=[
            {"key": "Profile", "value": visual_guidance.get("profile_id", "Approved profile")},
            {"key": "Visual intent", "value": visual_guidance.get("visual_intent", "—")},
            {"key": "Style direction", "value": visual_guidance.get("style_direction", "—")},
            {"key": "Approval basis", "value": f"Profile r{visual_guidance.get('profile_revision', '—')} · VBS r{visual_guidance.get('vbs_revision', '—')}"},
        ]))
        visual_children.append(ui.Text(
            "Read-only, non-generative constraint passed downstream to briefs.", variant="caption",
        ))
    else:
        visual_children.append(ui.Text(
            "No approved visual guidance linked yet (comes from Brand Strategy Hub).", variant="caption",
        ))

    comp_page = await ctx.store.query("site_competitors", where={"site_id": site_id}, limit=50)
    if comp_page.data:
        competitor_children = [ui.List(items=[
            ui.ListItem(
                id=d.id,
                title=d.data.get("name", d.id),
                subtitle=d.data.get("url", ""),
                meta=", ".join(d.data.get("competing_topics", [])[:3]),
            )
            for d in comp_page.data
        ])]
    else:
        competitor_children = [ui.Text("No competitors tracked yet — add one with add_site_competitor.", variant="caption")]

    author_page = await ctx.store.query("content_authors", where={"site_id": site_id}, limit=50)
    if author_page.data:
        author_children = [ui.List(items=[
            ui.ListItem(
                id=d.id,
                title=d.data.get("name", d.id),
                subtitle=", ".join(d.data.get("expertise_areas", [])[:3]),
            )
            for d in author_page.data
        ])]
    else:
        author_children = [ui.Text(
            "No named authors registered yet — required for E-E-A-T on YMYL sites.", variant="caption",
        )]

    # Only offer opportunity discovery once the mandatory content-audit gate
    # is satisfied -- matches discover_opportunities's own server-side check.
    discovery_children: list[ui.UINode] = [ui.Button(
        "🧭 Discover from Search Console",
        variant="primary", size="sm",
        on_click=ui.Call("discover_opportunities_from_search_console", site_id=site_id, limit=20),
        disabled=not bool(audit_page.data),
    )]
    cannibalization_body = await _open_cannibalization_findings_block(ctx, site_id)
    if cannibalization_body:
        discovery_children.append(ui.Divider())
        discovery_children.extend(cannibalization_body)
    discovery_children.append(ui.Form(
        action="check_keyword_cannibalization",
        submit_label="Check cannibalization",
        defaults={"site_id": site_id},
        children=[
            ui.Input(param_name="candidate_keyword",
                     placeholder="New topic/keyword to check before writing (optional)"),
        ],
    ))

    return ui.Accordion(allow_multiple=True, sections=[
        {"id": "content-audit", "title": "Content audit", "children": audit_children, "expanded": True},
        {"id": "content-decay", "title": "Content decay", "children": decay_children},
        {"id": "visual-guidance", "title": "Approved visual guidance", "children": visual_children},
        {"id": "competitors", "title": f"Tracked competitors ({len(comp_page.data)})", "children": competitor_children},
        {"id": "authors", "title": f"Named authors ({len(author_page.data)})", "children": author_children},
        {"id": "discovery", "title": "Discover & check cannibalization", "children": discovery_children},
    ])


async def _content_plan_tab_view(ctx, site_id: str) -> ui.UINode:
    """'Content Plan' tab: the monthly publication calendar (queue items
    that already have a scheduled_date), a 'Build calendar' form wrapping
    build_content_calendar, and the unscheduled backlog -- items already
    brief_ready or later that have no scheduled_date yet."""
    q_page = await ctx.store.query("queue_items", where={"site_id": site_id}, order_by="-created_at", limit=500)
    all_items = list(q_page.data)
    scheduled = sorted(
        (d for d in all_items if d.data.get("scheduled_date")),
        key=lambda d: d.data.get("scheduled_date", ""),
    )
    backlog = [
        d for d in all_items
        if not d.data.get("scheduled_date") and d.data.get("lifecycle_status") != "idea"
    ]

    def _queue_list_item(d, show_date: bool) -> ui.UINode:
        status = d.data.get("lifecycle_status", "idea")
        return ui.ListItem(
            id=d.id,
            title=d.data.get("working_title") or d.data.get("primary_query") or "(untitled)",
            subtitle=d.data.get("scheduled_date", "") if show_date else _STATUS_LABEL.get(status, status),
            meta=_STATUS_LABEL.get(status, status) if show_date else "",
            badge=ui.Badge(_STATUS_LABEL.get(status, status), color=_STATUS_COLOR.get(status, "gray")),
            on_click=ui.Call("__panel__brief", queue_item_id=d.id),
        )

    calendar_children: list[ui.UINode] = []
    if scheduled:
        calendar_children.append(ui.List(items=[_queue_list_item(d, show_date=True) for d in scheduled]))
    else:
        calendar_children.append(ui.Empty(message="Nothing scheduled yet — build a calendar below.", icon="🗓️"))

    build_children: list[ui.UINode] = [ui.Form(
        action="build_content_calendar",
        submit_label="Build calendar",
        defaults={"site_id": site_id},
        children=[
            ui.Input(param_name="year", placeholder=f"Year, e.g. {_date.today().year}"),
            ui.Input(param_name="month", placeholder="Month (1-12)"),
            ui.Input(param_name="posts_per_week", placeholder="Posts per week (default 2)"),
        ],
    )]

    backlog_children: list[ui.UINode] = []
    if backlog:
        backlog_children.append(ui.List(items=[_queue_list_item(d, show_date=False) for d in backlog]))
    else:
        backlog_children.append(ui.Text("Nothing ready to schedule yet.", variant="caption"))

    return ui.Accordion(allow_multiple=True, sections=[
        {"id": "calendar", "title": f"Publication calendar ({len(scheduled)} scheduled)", "children": calendar_children, "expanded": True},
        {"id": "build-calendar", "title": "Build a monthly calendar", "children": build_children},
        {"id": "backlog", "title": f"Unscheduled backlog ({len(backlog)})", "children": backlog_children},
    ])


async def _project_tabs_view(ctx, site_id: str, show_create_brief: str, tab: str) -> ui.UINode:
    """Project detail = a real ui.Tabs switcher (client-side, no round trip)
    with 3 tabs -- Overview / Content Plan / Content Briefs -- each one's
    content built as a ui.Accordion of collapsible sections. Default tab
    is 'strategy' (labelled 'Overview'), i.e. index 0 -- the first tab
    opens by default. `tab=` is still accepted so a deep link (e.g. the
    brief detail page's 'Back to Briefs' button) can pick a different
    initial tab.
    """
    tab_keys = [key for key, _label in _PROJECT_TAB_ORDER]
    default_index = tab_keys.index(tab) if tab in tab_keys else 0

    profile_page = await ctx.store.query("site_profiles", where={"site_id": site_id}, limit=1)
    profile = profile_page.data[0].data if profile_page.data else {}
    project_label = profile.get("brand_name") or site_id

    strategy_content = await _content_strategy_tab_view(ctx, site_id, profile)
    plan_content = await _content_plan_tab_view(ctx, site_id)
    briefs_content = await _briefs_catalog_view(ctx, site_id, show_create_brief)
    content_by_key = {"strategy": strategy_content, "plan": plan_content, "briefs": briefs_content}

    tabs = ui.Tabs(
        tabs=[{"label": label, "content": content_by_key[key]} for key, label in _PROJECT_TAB_ORDER],
        default_tab=default_index,
    )

    return ui.Stack(direction="v", gap=3, children=[
        ui.Header(text=project_label, level=2),
        tabs,
    ])


async def _briefs_catalog_view(ctx, site_id: str, show_create_brief: str = "") -> ui.UINode:
    """Central catalogue of one project's briefs. Visually mirrors Media
    Hub's _packages_view: a header carrying a live count + status
    breakdown, then a searchable ui.List over the full set already
    loaded -- just scoped to article_briefs for this one site_id instead
    of media packages. Carries a 'Create new brief' CTA above the list
    that reveals a ui.Dialog with every field create_brief actually accepts,
    the same show_x-param toggle pattern _projects_section uses for its
    'Add new project' dialog."""
    profile_page = await ctx.store.query("site_profiles", where={"site_id": site_id}, limit=1)
    profile = profile_page.data[0].data if profile_page.data else {}
    project_label = profile.get("brand_name") or site_id

    briefs_page = await ctx.store.query(
        "article_briefs", where={"site_id": site_id}, order_by="-created_at", limit=200
    )
    rows = [dict(d.data, id=d.id) for d in briefs_page.data]
    total = len(rows)

    # Map brief_id -> queue_item_id so clicking a brief opens its full
    # existing detail view (brief_panel's queue_item_id branch), instead
    # of duplicating that detail rendering here.
    q_page = await ctx.store.query("queue_items", where={"site_id": site_id}, limit=500)
    queue_item_by_brief_id = {
        d.data.get("brief_id"): d.id for d in q_page.data if d.data.get("brief_id")
    }

    create_brief_button = ui.Button(
        "➕ Create new brief", variant="primary", size="md",
        on_click=ui.Call("__panel__brief", site_id=site_id, show_create_brief="" if show_create_brief else "1"),
    )

    catalog_children: list[ui.UINode] = [
        ui.Text(_brief_status_breakdown(rows), variant="caption"),
        create_brief_button,
    ]

    if show_create_brief:
        catalog_children.append(await _create_brief_form(ctx, site_id, profile))

    if not rows:
        catalog_children.append(ui.Empty(message="No briefs yet for this project.", icon="📝"))
    else:
        items = [
            ui.ListItem(
                id=row["id"],
                title=row.get("working_title") or row.get("primary_query") or "(untitled brief)",
                subtitle=row.get("target_language", "") or "—",
                meta=_STATUS_LABEL.get(row.get("status", "idea"), row.get("status", "idea")),
                badge=ui.Badge(
                    _STATUS_LABEL.get(row.get("status", "idea"), row.get("status", "idea")),
                    color=_STATUS_COLOR.get(row.get("status", "idea"), "gray"),
                ),
                on_click=ui.Call("__panel__brief", queue_item_id=queue_item_by_brief_id.get(row["id"], "")),
            )
            for row in rows
        ]
        catalog_children.append(ui.List(items=items, searchable=True))

    # NOTE: this is rendered directly as the "Content Briefs" tab's body --
    # no wrapping ui.Accordion here. Unlike the Overview tab (a grab-bag of
    # several unrelated topics that benefits from being collapsible), this
    # tab has exactly one thing to show, so its content should render
    # immediately instead of hiding behind one more click.
    return ui.Stack(direction="v", gap=3, children=[
        ui.Header(text=f"{project_label} · Briefs ({total})", level=3),
        *catalog_children,
    ])


@ext.panel(
    "brief",
    slot="center",
    title="Opportunity / Brief",
    icon="📝",
    center_overlay=True,
)
async def brief_panel(ctx, queue_item_id: str = "", site_id: str = "", show_create_brief: str = "", tab: str = "", **kwargs) -> object:
    if not queue_item_id:
        if site_id:
            return await _project_tabs_view(ctx, site_id, show_create_brief, tab)
        return ui.Empty(message="Select a project to see its briefs.", icon="📝")

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

    # Back button to the briefs list this detail page was opened from --
    # gray, medium, left-pointing arrow icon on the left of the label, with
    # the label naming the actual destination rather than a bare "Back".
    # Panel navigation merges params with whatever is already open rather
    # than replacing them wholesale (see page-speed-insights' own
    # ui.Call("__panel__psi", view="") for the same pattern) -- so the
    # currently-open queue_item_id must be explicitly cleared here, or the
    # panel keeps rendering this same detail view instead of routing back
    # to the briefs catalog.
    back_button = ui.Button(
        "Back to Briefs", variant="secondary", size="md", icon="ArrowLeft",
        on_click=ui.Call("__panel__brief", site_id=q.get("site_id", ""), queue_item_id="", tab="briefs"),
    )

    header = ui.Header(title, level=2, subtitle=f"{q.get('site_id', '')} · {_STATUS_LABEL.get(status, status)}")

    kv_items = [
        {"key": "Status", "value": _STATUS_LABEL.get(status, status)},
        {"key": "Source", "value": opp.get("source", "—")},
        {"key": "Intent", "value": opp.get("intent") or brief.get("search_intent", "—")},
        {"key": "Priority score", "value": str(opp.get("total_priority_score", "—"))},
        {"key": "Impressions", "value": str(opp.get("impressions", "—"))},
        {"key": "Avg. position", "value": str(opp.get("avg_position", "—"))},
    ]
    accordion_sections = [
        {"id": "overview", "title": "Overview", "children": [ui.KeyValue(columns=2, items=kv_items)], "expanded": True},
    ]

    if brief:
        accordion_sections.append({
            "id": "outline", "title": "Outline",
            "children": [ui.Markdown(content=_outline_markdown(brief.get("outline", [])))],
        })
        visual_guidance = brief.get("approved_visual_guidance", {})
        image_text = brief.get("image_text", "")
        if brief.get("text_policy") == "allow_text" and image_text:
            text_policy_label = f'Render this text in the image: "{image_text}"'
        else:
            text_policy_label = "No embedded text in image"
        image_children = [
            ui.Markdown(content=_image_reqs_markdown(brief.get("image_requirements", []))),
            ui.Markdown(content=f"**In-image text policy:** {text_policy_label} (`{brief.get('text_policy', 'no_text')}`)"),
        ]
        if visual_guidance:
            site_page = await ctx.store.query("site_profiles", where={"site_id": brief.get("site_id", "")}, limit=1)
            current_guidance = site_page.data[0].data.get("approved_visual_guidance", {}) if site_page.data else {}
            handoff_available = _approved_visual_baseline_is_current(
                visual_guidance, current_guidance
            )
            handoff_control = (
                ui.Form(
                    action="build_media_brief_handoff",
                    submit_label="Build Media Studio handoff",
                    defaults={"brief_id": q.get("brief_id", "")},
                    children=[],
                )
                if handoff_available
                else ui.Form(
                    action="refresh_brief_visual_guidance",
                    submit_label="Refresh approved visual guidance",
                    defaults={"brief_id": q.get("brief_id", "")},
                    children=[ui.Text(
                        "Media Studio handoff unavailable: this brief uses a stale approved visual baseline. Refresh the read-only guidance from the current Site Profile first.",
                        variant="caption",
                    )],
                )
            )
            writer_status = (
                "ready — visual guidance will be included"
                if handoff_available
                else "visual guidance excluded — baseline stale, refresh it using the "
                     "button in the Media Studio section below"
            )
            media_status = (
                "ready — Build Media Studio handoff below"
                if handoff_available
                else "blocked — baseline stale, refresh below first"
            )
            image_children += [
                ui.Text("Approved visual guidance is attached · read-only", variant="caption"),
                ui.KeyValue(columns=1, items=[
                    {"key": "Visual intent", "value": visual_guidance.get("visual_intent", "—")},
                    {"key": "Style direction", "value": visual_guidance.get("style_direction", "—")},
                    {"key": "Approval basis", "value": f"Profile r{visual_guidance.get('profile_revision', '—')} · Visual Brand System (VBS) r{visual_guidance.get('vbs_revision', '—')}"},
                    {"key": "Snapshot", "value": visual_guidance.get("snapshot_hash", "—")},
                ]),
                ui.Text(
                    "Snapshot is a technical fingerprint for the audit trail only — nothing to act on here.",
                    variant="caption",
                ),
                ui.Divider(),
                ui.Text("→ Article Writer", variant="heading"),
                ui.KeyValue(columns=1, items=[{"key": "Writer handoff", "value": writer_status}]),
                ui.Text(
                    "No article draft is generated by this app; only the brief payload is assembled "
                    "for Article Writer to draft from.",
                    variant="caption",
                ),
                ui.Divider(),
                ui.Text("→ Media Studio", variant="heading"),
                ui.KeyValue(columns=1, items=[
                    {"key": "Media handoff", "value": media_status},
                    {"key": "Provider policy", "value": "Third-party first; Magnific only after technical failure"},
                ]),
                handoff_control,
                ui.Text(
                    "This creates no media package and does not generate images; it only assembles "
                    "the read-only brief Media Studio would need to start one.",
                    variant="caption",
                ),
            ]
        image_children_title = "Image requirements"
        accordion_sections.append({
            "id": "image_requirements", "title": image_children_title,
            "children": [ui.Text(
                "Read-only downstream guidance; no media is generated here",
                variant="caption",
            )] + image_children,
        })
        cta = brief.get("cta_goal", "")
        cta_block = [ui.Text(f"CTA goal: {cta}", variant="caption")] if cta else []
    else:
        accordion_sections.append({
            "id": "brief", "title": "Brief",
            "children": [ui.Text(
                "No brief yet. Ask Webbee to generate one for this opportunity "
                "(create_brief)."
            )],
        })
        cta_block = []

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
    status_row = ui.Row(gap=2, children=transitions or [ui.Text("Published — end of lifecycle.", variant="caption")])

    return ui.Stack(direction="v", gap=4, children=[
        back_button,
        header,
        ui.Accordion(allow_multiple=True, sections=accordion_sections),
        *cta_block,
        status_row,
    ])


def _quick_add_block(connected_sites: list[dict], existing_site_ids: set[str],
                     problems: list[dict] | None = None, has_cache: bool = True) -> object:
    """Quick Add: one real button per connected site not yet registered as a
    site profile here, pre-filling create_site_profile's site_id/domain/
    brand_name via ui.Call so a profile can be started in one click from
    whatever is already connected in WordPress Hub (or any future site
    provider in SITE_PROVIDER_APP_IDS) -- no retyping the domain, no chat.

    ALWAYS returns a card, never None: if there is nothing to offer, the card
    says WHY (no provider reachable / nothing connected / all already added /
    not loaded yet). The cache behind this is kept warm automatically by the
    csa_connected_sites_refresh schedule tick -- no Refresh button: a live
    ctx.extensions.call from inside a panel render is unreliable (see
    _cache_connected_sites), so a manual retry button would just be a coin
    flip.
    """
    problems = problems or []
    candidates = [s for s in connected_sites if s.get("site_id") not in existing_site_ids]

    if not has_cache and not connected_sites and not problems:
        return ui.Card(
            title="Quick Add — from connected sites",
            content=ui.Stack(direction="v", gap=2, children=[
                ui.Text(
                    "Not loaded yet — sites connected in WordPress Hub (or any "
                    "future site provider) appear here automatically within the hour.",
                    variant="caption",
                ),
            ]),
        )

    if candidates:
        body: list = [
            ui.Text(
                f"{len(candidates)} connected site(s) without a site profile yet — "
                "click one to register it.",
                variant="caption",
            ),
            ui.Stack(direction="h", gap=2, wrap=True, children=[
                ui.Stack(direction="v", gap=0, children=[
                    ui.Button(
                        _bare_domain(s.get("url", "")) or s["site_id"],
                        variant="secondary", size="sm", icon="Plus",
                        on_click=ui.Call(
                            "create_site_profile",
                            site_id=s["site_id"],
                            domain=(s.get("url", "").replace("https://", "").replace("http://", "").rstrip("/")
                                    or s["site_id"]),
                            brand_name=s.get("name") or "",
                        ),
                    ),
                    ui.Text(provider_display_name(s.get("provider", "")), variant="caption"),
                ])
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
        content=ui.Stack(direction="v", gap=2, children=body),
    )


_CANNIBALIZATION_ACTION_LABELS = {
    "merge": "🔗 Merge into one canonical article",
    "differentiate": "✂️ Differentiate angle/keyword focus",
    "canonicalize": "📌 Add canonical link, keep both",
    "dismiss": "✖️ Dismiss (false positive)",
}


async def _open_cannibalization_findings_block(ctx, site_id: str) -> list:
    """Render every OPEN cannibalization finding for this site as its own
    small card: the system's own recommendation is shown first (decided
    from overlap_score, never left for the user to work out), then all
    four options as direct action buttons so the user only has to pick
    one -- no free-text, no author required for this decision."""
    page = await ctx.store.query(
        "cannibalization_findings", where={"site_id": site_id, "status": "open"}, limit=50,
    )
    if not page.data:
        return []

    blocks: list = [ui.Text(f"⚔️ {len(page.data)} open cannibalization finding(s):", variant="caption")]
    for d in page.data:
        f = d.data
        rec = f.get("recommendation", "")
        rec_key = next((k for k in _CANNIBALIZATION_ACTION_LABELS if k in rec), "differentiate")
        titles = " vs. ".join(t for t in f.get("titles", []) if t) or "Untitled pair"
        buttons = [
            ui.Button(
                label + (" · recommended" if key == rec_key else ""),
                variant="primary" if key == rec_key else "secondary",
                size="sm", full_width=True,
                on_click=ui.Call("resolve_cannibalization_finding", finding_id=d.id, action=key),
            )
            for key, label in _CANNIBALIZATION_ACTION_LABELS.items()
        ]
        blocks.append(ui.Card(
            title=titles,
            subtitle=f"overlap {f.get('overlap_score', 0):.0%} · shared: {', '.join(f.get('shared_terms', [])[:6])}",
            content=ui.Stack(direction="v", gap=1, children=buttons),
        ))
    return blocks


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
        site_id = data.get("site_id", d.id)
        audit_page = await ctx.store.query("content_audits", where={"site_id": site_id}, limit=1)
        audit_items = [
            {"key": "Languages", "value": ", ".join(data.get("target_languages", [])) or "—"},
            {"key": "Categories", "value": ", ".join(data.get("content_categories", [])) or "—"},
            {"key": "Default CTA", "value": data.get("cta_default", "—")},
        ]
        if audit_page.data:
            audit = audit_page.data[0].data
            audit_items.append({
                "key": "Content audit",
                "value": (
                    f"{audit.get('total_posts', 0)} posts · {audit.get('thin_content_count', 0)} thin · "
                    f"{audit.get('cannibalization_pairs_found', 0)} cannibalizing pair(s) · "
                    f"as of {audit.get('audited_at', '?')}"
                ),
            })
            needs_doing = audit.get("needs_doing", [])
            audit_body = [ui.KeyValue(columns=1, items=audit_items)]
            if needs_doing:
                audit_body.append(ui.Text("Needs doing:", variant="caption"))
                audit_body.append(ui.List(items=[
                    ui.ListItem(id=f"needs-{i}", title=item) for i, item in enumerate(needs_doing)
                ]))
        else:
            audit_items.append({"key": "Content audit", "value": "⚠️ Never run — required before new opportunities"})
            audit_body = [ui.KeyValue(columns=1, items=audit_items)]

        decay_page = await ctx.store.query("content_decay_reports", where={"site_id": site_id}, limit=1)
        decay_body = []
        if decay_page.data:
            decay = decay_page.data[0].data
            decay_body = [
                ui.KeyValue(columns=1, items=[{
                    "key": "Content decay",
                    "value": (
                        f"{decay.get('decaying_count', 0)} decaying · {decay.get('improving_count', 0)} improving · "
                        f"{decay.get('new_count', 0)} new baseline · as of {decay.get('checked_at', '?')}"
                    ),
                }]),
            ]
            decaying_items = decay.get("decaying_items", [])
            if decaying_items:
                decay_body.append(ui.Text("Needs refreshing:", variant="caption"))
                decay_body.append(ui.List(items=[
                    ui.ListItem(id=f"decay-{i}", title=item.get("url", "")) for i, item in enumerate(decaying_items[:5])
                ]))

        visual_guidance = data.get("approved_visual_guidance", {})
        visual_body = []
        if visual_guidance:
            visual_body = [
                ui.Text("Approved visual guidance · read-only", variant="caption"),
                ui.KeyValue(columns=1, items=[
                    {"key": "Profile", "value": visual_guidance.get("profile_id", "Approved profile")},
                    {"key": "Visual intent", "value": visual_guidance.get("visual_intent", "—")},
                    {"key": "Style direction", "value": visual_guidance.get("style_direction", "—")},
                    {"key": "Provider policy", "value": "Third-party first; Magnific only after technical failure"},
                    {"key": "Approval basis", "value": f"Profile r{visual_guidance.get('profile_revision', '—')} · VBS r{visual_guidance.get('vbs_revision', '—')}"},
                    {"key": "Snapshot", "value": visual_guidance.get("snapshot_hash", "—")},
                ]),
                ui.Text(
                    "This guidance is passed downstream as a non-generative constraint; it does not create or generate media.",
                    variant="caption",
                ),
            ]

        audit_button = ui.Button(
            "🔎 Run content audit" if not audit_page.data else "🔎 Re-run content audit",
            variant="secondary", size="sm", full_width=True,
            on_click=ui.Call("run_content_audit", site_id=site_id),
        )

        # Only offer opportunity discovery once the mandatory content-audit
        # gate is satisfied — matches discover_opportunities's own server-side
        # check, so the button never fires into a guaranteed error.
        discover_button = ui.Button(
            "🧭 Discover from Search Console",
            variant="primary", size="sm", full_width=True,
            on_click=ui.Call("discover_opportunities_from_search_console", site_id=site_id, limit=20),
            disabled=not bool(audit_page.data),
        )

        cannibalization_form = ui.Form(
            action="check_keyword_cannibalization",
            submit_label="Check cannibalization",
            defaults={"site_id": site_id},
            children=[
                ui.Input(
                    param_name="candidate_keyword",
                    placeholder="New topic/keyword to check before writing (optional)",
                ),
            ],
        )

        cannibalization_body = _open_cannibalization_findings_block(ctx, site_id)

        cards.append(
            ui.Card(
                title=data.get("brand_name") or site_id,
                subtitle=data.get("domain", ""),
                content=ui.Stack(
                    direction="v", gap=2,
                    children=audit_body + decay_body + visual_body
                    + [audit_button, discover_button]
                    + await cannibalization_body
                    + [cannibalization_form],
                ),
            )
        )

    return ui.Stack(
        direction="v", gap=3,
        children=cards + [quick_add, new_site_form],
    )
