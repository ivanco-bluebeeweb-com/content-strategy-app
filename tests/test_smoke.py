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
    AddSiteCompetitorParams, ListSiteCompetitorsParams,
    CreateContentAuthorParams, ListContentAuthorsParams, BuildWriterBriefParams,
    UpdateSiteProfileParams,
    ContentPerformanceSignal, TrackContentDecayParams, GetContentDecayParams,
    RecordEditorialSignoffParams,
    RecordKpiSnapshotParams, GetKpiDashboardParams,
    CreateOutreachTargetParams, UpdateOutreachStatusParams, ListOutreachTargetsParams,
    GetLinkBuildingReportParams,
)


async def _seed_audit(ctx, site_id: str) -> None:
    """Seed an already-audited, link-policy-ready site for unrelated tests."""
    await ctx.store.create("content_audits", {"site_id": site_id, "audited_at": "test-seed"})
    await _configure_required_link_inputs(ctx, site_id=site_id)


def _mock_action_pages(pages):
    async def handler(**kwargs):
        return pages
    return handler


async def _configure_required_link_inputs(ctx, *, site_id="g4s.md", language="en"):
    """A valid test site has factual action pages and verified sources.

    Tests must configure both, because production refuses to invent either URL.
    """
    profiles = await ctx.store.query("site_profiles", where={"site_id": site_id}, limit=1)
    languages = list(profiles.data[0].data.get("target_languages") or [language]) if profiles.data else [language]
    ctx.extensions.register("wordpress-hub", "list_pages_full", _mock_action_pages([
        {"title": "Contact us", "slug": f"contact-{lang}", "link": f"https://{site_id}/{lang}/contact", "content": "", "lang": lang}
        for lang in languages
    ]))
    if profiles.data:
        await ctx.store.update("site_profiles", profiles.data[0].id, {
            "external_sources_i18n": {lang: [f"https://sources.example/{lang}"] for lang in languages},
        })


@pytest.mark.asyncio
async def test_sources_panel_shows_approved_visual_guidance_as_read_only_context():
    ctx = MockContext()
    await m.create_site_profile(
        ctx,
        CreateSiteProfileParams(
            site_id="visual.example",
            domain="visual.example",
            brand_name="Visual Site",
            approved_visual_guidance=_approved_visual_guidance(),
        ),
    )

    rendered = repr(await m.sources_panel(ctx))
    assert "Approved visual guidance" in rendered
    assert "read-only" in rendered
    assert "Grounded operational confidence" in rendered
    assert "Third-party first; Magnific only after technical failure" in rendered
    assert "Profile r2" in rendered
    assert "VBS r4" in rendered
    assert "sha256:approved-basis" in rendered
    assert "does not create or generate media" in rendered


@pytest.mark.asyncio
async def test_update_and_list_site_profile_return_approved_visual_guidance_in_api_response():
    """Regression: to_site_profile() must round-trip approved_visual_guidance.

    The panels (sources_panel/queue_panel) read the field straight from the
    store and always looked correct, which hid a real bug: to_site_profile()
    -- used by update_site_profile's and list_site_profiles' own ActionResult
    payloads -- silently dropped approved_visual_guidance, so any external
    caller (e.g. Brand Strategy Hub relaying a handoff) saw {} back even
    though the write itself succeeded.
    """
    ctx = MockContext()
    await m.create_site_profile(
        ctx, CreateSiteProfileParams(site_id="roundtrip.example", domain="roundtrip.example")
    )
    guidance = _approved_visual_guidance()
    from schemas import UpdateSiteProfileParams, ListSiteProfilesParams

    update_result = await m.update_site_profile(
        ctx, UpdateSiteProfileParams(site_id="roundtrip.example", approved_visual_guidance=guidance)
    )
    assert update_result.status == "success"
    assert update_result.data.approved_visual_guidance.get("profile_id") == guidance["profile_id"]
    assert update_result.data.approved_visual_guidance.get("snapshot_hash") == guidance["snapshot_hash"]

    listed = await m.list_site_profiles(ctx, ListSiteProfilesParams())
    profile = next(p for p in listed.data.items if p.site_id == "roundtrip.example")
    assert profile.approved_visual_guidance.get("profile_id") == guidance["profile_id"]
    assert profile.approved_visual_guidance.get("vbs_revision") == guidance["vbs_revision"]


# NOTE: test_queue_panel_labels_current_and_stale_approved_visual_baselines was
# removed here -- it asserted "Visual baseline: current/stale" text inside the
# left-sidebar queue-item list, which was intentionally deleted from
# queue_panel (per explicit request: no queue/briefs listing + no "Filter by
# site" Select in the sidebar, full stop). The visual-baseline current/stale
# computation itself is untouched and still covered directly by the
# get_content_calendar tests (visual_baseline_state assertions below).


@pytest.mark.asyncio
async def test_brief_panel_shows_writer_handoff_readiness_independent_of_media():
    """P0-B: the Brief screen must say whether Article Writer will actually
    receive the visual guidance, not just whether Media Studio's handoff
    button is available -- these are two different downstream consumers of
    the same baseline and can diverge (see build_writer_brief, which drops
    stale guidance from the Writer payload the same way Media does)."""
    from schemas import UpdateSiteProfileParams
    ctx = MockContext()
    queue_item_id, brief_id = await _site_with_brief_ready_queue_item(ctx)
    guidance = _approved_visual_guidance()
    await m.update_site_profile(
        ctx, UpdateSiteProfileParams(site_id="g4s.md", approved_visual_guidance=guidance)
    )
    brief_doc = await ctx.store.get("article_briefs", brief_id)
    await ctx.store.update("article_briefs", brief_doc.id, {"approved_visual_guidance": guidance})

    current_rendered = repr(await m.brief_panel(ctx, queue_item_id=queue_item_id))
    assert "ready — visual guidance will be included" in current_rendered
    assert "Writer handoff" in current_rendered

    updated_guidance = _approved_visual_guidance()
    updated_guidance["profile_revision"] = 3
    updated_guidance["snapshot_hash"] = "sha256:new-approved-basis"
    await m.update_site_profile(
        ctx, UpdateSiteProfileParams(site_id="g4s.md", approved_visual_guidance=updated_guidance)
    )

    stale_rendered = repr(await m.brief_panel(ctx, queue_item_id=queue_item_id))
    assert "visual guidance excluded — baseline stale" in stale_rendered
    assert "Writer handoff" in stale_rendered


@pytest.mark.asyncio
async def test_brief_panel_shows_unified_readonly_handoff_review_for_writer_and_media():
    """P0-C: one compact read-only handoff review on the Brief screen must show
    BOTH downstream targets (Writer and Media Studio) with their own readiness,
    the shared approval provenance, the explicit 'no generation happens here'
    boundary, and Media's third-party-first provider policy -- all without any
    send/create/generate action beyond the existing read-only handoff builders."""
    from schemas import UpdateSiteProfileParams
    ctx = MockContext()
    queue_item_id, brief_id = await _site_with_brief_ready_queue_item(ctx)
    guidance = _approved_visual_guidance()
    await m.update_site_profile(
        ctx, UpdateSiteProfileParams(site_id="g4s.md", approved_visual_guidance=guidance)
    )
    brief_doc = await ctx.store.get("article_briefs", brief_id)
    await ctx.store.update("article_briefs", brief_doc.id, {"approved_visual_guidance": guidance})

    rendered = repr(await m.brief_panel(ctx, queue_item_id=queue_item_id))

    # Both target apps named explicitly.
    assert "Article Writer" in rendered
    assert "Media Studio" in rendered
    # Shared approval provenance (profile + VBS revisions, snapshot hash).
    assert "Profile r2" in rendered
    assert "Visual Brand System (VBS) r4" in rendered
    assert "sha256:approved-basis" in rendered
    # Explicit generation boundary and Media's provider policy.
    assert "does not generate images" in rendered
    assert "Third-party first; Magnific only after technical failure" in rendered
    # No article draft is produced by this screen either.
    assert "No article draft is generated by this app" in rendered


@pytest.mark.asyncio
async def test_content_calendar_reports_current_and_stale_visual_baseline_without_guidance_payload():
    from schemas import BuildContentCalendarParams, GetContentCalendarParams, UpdateSiteProfileParams
    ctx = MockContext()
    _queue_item_id, brief_id = await _site_with_brief_ready_queue_item(ctx)
    guidance = _approved_visual_guidance()
    await m.update_site_profile(
        ctx, UpdateSiteProfileParams(site_id="g4s.md", approved_visual_guidance=guidance)
    )
    brief_doc = await ctx.store.get("article_briefs", brief_id)
    await ctx.store.update("article_briefs", brief_doc.id, {"approved_visual_guidance": guidance})
    queue_items = await ctx.store.query("queue_items", where={"brief_id": brief_id}, limit=1)
    await ctx.store.update("queue_items", queue_items.data[0].id, {"scheduled_date": "2026-08-12"})

    current = await m.get_content_calendar(ctx, GetContentCalendarParams(site_id="g4s.md"))
    assert current.status == "success"
    assert current.data.items[0].visual_baseline_state == "current"
    assert not hasattr(current.data.items[0], "approved_visual_guidance")

    await ctx.store.update("queue_items", queue_items.data[0].id, {"scheduled_date": ""})
    built = await m.build_content_calendar(
        ctx, BuildContentCalendarParams(site_id="g4s.md", year=2026, month=8, posts_per_week=1)
    )
    assert built.status == "success"
    assert built.data.items[0].visual_baseline_state == "current"
    assert not hasattr(built.data.items[0], "approved_visual_guidance")

    updated_guidance = _approved_visual_guidance()
    updated_guidance["profile_revision"] = 3
    updated_guidance["snapshot_hash"] = "sha256:new-approved-basis"
    await m.update_site_profile(
        ctx, UpdateSiteProfileParams(site_id="g4s.md", approved_visual_guidance=updated_guidance)
    )

    stale = await m.get_content_calendar(ctx, GetContentCalendarParams(site_id="g4s.md"))
    assert stale.status == "success"
    assert stale.data.items[0].visual_baseline_state == "stale"


