"""Extension test suite — exercises the core flow (create site profile ->
discover opportunities -> create brief -> queue) against imperal_sdk's
MockContext, per the platform's testing guide.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from imperal_sdk.testing import MockContext

import main as m
from schemas import (
    CreateSiteProfileParams, DiscoverOpportunitiesParams, QuerySignal,
    CreateBriefParams, UpdateQueueStatusParams, ListConnectedSitesParams,
)


@pytest.mark.asyncio
async def test_create_site_profile_happy_path():
    ctx = MockContext()
    result = await m.create_site_profile(
        ctx, CreateSiteProfileParams(site_id="g4s.md", domain="g4s.md")
    )
    assert result.status == "success"


@pytest.mark.asyncio
async def test_create_site_profile_rejects_duplicate():
    ctx = MockContext()
    await m.create_site_profile(
        ctx, CreateSiteProfileParams(site_id="g4s.md", domain="g4s.md")
    )
    dup = await m.create_site_profile(
        ctx, CreateSiteProfileParams(site_id="g4s.md", domain="g4s.md")
    )
    assert dup.status == "error"


@pytest.mark.asyncio
async def test_discover_opportunities_creates_and_scores():
    ctx = MockContext()
    await m.create_site_profile(
        ctx, CreateSiteProfileParams(site_id="g4s.md", domain="g4s.md")
    )
    result = await m.discover_opportunities(
        ctx,
        DiscoverOpportunitiesParams(
            site_id="g4s.md",
            queries=[
                QuerySignal(query="security services chisinau", source="gsc",
                            impressions=500, clicks=10, ctr=0.02, avg_position=8.5),
                QuerySignal(query="how much does a security guard cost", source="gsc",
                            impressions=200, clicks=2, ctr=0.01, avg_position=15.0),
            ],
        ),
    )
    assert result.status == "success"
    assert len(result.data.items) == 2


@pytest.mark.asyncio
async def test_list_opportunities_filters_by_site():
    ctx = MockContext()
    await m.create_site_profile(
        ctx, CreateSiteProfileParams(site_id="g4s.md", domain="g4s.md")
    )
    await m.discover_opportunities(
        ctx,
        DiscoverOpportunitiesParams(
            site_id="g4s.md",
            queries=[
                QuerySignal(query="security services chisinau", source="gsc",
                            impressions=500, clicks=10, ctr=0.02, avg_position=8.5),
            ],
        ),
    )
    from schemas import ListOpportunitiesParams
    result = await m.list_opportunities(ctx, ListOpportunitiesParams(site_id="g4s.md"))
    assert result.status == "success"
    assert len(result.data.items) == 1


@pytest.mark.asyncio
async def test_create_brief_and_queue_advances():
    ctx = MockContext()
    await m.create_site_profile(
        ctx, CreateSiteProfileParams(site_id="g4s.md", domain="g4s.md")
    )
    disc = await m.discover_opportunities(
        ctx,
        DiscoverOpportunitiesParams(
            site_id="g4s.md",
            queries=[
                QuerySignal(query="security services chisinau", source="gsc",
                            impressions=500, clicks=10, ctr=0.02, avg_position=8.5),
            ],
        ),
    )
    top_opp = disc.data.items[0]

    brief_result = await m.create_brief(ctx, CreateBriefParams(opportunity_id=top_opp.id))
    assert brief_result.status == "success"

    from schemas import ListQueueParams
    queue_result = await m.list_queue(ctx, ListQueueParams(site_id="g4s.md"))
    matching = [q for q in queue_result.data.items if q.opportunity_id == top_opp.id]
    assert matching and matching[0].lifecycle_status == "brief_ready"


@pytest.mark.asyncio
async def test_update_queue_status_happy_and_missing():
    ctx = MockContext()
    await m.create_site_profile(
        ctx, CreateSiteProfileParams(site_id="g4s.md", domain="g4s.md")
    )
    disc = await m.discover_opportunities(
        ctx,
        DiscoverOpportunitiesParams(
            site_id="g4s.md",
            queries=[
                QuerySignal(query="security services chisinau", source="gsc",
                            impressions=500, clicks=10, ctr=0.02, avg_position=8.5),
            ],
        ),
    )
    top_opp = disc.data.items[0]
    await m.create_brief(ctx, CreateBriefParams(opportunity_id=top_opp.id))

    from schemas import ListQueueParams
    queue_result = await m.list_queue(ctx, ListQueueParams(site_id="g4s.md"))
    queue_item_id = queue_result.data.items[0].id

    ok = await m.update_queue_status(
        ctx, UpdateQueueStatusParams(queue_item_id=queue_item_id, lifecycle_status="approved")
    )
    assert ok.status == "success"

    missing = await m.update_queue_status(
        ctx, UpdateQueueStatusParams(queue_item_id="nonexistent", lifecycle_status="approved")
    )
    assert missing.status == "error"


async def _site_with_brief_ready_queue_item(ctx, query="security services chisinau"):
    """Shared setup: profile -> opportunity -> brief -> queue item at brief_ready."""
    await m.create_site_profile(
        ctx, CreateSiteProfileParams(site_id="g4s.md", domain="g4s.md", brand_name="G4S")
    )
    disc = await m.discover_opportunities(
        ctx,
        DiscoverOpportunitiesParams(
            site_id="g4s.md",
            queries=[
                QuerySignal(query=query, source="gsc",
                            impressions=500, clicks=10, ctr=0.02, avg_position=8.5),
            ],
        ),
    )
    top_opp = disc.data.items[0]
    brief_result = await m.create_brief(ctx, CreateBriefParams(opportunity_id=top_opp.id))
    from schemas import ListQueueParams
    queue_result = await m.list_queue(ctx, ListQueueParams(site_id="g4s.md"))
    queue_item_id = queue_result.data.items[0].id
    return queue_item_id, brief_result.data.id


@pytest.mark.asyncio
async def test_build_content_calendar_schedules_unscheduled_items():
    from schemas import BuildContentCalendarParams
    ctx = MockContext()
    await _site_with_brief_ready_queue_item(ctx)

    result = await m.build_content_calendar(
        ctx,
        BuildContentCalendarParams(
            site_id="g4s.md", year=2026, month=9, posts_per_week=2, weekdays=[1, 4]
        ),
    )
    assert result.status == "success"
    assert len(result.data.items) == 1
    assert result.data.items[0].scheduled_date.startswith("2026-09-")


@pytest.mark.asyncio
async def test_build_content_calendar_no_matching_weekdays_errors():
    from schemas import BuildContentCalendarParams
    ctx = MockContext()
    await _site_with_brief_ready_queue_item(ctx)

    result = await m.build_content_calendar(
        ctx,
        BuildContentCalendarParams(
            site_id="g4s.md", year=2026, month=9, posts_per_week=1, weekdays=[]
        ),
    )
    assert result.status == "error"


@pytest.mark.asyncio
async def test_get_content_calendar_filters_by_site_year_month():
    from schemas import BuildContentCalendarParams, GetContentCalendarParams
    ctx = MockContext()
    await _site_with_brief_ready_queue_item(ctx)
    await m.build_content_calendar(
        ctx,
        BuildContentCalendarParams(
            site_id="g4s.md", year=2026, month=9, posts_per_week=2, weekdays=[1, 4]
        ),
    )

    result = await m.get_content_calendar(
        ctx, GetContentCalendarParams(site_id="g4s.md", year=2026, month=9)
    )
    assert result.status == "success"
    assert len(result.data.items) == 1

    empty = await m.get_content_calendar(
        ctx, GetContentCalendarParams(site_id="g4s.md", year=2026, month=10)
    )
    assert len(empty.data.items) == 0


@pytest.mark.asyncio
async def test_link_external_article_stores_ids_and_requires_a_field():
    from schemas import LinkExternalArticleParams
    ctx = MockContext()
    queue_item_id, _brief_id = await _site_with_brief_ready_queue_item(ctx)

    result = await m.link_external_article(
        ctx,
        LinkExternalArticleParams(
            queue_item_id=queue_item_id,
            external_project_id="proj-1",
            external_article_id="art-1",
        ),
    )
    assert result.status == "success"
    assert result.data.external_project_id == "proj-1"
    assert result.data.external_article_id == "art-1"

    empty = await m.link_external_article(
        ctx, LinkExternalArticleParams(queue_item_id=queue_item_id)
    )
    assert empty.status == "error"

    missing = await m.link_external_article(
        ctx, LinkExternalArticleParams(queue_item_id="nonexistent", external_project_id="x")
    )
    assert missing.status == "error"


@pytest.mark.asyncio
async def test_build_writer_brief_assembles_article_writer_payload():
    from schemas import BuildWriterBriefParams
    ctx = MockContext()
    _queue_item_id, brief_id = await _site_with_brief_ready_queue_item(ctx)

    result = await m.build_writer_brief(ctx, BuildWriterBriefParams(brief_id=brief_id))
    assert result.status == "success"
    payload = result.data
    assert payload.brief_id == brief_id
    assert payload.suggested_project_name == "G4S"
    assert payload.site_url == "g4s.md"
    assert payload.target_keyword == "security services chisinau"
    assert payload.body  # Markdown body assembled from the brief
    assert payload.queue_item_id  # linked back to the originating queue item


@pytest.mark.asyncio
async def test_build_writer_brief_missing_brief_errors():
    from schemas import BuildWriterBriefParams
    ctx = MockContext()
    result = await m.build_writer_brief(ctx, BuildWriterBriefParams(brief_id="nonexistent"))
    assert result.status == "error"


# ──────────────────────────────────────────────────────────────────────────
# Panel rendering — sources_panel must offer a real create_site_profile
# ui.Form directly in the panel, both empty and populated. Requirement:
# "a UI I control that reaches every detail without talking in chat" --
# a panel that tells the user to go type in chat instead is the anti-pattern.
# ──────────────────────────────────────────────────────────────────────────

def _walk(node, seen_types):
    node_type = getattr(node, "type", None)
    if node_type is not None:
        seen_types.add(node_type)
        props = getattr(node, "props", {}) or {}
        for value in props.values():
            if isinstance(value, list):
                for item in value:
                    _walk(item, seen_types)
            else:
                _walk(value, seen_types)
    return seen_types


@pytest.mark.asyncio
async def test_sources_panel_empty_state_has_create_form():
    ctx = MockContext()
    node = await m.sources_panel(ctx)
    types = _walk(node, set())
    assert "Form" in types
    assert "Input" in types
    assert "Empty" in types


@pytest.mark.asyncio
async def test_sources_panel_with_sites_still_offers_create_form():
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="g4s.md", domain="g4s.md"))
    node = await m.sources_panel(ctx)
    types = _walk(node, set())
    assert "Card" in types
    assert "Form" in types


# ──────────────────────────────────────────────────────────────────────────
# Quick Add — sites already connected in WordPress Hub (or any future
# site-provider app registered in SITE_PROVIDER_APP_IDS) surface as one-click
# "create a site profile" buttons, via ctx.extensions.call IPC.
# The card must ALWAYS render: a silently missing card is unfixable from the UI.
# ──────────────────────────────────────────────────────────────────────────

def _wp_provider(ctx):
    ctx.extensions.register(
        "wp-site-connector", "list_connected_sites",
        lambda **kw: [{"site_id": "g4s.md", "name": "G4S Moldova",
                       "url": "https://g4s.md", "status": "connected"}],
    )


@pytest.mark.asyncio
async def test_fetch_connected_sites_calls_every_registered_provider():
    ctx = MockContext()
    _wp_provider(ctx)
    sites, problems = await m.fetch_connected_sites(ctx)
    assert problems == []
    assert len(sites) == 1
    assert sites[0]["site_id"] == "g4s.md"
    assert sites[0]["name"] == "G4S Moldova"
    assert sites[0]["provider"] == "wp-site-connector"


@pytest.mark.asyncio
async def test_provider_slug_site_id_is_normalised_to_the_bare_domain():
    """WordPress Hub keys sites by slug ('g4s-md') while these hubs key them
    by domain ('g4s.md'). Quick Add must offer the DOMAIN form, otherwise a
    click would create a duplicate profile beside the existing one."""
    ctx = MockContext()
    ctx.extensions.register(
        "wp-site-connector", "list_connected_sites",
        lambda **kw: [{"site_id": "g4s-md", "name": "G4S Moldova",
                       "url": "https://www.g4s.md/", "status": "connected"}],
    )
    sites, problems = await m.fetch_connected_sites(ctx)
    assert problems == []
    assert sites[0]["site_id"] == "g4s.md"          # normalised, www stripped
    assert sites[0]["provider_site_id"] == "g4s-md"  # original kept for traceability


@pytest.mark.asyncio
async def test_fetch_connected_sites_reports_unreachable_provider_instead_of_hiding_it():
    """A provider that cannot be reached must be REPORTED, not swallowed."""
    ctx = MockContext()  # no providers registered at all
    sites, problems = await m.fetch_connected_sites(ctx)
    assert sites == []
    assert len(problems) == 1
    assert problems[0]["provider"] == "wp-site-connector"
    assert problems[0]["reason"]


@pytest.mark.asyncio
async def test_sources_panel_shows_not_loaded_yet_before_first_refresh():
    """Panel RENDER never calls the flaky IPC path directly -- before the
    cache is warmed by a real list_connected_sites call, the card says so
    honestly instead of erroring or vanishing."""
    ctx = MockContext()
    _wp_provider(ctx)
    node = await m.sources_panel(ctx)
    rendered = repr(node)
    assert "Quick Add" in rendered
    assert "Not loaded yet" in rendered
    assert "Refresh" in rendered


@pytest.mark.asyncio
async def test_sources_panel_shows_quick_add_for_unclaimed_connected_site_after_cache_warm():
    ctx = MockContext()
    _wp_provider(ctx)
    await m.list_connected_sites(ctx, ListConnectedSitesParams())  # warms the cache
    node = await m.sources_panel(ctx)
    rendered = repr(node)
    assert "Quick Add" in rendered
    assert "G4S Moldova" in rendered
    assert "create_site_profile" in rendered


@pytest.mark.asyncio
async def test_sources_panel_quick_add_card_visible_even_when_provider_unreachable():
    """The whole point of the fix: no provider reachable still shows the card,
    naming the provider and the reason, plus a Refresh button."""
    ctx = MockContext()  # no providers registered
    await m.list_connected_sites(ctx, ListConnectedSitesParams())  # warms the cache with the failure
    node = await m.sources_panel(ctx)
    rendered = repr(node)
    assert "Quick Add" in rendered
    assert "Could not read connected sites from" in rendered
    assert "wp-site-connector" in rendered
    assert "Refresh" in rendered


@pytest.mark.asyncio
async def test_sources_panel_quick_add_card_stays_visible_when_all_sites_tracked():
    ctx = MockContext()
    _wp_provider(ctx)
    await m.list_connected_sites(ctx, ListConnectedSitesParams())  # warms the cache
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="g4s.md", domain="g4s.md"))
    node = await m.sources_panel(ctx)
    rendered = repr(node)
    assert "Quick Add" in rendered
    assert "already has a site profile" in rendered
    quick_add_section = rendered.split("Quick Add")[1].split("New site")[0]
    assert "create_site_profile" not in quick_add_section


@pytest.mark.asyncio
async def test_quick_add_prefills_domain_stripped_of_scheme():
    """The button must hand create_site_profile a clean domain, not a URL."""
    ctx = MockContext()
    _wp_provider(ctx)
    await m.list_connected_sites(ctx, ListConnectedSitesParams())  # warms the cache
    node = await m.sources_panel(ctx)
    rendered = repr(node)
    assert "'domain': 'g4s.md'" in rendered
    assert "https://g4s.md'" not in rendered.split("Quick Add")[1].split("New site")[0]


@pytest.mark.asyncio
async def test_list_connected_sites_function_reports_tracked_flag():
    ctx = MockContext()
    _wp_provider(ctx)
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="g4s.md", domain="g4s.md"))
    result = await m.list_connected_sites(ctx, ListConnectedSitesParams())
    assert result.status == "success"
    assert len(result.data.items) == 1
    assert result.data.items[0].already_tracked is True
    assert result.data.items[0].provider == "wp-site-connector"


@pytest.mark.asyncio
async def test_list_connected_sites_function_surfaces_provider_failure_in_summary():
    ctx = MockContext()  # no providers registered
    result = await m.list_connected_sites(ctx, ListConnectedSitesParams())
    assert result.status == "success"
    assert result.data.items == []
    assert "Could not read from" in result.summary
