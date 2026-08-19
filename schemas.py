"""Pydantic params models + SDL entity contracts for Content Strategy app.

All params models are module-scope (V17 federal invariant).
Entities/EntityLists follow the read-tool contract (V23): a single record
is an sdl.Entity subclass, a list result is sdl.EntityList[T] — never a
bare dict wrapper.
"""
from __future__ import annotations

from pydantic import BaseModel, Field
from imperal_sdk import sdl

# ──────────────────────────────────────────────────────────────────────────
# Domain entities
# ──────────────────────────────────────────────────────────────────────────


class SiteProfile(sdl.Entity):
    """One managed site (e.g. g4s.md, climtec.md)."""
    site_id: str = ""  # stable business key, e.g. 'g4s.md' — NOT the store doc id
    domain: str = ""
    brand_name: str = ""
    business_description: str = ""
    business_description_i18n: dict[str, str] = {}  # optional per-language override, e.g. {"ru": "...", "ro": "..."}
    target_languages: list[str] = []
    content_categories: list[str] = []
    content_categories_i18n: dict[str, list[str]] = {}  # optional per-language override, e.g. {"ru": [...], "ro": [...]} -- avoids splicing untranslated category text into another language's generated titles
    cta_default: str = ""
    cta_default_i18n: dict[str, str] = {}  # optional per-language override for cta_default
    external_sources_i18n: dict[str, list[str]] = {}  # verified source URLs, keyed by source language
    approved_visual_guidance: dict[str, object] = {}  # read-only payload from Brand Strategy Hub's approved Visual Profile handoff
    requires_named_author: bool = False  # E-E-A-T gate: YMYL/expertise-sensitive sites must attach a real ContentAuthor to every brief


class SiteProfileList(sdl.EntityList[SiteProfile]):
    pass


class UpdateSiteProfileParams(BaseModel):
    """Patch an existing site profile with only the given fields. Needed so
    a re-run of build_content_strategy_handoff (Brand Strategy Hub) can
    actually refresh a site profile's content_categories/business_description
    etc. -- create_site_profile refuses to run again once a site_id exists,
    and until this existed there was NO way to update one afterwards."""
    site_id: str = Field(description="Site id from list_site_profiles — never invent it")
    brand_name: str | None = Field(default=None, description="New brand name; omit to keep")
    business_description: str | None = Field(default=None, description="New business description; omit to keep")
    business_description_i18n: dict[str, str] | None = Field(default=None, description="Replace per-language business_description overrides; omit to keep")
    target_languages: list[str] | None = Field(default=None, description="Replace target languages; omit to keep")
    content_categories: list[str] | None = Field(default=None, description="Replace content categories/topics; omit to keep")
    content_categories_i18n: dict[str, list[str]] | None = Field(default=None, description="Replace per-language content_categories overrides, e.g. {'ru': [...], 'ro': [...]}; omit to keep. Prevents generate_strategic_topics from splicing untranslated category text into another language's titles.")
    cta_default: str | None = Field(default=None, description="New default CTA; omit to keep")
    cta_default_i18n: dict[str, str] | None = Field(default=None, description="Replace per-language CTA overrides; omit to keep")
    requires_named_author: bool | None = Field(default=None, description="Replace the E-E-A-T named-author requirement flag; omit to keep")
    external_sources_i18n: dict[str, list[str]] | None = Field(default=None, description="Replace verified external source URLs keyed by source language, e.g. {'ru': ['https://...'], 'ro': ['https://...']}; omit to keep")
    approved_visual_guidance: dict[str, object] | None = Field(default=None, description="Replace the read-only approved Visual Profile guidance relayed from Brand Strategy Hub; omit to keep. This stores guidance only and never creates or generates media.")


class Opportunity(sdl.Entity):
    """One candidate content opportunity, sourced from GSC / SEO Audit / manual /
    strategic gap analysis (this app's own topic-generation engine)."""
    site_id: str = ""
    source: str = ""  # gsc | seo_audit | manual | mixed | strategic_gap_analysis
    primary_query: str = ""
    supporting_queries: list[str] = []
    query_cluster_label: str = ""
    intent: str = ""  # informational | commercial | navigational
    funnel_stage: str = ""  # tofu | mofu | bofu -- where this topic sits in the buyer journey, never left implicit
    funnel_stage_reason: str = ""  # why this stage was assigned -- auditable, not a silent guess
    impressions: int = 0
    clicks: int = 0
    ctr: float = 0.0
    avg_position: float = 0.0
    business_relevance_score: float = 0.0
    seo_opportunity_score: float = 0.0
    total_priority_score: float = 0.0
    recommended_content_type: str = ""
    recommended_target_url: str = ""
    strategic_rationale: str = ""  # for source='strategic_gap_analysis': WHY this topic was generated (which gap it fills)
    status: str = "idea"  # idea|brief_ready|draft_requested|draft_ready|approved|published


class OpportunityList(sdl.EntityList[Opportunity]):
    pass