@pytest.mark.asyncio
async def test_queue_panel_empty_state_has_no_sites_button():
    """The 'Sites -- manage & Quick Add' button was removed from this sidebar
    per explicit request -- Sites/Quick Add is reached through normal panel
    navigation instead of a duplicate shortcut button here."""
    ctx = MockContext()
    node = await m.queue_panel(ctx)
    rendered = repr(node)
    assert "__panel__sources" not in rendered


@pytest.mark.asyncio
async def test_queue_panel_with_items_still_has_no_sites_button():
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="g4s.md", domain="g4s.md"))
    await _seed_audit(ctx, "g4s.md")
    await m.discover_opportunities(ctx, DiscoverOpportunitiesParams(
        site_id="g4s.md",
        signals=[QuerySignal(query="security services chisinau", clicks=10, impressions=200, position=8.0)],
    ))
    node = await m.queue_panel(ctx)
    rendered = repr(node)
    assert "__panel__sources" not in rendered


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
    await _seed_audit(ctx, "g4s.md")
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
    await _seed_audit(ctx, "g4s.md")
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
    await _seed_audit(ctx, "g4s.md")
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
    await _seed_audit(ctx, "g4s.md")
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


@pytest.mark.asyncio
async def test_decide_image_text_policy_defaults_to_no_text():
    """No candidate_text at all -- stays text-free regardless of intent."""
    from converters import decide_image_text_policy
    assert decide_image_text_policy("informational") == ("no_text", "")
    assert decide_image_text_policy("navigational") == ("no_text", "")
    assert decide_image_text_policy("commercial") == ("no_text", "")


@pytest.mark.asyncio
async def test_decide_image_text_policy_allows_text_for_commercial_intent_with_real_wording():
    """Commercial intent PLUS actual candidate text -> allow_text with the
    exact words carried through, never a vague 'text is fine' with nothing
    concrete to render."""
    from converters import decide_image_text_policy
    assert decide_image_text_policy("commercial", candidate_text="Book a free quote") == (
        "allow_text", "Book a free quote",
    )


@pytest.mark.asyncio
async def test_decide_image_text_policy_non_commercial_intent_ignores_candidate_text():
    from converters import decide_image_text_policy
    assert decide_image_text_policy("informational", candidate_text="Book a free quote") == ("no_text", "")


@pytest.mark.asyncio
async def test_decide_image_text_policy_approved_no_text_pattern_overrides_commercial_intent():
    from converters import decide_image_text_policy
    assert decide_image_text_policy(
        "commercial", ["No embedded text or logos"], candidate_text="Book a free quote",
    ) == ("no_text", "")


@pytest.mark.asyncio
async def test_create_brief_sets_no_text_policy_for_informational_query():
    ctx = MockContext()
    queue_item_id, brief_id = await _site_with_brief_ready_queue_item(
        ctx, query="what is video surveillance"
    )
    brief_doc = await ctx.store.get("article_briefs", brief_id)
    assert brief_doc.data["text_policy"] == "no_text"


@pytest.mark.asyncio
async def test_create_brief_sets_allow_text_policy_for_commercial_query():
    """Commercial intent + a real CTA on the site profile -> allow_text with
    the actual CTA wording carried through as image_text -- never allow_text
    with nothing concrete to render."""
    ctx = MockContext()
    queue_item_id, brief_id = await _site_with_brief_ready_queue_item(
        ctx, query="security services chisinau", cta_default="Get a free security audit",
    )
    brief_doc = await ctx.store.get("article_briefs", brief_id)
    assert brief_doc.data["text_policy"] == "allow_text"
    assert brief_doc.data["image_text"] == "Get a free security audit"


@pytest.mark.asyncio
async def test_create_brief_commercial_query_without_real_cta_stays_no_text():
    """Commercial intent alone is not enough -- without a real candidate
    text to render, the brief must stay text-free rather than invent
    wording for the image model."""
    ctx = MockContext()
    queue_item_id, brief_id = await _site_with_brief_ready_queue_item(
        ctx, query="security services chisinau",
    )
    brief_doc = await ctx.store.get("article_briefs", brief_id)
    assert brief_doc.data["text_policy"] == "no_text"
    assert brief_doc.data["image_text"] == ""


@pytest.mark.asyncio
async def test_build_media_brief_handoff_carries_text_policy_and_brand_override_wins():
    """A commercial-intent brief would normally get 'allow_text', but an
    approved VBS/Visual Profile forbidding in-image text must always win --
    Content Strategy has no authority to override an approved brand rule."""
    from schemas import UpdateSiteProfileParams, BuildMediaBriefHandoffParams
    ctx = MockContext()
    queue_item_id, brief_id = await _site_with_brief_ready_queue_item(
        ctx, query="security services chisinau"
    )
    guidance = _approved_visual_guidance()
    guidance["prohibited_patterns"] = ["No embedded text or logos"]
    await m.update_site_profile(
        ctx, UpdateSiteProfileParams(site_id="g4s.md", approved_visual_guidance=guidance)
    )
    brief_doc = await ctx.store.get("article_briefs", brief_id)
    await ctx.store.update("article_briefs", brief_doc.id, {"approved_visual_guidance": guidance})

    handoff = await m.build_media_brief_handoff(
        ctx, BuildMediaBriefHandoffParams(brief_id=brief_id)
    )
    assert handoff.status == "success"
    assert handoff.data.text_policy == "no_text"


async def _site_with_brief_ready_queue_item(ctx, query="security services chisinau", cta_default=""):
    """Shared setup: profile -> opportunity -> brief -> queue item at brief_ready."""
    await m.create_site_profile(
        ctx, CreateSiteProfileParams(
            site_id="g4s.md", domain="g4s.md", brand_name="G4S", cta_default=cta_default,
        )
    )
    await _seed_audit(ctx, "g4s.md")
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


def _approved_visual_guidance():
    return {
        "profile_id": "approved-profile-1",
        "profile_revision": 2,
        "vbs_id": "vbs-1",
        "vbs_revision": 4,
        "snapshot_hash": "sha256:approved-basis",
        "visual_intent": "Grounded operational confidence",
        "style_direction": "Documentary realism",
        "prohibited_patterns": ["Synthetic likenesses"],
        "provider_policy": "third_party_only_unless_technical_failure",
        "generation_boundary": "No generation is performed by this handoff.",
    }


@pytest.mark.asyncio
async def test_approved_visual_guidance_flows_from_site_profile_to_writer_brief_without_media_generation():
    from schemas import BuildWriterBriefParams, UpdateSiteProfileParams
    ctx = MockContext()
    _queue_item_id, brief_id = await _site_with_brief_ready_queue_item(ctx)
    guidance = _approved_visual_guidance()
    guidance["style_direction"] = "Documentary realism; show real working environments"
    guidance["prohibited_patterns"] = ["Synthetic likenesses", "Faces used as identity claims"]

    updated = await m.update_site_profile(
        ctx, UpdateSiteProfileParams(site_id="g4s.md", approved_visual_guidance=guidance)
    )
    assert updated.status == "success"

    # A newly-created brief copies this field from Site Profile. The fixture’s
    # opportunity already has its one permitted brief, so model the stored
    # downstream record directly and validate the reader contract from it.
    brief_doc = await ctx.store.get("article_briefs", brief_id)
    await ctx.store.update("article_briefs", brief_doc.id, {"approved_visual_guidance": guidance})
    result = await m.build_writer_brief(ctx, BuildWriterBriefParams(brief_id=brief_id))

    assert result.status == "success"
    assert result.data.approved_visual_guidance == guidance
    assert "## Approved visual guidance (non-generative)" in result.data.body
    assert "Grounded operational confidence" in result.data.body
    assert "third-party providers first; Magnific only after other providers technically fail" in result.data.body
    assert "generate media" in result.data.body


@pytest.mark.asyncio
async def test_build_writer_brief_omits_stale_visual_guidance():
    from schemas import BuildWriterBriefParams, UpdateSiteProfileParams
    ctx = MockContext()
    _queue_item_id, brief_id = await _site_with_brief_ready_queue_item(ctx)
    stale_guidance = _approved_visual_guidance()
    await m.update_site_profile(
        ctx, UpdateSiteProfileParams(site_id="g4s.md", approved_visual_guidance=stale_guidance)
    )
    brief_doc = await ctx.store.get("article_briefs", brief_id)
    await ctx.store.update("article_briefs", brief_doc.id, {"approved_visual_guidance": stale_guidance})

    current_guidance = _approved_visual_guidance()
    current_guidance["profile_revision"] = 3
    current_guidance["snapshot_hash"] = "sha256:new-approved-basis"
    await m.update_site_profile(
        ctx, UpdateSiteProfileParams(site_id="g4s.md", approved_visual_guidance=current_guidance)
    )

    result = await m.build_writer_brief(ctx, BuildWriterBriefParams(brief_id=brief_id))
    assert result.status == "success"
    assert result.data.approved_visual_guidance == {}
    assert "## Approved visual guidance (non-generative)" not in result.data.body
    assert "baseline is stale and was intentionally omitted" in result.data.body
    assert "Grounded operational confidence" not in result.data.body


