# SEO pipeline — development plan and visible delivery baseline

**Updated:** 2026-08-07  
**Scope:** SEO pipeline only.  
**Working rule:** one vertical slice at a time: implement → test → deploy/push → confirm what is visible in Imperal panel.

---

## Why this plan exists

Technical changes that do not produce a clear, observable result in the live product do **not** count as meaningful delivery by themselves. Every next slice must end with:

1. a named user flow;
2. a concrete place in the Imperal panel where it appears;
3. an explicit success state and failure/stale state;
4. automated regression coverage;
5. build, validation, commit, and push.

No image generation, media-package creation, or asset upload belongs to the current scope. Visual guidance is read-only. Provider policy stays: **third-party providers first; Magnific only after a technical failure of other providers.**

---

## Current baseline — already built and pushed

### VBS source baseline — code-verified

**Brand Strategy Hub** is the source of truth for an approved Visual Brand System (VBS) and Visual Profile. It has already delivered the following, with **96 automated tests passing** on 2026-08-07:

1. Private, tenant-local VBS workspace with explicit owner claim.
2. VBS revisions plus evidence registration and human evidence review (`reviewed_valid`, hypothesis, rejected, archived).
3. Role-based access: owner, editor, reviewer, viewer; legacy access migration requires owner action.
4. Immutable approval evidence snapshots, approval-basis verification, and fail-closed integrity gates.
5. Auditable Visual Profile drafts and human approval bound to an approved VBS and its reviewed evidence basis.
6. Read-only, non-personal downstream handoffs for Content Strategy and Media guidance.
7. Brand Strategy panel states for VBS, evidence, profile approval, integrity pause, and approved handoffs.

**Latest VBS commits:**

- `821691d` — expose approved visual handoffs in Brand Strategy UI
- `2299448` — add read-only approved Visual Profile → Media guidance handoff
- `f1b07e6` — add read-only approved Visual Profile → Content Strategy handoff
- `e95b988` — complete Visual Profile approval UI

**Important distinction:** this baseline is verified in code and tests. It is **not yet counted as live-product proof** until its panel workflow is exercised successfully with a representative brand by a user.

### What works in code

The approved Visual Profile / VBS baseline travels safely through:

`Brand Strategy approval → Site Profile → Article Brief → Writer / Media handoff`

It is fail-closed:

- approval basis uses `profile_id`, `profile_revision`, `vbs_id`, `vbs_revision`, and `snapshot_hash`;
- an outdated basis is marked **stale**;
- stale guidance cannot be sent to Media Studio;
- stale guidance is omitted from Writer handoff;
- Refresh copies the current approved Site Profile guidance into the brief without generating media.

### What should now be visible in the panel

**Imperal panel → Content Strategy Hub**

- **Sites / Sources:** approved visual guidance shows visual direction, provider policy, Profile/VBS revisions and snapshot hash.
- **Editorial Queue:** briefs with visual guidance show `Visual baseline: current` or `Visual baseline: stale`.
- **Brief:**
  - current baseline: `Build Media Studio handoff` is available;
  - stale baseline: Media handoff is unavailable and `Refresh approved visual guidance` is offered.
- **Calendar API response:** exposes `visual_baseline_state` without copying visual guidance into queue/calendar records.

### Latest delivered commits

- `0c00fcd` — centralised fail-closed visual-baseline comparison
- `d3f7ff4` — omit stale visual guidance from Writer handoff
- `b59e19d` — preserve baseline status while scheduling a calendar
- `84a08ce` — expose baseline status in content calendar
- `574b7d8` — show baseline status in editorial queue
- `4c0cdab` — show approval provenance in Sources UI
- `cedd8db` — refresh stale guidance before Media handoff

---

## Phase P0-A — prove the current UI flow in live panel

**Goal:** validate that the already built safety flow is actually visible and usable, not merely covered by tests.

### User-visible flow

1. Open **Content Strategy Hub → Editorial Queue**.
2. Open a brief marked `Visual baseline: stale`.
3. See `Refresh approved visual guidance`.
4. Run refresh.
5. Reopen the brief and see `Build Media Studio handoff`.
6. Confirm the handoff is read-only: no media package, no images, no generation.

### Definition of done

- The full flow is confirmed in the live panel on one real/representative brief.
- Any panel/render/action-wiring defect becomes a platform/product issue note, with reproduction steps.
- No new functionality is added before this check is complete.

**Status:** `LIVE-PROVEN — 2026-08-09`
**Owner:** Vlad checked the panel and confirmed the flow live (screenshot on file).