class TopicCluster(sdl.Entity):
    """Grouped content theme across related queries."""
    site_id: str = ""
    label: str = ""
    primary_topic: str = ""
    related_queries: list[str] = []
    dominant_intent: str = ""
    gap_level: str = ""  # none|partial|full
    priority_score: float = 0.0


class TopicClusterList(sdl.EntityList[TopicCluster]):
    pass


class ArticleBrief(sdl.Entity, sdl.Bodied):
    """Structured brief handed downstream to article writing / image planning."""
    site_id: str = ""
    opportunity_id: str = ""
    working_title: str = ""
    target_language: str = ""
    target_audience: str = ""
    search_intent: str = ""
    funnel_stage: str = ""  # tofu | mofu | bofu -- copied from the source opportunity, drives outline shape and CTA strength
    primary_query: str = ""
    secondary_queries: list[str] = []
    outline: list[str] = []
    cta_goal: str = ""
    internal_link_targets: list[str] = []
    differentiation_notes: str = ""
    image_requirements: list[str] = []
    text_policy: str = "no_text"  # Content Strategy's own signal into the image prompt: 'no_text' or 'allow_text'
    image_text: str = ""  # the EXACT words to render when text_policy='allow_text'; always "" for 'no_text'
    approved_visual_guidance: dict[str, object] = {}  # approved, non-generative VBS/Profile constraint copied from the site
    key_action_page_url: str = ""
    key_action_page_language: str = ""
    key_action_page_reason: str = ""
    external_link_url: str = ""
    external_link_language: str = ""
    external_link_language_priority: list[str] = []
    author_id: str = ""
    author_name: str = ""
    author_bio: str = ""
    author_credentials: list[str] = []
    resolved_category: str = ""  # WordPress category name this brief is resolved to -- either an existing category chosen by real overlap, or a new name to create; never a generic default
    resolved_category_id: int = 0  # WordPress term id when matched to an EXISTING category; 0 when resolved_category is a brand-new name to be created at publish time
    category_resolution_reason: str = ""  # why this category was chosen -- auditable, not a silent guess
    brand_readiness_checked: bool = False  # True once create_brief has run the mandatory Brand Strategy Hub readiness gate for this brief's site
    brand_id: str = ""  # Brand Strategy Hub brand_id resolved during the readiness gate, if any
    status: str = "brief_ready"


class ArticleBriefList(sdl.EntityList[ArticleBrief]):
    pass


class ContentAuthor(sdl.Entity):
    """A real, named author with declared expertise — the E-E-A-T unit this
    pipeline attaches to a brief instead of leaving articles unattributed.
    Not a generated persona: name/bio/credentials are entered by a human
    about a real person (the site owner, an in-house expert, or a
    contracted writer with actual domain credentials)."""
    site_id: str = ""
    name: str = ""
    bio: str = ""
    credentials: list[str] = []  # e.g. ["Certified HVAC Engineer", "10+ years in refrigeration"]
    expertise_areas: list[str] = []  # topics this author is qualified to write about
    author_page_url: str = ""  # this site's own /author/slug page, if it exists
    same_as: list[str] = []  # LinkedIn/professional profile URLs for Person schema's sameAs


class ContentAuthorList(sdl.EntityList[ContentAuthor]):
    pass


class CreateContentAuthorParams(BaseModel):
    site_id: str = Field(description="Site id from list_site_profiles — never invent it")
    name: str = Field(min_length=1, description="Real author name, e.g. 'Ion Popescu'")
    bio: str = Field("", description="Short bio establishing real expertise/experience")
    credentials: list[str] = Field(default_factory=list, description="Real credentials/qualifications, e.g. ['Certified HVAC Engineer']")
    expertise_areas: list[str] = Field(default_factory=list, description="Topics this author is qualified to write about")
    author_page_url: str = Field("", description="This site's own author bio page URL, if published")
    same_as: list[str] = Field(default_factory=list, description="Professional profile URLs (LinkedIn etc.) for Person schema's sameAs")


class ListContentAuthorsParams(BaseModel):
    site_id: str = Field("", description="Optional site filter. Empty = all sites.")
    limit: int = Field(20, description="Max items to return (1-100)")


class QueueItem(sdl.Entity):
    """Editorial workflow state — one row per opportunity/brief in flight."""
    site_id: str = ""
    brief_id: str = ""
    opportunity_id: str = ""
    target_language: str = ""
    content_type: str = "article"
    lifecycle_status: str = "idea"
    assigned_agent: str = "Webbee"
    published_url: str = ""
    scheduled_date: str = ""  # YYYY-MM-DD — set by build_content_calendar
    external_project_id: str = ""  # Article Writer project id, once linked
    external_article_id: str = ""  # Article Writer article id, once linked
    fact_checked: bool = False  # a named human has verified factual claims/citations
    fact_checked_by: str = ""  # real name of the human who fact-checked it
    fact_checked_at: str = ""
    edited_by: str = ""  # real name of the human editor who reviewed/edited the draft
    edited_at: str = ""


class QueueItemList(sdl.EntityList[QueueItem]):
    pass