def test_approved_visual_baseline_comparison_fails_closed_on_missing_basis():
    current = _approved_visual_guidance()
    incomplete = _approved_visual_guidance()
    incomplete.pop("snapshot_hash")

    assert m._approved_visual_baseline_is_current(_approved_visual_guidance(), current)
    assert not m._approved_visual_baseline_is_current(incomplete, current)
    assert not m._approved_visual_baseline_is_current({}, current)


@pytest.mark.asyncio
async def test_site_profile_rejects_visual_guidance_without_approved_basis():
    ctx = MockContext()
    result = await m.create_site_profile(
        ctx,
        CreateSiteProfileParams(
            site_id="unsafe.example",
            domain="unsafe.example",
            approved_visual_guidance={"profile_id": "unverified", "visual_intent": "Arbitrary prompt"},
        ),
    )
    assert result.status == "error"
    assert "approval basis" in repr(result)


@pytest.mark.asyncio
async def test_brief_panel_exposes_read_only_media_handoff_for_approved_visual_guidance():
    from schemas import UpdateSiteProfileParams
    ctx = MockContext()
    queue_item_id, brief_id = await _site_with_brief_ready_queue_item(ctx)
    guidance = _approved_visual_guidance()
    await m.update_site_profile(
        ctx, UpdateSiteProfileParams(site_id="g4s.md", approved_visual_guidance=guidance)
    )
    brief_doc = await ctx.store.get("article_briefs", brief_id)
    await ctx.store.update("article_briefs", brief_doc.id, {"approved_visual_guidance": guidance})

    rendered = repr(await m.brief_panel(ctx, queue_item_id=queue_item_id))
    assert "Approved visual guidance is attached" in rendered
    assert "Build Media Studio handoff" in rendered
    assert "build_media_brief_handoff" in rendered
    assert "Third-party first; Magnific only after technical failure" in rendered
    assert "Profile r2" in rendered
    assert "Visual Brand System (VBS) r4" in rendered
    assert "sha256:approved-basis" in rendered
    assert "does not generate images" in rendered


@pytest.mark.asyncio
async def test_brief_panel_hides_media_handoff_for_stale_visual_guidance():
    from schemas import UpdateSiteProfileParams
    ctx = MockContext()
    queue_item_id, brief_id = await _site_with_brief_ready_queue_item(ctx)
    stale_guidance = _approved_visual_guidance()
    await m.update_site_profile(
        ctx, UpdateSiteProfileParams(site_id="g4s.md", approved_visual_guidance=stale_guidance)
    )
    brief_doc = await ctx.store.get("article_briefs", brief_id)
    await ctx.store.update("article_briefs", brief_doc.id, {"approved_visual_guidance": stale_guidance})

    current_guidance = _approved_visual_guidance()
    current_guidance["profile_revision"] = 3
    current_guidance["snapshot_hash"] = "sha256:new-approved-basis"
    await m.update_site_profile(
        ctx, UpdateSiteProfileParams(site_id="g4s.md", approved_visual_guidance=current_guidance)
    )

    rendered = repr(await m.brief_panel(ctx, queue_item_id=queue_item_id))
    assert "Media Studio handoff unavailable" in rendered
    assert "stale approved visual baseline" in rendered
    assert "Refresh approved visual guidance" in rendered
    assert "refresh_brief_visual_guidance" in rendered
    assert "Build Media Studio handoff" not in rendered


@pytest.mark.asyncio
async def test_media_brief_handoff_preserves_approved_guidance_without_creating_media():
    from schemas import BuildMediaBriefHandoffParams, BuildWriterBriefParams, UpdateSiteProfileParams
    ctx = MockContext()
    _queue_item_id, brief_id = await _site_with_brief_ready_queue_item(ctx)
    guidance = _approved_visual_guidance()
    await m.update_site_profile(
        ctx, UpdateSiteProfileParams(site_id="g4s.md", approved_visual_guidance=guidance)
    )
    brief_doc = await ctx.store.get("article_briefs", brief_id)
    await ctx.store.update("article_briefs", brief_doc.id, {"approved_visual_guidance": guidance})

    result = await m.build_media_brief_handoff(
        ctx, BuildMediaBriefHandoffParams(brief_id=brief_id)
    )
    assert result.status == "success"
    payload = result.data
    assert payload.source_brief_id == brief_id
    assert payload.approved_visual_profile_id == "approved-profile-1"
    assert payload.approved_visual_profile_revision == 2
    assert payload.approved_vbs_id == "vbs-1"
    assert payload.approved_vbs_revision == 4
    assert payload.approved_snapshot_hash == "sha256:approved-basis"
    assert payload.model == "auto"
    assert payload.provider_policy == "third_party_only_unless_technical_failure"
    assert "Documentary realism" in payload.style_direction
    assert "Avoid: Synthetic likenesses" in payload.style_direction
    assert "does not create a media package or generate assets" in payload.generation_boundary

    missing = await m.build_media_brief_handoff(
        ctx, BuildMediaBriefHandoffParams(brief_id="missing-brief")
    )
    assert missing.status == "error"


@pytest.mark.asyncio
async def test_media_brief_handoff_rejects_stale_visual_guidance_basis():
    from schemas import BuildMediaBriefHandoffParams, UpdateSiteProfileParams
    ctx = MockContext()
    _queue_item_id, brief_id = await _site_with_brief_ready_queue_item(ctx)
    stale_guidance = _approved_visual_guidance()
    await m.update_site_profile(
        ctx, UpdateSiteProfileParams(site_id="g4s.md", approved_visual_guidance=stale_guidance)
    )
    brief_doc = await ctx.store.get("article_briefs", brief_id)
    await ctx.store.update("article_briefs", brief_doc.id, {"approved_visual_guidance": stale_guidance})

    current_guidance = _approved_visual_guidance()
    current_guidance["profile_revision"] = 3
    current_guidance["snapshot_hash"] = "sha256:new-approved-basis"
    updated = await m.update_site_profile(
        ctx, UpdateSiteProfileParams(site_id="g4s.md", approved_visual_guidance=current_guidance)
    )
    assert updated.status == "success"

    result = await m.build_media_brief_handoff(
        ctx, BuildMediaBriefHandoffParams(brief_id=brief_id)
    )
    assert result.status == "error"
    assert "stale" in repr(result)
    assert "Rebuild the brief" in repr(result)


@pytest.mark.asyncio
async def test_refresh_brief_visual_guidance_unblocks_media_handoff():
    from schemas import (
        BuildMediaBriefHandoffParams,
        RefreshBriefVisualGuidanceParams,
        UpdateSiteProfileParams,
    )
    ctx = MockContext()
    _queue_item_id, brief_id = await _site_with_brief_ready_queue_item(ctx)
    stale_guidance = _approved_visual_guidance()
    await m.update_site_profile(
        ctx, UpdateSiteProfileParams(site_id="g4s.md", approved_visual_guidance=stale_guidance)
    )
    brief_doc = await ctx.store.get("article_briefs", brief_id)
    await ctx.store.update("article_briefs", brief_doc.id, {"approved_visual_guidance": stale_guidance})

    current_guidance = _approved_visual_guidance()
    current_guidance["profile_revision"] = 3
    current_guidance["snapshot_hash"] = "sha256:new-approved-basis"
    await m.update_site_profile(
        ctx, UpdateSiteProfileParams(site_id="g4s.md", approved_visual_guidance=current_guidance)
    )

    refreshed = await m.refresh_brief_visual_guidance(
        ctx, RefreshBriefVisualGuidanceParams(brief_id=brief_id)
    )
    assert refreshed.status == "success"
    assert refreshed.data.approved_visual_guidance["profile_revision"] == 3
    assert refreshed.data.approved_visual_guidance["snapshot_hash"] == "sha256:new-approved-basis"

    handoff = await m.build_media_brief_handoff(
        ctx, BuildMediaBriefHandoffParams(brief_id=brief_id)
    )
    assert handoff.status == "success"
    assert handoff.data.approved_visual_profile_revision == 3
    assert handoff.data.approved_snapshot_hash == "sha256:new-approved-basis"

    rendered = repr(await m.brief_panel(ctx, queue_item_id=_queue_item_id))
    assert "Build Media Studio handoff" in rendered
    assert "refresh_brief_visual_guidance" not in rendered
    assert "does not generate images" in rendered


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
        "wordpress-hub", "list_connected_sites",
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
    assert sites[0]["provider"] == "wordpress-hub"


