"""Lightweight local smoke test — exercises the core flow (create site profile
-> discover opportunities -> create brief -> queue) against an in-memory fake
ctx.store, without needing a live web-kernel or deploy cycle.

Run: source .venv/bin/activate && python tests/test_smoke.py
"""
import asyncio
import sys
import uuid
from dataclasses import dataclass
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
    import main as m
    from schemas import (
        CreateSiteProfileParams, DiscoverOpportunitiesParams, QuerySignal,
        CreateBriefParams, ListOpportunitiesParams, UpdateQueueStatusParams,
    )

    ctx = FakeCtx()

    r = await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="g4s.md", domain="g4s.md"))
    assert r.status == "success", r
    print(f"✓ create_site_profile: {r.data.site_id}")

    r_dup = await m.create_site_profile(ctx, CreateSiteProfileParams(site_id="g4s.md", domain="g4s.md"))
    assert r_dup.status == "error", "expected duplicate site_id to be rejected"
    print("✓ duplicate site_id correctly rejected")

    r2 = await m.discover_opportunities(
        ctx,
        DiscoverOpportunitiesParams(
            site_id="g4s.md",
            queries=[
                QuerySignal(query="security services chisinau", source="gsc", impressions=500, clicks=10, ctr=0.02, avg_position=8.0),
                QuerySignal(query="how much does a security guard cost", source="gsc", impressions=200, clicks=30, ctr=0.15, avg_position=3.0),
            ],
        ),
    )
    assert r2.status == "success", r2
    print(f"✓ discover_opportunities: {len(r2.data.items)} created, top score={r2.data.items[0].total_priority_score}")

    r3 = await m.list_opportunities(ctx, ListOpportunitiesParams(site_id="g4s.md"))
    assert r3.status == "success" and len(r3.data.items) == 2
    print("✓ list_opportunities filter by site works")

    top_opp = r2.data.items[0]
    r4 = await m.create_brief(ctx, CreateBriefParams(opportunity_id=top_opp.id))
    assert r4.status == "success", r4
    print(f"✓ create_brief: {r4.data.working_title}")

    q_page = await ctx.store.query("queue_items", limit=500)
    matching = [d.data for d in q_page.data if d.data.get("opportunity_id") == top_opp.id]
    assert matching and matching[0]["lifecycle_status"] == "brief_ready", matching
    print("✓ queue item auto-advanced to brief_ready")

    queue_doc_id = [d.id for d in q_page.data if d.data.get("opportunity_id") == top_opp.id][0]
    r6 = await m.update_queue_status(ctx, UpdateQueueStatusParams(queue_item_id=queue_doc_id, lifecycle_status="approved"))
    assert r6.status == "success", r6
    print("✓ update_queue_status -> approved")

    r7 = await m.update_queue_status(ctx, UpdateQueueStatusParams(queue_item_id="nonexistent", lifecycle_status="approved"))
    assert r7.status == "error"
    print("✓ unknown queue_item_id correctly rejected")

    print("\nALL SMOKE CHECKS PASSED ✅")


if __name__ == "__main__":
    asyncio.run(main())