# ──────────────────────────────────────────────────────────────────────────
# @chat.function params models
# ──────────────────────────────────────────────────────────────────────────


class QuerySignal(BaseModel):
    """One query-level signal, typically fetched from Google Search Console
    (top_queries / striking_distance) by the orchestrator and passed in here.
    This app does not call other extensions directly — Webbee chains the
    GSC connector's output into this tool's input."""
    query: str = Field(description="The search query text")
    impressions: int = Field(0, description="Search impressions for this query")
    clicks: int = Field(0, description="Clicks for this query")
    ctr: float = Field(0.0, description="Click-through rate, 0-1")
    avg_position: float = Field(0.0, description="Average SERP position")
    source: str = Field("gsc", description="Where this signal came from: gsc|seo_audit|manual")


class DiscoverOpportunitiesParams(BaseModel):
    site_id: str = Field(description="Site to discover opportunities for, e.g. 'g4s.md' or 'climtec.md'")
    queries: list[QuerySignal] = Field(
        default_factory=list,
        description=(
            "Query signals to turn into opportunities — typically the output of "
            "the Google Search Console connector's top_queries or "
            "striking_distance tools, fetched first and passed in here. "
            "If empty, no opportunities are created (this tool does not fetch "
            "GSC data itself)."
        ),
    )
    limit: int = Field(20, description="Max opportunities to surface (1-100)")


class DiscoverOpportunitiesFromSearchConsoleParams(BaseModel):
    """UI-only convenience wrapper: fetches the site's own Search Console
    top_queries via IPC (no manual JSON copy-paste) and feeds them straight
    into discover_opportunities's existing scoring/clustering/audit-gate
    logic. Exists so the panel's 'Discover from Search Console' button has
    something concrete to call — discover_opportunities itself stays pure
    and testable, taking queries as plain input."""
    site_id: str = Field(description="Site id from list_site_profiles — never invent it")
    limit: int = Field(20, description="Max Search Console query rows to fetch and turn into opportunities (1-100)")


class GenerateStrategicTopicsParams(BaseModel):
    """Params for the 'beyond existing opportunities' strategic topic engine.

    Unlike discover_opportunities (which only scores/clusters query signals
    the caller already fetched from GSC/SEO Audit), this reasons from the
    site's OWN declared content_categories and produces genuinely NEW
    candidate topics that no query signal has surfaced yet -- covering
    tofu/mofu/bofu funnel stages deliberately rather than by accident."""
    site_id: str = Field(description="Site id from list_site_profiles — never invent it")
    language: str = Field("", description="Language code to generate topic titles in (e.g. 'ro', 'ru'). Empty = site profile's first target language.")
    per_category: int = Field(3, description="How many new candidate topics to generate per uncovered content category (1-10)")
    funnel_focus: str = Field("", description="Optional: force every generated topic to one funnel stage -- 'tofu'|'mofu'|'bofu'. Empty = rotate evenly across all three stages per category. Use e.g. 'bofu' when TOFU/MOFU coverage already comes from real query signals and the deliberate gap is specifically bottom-of-funnel, ready-to-buy content.")
    limit: int = Field(20, description="Max strategic opportunities to create this round (1-100)")


class ListOpportunitiesParams(BaseModel):
    site_id: str = Field("", description="Optional site filter. Empty = all sites.")
    status: str = Field("", description="Optional lifecycle status filter, e.g. 'idea', 'approved'.")
    limit: int = Field(20, description="Max items to return (1-100)")


class CreateBriefParams(BaseModel):
    opportunity_id: str = Field(description="UUID of the opportunity to turn into a brief — from list_opportunities, never invented")
    target_language: str = Field(
        "", description="Language code for this brief (e.g. 'ru', 'ro'). Empty = use the site "
                        "profile's first target language. Pass explicitly to create one brief "
                        "per language for the same opportunity — a multilingual site is expected "
                        "to get a separate brief per language, not one brief reused across languages."
    )
    target_query: str = Field(
        "", description="The REAL query/title text for THIS brief's target_language, e.g. pulled "
                        "from that language's own Search Console data. An Opportunity is a "
                        "language-neutral topic and its stored primary_query is written in "
                        "whatever language it was originally discovered in -- reusing that text "
                        "as-is for a brief in a DIFFERENT language would give the brief a title in "
                        "the wrong language. Required whenever the opportunity's own query text is "
                        "written in a different script than target_language (create_brief detects "
                        "this and errors with TARGET_QUERY_REQUIRED if target_query is missing); "
                        "optional otherwise. Never invent this value -- pass real text or omit it."
    )
    author_id: str = Field("", description="ContentAuthor id from list_content_authors to attribute this article to. Required when the site profile has requires_named_author=true; optional otherwise but recommended for E-E-A-T.")


class ListBriefsParams(BaseModel):
    site_id: str = Field("", description="Optional site filter. Empty = all sites.")
    limit: int = Field(20, description="Max items to return (1-100)")


class ListQueueParams(BaseModel):
    site_id: str = Field("", description="Optional site filter. Empty = all sites.")
    lifecycle_status: str = Field("", description="Optional status filter, e.g. 'idea', 'brief_ready', 'approved', 'published'.")
    limit: int = Field(50, description="Max items to return (1-100)")