@pytest.mark.asyncio
async def test_provider_slug_site_id_is_normalised_to_the_bare_domain():
    """WordPress Hub keys sites by slug ('g4s-md') while these hubs key them
    by domain ('g4s.md'). Quick Add must offer the DOMAIN form, otherwise a
    click would create a duplicate profile beside the existing one."""
    ctx = MockContext()
    ctx.extensions.register(
        "wordpress-hub", "list_connected_sites",
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
    assert problems[0]["provider"] == "wordpress-hub"
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
    assert "wordpress-hub" in rendered
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
    assert result.data.items[0].provider == "wordpress-hub"


@pytest.mark.asyncio
async def test_list_connected_sites_function_surfaces_provider_failure_in_summary():
    ctx = MockContext()  # no providers registered
    result = await m.list_connected_sites(ctx, ListConnectedSitesParams())
    assert result.status == "success"
    assert result.data.items == []
    assert "Could not read from" in result.summary


@pytest.mark.asyncio
async def test_add_site_competitor_and_list_by_site():
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="climtec.md", domain="climtec.md"))
    result = await m.add_site_competitor(ctx, AddSiteCompetitorParams(
        site_id="climtec.md", name="Ventclima", url="https://ventclima.md/",
        competing_topics=["ventilare comerciala"], strengths=["turnkey install"],
    ))
    assert result.status == "success"
    assert result.data.site_id == "climtec.md"
    assert result.data.title == "Ventclima"

    listed = await m.list_site_competitors(ctx, ListSiteCompetitorsParams(site_id="climtec.md"))
    assert listed.status == "success"
    assert len(listed.data.items) == 1
    assert listed.data.items[0].title == "Ventclima"


@pytest.mark.asyncio
async def test_add_site_competitor_requires_existing_site_profile():
    ctx = MockContext()
    result = await m.add_site_competitor(ctx, AddSiteCompetitorParams(site_id="nope.md", name="X"))
    assert result.status == "error"


@pytest.mark.asyncio
async def test_create_brief_supports_one_brief_per_language():
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(
        site_id="climtec.md", domain="climtec.md", target_languages=["ru", "ro"],
    ))
    await _seed_audit(ctx, "climtec.md")
    discovered = await m.discover_opportunities(ctx, DiscoverOpportunitiesParams(
        site_id="climtec.md",
        queries=[QuerySignal(query="recuperator de caldura", impressions=10, clicks=1, ctr=0.1, avg_position=15.0)],
    ))
    opp_id = discovered.data.items[0].id

    # The opportunity's stored primary_query is Latin-script (Romanian), so a
    # 'ru' brief needs its own real target_query -- reusing the Romanian text
    # as-is would give a Russian brief a Romanian/wrong-language title (the
    # reported bug: RU and RO briefs both titled in the same language).
    ru_brief = await m.create_brief(ctx, CreateBriefParams(
        opportunity_id=opp_id, target_language="ru", target_query="рекуператор тепла",
    ))
    assert ru_brief.status == "success"
    assert ru_brief.data.target_language == "ru"
    assert ru_brief.data.working_title == "Рекуператор тепла"

    ro_brief = await m.create_brief(ctx, CreateBriefParams(opportunity_id=opp_id, target_language="ro"))
    assert ro_brief.status == "success"
    assert ro_brief.data.target_language == "ro"
    assert ro_brief.data.id != ru_brief.data.id
    # 'ro' brief needed no target_query override -- the opportunity's own
    # Latin-script query already matches the 'ro' target language.
    assert ro_brief.data.working_title == "Recuperator de caldura"

    dup = await m.create_brief(ctx, CreateBriefParams(
        opportunity_id=opp_id, target_language="ru", target_query="рекуператор тепла",
    ))
    assert dup.status == "error"

    from schemas import ListQueueParams
    queue = await m.list_queue(ctx, ListQueueParams(site_id="climtec.md"))
    langs = sorted(item.target_language for item in queue.data.items if item.opportunity_id == opp_id)
    assert langs == ["ro", "ru"]


@pytest.mark.asyncio
async def test_create_brief_requires_target_query_when_script_mismatches_language():
    """The exact reported bug: an opportunity discovered from a Russian query
    must not silently become a Romanian brief's title too. Without a real
    target_query for the mismatched language, create_brief must refuse
    instead of reusing the wrong-language text."""
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(
        site_id="climtec.md", domain="climtec.md", target_languages=["ru", "ro"],
    ))
    await _seed_audit(ctx, "climtec.md")
    discovered = await m.discover_opportunities(ctx, DiscoverOpportunitiesParams(
        site_id="climtec.md",
        queries=[QuerySignal(query="душно в комнате с открытым окном", impressions=10, clicks=1, ctr=0.1, avg_position=15.0)],
    ))
    opp_id = discovered.data.items[0].id

    ru_brief = await m.create_brief(ctx, CreateBriefParams(opportunity_id=opp_id, target_language="ru"))
    assert ru_brief.status == "success"
    assert ru_brief.data.working_title == "Душно в комнате с открытым окном"

    ro_without_query = await m.create_brief(ctx, CreateBriefParams(opportunity_id=opp_id, target_language="ro"))
    assert ro_without_query.status == "error"
    assert "TARGET_QUERY_REQUIRED" in ro_without_query.error

    ro_brief = await m.create_brief(ctx, CreateBriefParams(
        opportunity_id=opp_id, target_language="ro", target_query="e infundat in camera cu geamul deschis",
    ))
    assert ro_brief.status == "success"
    assert ro_brief.data.working_title == "E infundat in camera cu geamul deschis"
    assert ro_brief.data.working_title != ru_brief.data.working_title


# ─────────── run_content_audit / get_content_audit / check_keyword_cannibalization ───────────

from schemas import RunContentAuditParams, GetContentAuditParams, CheckCannibalizationParams


def _mock_posts_full(posts):
    async def handler(**kwargs):
        return posts
    return handler


@pytest.mark.asyncio
async def test_run_content_audit_flags_thin_and_missing_excerpt():
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="climtec.md", domain="climtec.md"))
    ctx.extensions.register("wordpress-hub", "list_posts_full", _mock_posts_full([
        {"id": 1, "title": "Ventilatie", "slug": "ventilatie", "link": "https://climtec.md/ventilatie",
         "content": "<p>" + ("cuvant " * 50) + "</p>", "excerpt": "", "lang": "ro", "categories": []},
        {"id": 2, "title": "Climatizare", "slug": "climatizare", "link": "https://climtec.md/climatizare",
         "content": "<p>" + ("aer conditionat instalare service " * 200) + "</p>",
         "excerpt": "Ghid complet", "lang": "ro", "categories": []},
    ]))
    result = await m.run_content_audit(ctx, RunContentAuditParams(site_id="climtec.md"))
    assert result.status == "success"
    assert result.data.total_posts == 2
    assert result.data.thin_content_count == 1
    assert result.data.missing_excerpt_count == 1
    assert result.data.needs_doing  # never silently empty when there IS something to do


@pytest.mark.asyncio
async def test_run_content_audit_detects_cannibalization_pair():
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="climtec.md", domain="climtec.md"))
    shared_text = "sistem ventilatie comerciala birou spatiu recuperator caldura instalare pret cost"
    ctx.extensions.register("wordpress-hub", "list_posts_full", _mock_posts_full([
        {"id": 1, "title": "Cum alegi sistemul de ventilare A", "slug": "a", "link": "https://climtec.md/a",
         "content": f"<p>{shared_text} {shared_text}</p>", "excerpt": "x", "lang": "ro", "categories": []},
        {"id": 2, "title": "Cum alegi sistemul de ventilare B", "slug": "b", "link": "https://climtec.md/b",
         "content": f"<p>{shared_text} {shared_text}</p>", "excerpt": "y", "lang": "ro", "categories": []},
    ]))
    result = await m.run_content_audit(ctx, RunContentAuditParams(site_id="climtec.md"))
    assert result.status == "success"
    assert result.data.cannibalization_pairs_found >= 1


@pytest.mark.asyncio
async def test_run_content_audit_missing_site_profile_errors():
    ctx = MockContext()
    result = await m.run_content_audit(ctx, RunContentAuditParams(site_id="nope.md"))
    assert result.status == "error"


@pytest.mark.asyncio
async def test_get_content_audit_without_prior_run_errors():
    ctx = MockContext()
    result = await m.get_content_audit(ctx, GetContentAuditParams(site_id="climtec.md"))
    assert result.status == "error"


@pytest.mark.asyncio
async def test_get_content_audit_reads_back_saved_report():
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="climtec.md", domain="climtec.md"))
    ctx.extensions.register("wordpress-hub", "list_posts_full", _mock_posts_full([
        {"id": 1, "title": "X", "slug": "x", "link": "https://climtec.md/x",
         "content": "<p>" + ("word " * 400) + "</p>", "excerpt": "e", "lang": "ro", "categories": []},
    ]))
    await m.run_content_audit(ctx, RunContentAuditParams(site_id="climtec.md"))
    result = await m.get_content_audit(ctx, GetContentAuditParams(site_id="climtec.md"))
    assert result.status == "success"
    assert result.data.total_posts == 1


@pytest.mark.asyncio
async def test_discover_opportunities_blocked_without_content_audit():
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="climtec.md", domain="climtec.md"))
    result = await m.discover_opportunities(ctx, DiscoverOpportunitiesParams(
        site_id="climtec.md",
        queries=[QuerySignal(query="test query", impressions=10, clicks=1, ctr=0.1, avg_position=10.0)],
    ))
    assert result.status == "error"
    assert result.error_code == "CONTENT_AUDIT_REQUIRED"


@pytest.mark.asyncio
async def test_check_keyword_cannibalization_flags_candidate_against_existing():
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="climtec.md", domain="climtec.md"))
    shared_text = "sistem ventilatie comerciala birou spatiu recuperator caldura instalare pret cost"
    ctx.extensions.register("wordpress-hub", "list_posts_full", _mock_posts_full([
        {"id": 1, "title": "Cum alegi sistemul de ventilare pentru spatii comerciale",
         "slug": "a", "link": "https://climtec.md/a",
         "content": f"<p>{shared_text} {shared_text} {shared_text}</p>", "excerpt": "x",
         "lang": "ro", "categories": []},
    ]))
    await m.run_content_audit(ctx, RunContentAuditParams(site_id="climtec.md"))
    result = await m.check_keyword_cannibalization(ctx, CheckCannibalizationParams(
        site_id="climtec.md",
        candidate_keyword="sistem ventilatie comerciala birou spatiu instalare",
    ))
    assert result.status == "success"
    assert result.data.total >= 1


