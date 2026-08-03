"""Lightweight local smoke test — exercises the core flow (create site profile
-> discover opportunities -> create brief -> queue) against an in-memory fake
ctx.store, without needing a live web-kernel or deploy cycle.

Run: source .venv/bin/activate && python tests/test_smoke.py
"""
import asyncio
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@dataclass
class FakeDoc:
    id: str
    data: dict


@dataclass
class FakePage:
    data: list


class FakeStore:
    """Minimal in-memory stand-in for ctx.store: create/get/query/update."""

    def __init__(self):
        self._collections: dict[str, dict[str, dict]] = {}

    def _coll(self, name):
        return self._collections.setdefault(name, {})

    async def create(self, collection, data):
        doc_id = str(uuid.uuid4())
        self._coll(collection)[doc_id] = dict(data)
        return FakeDoc(id=doc_id, data=dict(data))

    async def get(self, collection, doc_id):
        d = self._coll(collection).get(doc_id)
        return FakeDoc(id=doc_id, data=dict(d)) if d is not None else None

    async def query(self, collection, where=None, order_by=None, limit=1000):
        items = [FakeDoc(id=k, data=dict(v)) for k, v in self._coll(collection).items()]
        if where:
            items = [d for d in items if all(d.data.get(k) == v for k, v in where.items())]
        return FakePage(data=items[:limit])

    async def update(self, collection, doc_id, patch):
        self._coll(collection)[doc_id].update(patch)
        return FakeDoc(id=doc_id, data=dict(self._coll(collection)[doc_id]))


class FakeCtx:
    def __init__(self):
        self.store = FakeStore()


async def main():
    import handlers_chat as h
    from schemas import (
        CreateSiteProfileParams, DiscoverOpportunitiesParams, QuerySignal,
        CreateBriefParams, UpdateQueueStatusParams,
    )

    ctx = FakeCtx()

    # 1. Create site profile
    r = await h.create_site_profile(ctx, CreateSiteProfileParams(
        site_id="g4s.md", domain="g4s.md", brand_name="G4S",
        business_description="Security services", target_languages=["ro", "ru"],
        content_categories=["security"], cta_default="Request a quote",
    ))
    assert r.status == "success", r
    print("✓ create_site_profile:", r.data.site_id if hasattr(r.data, "site_id") else r.data)

    # Duplicate should fail cleanly
    r_dup = await h.create_site_profile(ctx, CreateSiteProfileParams(site_id="g4s.md", domain="g4s.md"))
    assert r_dup.status == "error", "expected duplicate site_id to be rejected"
    print("✓ duplicate site_id correctly rejected")

    # 2. Discover opportunities from fake query signals
    r2 = await h.discover_opportunities(ctx, DiscoverOpportunitiesParams(
        site_id="g4s.md",
        queries=[
            QuerySignal(query="security services chisinau", impressions=500, clicks=5, ctr=0.01, avg_position=8.0, source="gsc"),
            QuerySignal(query="video surveillance price", impressions=300, clicks=2, ctr=0.006, avg_position=12.0, source="gsc"),
        ],
    ))
    assert r2.status == "success", r2
    opps = r2.data.items
    assert len(opps) == 2
    print(f"✓ discover_opportunities: {len(opps)} created, top score={opps[0].total_priority_score:.1f}")

    # 3. List opportunities filtered by site
    r3 = await h.list_opportunities(ctx, __import__("schemas").ListOpportunitiesParams(site_id="g4s.md"))
    assert r3.status == "success" and len(r3.data.items) == 2
    print("✓ list_opportunities filter by site works")

    # 4. Create a brief from the top opportunity
    top_opp_id = opps[0].id
    r4 = await h.create_brief(ctx, CreateBriefParams(
        opportunity_id=top_opp_id, working_title="Security Services in Chisinau — What to Expect",
        target_language="ru", target_audience="SMB owners",
        outline=["Intro", "What services exist", "Pricing factors", "CTA"],
        cta_goal="Request a quote", image_requirements=["featured", "inline_1"],
    ))
    assert r4.status == "success", r4
    brief_id = r4.data.id
    print("✓ create_brief:", r4.data.working_title)

    # 5. Queue item for that opportunity should now be brief_ready
    r5 = await h.list_queue(ctx, __import__("schemas").ListQueueParams(site_id="g4s.md"))
    assert r5.status == "success"
    matching = [q for q in r5.data.items if q.opportunity_id == top_opp_id]
    assert matching and matching[0].lifecycle_status == "brief_ready", matching
    print("✓ queue item auto-advanced to brief_ready")

    # 6. Move to approved
    qid = matching[0].id
    r6 = await h.update_queue_status(ctx, UpdateQueueStatusParams(queue_item_id=qid, lifecycle_status="approved"))
    assert r6.status == "success", r6
    print("✓ update_queue_status -> approved")

    # 7. Unknown page/queue id should error, not crash
    r7 = await h.update_queue_status(ctx, UpdateQueueStatusParams(queue_item_id="does-not-exist", lifecycle_status="approved"))
    assert r7.status == "error"
    print("✓ unknown queue_item_id correctly rejected")

    print("\nALL SMOKE CHECKS PASSED ✅")


if __name__ == "__main__":
    asyncio.run(main())