class UpdateQueueStatusParams(BaseModel):
    queue_item_id: str = Field(description="UUID of the queue item to update — from list_queue, never invented")
    lifecycle_status: str = Field(description="New status: idea|brief_ready|draft_requested|draft_ready|approved|published")
    published_url: str = Field("", description="Optional published URL, set when lifecycle_status='published'")


class RecordEditorialSignoffParams(BaseModel):
    queue_item_id: str = Field(description="UUID of the queue item to sign off on — from list_queue, never invented")
    fact_checked_by: str = Field("", description="Real name of the human who fact-checked the draft's claims/citations. Empty = leave fact-check status unchanged.")
    edited_by: str = Field("", description="Real name of the human editor who reviewed/edited the draft. Empty = leave edit status unchanged.")


class CreateSiteProfileParams(BaseModel):
    site_id: str = Field(description="Unique site identifier, e.g. 'g4s.md'")
    domain: str = Field(description="Domain, e.g. 'g4s.md'")
    brand_name: str = Field("", description="Brand/company name")
    business_description: str = Field("", description="One or two sentences on what the business does")
    business_description_i18n: dict[str, str] = Field(default_factory=dict, description="Optional per-language override of business_description, e.g. {'ru': '...', 'ro': '...'} — used by create_brief so target_audience is written in the brief's actual target_language instead of always falling back to business_description")
    target_languages: list[str] = Field(default_factory=list, description="Target languages for content, e.g. ['ru','ro']")
    content_categories: list[str] = Field(default_factory=list, description="Content categories/topics this site covers")
    cta_default: str = Field("", description="Default call-to-action goal for articles on this site")
    cta_default_i18n: dict[str, str] = Field(default_factory=dict, description="Optional per-language override of cta_default, e.g. {'ru': '...', 'ro': '...'}")
    requires_named_author: bool = Field(False, description="E-E-A-T gate: when true, create_brief refuses to run until a ContentAuthor is registered and passed in, and the brief always carries a named author for Article Writer/Rank Math. Set true for YMYL topics (health, finance, legal, safety).")
    external_sources_i18n: dict[str, list[str]] = Field(default_factory=dict, description="Verified authoritative external source URLs keyed by their content language, e.g. {'ru': ['https://...'], 'ro': ['https://...']}. The pipeline selects the article language first, then this site's configured language-priority fallback.")
    approved_visual_guidance: dict[str, object] = Field(default_factory=dict, description="Read-only approved Visual Profile guidance relayed from Brand Strategy Hub. It is appended to downstream briefs as a non-generative media constraint.")


class ListSiteProfilesParams(BaseModel):
    limit: int = Field(20, description="Max items to return (1-100)")


# ──────────────────────────────────────────────────────────────────────────
# Monthly content calendar — publication grid
# ──────────────────────────────────────────────────────────────────────────

class ContentCalendarEntry(sdl.Entity):
    """One scheduled slot on the monthly publication grid."""
    site_id: str = ""
    queue_item_id: str = ""
    scheduled_date: str = ""  # YYYY-MM-DD
    working_title: str = ""
    lifecycle_status: str = "idea"
    content_type: str = "article"
    visual_baseline_state: str = "not_attached"  # current|stale|not_attached


class ContentCalendarEntryList(sdl.EntityList[ContentCalendarEntry]):
    pass


class BuildContentCalendarParams(BaseModel):
    site_id: str = Field(description="Site to build the monthly calendar for, e.g. 'g4s.md'")
    year: int = Field(description="Calendar year, e.g. 2026")
    month: int = Field(ge=1, le=12, description="Calendar month, 1-12")
    posts_per_week: int = Field(2, ge=1, le=14, description="How many publication slots per week to fill")
    weekdays: list[int] = Field(
        default_factory=lambda: [1, 4],
        description="Preferred ISO weekdays for publishing (1=Monday..7=Sunday); cycled to fill posts_per_week",
    )
    queue_item_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Explicit queue items (from list_queue, status brief_ready or later) to place "
            "on the grid, in priority order. If empty, the highest-priority unscheduled "
            "items for this site are picked automatically."
        ),
    )


class GetContentCalendarParams(BaseModel):
    site_id: str = Field("", description="Optional site filter. Empty = all sites.")
    year: int = Field(0, description="Optional calendar year filter; 0 = all")
    month: int = Field(0, ge=0, le=12, description="Optional calendar month filter (1-12); 0 = all")


# ──────────────────────────────────────────────────────────────────────────
# Article Writer pipeline linkage — pass-through handoff, no direct IPC
# ──────────────────────────────────────────────────────────────────────────

class LinkExternalArticleParams(BaseModel):
    queue_item_id: str = Field(description="UUID of the queue item to link — from list_queue, never invented")
    external_project_id: str = Field("", description="Article Writer project id (imperal-article-writer-extension), once created")
    external_article_id: str = Field("", description="Article Writer article id, once created via that app's create_article")