@pytest.mark.asyncio
async def test_check_keyword_cannibalization_requires_audit_for_candidate_check():
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="climtec.md", domain="climtec.md"))
    result = await m.check_keyword_cannibalization(ctx, CheckCannibalizationParams(
        site_id="climtec.md", candidate_keyword="anything",
    ))
    assert result.status == "error"


@pytest.mark.asyncio
async def test_resolve_cannibalization_finding_records_chosen_action():
    """The system decides+shows its own recommendation from overlap_score;
    the user only picks one of the offered options -- no author needed."""
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="climtec.md", domain="climtec.md"))
    shared_text = "sistem ventilatie comerciala birou spatiu recuperator caldura instalare pret cost"
    ctx.extensions.register("wordpress-hub", "list_posts_full", _mock_posts_full([
        {"id": 1, "title": "A", "slug": "a", "link": "https://climtec.md/a",
         "content": f"<p>{shared_text} {shared_text}</p>", "excerpt": "x", "lang": "ro", "categories": []},
        {"id": 2, "title": "B", "slug": "b", "link": "https://climtec.md/b",
         "content": f"<p>{shared_text} {shared_text}</p>", "excerpt": "y", "lang": "ro", "categories": []},
    ]))
    await m.run_content_audit(ctx, RunContentAuditParams(site_id="climtec.md"))
    found = await m.check_keyword_cannibalization(ctx, CheckCannibalizationParams(site_id="climtec.md"))
    finding_id = found.data.items[0].id

    result = await m.resolve_cannibalization_finding(
        ctx, m.ResolveCannibalizationParams(finding_id=finding_id, action="merge"),
    )
    assert result.status == "success"
    assert result.data.chosen_action == "merge"
    assert result.data.status == "resolved"

    refreshed = await m.check_keyword_cannibalization(ctx, CheckCannibalizationParams(site_id="climtec.md"))
    resolved_row = next(f for f in refreshed.data.items if f.id == finding_id)
    assert resolved_row.status == "resolved"
    assert resolved_row.chosen_action == "merge"


@pytest.mark.asyncio
async def test_resolve_cannibalization_finding_dismiss_sets_dismissed_status():
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="climtec.md", domain="climtec.md"))
    shared_text = "sistem ventilatie comerciala birou spatiu recuperator caldura instalare pret cost"
    ctx.extensions.register("wordpress-hub", "list_posts_full", _mock_posts_full([
        {"id": 1, "title": "A", "slug": "a", "link": "https://climtec.md/a",
         "content": f"<p>{shared_text} {shared_text}</p>", "excerpt": "x", "lang": "ro", "categories": []},
        {"id": 2, "title": "B", "slug": "b", "link": "https://climtec.md/b",
         "content": f"<p>{shared_text} {shared_text}</p>", "excerpt": "y", "lang": "ro", "categories": []},
    ]))
    await m.run_content_audit(ctx, RunContentAuditParams(site_id="climtec.md"))
    found = await m.check_keyword_cannibalization(ctx, CheckCannibalizationParams(site_id="climtec.md"))
    finding_id = found.data.items[0].id

    result = await m.resolve_cannibalization_finding(
        ctx, m.ResolveCannibalizationParams(finding_id=finding_id, action="dismiss"),
    )
    assert result.status == "success"
    assert result.data.status == "dismissed"


@pytest.mark.asyncio
async def test_resolve_cannibalization_finding_rejects_invalid_action():
    ctx = MockContext()
    result = await m.resolve_cannibalization_finding(
        ctx, m.ResolveCannibalizationParams(finding_id="nope", action="rewrite everything"),
    )
    assert result.status == "error"


@pytest.mark.asyncio
async def test_resolve_cannibalization_finding_missing_id_errors():
    ctx = MockContext()
    result = await m.resolve_cannibalization_finding(
        ctx, m.ResolveCannibalizationParams(finding_id="does-not-exist", action="merge"),
    )
    assert result.status == "error"


@pytest.mark.asyncio
async def test_sources_panel_shows_open_cannibalization_finding_with_option_buttons():
    """Open findings must render as direct action buttons (merge/differentiate/
    canonicalize/dismiss) in the sources panel -- never a free-text field, and
    never gated behind an author requirement."""
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="climtec.md", domain="climtec.md"))
    shared_text = "sistem ventilatie comerciala birou spatiu recuperator caldura instalare pret cost"
    ctx.extensions.register("wordpress-hub", "list_posts_full", _mock_posts_full([
        {"id": 1, "title": "A", "slug": "a", "link": "https://climtec.md/a",
         "content": f"<p>{shared_text} {shared_text}</p>", "excerpt": "x", "lang": "ro", "categories": []},
        {"id": 2, "title": "B", "slug": "b", "link": "https://climtec.md/b",
         "content": f"<p>{shared_text} {shared_text}</p>", "excerpt": "y", "lang": "ro", "categories": []},
    ]))
    await m.run_content_audit(ctx, RunContentAuditParams(site_id="climtec.md"))
    node = await m.sources_panel(ctx)
    rendered = repr(node)
    assert "resolve_cannibalization_finding" in rendered
    assert "recommended" in rendered
    assert "open cannibalization finding" in rendered


@pytest.mark.asyncio
async def test_sources_panel_omits_resolved_cannibalization_finding():
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="climtec.md", domain="climtec.md"))
    shared_text = "sistem ventilatie comerciala birou spatiu recuperator caldura instalare pret cost"
    ctx.extensions.register("wordpress-hub", "list_posts_full", _mock_posts_full([
        {"id": 1, "title": "A", "slug": "a", "link": "https://climtec.md/a",
         "content": f"<p>{shared_text} {shared_text}</p>", "excerpt": "x", "lang": "ro", "categories": []},
        {"id": 2, "title": "B", "slug": "b", "link": "https://climtec.md/b",
         "content": f"<p>{shared_text} {shared_text}</p>", "excerpt": "y", "lang": "ro", "categories": []},
    ]))
    await m.run_content_audit(ctx, RunContentAuditParams(site_id="climtec.md"))
    found = await m.check_keyword_cannibalization(ctx, CheckCannibalizationParams(site_id="climtec.md"))
    finding_id = found.data.items[0].id
    await m.resolve_cannibalization_finding(
        ctx, m.ResolveCannibalizationParams(finding_id=finding_id, action="merge"),
    )
    node = await m.sources_panel(ctx)
    rendered = repr(node)
    assert "open cannibalization finding" not in rendered


@pytest.mark.asyncio
async def test_sources_panel_shows_audit_status_and_needs_doing():
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="climtec.md", domain="climtec.md"))
    ctx.extensions.register("wordpress-hub", "list_posts_full", _mock_posts_full([
        {"id": 1, "title": "X", "slug": "x", "link": "https://climtec.md/x",
         "content": "<p>" + ("word " * 50) + "</p>", "excerpt": "", "lang": "ro", "categories": []},
    ]))
    await m.run_content_audit(ctx, RunContentAuditParams(site_id="climtec.md"))
    node = await m.sources_panel(ctx)
    rendered = repr(node)
    assert "Content audit" in rendered
    assert "Needs doing" in rendered or "thin" in rendered.lower()


@pytest.mark.asyncio
async def test_sources_panel_shows_never_audited_warning():
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="climtec.md", domain="climtec.md"))
    node = await m.sources_panel(ctx)
    rendered = repr(node)
    assert "Never run" in rendered


@pytest.mark.asyncio
async def test_create_brief_requires_real_action_page_and_verified_external_source():
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="g4s.md", domain="g4s.md"))
    await _seed_audit(ctx, "g4s.md")
    discovered = await m.discover_opportunities(ctx, DiscoverOpportunitiesParams(
        site_id="g4s.md", queries=[QuerySignal(query="security services", impressions=10, clicks=1, ctr=0.1, avg_position=10)],
    ))
    ctx.extensions.register("wordpress-hub", "list_pages_full", _mock_action_pages([]))
    result = await m.create_brief(ctx, CreateBriefParams(opportunity_id=discovered.data.items[0].id))
    assert result.status == "error"
    assert "KEY_ACTION_PAGE_REQUIRED" in result.error


@pytest.mark.asyncio
async def test_create_brief_resolves_action_page_when_wordpress_reports_no_lang():
    """WordPress's own REST API never actually exposes Polylang's per-page
    language on /wp/v2/pages -- every real page comes back with lang="".
    create_brief must fall back to detecting the script (Cyrillic vs Latin)
    from the page's own title/content, exactly like run_content_audit
    already does for posts, instead of rejecting every real action page."""
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(
        site_id="climtec.md", domain="climtec.md", target_languages=["ru", "ro"],
        external_sources_i18n={"ru": ["https://source.example/ru"]},
    ))
    await _seed_audit(ctx, "climtec.md")
    ctx.extensions.register("wordpress-hub", "list_pages_full", _mock_action_pages([
        {"title": "Контакты", "slug": "contact-ru", "link": "https://climtec.md/ru/contact", "content": "", "lang": ""},
        {"title": "Contacte", "slug": "contacte", "link": "https://climtec.md/contacte", "content": "", "lang": ""},
    ]))
    disc = await m.discover_opportunities(ctx, DiscoverOpportunitiesParams(
        site_id="climtec.md", target_language="ru",
        queries=[QuerySignal(query="рекуператор тепла", impressions=10, clicks=1, ctr=0.1, avg_position=10)],
    ))
    result = await m.create_brief(ctx, CreateBriefParams(opportunity_id=disc.data.items[0].id))
    assert result.status == "success"
    assert result.data.key_action_page_url == "https://climtec.md/ru/contact"


