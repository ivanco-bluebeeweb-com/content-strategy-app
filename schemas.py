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
    target_languages: list[str] = []
    content_categories: list[str] = []
    cta_default: str = ""


class SiteProfileList(sdl.EntityList[SiteProfile]):
    pass


class Opportunity(sdl.Entity):
    """One candidate content opportunity, sourced from GSC / SEO Audit / manual."""
    site_id: str = ""
    source: str = ""  # gsc | seo_audit | manual | mixed
    primary_query: str = ""
    supporting_queries: list[str] = []
    query_cluster_label: str = ""
    intent: str = ""  # informational | commercial | navigational
    impressions: int = 0
    clicks: int = 0
    ctr: float = 0.0
    avg_position: float = 0.0
    business_relevance_score: float = 0.0
    seo_opportunity_score: float = 0.0
    total_priority_score: float = 0.0
    recommended_content_type: str = ""
    recommended_target_url: str = ""
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
    primary_query: str = ""
    secondary_queries: list[str] = []
    outline: list[str] = []
    cta_goal: str = ""
    internal_link_targets: list[str] = []
    differentiation_notes: str = ""
    image_requirements: list[str] = []
    status: str = "brief_ready"


class ArticleBriefList(sdl.EntityList[ArticleBrief]):
    pass


class QueueItem(sdl.Entity):
    """Editorial workflow state — one row per opportunity/brief in flight."""
    site_id: str = ""
    brief_id: str = ""
    opportunity_id: str = ""
    content_type: str = "article"
    lifecycle_status: str = "idea"
    assigned_agent: str = "Webbee"
    published_url: str = ""


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


class ListOpportunitiesParams(BaseModel):
    site_id: str = Field("", description="Optional site filter. Empty = all sites.")
    status: str = Field("", description="Optional lifecycle status filter, e.g. 'idea', 'approved'.")
    limit: int = Field(20, description="Max items to return (1-100)")


class CreateBriefParams(BaseModel):
    opportunity_id: str = Field(description="UUID of the opportunity to turn into a brief — from list_opportunities, never invented")


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


class CreateSiteProfileParams(BaseModel):
    site_id: str = Field(description="Unique site identifier, e.g. 'g4s.md'")
    domain: str = Field(description="Domain, e.g. 'g4s.md'")
    brand_name: str = Field("", description="Brand/company name")
    business_description: str = Field("", description="One or two sentences on what the business does")
    target_languages: list[str] = Field(default_factory=list, description="Target languages for content, e.g. ['ru','ro']")
    content_categories: list[str] = Field(default_factory=list, description="Content categories/topics this site covers")
    cta_default: str = Field("", description="Default call-to-action goal for articles on this site")


class ListSiteProfilesParams(BaseModel):
    limit: int = Field(20, description="Max items to return (1-100)")