class BuildWriterBriefParams(BaseModel):
    brief_id: str = Field(description="UUID of an existing article brief — from list_briefs, never invented")


class MediaBriefHandoff(sdl.Entity):
    """Read-only payload for Media Studio's create_media_brief; never generates assets."""
    site: str = ""
    article_title: str = ""
    native_title: str = ""
    summary: str = ""
    lang: str = ""
    inline_count: int = 2
    model: str = "auto"  # third-party provider default; Magnific is technical-failure fallback only
    style_direction: str = ""
    text_policy: str = "no_text"  # carried from the brief's own text_policy into Media Studio's create_media_brief
    image_text: str = ""  # the EXACT words carried from the brief -- required by Media Studio whenever text_policy='allow_text'
    visual_subject: str = ""  # what's physically in the shot + action -- derived from primary_query+resolved_category, never invented; "" only if both are somehow blank
    visual_environment: str = ""  # scene/setting -- from the approved Visual Profile's own visual_intent when set; "" is honest (no visual_intent on file) and Media Studio's own generic fallback still guarantees the final prompt is never blank there
    provider_policy: str = "third_party_only_unless_technical_failure"
    generation_boundary: str = "This handoff does not create a media package or generate assets."
    source_brief_id: str = ""
    approved_visual_profile_id: str = ""
    approved_visual_profile_revision: int = 0
    approved_vbs_id: str = ""
    approved_vbs_revision: int = 0
    approved_snapshot_hash: str = ""


class BuildMediaBriefHandoffParams(BaseModel):
    brief_id: str = Field(description="UUID of an existing article brief — from list_briefs, never invented")


class RefreshBriefVisualGuidanceParams(BaseModel):
    brief_id: str = Field(description="UUID of an existing article brief — from list_briefs, never invented")


class UpdateBriefTitleParams(BaseModel):
    brief_id: str = Field(description="UUID of an existing article brief — from list_briefs, never invented")
    working_title: str = Field(min_length=1, description="The corrected title, written in the brief's OWN target_language — never invent a translation, pass real text (e.g. from that language's own Search Console data or a human-provided translation)")


class WriterBrief(sdl.Entity, sdl.Bodied):
    """Everything Article Writer needs to create_project/create_article/generate_article
    for this brief, assembled in the exact shape that app's tools expect. There is no
    direct extension-to-extension call here (no cross-extension IPC exists on this
    platform) — Webbee reads this entity's fields and passes them into Article Writer's
    own tools in the next chat turn. The `body` (from sdl.Bodied) is the full brief as
    Markdown, ready to paste as Article Writer's brief/keyword input."""
    site_id: str = ""
    queue_item_id: str = ""
    brief_id: str = ""
    suggested_project_name: str = ""  # -> imperal-article-writer-extension.create_project(name=...)
    site_url: str = ""  # -> create_project(site_url=...)
    target_keyword: str = ""  # -> create_article(keyword=...) / generate_article
    working_title: str = ""  # -> create_article(title=...)
    target_audience: str = ""
    secondary_queries: list[str] = []
    outline: list[str] = []
    cta_goal: str = ""
    internal_link_targets: list[str] = []
    key_action_page_url: str = ""
    key_action_page_language: str = ""
    key_action_page_reason: str = ""
    external_link_url: str = ""
    external_link_language: str = ""
    external_link_language_priority: list[str] = []
    differentiation_notes: str = ""
    author_name: str = ""
    author_bio: str = ""
    author_credentials: list[str] = []
    resolved_category: str = ""  # -> wordpress-hub create_post(category=...) -- always resolved by create_brief, never a generic default
    category_resolution_reason: str = ""
    approved_visual_guidance: dict[str, object] = {}


# ──────────────────────────────────────────────────────────────────────────
# Cross-app site discovery (Quick Add source)
# ──────────────────────────────────────────────────────────────────────────


class ConnectedSite(sdl.Entity):
    """One site read from a site-provider extension (WordPress Hub today,
    more providers later) — the raw material behind the Quick Add list."""
    site_id: str = ""
    url: str = ""
    status: str = ""
    provider: str = ""
    already_tracked: bool = False


class ConnectedSiteList(sdl.EntityList[ConnectedSite]):
    pass


class ListConnectedSitesParams(BaseModel):
    limit: int = Field(50, description="Max items to return (1-100)")


# ──────────────────────────────────────────────────────────────────────────
# Web-level competitor tracking (site-scoped — distinct from Brand Strategy
# Hub's brand-scoped competitors; a site can rank against different SERP
# competitors than the brand's business-level rivals).
# ──────────────────────────────────────────────────────────────────────────


class SiteCompetitorProfile(sdl.Entity):
    """One tracked competitor for a managed site — SERP/content rival,
    not necessarily the same set as the brand's business-level competitors."""
    site_id: str = ""
    url: str = ""
    notes: str = ""
    competing_topics: list[str] = []
    strengths: list[str] = []
    weaknesses: list[str] = []


class SiteCompetitorProfileList(sdl.EntityList[SiteCompetitorProfile]):
    pass