@pytest.mark.asyncio
async def test_create_brief_selects_article_language_external_source_then_fallback():
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(
        site_id="climtec.md", domain="climtec.md", target_languages=["ru", "ro"],
        external_sources_i18n={"ro": ["https://source.example/ro"]},
    ))
    await _seed_audit(ctx, "climtec.md")
    ctx.extensions.register("wordpress-hub", "list_pages_full", _mock_action_pages([
        {"title": "Контакты", "slug": "contact-ru", "link": "https://climtec.md/ru/contact", "content": "", "lang": "ru"},
        {"title": "Contact", "slug": "contact", "link": "https://climtec.md/contact", "content": "", "lang": "ro"},
    ]))
    profiles = await ctx.store.query("site_profiles", where={"site_id": "climtec.md"}, limit=1)
    await ctx.store.update("site_profiles", profiles.data[0].id, {
        "external_sources_i18n": {"ro": ["https://source.example/ro"]},
    })
    discovered = await m.discover_opportunities(ctx, DiscoverOpportunitiesParams(
        site_id="climtec.md", queries=[QuerySignal(query="рекуперация тепла", impressions=10, clicks=1, ctr=0.1, avg_position=10)],
    ))
    result = await m.create_brief(ctx, CreateBriefParams(
        opportunity_id=discovered.data.items[0].id, target_language="ru",
    ))
    assert result.status == "success"
    assert result.data.key_action_page_url == "https://climtec.md/ru/contact"
    assert result.data.external_link_url == "https://source.example/ro"
    assert result.data.external_link_language == "ro"
    assert result.data.external_link_language_priority == ["ru", "ro"]


# ──────────────────────────────────────────────────────────────────────────
# Sidebar "Projects" section — existing site profiles surfaced as clickable
# projects, with an "Add new project" Dialog, and a center-panel brief
# catalogue per project (mirrors Media Hub's own packages catalogue view).
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_queue_panel_shows_projects_section_with_existing_sites():
    """No 'Projects' header (removed per explicit request) -- the project
    list and Add-project CTA stand on their own. The list is also not
    searchable (search removed per explicit request) and the Add button is
    the blue/primary CTA of this sidebar."""
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(
        site_id="g4s.md", domain="g4s.md", brand_name="G4S Moldova",
    ))
    node = await m.queue_panel(ctx)
    rendered = repr(node)
    assert "G4S Moldova" in rendered
    assert "Add new project" in rendered
    assert "'variant': 'primary'" in rendered
    assert "'searchable': False" in rendered


@pytest.mark.asyncio
async def test_queue_panel_projects_section_empty_state():
    ctx = MockContext()
    node = await m.queue_panel(ctx)
    rendered = repr(node)
    assert "No projects yet" in rendered
    assert "Add new project" in rendered
    assert "'variant': 'primary'" in rendered


@pytest.mark.asyncio
async def test_queue_panel_project_click_routes_to_brief_panel_with_site_id():
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="g4s.md", domain="g4s.md"))
    rendered = repr(await m.queue_panel(ctx))
    assert "'site_id': 'g4s.md'" in rendered
    assert "__panel__brief" in rendered


@pytest.mark.asyncio
async def test_queue_panel_add_project_button_opens_dialog_on_demand():
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="g4s.md", domain="g4s.md"))

    closed_rendered = repr(await m.queue_panel(ctx))
    assert "Dialog" not in closed_rendered

    open_rendered = repr(await m.queue_panel(ctx, show_add_project="1"))
    assert "Dialog" in open_rendered
    assert "Add new project" in open_rendered
    assert "create_site_profile" in open_rendered


@pytest.mark.asyncio
async def test_brief_panel_detail_view_has_back_to_briefs_button():
    """The brief detail screen (queue_item_id given) must carry a gray,
    medium, left-arrow-icon 'back' button naming its real destination --
    per the platform UX rule -- that routes back to this project's briefs
    catalog (brief_panel(site_id=...)), not a bare unlabeled 'Back'.

    Panel navigation merges call params with whatever is already open
    rather than replacing them, so the button must explicitly clear
    queue_item_id -- otherwise clicking it would still render this same
    detail view (the currently-open queue_item_id survives the call) and
    the button would not actually navigate anywhere, which is exactly the
    reported bug."""
    ctx = MockContext()
    queue_item_id, _brief_id = await _site_with_brief_ready_queue_item(ctx)
    rendered = repr(await m.brief_panel(ctx, queue_item_id=queue_item_id))
    assert "Back to Briefs" in rendered
    assert "'variant': 'secondary'" in rendered
    assert "'icon': 'ArrowLeft'" in rendered
    assert "'site_id': 'g4s.md'" in rendered
    assert "'queue_item_id': ''" in rendered

    # Simulate the actual click: __panel__brief re-invoked with the button's
    # own params (site_id set, queue_item_id explicitly cleared) must now
    # render the briefs catalog, not the same detail view again.
    back_rendered = repr(await m.brief_panel(ctx, site_id="g4s.md", queue_item_id=""))
    assert "Briefs (1)" in back_rendered


@pytest.mark.asyncio
async def test_brief_panel_shows_project_briefs_catalog_when_site_id_given_without_queue_item():
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(
        site_id="g4s.md", domain="g4s.md", brand_name="G4S Moldova",
    ))
    await _seed_audit(ctx, "g4s.md")
    disc = await m.discover_opportunities(ctx, DiscoverOpportunitiesParams(
        site_id="g4s.md",
        queries=[QuerySignal(query="security services chisinau", clicks=10, impressions=200, ctr=0.05, avg_position=8.0)],
    ))
    await m.create_brief(ctx, CreateBriefParams(opportunity_id=disc.data.items[0].id))

    rendered = repr(await m.brief_panel(ctx, site_id="g4s.md"))
    assert "G4S Moldova" in rendered
    assert "Briefs (1)" in rendered
    assert "Create new brief" in rendered


@pytest.mark.asyncio
async def test_brief_panel_create_brief_button_reveals_form_with_every_create_brief_field():
    """The 'Create new brief' CTA above the briefs list must reveal a form
    carrying every field create_brief actually accepts: which opportunity,
    target language, the real target_query text for that language, and
    an author picker (required when the site needs a named author)."""
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(
        site_id="g4s.md", domain="g4s.md", brand_name="G4S Moldova",
        target_languages=["en", "ro"], requires_named_author=True,
    ))
    await _seed_audit(ctx, "g4s.md")
    await m.discover_opportunities(ctx, DiscoverOpportunitiesParams(
        site_id="g4s.md",
        queries=[QuerySignal(query="security services chisinau", clicks=10, impressions=200, ctr=0.05, avg_position=8.0)],
    ))

    # Button not yet clicked: the dialog must not render.
    collapsed = repr(await m.brief_panel(ctx, site_id="g4s.md"))
    assert "Create new brief" in collapsed
    assert "type='Dialog'" not in collapsed

    # No author registered yet -> the dialog must say so instead of silently
    # letting a brief be created without one for a site requiring it.
    no_author_rendered = repr(await m.brief_panel(ctx, site_id="g4s.md", show_create_brief="1"))
    assert "type='Dialog'" in no_author_rendered
    assert "'function': 'create_brief'" in no_author_rendered
    assert "'param_name': 'opportunity_id'" in no_author_rendered
    assert "'param_name': 'target_language'" in no_author_rendered
    assert "'param_name': 'target_query'" in no_author_rendered
    assert "No content author registered" in no_author_rendered

    await m.create_content_author(ctx, CreateContentAuthorParams(site_id="g4s.md", name="Ion Popescu"))
    with_author_rendered = repr(await m.brief_panel(ctx, site_id="g4s.md", show_create_brief="1"))
    assert "'param_name': 'author_id'" in with_author_rendered
    assert "Ion Popescu" in with_author_rendered


@pytest.mark.asyncio
async def test_brief_panel_project_catalog_empty_state():
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="g4s.md", domain="g4s.md"))
    rendered = repr(await m.brief_panel(ctx, site_id="g4s.md"))
    assert "Briefs (0)" in rendered
    assert "No briefs yet for this project" in rendered


@pytest.mark.asyncio
async def test_brief_panel_with_neither_queue_item_nor_site_shows_generic_empty_state():
    ctx = MockContext()
    node = await m.brief_panel(ctx)
    rendered = repr(node)
    assert "Select a project" in rendered


@pytest.mark.asyncio
async def test_create_site_profile_refreshes_queue_and_brief_panels():
    ctx = MockContext()
    result = await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="g4s.md", domain="g4s.md"))
    assert "queue" in result.refresh_panels
    assert "brief" in result.refresh_panels
    assert "sources" in result.refresh_panels


# ──────────────────────────────────────────────────────────────────────────
# E-E-A-T: content authors and the requires_named_author gate
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_content_author_requires_existing_site():
    ctx = MockContext()
    result = await m.create_content_author(ctx, CreateContentAuthorParams(
        site_id="nope.md", name="Ion Popescu",
    ))
    assert result.status == "error"
    assert "create_site_profile" in result.error


