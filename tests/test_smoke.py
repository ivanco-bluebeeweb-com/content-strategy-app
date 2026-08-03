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
    CreateBriefParams, UpdateQueueStatusParams,
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