class AddSiteCompetitorParams(BaseModel):
    site_id: str = Field(description="Site id from list_site_profiles — never invent it")
    name: str = Field(min_length=1, description="Competitor name")
    url: str = Field("", description="Competitor website")
    competing_topics: list[str] = Field(
        default_factory=list,
        description="Topics/queries where this competitor ranks or competes for the same content",
    )
    strengths: list[str] = Field(default_factory=list, description="What the competitor does well")
    weaknesses: list[str] = Field(default_factory=list, description="Where the competitor falls short")
    notes: str = Field("", description="Freeform observations")


class ListSiteCompetitorsParams(BaseModel):
    site_id: str = Field("", description="Optional site filter. Empty = all sites.")
    limit: int = Field(20, description="Max items to return (1-100)")


# ──────────────────────────────────────────────────────────────────────────
# Mandatory pre-strategy content audit + keyword cannibalization
#
# The content pipeline must never plan new articles blind to what already
# exists: discover_opportunities is gated on a recent audit existing for the
# site (see main.py), and the audit surfaces cannibalization explicitly so
# it is visible in the panel, not just implied by "everything looks fine".
# ──────────────────────────────────────────────────────────────────────────


class ExistingContentItem(sdl.Entity):
    """One already-published post pulled live from the site for the audit."""
    site_id: str = ""
    slug: str = ""
    link: str = ""
    word_count: int = 0
    is_thin: bool = False  # < 300 words — too short to compete for its topic
    categories: list[int] = []
    lang: str = ""
    top_terms: list[str] = []  # cheap keyword signature used for cannibalization matching


class ExistingContentItemList(sdl.EntityList[ExistingContentItem]):
    pass


class CannibalizationFinding(sdl.Entity):
    """Two or more existing articles competing for the same topic/keywords —
    they will split ranking signal instead of reinforcing one page. The
    recommendation is decided by the system itself from overlap_score
    (never left for the user to work out); the user's job is only to pick
    one of the offered options via resolve_cannibalization_finding."""
    site_id: str = ""
    shared_terms: list[str] = []
    overlap_score: float = 0.0  # 0..1, share of significant terms in common
    urls: list[str] = []
    titles: list[str] = []
    recommendation: str = ""  # merge | differentiate | canonicalize -- decided by the system, offered as the default option
    status: str = "open"  # open | resolved | dismissed
    chosen_action: str = ""  # merge | differentiate | canonicalize | dismiss -- set by resolve_cannibalization_finding
    resolved_at: str = ""


class CannibalizationFindingList(sdl.EntityList[CannibalizationFinding]):
    pass


class ResolveCannibalizationParams(BaseModel):
    finding_id: str = Field(description="Cannibalization finding id from check_keyword_cannibalization")
    action: str = Field(
        description="The option the user picked in response to the system's own recommendation: "
                     "merge | differentiate | canonicalize | dismiss"
    )


class ContentAuditReport(sdl.Entity):
    """Result of a deep audit of a site's EXISTING content — mandatory
    before any new content strategy work for that site. Fields deliberately
    make gaps visible rather than only reporting what's fine."""
    site_id: str = ""
    total_posts: int = 0
    posts_by_language: dict[str, int] = Field(default_factory=dict)
    thin_content_count: int = 0
    thin_content_urls: list[str] = Field(default_factory=list)
    missing_excerpt_count: int = 0
    cannibalization_pairs_found: int = 0
    covered_topics: list[str] = Field(default_factory=list)
    needs_doing: list[str] = Field(default_factory=list)  # explicit action items, always shown
    audited_at: str = ""


class RunContentAuditParams(BaseModel):
    site_id: str = Field(description="Site id from list_site_profiles — never invent it")


class GetContentAuditParams(BaseModel):
    site_id: str = Field(description="Site id from list_site_profiles — never invent it")


class ContentPerformanceSignal(BaseModel):
    """One URL's performance-over-a-period reading, typically fetched from the
    Google Search Console connector (top_queries aggregated by page) or the
    DataForSEO connector (check_serp_ranking / rank history) by the
    orchestrator and passed in here. This app does not call other extensions
    directly for this — Webbee chains that connector's output into this
    tool's input, exactly like QuerySignal for discover_opportunities."""
    url: str = Field(description="The published page's own URL, matched against existing_content_items")
    clicks: int = Field(0, description="Clicks over the reporting period")
    impressions: int = Field(0, description="Impressions over the reporting period")
    avg_position: float = Field(0.0, description="Average SERP position over the reporting period")
    period_days: int = Field(28, description="Length of the reporting period in days, e.g. 28 for GSC's default window")
    source: str = Field("gsc", description="Where this signal came from: gsc|dataforseo|manual")


class TrackContentDecayParams(BaseModel):
    site_id: str = Field(description="Site id from list_site_profiles — never invent it")
    signals: list[ContentPerformanceSignal] = Field(
        default_factory=list,
        description=(
            "Current-period performance readings for this site's published "
            "URLs. Each is compared against the PREVIOUS reading recorded "
            "for that same URL (from the last time this was called) to "
            "detect decay -- so this must be called periodically (e.g. "
            "monthly) for a trend to build up. The first call for a URL "
            "only establishes the baseline; it cannot yet show decay."
        ),
    )