@pytest.mark.asyncio
async def test_create_brief_blocks_without_author_when_site_requires_one():
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(
        site_id="climtec.md", domain="climtec.md", requires_named_author=True,
    ))
    await _seed_audit(ctx, "climtec.md")
    await _configure_required_link_inputs(ctx, site_id="climtec.md")
    discovered = await m.discover_opportunities(ctx, DiscoverOpportunitiesParams(
        site_id="climtec.md",
        queries=[QuerySignal(query="heat pump install", impressions=10, clicks=1, ctr=0.1, avg_position=10)],
    ))
    result = await m.create_brief(ctx, CreateBriefParams(opportunity_id=discovered.data.items[0].id))
    assert result.status == "error"
    assert "NAMED_AUTHOR_REQUIRED" in result.error


@pytest.mark.asyncio
async def test_create_brief_succeeds_with_real_author_and_carries_attribution():
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(
        site_id="climtec.md", domain="climtec.md", requires_named_author=True,
    ))
    await _seed_audit(ctx, "climtec.md")
    await _configure_required_link_inputs(ctx, site_id="climtec.md")
    author = await m.create_content_author(ctx, CreateContentAuthorParams(
        site_id="climtec.md", name="Ion Popescu", bio="15 years in HVAC engineering.",
        credentials=["Certified HVAC Engineer"], expertise_areas=["heat pumps"],
    ))
    assert author.status == "success"
    discovered = await m.discover_opportunities(ctx, DiscoverOpportunitiesParams(
        site_id="climtec.md",
        queries=[QuerySignal(query="heat pump install", impressions=10, clicks=1, ctr=0.1, avg_position=10)],
    ))
    result = await m.create_brief(ctx, CreateBriefParams(
        opportunity_id=discovered.data.items[0].id, author_id=author.data.id,
    ))
    assert result.status == "success"
    assert result.data.author_name == "Ion Popescu"
    assert result.data.author_credentials == ["Certified HVAC Engineer"]

    writer_brief = await m.build_writer_brief(ctx, BuildWriterBriefParams(brief_id=result.data.id))
    assert writer_brief.status == "success"
    assert writer_brief.data.author_name == "Ion Popescu"
    assert "Ion Popescu" in writer_brief.data.body
    assert "E-E-A-T attribution" in writer_brief.data.body


@pytest.mark.asyncio
async def test_create_brief_rejects_author_from_a_different_site():
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="g4s.md", domain="g4s.md"))
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="climtec.md", domain="climtec.md"))
    await _seed_audit(ctx, "g4s.md")
    await _configure_required_link_inputs(ctx, site_id="g4s.md")
    other_author = await m.create_content_author(ctx, CreateContentAuthorParams(
        site_id="climtec.md", name="Wrong Site Author",
    ))
    discovered = await m.discover_opportunities(ctx, DiscoverOpportunitiesParams(
        site_id="g4s.md",
        queries=[QuerySignal(query="security services", impressions=10, clicks=1, ctr=0.1, avg_position=10)],
    ))
    result = await m.create_brief(ctx, CreateBriefParams(
        opportunity_id=discovered.data.items[0].id, author_id=other_author.data.id,
    ))
    assert result.status == "error"
    assert "not found for site" in result.error


@pytest.mark.asyncio
async def test_list_content_authors_filters_by_site():
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="g4s.md", domain="g4s.md"))
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="climtec.md", domain="climtec.md"))
    await m.create_content_author(ctx, CreateContentAuthorParams(site_id="g4s.md", name="A"))
    await m.create_content_author(ctx, CreateContentAuthorParams(site_id="climtec.md", name="B"))
    result = await m.list_content_authors(ctx, ListContentAuthorsParams(site_id="g4s.md"))
    assert result.status == "success"
    assert result.data.total == 1
    assert result.data.items[0].name == "A"


@pytest.mark.asyncio
async def test_update_site_profile_toggles_requires_named_author():
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="g4s.md", domain="g4s.md"))
    result = await m.update_site_profile(ctx, UpdateSiteProfileParams(
        site_id="g4s.md", requires_named_author=True,
    ))
    assert result.status == "success"
    assert result.data.requires_named_author is True


@pytest.mark.asyncio
async def test_purge_pipeline_data_removes_content_authors():
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="g4s.md", domain="g4s.md"))
    await m.create_content_author(ctx, CreateContentAuthorParams(site_id="g4s.md", name="A"))
    from schemas import PurgePipelineDataParams
    result = await m.purge_pipeline_data(ctx, PurgePipelineDataParams(confirm_wipe=True))
    assert result.status == "success"
    assert result.data.authors_removed == 1


# ──────────────────────────────────────────────────────────────────────────
# Phase 2: content decay tracking
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_track_content_decay_requires_existing_site():
    ctx = MockContext()
    result = await m.track_content_decay(ctx, TrackContentDecayParams(
        site_id="nope.md", signals=[ContentPerformanceSignal(url="https://nope.md/x", clicks=10)],
    ))
    assert result.status == "error"
    assert "create_site_profile" in result.error


@pytest.mark.asyncio
async def test_track_content_decay_first_call_sets_baseline_only():
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="g4s.md", domain="g4s.md"))
    result = await m.track_content_decay(ctx, TrackContentDecayParams(
        site_id="g4s.md",
        signals=[ContentPerformanceSignal(url="https://g4s.md/blog/a", clicks=50, impressions=500, avg_position=8.0)],
    ))
    assert result.status == "success"
    assert result.data.new_count == 1
    assert result.data.decaying_count == 0
    assert result.data.decaying_items == []


@pytest.mark.asyncio
async def test_track_content_decay_detects_decay_on_second_call():
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="g4s.md", domain="g4s.md"))
    await m.track_content_decay(ctx, TrackContentDecayParams(
        site_id="g4s.md",
        signals=[ContentPerformanceSignal(url="https://g4s.md/blog/a", clicks=100, impressions=1000, avg_position=5.0)],
    ))
    result = await m.track_content_decay(ctx, TrackContentDecayParams(
        site_id="g4s.md",
        signals=[ContentPerformanceSignal(url="https://g4s.md/blog/a", clicks=50, impressions=900, avg_position=9.0)],
    ))
    assert result.status == "success"
    assert result.data.decaying_count == 1
    assert result.data.decaying_items[0].url == "https://g4s.md/blog/a"
    assert result.data.decaying_items[0].verdict == "decaying"
    assert "Refresh" in result.data.decaying_items[0].recommendation


@pytest.mark.asyncio
async def test_track_content_decay_detects_improving_content():
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="g4s.md", domain="g4s.md"))
    await m.track_content_decay(ctx, TrackContentDecayParams(
        site_id="g4s.md",
        signals=[ContentPerformanceSignal(url="https://g4s.md/blog/b", clicks=20, impressions=400, avg_position=15.0)],
    ))
    result = await m.track_content_decay(ctx, TrackContentDecayParams(
        site_id="g4s.md",
        signals=[ContentPerformanceSignal(url="https://g4s.md/blog/b", clicks=60, impressions=600, avg_position=6.0)],
    ))
    assert result.status == "success"
    assert result.data.improving_count == 1
    assert result.data.decaying_count == 0


@pytest.mark.asyncio
async def test_get_content_decay_requires_prior_tracking():
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="g4s.md", domain="g4s.md"))
    result = await m.get_content_decay(ctx, GetContentDecayParams(site_id="g4s.md"))
    assert result.status == "error"
    assert "track_content_decay" in result.error


@pytest.mark.asyncio
async def test_get_content_decay_reads_back_saved_report():
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="g4s.md", domain="g4s.md"))
    await m.track_content_decay(ctx, TrackContentDecayParams(
        site_id="g4s.md",
        signals=[ContentPerformanceSignal(url="https://g4s.md/blog/a", clicks=50, avg_position=8.0)],
    ))
    result = await m.get_content_decay(ctx, GetContentDecayParams(site_id="g4s.md"))
    assert result.status == "success"
    assert result.data.tracked_count == 1


@pytest.mark.asyncio
async def test_purge_pipeline_data_removes_content_decay_records():
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="g4s.md", domain="g4s.md"))
    await m.track_content_decay(ctx, TrackContentDecayParams(
        site_id="g4s.md",
        signals=[ContentPerformanceSignal(url="https://g4s.md/blog/a", clicks=50, avg_position=8.0)],
    ))
    from schemas import PurgePipelineDataParams
    result = await m.purge_pipeline_data(ctx, PurgePipelineDataParams(confirm_wipe=True))
    assert result.status == "success"
    assert result.data.decay_readings_removed == 2  # 1 reading + 1 report


# ──────────────────────────────────────────────────────────────────────────
# Phase 3: editorial lifecycle — fact-check/edit signoff gate
# ──────────────────────────────────────────────────────────────────────────

async def _make_queue_item(ctx, site_id="g4s.md"):
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id=site_id, domain=site_id))
    await _seed_audit(ctx, site_id)
    disc = await m.discover_opportunities(ctx, DiscoverOpportunitiesParams(
        site_id=site_id,
        queries=[QuerySignal(query="security services chisinau", impressions=500, clicks=10, ctr=0.02, avg_position=8.5)],
    ))
    await m.create_brief(ctx, CreateBriefParams(opportunity_id=disc.data.items[0].id))
    from schemas import ListQueueParams
    queue_result = await m.list_queue(ctx, ListQueueParams(site_id=site_id))
    return queue_result.data.items[0].id