### Live proof — 2026-08-09

Before this flow could be checked at all, Climtec's site profile had **no** approved
visual baseline stored yet (`approved_visual_guidance: {}`), so no queue item could
show `current` or `stale`. While wiring it up, a real bug surfaced and was fixed
first (see commit `b67feda`, content-strategy-app): `converters.py`'s
`to_site_profile()` never read `approved_visual_guidance` back from the store, so
`update_site_profile`/`list_site_profiles` silently returned `{}` even after a
successful write. Panels were unaffected (they read the store directly), which is
exactly why this stayed invisible until an external caller's own API response was
checked. Fixed, covered by a new regression test (54/54 passing), deployed
(`b67fedaa`), and verified live with a real API call before touching the panel.

With the baseline written and one real brief refreshed, Vlad opened
**Content Strategy Hub → Editorial Queue → "рекуператор для квартиры"** and
confirmed live: `Visual baseline: current`, approval basis (Profile r1 · VBS r2),
snapshot hash, and `Build Media Studio handoff` visible and read-only as designed.

---

## Phase P0-B — make Writer readiness visible in Brief UI

**Goal:** the same Brief screen must communicate readiness for **both** downstream routes, not only Media Studio.

### User-visible result

In **Content Strategy Hub → Brief**:

- `Writer handoff: ready` when baseline is current;
- `Writer handoff: visual guidance excluded — baseline stale` when it is stale;
- the existing Refresh action remains the recovery path;
- no generation controls appear.

### Definition of done

- One brief screen visibly explains Media and Writer readiness independently.
- `current` and `stale` states have UI regression tests.
- Build / validation / push pass.

**Status:** `LIVE-IMPLEMENTED — 2026-08-09`

Implemented directly (no separate UI spike needed: same brief_panel code path
already proven live in P0-A, same handoff_available flag reused, zero new
schema/query). Verified: 55/55 tests pass, imperal validate 0 errors/0
warnings, committed (`04b6a45`), pushed, deployed (`04b6a453`). Live check:
open **Content Strategy Hub → Editorial Queue → "рекуператор для квартиры"**
→ Brief → Image requirements card now additionally shows
`Writer handoff: ready — visual guidance will be included` beneath the
existing Media Studio handoff control, since this brief's baseline is
current as of P0-A.

---

## Phase P0-C — visible, read-only handoff review

**Goal:** make each assembled downstream payload inspectable before it leaves Content Strategy.

### User-visible result

A Brief shows a compact read-only handoff review:

- target app: Writer or Media Studio;
- baseline state and approval provenance;
- explicit boundary: `No media is generated here`;
- third-party-first provider policy for Media;
- no send/create/generate action in this app.

### Definition of done

- The user can see exactly what guidance will be handed over, without needing logs or code.
- No asset generation or upload is introduced.
- Regression coverage protects data minimisation and stale fail-closed behaviour.

**Status:** `LIVE-IMPLEMENTED — 2026-08-09`

The old Media-only handoff block on the Image requirements card was replaced
by one unified read-only review naming both downstream consumers: "→ Article
Writer" (readiness + boundary text) and "→ Media Studio" (readiness +
provider policy), sitting under the same shared approval provenance
(visual intent, style direction, Profile/VBS revision, snapshot hash). The
existing Build/Refresh Form is unchanged, just relocated under the Media
section it belongs to. No new action, no schema change. 56/56 tests pass
(one P0-B test updated for the restructured copy, one new P0-C test asserts
both sections + provenance + boundary + provider policy render together).
imperal validate: 0 errors, 0 warnings. Committed `d009b74`, deployed
`d009b74d`. Live check: same brief as before
(**Editorial Queue → "рекуператор для квартиры" → Brief**) now shows one
card with both "→ Article Writer" and "→ Media Studio" sections.

---

## Explicitly out of scope until the P0 UI flow is proven

- Creating Media Studio packages or generating images.
- Direct image upload to WordPress.
- Personal imagery, facial recognition, face swap, synthetic likenesses, or consent/license collection UI.
- Reopening audit/evidence hash hardening without a newly observed defect.
- Broad SEO features unrelated to this approval-to-handoff slice.

---

## Working cadence

For every slice, Webbee will report only:

- **what became visible in the panel;**
- **where to see it;**
- **which commit was pushed;**
- **test/build/validation result;**
- **the next single visible slice.**

A commit without a panel-visible outcome will be described as maintenance, not as product progress.