class DecayingContentItem(BaseModel):
    """One URL's decay verdict from comparing this reading to its last one."""
    url: str = ""
    title: str = ""
    previous_clicks: int = 0
    current_clicks: int = 0
    click_change_pct: float = 0.0  # negative = clicks fell
    previous_position: float = 0.0
    current_position: float = 0.0
    position_change: float = 0.0  # positive = ranking got WORSE (position number went up)
    verdict: str = "new"  # new | decaying | improving | stable
    recommendation: str = ""


class ContentDecayReport(sdl.Entity):
    """Result of comparing a site's published URLs' current performance
    against their own last recorded reading -- surfaces which articles are
    losing traffic/ranking so they can be refreshed before they die
    entirely, instead of only ever writing new content."""
    site_id: str = ""
    tracked_count: int = 0
    decaying_count: int = 0
    improving_count: int = 0
    new_count: int = 0
    decaying_items: list[DecayingContentItem] = Field(default_factory=list)
    needs_doing: list[str] = Field(default_factory=list)
    checked_at: str = ""


class GetContentDecayParams(BaseModel):
    site_id: str = Field(description="Site id from list_site_profiles — never invent it")


class PurgePipelineDataParams(BaseModel):
    """No fields: gated purely by action_type="destructive" (platform's own
    KAV confirmation card). A manual confirm_wipe field here would
    double-prompt and break the platform's "what you saw is what runs"
    guarantee -- see POST_AUDIT_LOG_STANDARD.md at the Apps root."""
    pass


class PurgeSitePipelineDataParams(BaseModel):
    site_id: str = Field(description="Site id from list_site_profiles — never invent it. Only THIS site's pipeline data is removed; every other site's opportunities/briefs/queue/etc. are untouched.")


class PurgeResult(sdl.Entity):
    """Outcome of a full pipeline data wipe — counts removed per collection,
    and which site profiles were kept untouched."""
    opportunities_removed: int = 0
    briefs_removed: int = 0
    queue_items_removed: int = 0
    competitors_removed: int = 0
    content_audits_removed: int = 0
    cannibalization_findings_removed: int = 0
    authors_removed: int = 0
    decay_readings_removed: int = 0
    kpi_snapshots_removed: int = 0
    outreach_targets_removed: int = 0
    kept_site_ids: list[str] = Field(default_factory=list)


class CheckCannibalizationParams(BaseModel):
    site_id: str = Field(description="Site id from list_site_profiles — never invent it")
    candidate_keyword: str = Field(
        "", description="Optional: a NEW topic/keyword being considered for a fresh article — "
                        "checked against existing content so a duplicate topic is caught BEFORE writing, "
                        "not after. Empty = audit existing articles against each other only.")


# ──────────────────────────────────────────────────────────────────────────
# Phase 6: active link-building / outreach workflow
#
# Turns the currently passive backlink monitoring (DataForSEO tracks a
# competitor's backlink profile) into an ACTIVE, human-run outreach
# pipeline: concrete named targets with a real lifecycle status, so
# link-building becomes trackable work instead of only observed data.
# ──────────────────────────────────────────────────────────────────────────

_OUTREACH_STATUSES = (
    "prospected", "contacted", "replied", "link_acquired", "declined", "no_response"
)


class CreateOutreachTargetParams(BaseModel):
    site_id: str = Field(description="Site id from list_site_profiles — never invent it")
    target_domain: str = Field(min_length=1, description="Domain being targeted for a backlink/mention, e.g. 'example-blog.com'")
    target_url: str = Field("", description="Specific page URL on that domain, if known (e.g. a broken-link or resource page)")
    tactic: str = Field(
        "guest_post",
        description="Link-building tactic: guest_post|broken_link|resource_page|linkable_asset|digital_pr|mention_upgrade|other",
    )
    linked_article_url: str = Field("", description="This site's own published URL being pitched/linked, if applicable")
    contact_name: str = Field("", description="Real name of the contact person at the target site, if known")
    contact_email: str = Field("", description="Contact email for outreach, if known")
    notes: str = Field("", description="Freeform context: why this target, what pitch angle, source of the lead")


class UpdateOutreachStatusParams(BaseModel):
    outreach_id: str = Field(description="UUID of the outreach target to update — from list_outreach_targets, never invented")
    status: str = Field(description=f"New status: one of {', '.join(_OUTREACH_STATUSES)}")
    notes: str = Field("", description="Optional note to append about this status change, e.g. reply content or reason for decline")
    acquired_url: str = Field("", description="The actual URL where the backlink now lives, set when status='link_acquired'")