@pytest.mark.asyncio
async def test_update_queue_status_blocks_publish_without_fact_check():
    ctx = MockContext()
    queue_item_id = await _make_queue_item(ctx)
    result = await m.update_queue_status(ctx, UpdateQueueStatusParams(
        queue_item_id=queue_item_id, lifecycle_status="published", published_url="https://g4s.md/blog/x",
    ))
    assert result.status == "error"
    assert "FACT_CHECK_REQUIRED" in result.error


@pytest.mark.asyncio
async def test_record_editorial_signoff_then_publish_succeeds():
    ctx = MockContext()
    queue_item_id = await _make_queue_item(ctx)
    signoff = await m.record_editorial_signoff(ctx, RecordEditorialSignoffParams(
        queue_item_id=queue_item_id, fact_checked_by="Maria Editor", edited_by="Ion Editor",
    ))
    assert signoff.status == "success"
    assert signoff.data.fact_checked is True
    assert signoff.data.fact_checked_by == "Maria Editor"
    assert signoff.data.edited_by == "Ion Editor"

    result = await m.update_queue_status(ctx, UpdateQueueStatusParams(
        queue_item_id=queue_item_id, lifecycle_status="published", published_url="https://g4s.md/blog/x",
    ))
    assert result.status == "success"
    assert result.data.lifecycle_status == "published"


@pytest.mark.asyncio
async def test_record_editorial_signoff_requires_existing_queue_item():
    ctx = MockContext()
    result = await m.record_editorial_signoff(ctx, RecordEditorialSignoffParams(
        queue_item_id="nonexistent", fact_checked_by="Maria Editor",
    ))
    assert result.status == "error"


@pytest.mark.asyncio
async def test_record_editorial_signoff_edited_only_does_not_satisfy_fact_check_gate():
    ctx = MockContext()
    queue_item_id = await _make_queue_item(ctx)
    await m.record_editorial_signoff(ctx, RecordEditorialSignoffParams(
        queue_item_id=queue_item_id, edited_by="Ion Editor",
    ))
    result = await m.update_queue_status(ctx, UpdateQueueStatusParams(
        queue_item_id=queue_item_id, lifecycle_status="published",
    ))
    assert result.status == "error"
    assert "FACT_CHECK_REQUIRED" in result.error


# ──────────────────────────────────────────────────────────────────────────
# Phase 5: unified KPI dashboard
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_record_kpi_snapshot_requires_existing_site():
    ctx = MockContext()
    result = await m.record_kpi_snapshot(ctx, RecordKpiSnapshotParams(
        site_id="nope.md", period_label="2026-07", ga4_sessions=100,
    ))
    assert result.status == "error"
    assert "create_site_profile" in result.error


@pytest.mark.asyncio
async def test_get_kpi_dashboard_requires_prior_snapshot():
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="g4s.md", domain="g4s.md"))
    result = await m.get_kpi_dashboard(ctx, GetKpiDashboardParams(site_id="g4s.md"))
    assert result.status == "error"
    assert "record_kpi_snapshot" in result.error


@pytest.mark.asyncio
async def test_kpi_dashboard_shows_trend_after_two_snapshots():
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="g4s.md", domain="g4s.md"))
    await m.record_kpi_snapshot(ctx, RecordKpiSnapshotParams(
        site_id="g4s.md", period_label="2026-06",
        ga4_sessions=1000, ga4_conversions=20, gsc_clicks=500, dataforseo_avg_rank=12.0,
    ))
    result = await m.record_kpi_snapshot(ctx, RecordKpiSnapshotParams(
        site_id="g4s.md", period_label="2026-07",
        ga4_sessions=1200, ga4_conversions=15, gsc_clicks=400, dataforseo_avg_rank=15.0,
    ))
    assert result.status == "success"

    dash = await m.get_kpi_dashboard(ctx, GetKpiDashboardParams(site_id="g4s.md"))
    assert dash.status == "success"
    assert dash.data.latest_period == "2026-07"
    assert len(dash.data.trend) > 0
    assert any("conversions dropped" in n for n in dash.data.needs_doing)
    assert any("keyword rank got worse" in n for n in dash.data.needs_doing)


@pytest.mark.asyncio
async def test_purge_pipeline_data_removes_kpi_records():
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="g4s.md", domain="g4s.md"))
    await m.record_kpi_snapshot(ctx, RecordKpiSnapshotParams(site_id="g4s.md", period_label="2026-07", ga4_sessions=100))
    from schemas import PurgePipelineDataParams
    result = await m.purge_pipeline_data(ctx, PurgePipelineDataParams(confirm_wipe=True))
    assert result.status == "success"
    assert result.data.kpi_snapshots_removed >= 2  # 1 snapshot + 1 dashboard report


# ──────────────────────────────────────────────────────────────────────────
# Phase 6: active link-building / outreach workflow
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_link_building_target_requires_existing_site():
    ctx = MockContext()
    result = await m.add_link_building_target(ctx, CreateOutreachTargetParams(
        site_id="nope.md", target_domain="example-blog.com",
    ))
    assert result.status == "error"
    assert "create_site_profile" in result.error


@pytest.mark.asyncio
async def test_add_link_building_target_starts_prospected():
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="g4s.md", domain="g4s.md"))
    result = await m.add_link_building_target(ctx, CreateOutreachTargetParams(
        site_id="g4s.md", target_domain="example-blog.com", tactic="guest_post",
    ))
    assert result.status == "success"
    assert result.data.status == "prospected"
    assert len(result.data.status_history) == 1


@pytest.mark.asyncio
async def test_update_outreach_status_rejects_invalid_status():
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="g4s.md", domain="g4s.md"))
    created = await m.add_link_building_target(ctx, CreateOutreachTargetParams(
        site_id="g4s.md", target_domain="example-blog.com",
    ))
    bad = await m.update_outreach_status(ctx, UpdateOutreachStatusParams(
        outreach_id=created.data.id, status="not_a_real_status",
    ))
    assert bad.status == "error"


@pytest.mark.asyncio
async def test_update_outreach_status_to_link_acquired_records_url():
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="g4s.md", domain="g4s.md"))
    created = await m.add_link_building_target(ctx, CreateOutreachTargetParams(
        site_id="g4s.md", target_domain="example-blog.com",
    ))
    ok = await m.update_outreach_status(ctx, UpdateOutreachStatusParams(
        outreach_id=created.data.id, status="link_acquired", acquired_url="https://example-blog.com/post-with-link",
    ))
    assert ok.status == "success"
    assert ok.data.status == "link_acquired"
    assert ok.data.acquired_url == "https://example-blog.com/post-with-link"
    assert len(ok.data.status_history) == 2


@pytest.mark.asyncio
async def test_list_link_building_targets_filters_by_status():
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="g4s.md", domain="g4s.md"))
    a = await m.add_link_building_target(ctx, CreateOutreachTargetParams(site_id="g4s.md", target_domain="a.com"))
    await m.add_link_building_target(ctx, CreateOutreachTargetParams(site_id="g4s.md", target_domain="b.com"))
    await m.update_outreach_status(ctx, UpdateOutreachStatusParams(outreach_id=a.data.id, status="contacted"))

    all_targets = await m.list_link_building_targets(ctx, ListOutreachTargetsParams(site_id="g4s.md"))
    assert all_targets.data.total == 2

    contacted_only = await m.list_link_building_targets(ctx, ListOutreachTargetsParams(site_id="g4s.md", status="contacted"))
    assert contacted_only.data.total == 1


@pytest.mark.asyncio
async def test_get_link_building_report_requires_targets():
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="g4s.md", domain="g4s.md"))
    result = await m.get_link_building_report(ctx, GetLinkBuildingReportParams(site_id="g4s.md"))
    assert result.status == "error"
    assert "add_link_building_target" in result.error


@pytest.mark.asyncio
async def test_get_link_building_report_computes_conversion_and_followups():
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="g4s.md", domain="g4s.md"))
    a = await m.add_link_building_target(ctx, CreateOutreachTargetParams(site_id="g4s.md", target_domain="a.com"))
    b = await m.add_link_building_target(ctx, CreateOutreachTargetParams(site_id="g4s.md", target_domain="b.com"))
    await m.add_link_building_target(ctx, CreateOutreachTargetParams(site_id="g4s.md", target_domain="c.com"))

    await m.update_outreach_status(ctx, UpdateOutreachStatusParams(outreach_id=a.data.id, status="contacted"))
    await m.update_outreach_status(ctx, UpdateOutreachStatusParams(outreach_id=b.data.id, status="link_acquired", acquired_url="https://b.com/x"))

    report = await m.get_link_building_report(ctx, GetLinkBuildingReportParams(site_id="g4s.md"))
    assert report.status == "success"
    assert report.data.total_targets == 3
    assert report.data.links_acquired == 1
    assert any("a.com" in n for n in report.data.needs_doing)


@pytest.mark.asyncio
async def test_purge_pipeline_data_removes_outreach_targets():
    ctx = MockContext()
    await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="g4s.md", domain="g4s.md"))
    await m.add_link_building_target(ctx, CreateOutreachTargetParams(site_id="g4s.md", target_domain="a.com"))
    from schemas import PurgePipelineDataParams
    result = await m.purge_pipeline_data(ctx, PurgePipelineDataParams(confirm_wipe=True))
    assert result.status == "success"
    assert result.data.outreach_targets_removed >= 1