class OutreachTarget(sdl.Entity):
    """One tracked link-building outreach effort — a named target site,
    tactic, and its real lifecycle status. This is deliberately manual
    and human-run: no outreach email is ever sent by this app."""
    site_id: str = ""
    target_domain: str = ""
    target_url: str = ""
    tactic: str = "guest_post"
    linked_article_url: str = ""
    contact_name: str = ""
    contact_email: str = ""
    status: str = "prospected"
    acquired_url: str = ""
    notes: str = ""
    status_history: list[dict] = Field(default_factory=list, description="Chronological [{status, at}] log of every status change")
    created_at: str = ""
    updated_at: str = ""


class OutreachTargetList(sdl.EntityList[OutreachTarget]):
    pass


class ListOutreachTargetsParams(BaseModel):
    site_id: str = Field("", description="Optional site filter. Empty = all sites.")
    status: str = Field("", description="Optional status filter, e.g. 'contacted', 'link_acquired'.")
    limit: int = Field(50, description="Max items to return (1-100)")


class LinkBuildingReport(sdl.Entity):
    """Summary of a site's active outreach pipeline -- counts by status
    plus a simple reply/acquisition rate, so link-building is a visible
    funnel instead of a pile of untracked emails."""
    site_id: str = ""
    total_targets: int = 0
    by_status: dict[str, int] = Field(default_factory=dict)
    reply_rate_pct: float = 0.0  # replied+link_acquired+declined / (contacted+replied+link_acquired+declined+no_response)
    acquisition_rate_pct: float = 0.0  # link_acquired / total_targets
    links_acquired: int = 0
    needs_doing: list[str] = Field(default_factory=list)


class GetLinkBuildingReportParams(BaseModel):
    site_id: str = Field(description="Site id from list_site_profiles — never invent it")


# ──────────────────────────────────────────────────────────────────────────
# Phase 5: unified KPI dashboard
#
# Same pattern as QuerySignal / ContentPerformanceSignal: this app does not
# call GA4, GSC, or DataForSEO connectors directly. The caller fetches each
# connector's own numbers first (google-analytics-bluebee's get_overview /
# compare_periods, google-search-console-connector's top_queries,
# dataforseo-connector's list_tracked_keywords / get_backlink_profile) and
# passes the resulting totals in here as one periodic snapshot.
# ──────────────────────────────────────────────────────────────────────────

class RecordKpiSnapshotParams(BaseModel):
    site_id: str = Field(description="Site id from list_site_profiles — never invent it")
    period_label: str = Field(description="Reporting period label, e.g. '2026-07' or '2026-W28' — used to order snapshots and label the trend")
    ga4_sessions: int = Field(0, description="GA4 sessions for the period (from get_overview/compare_periods)")
    ga4_users: int = Field(0, description="GA4 active/total users for the period")
    ga4_conversions: int = Field(0, description="GA4 key events (conversions) for the period")
    gsc_clicks: int = Field(0, description="Search Console clicks for the period (from top_queries aggregated)")
    gsc_impressions: int = Field(0, description="Search Console impressions for the period")
    gsc_avg_position: float = Field(0.0, description="Search Console average position for the period")
    dataforseo_avg_rank: float = Field(0.0, description="Average tracked-keyword rank for the period (from list_tracked_keywords)")
    dataforseo_keywords_top10: int = Field(0, description="Count of tracked keywords ranking in the top 10 for the period")
    referring_domains: int = Field(0, description="Backlink referring-domain count for the period (from get_backlink_profile), 0 if not tracked")
    notes: str = Field("", description="Optional free-text context for this snapshot, e.g. 'algorithm update' or 'launched 3 new articles'")


class KpiSnapshot(sdl.Entity):
    """One recorded periodic KPI reading for a site, combining GA4 + GSC +
    DataForSEO numbers the caller already fetched -- this app never calls
    those connectors itself."""
    site_id: str = ""
    period_label: str = ""
    ga4_sessions: int = 0
    ga4_users: int = 0
    ga4_conversions: int = 0
    gsc_clicks: int = 0
    gsc_impressions: int = 0
    gsc_avg_position: float = 0.0
    dataforseo_avg_rank: float = 0.0
    dataforseo_keywords_top10: int = 0
    referring_domains: int = 0
    notes: str = ""
    recorded_at: str = ""


class KpiSnapshotList(sdl.EntityList[KpiSnapshot]):
    pass


class KpiTrendDelta(BaseModel):
    """Change of one metric between the two most recent snapshots."""
    metric: str = ""
    previous: float = 0.0
    current: float = 0.0
    change_pct: float = 0.0


class KpiDashboardReport(sdl.Entity):
    """Unified view of a site's most recent KPI snapshot plus its trend
    against the previous one -- the single place to see GA4 + GSC +
    DataForSEO movement together instead of three separate apps."""
    site_id: str = ""
    latest_period: str = ""
    latest: dict[str, object] = Field(default_factory=dict)
    trend: list[KpiTrendDelta] = Field(default_factory=list)
    history_periods: list[str] = Field(default_factory=list)  # most recent first, up to 12
    needs_doing: list[str] = Field(default_factory=list)


class GetKpiDashboardParams(BaseModel):
    site_id: str = Field(description="Site id from list_site_profiles — never invent it")
