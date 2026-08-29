# Connecting a Lovable frontend

The backend hands you one JSON object per run. Everything a UI needs is in it, and
`views` is pre-denormalised so you can render without joining anything.

[`sye.ts`](sye.ts) types all of it and includes a small client. Copy it into your
Lovable project (e.g. `src/lib/sye.ts`).

---

## Path A — static payload (fastest, no backend to host)

Best for a demo: build the whole UI against a real run, then switch to live later by
changing one line.

```bash
uv run python scripts/run_demo.py examples/demo_easy.json
cat data/demo_runs/<run_id>/lovable_payload.json      # ~200 KB of plain JSON
```

Add that file to your Lovable project as `src/data/run.json`, then:

```ts
import type { LovablePayload } from "@/lib/sye";
import raw from "@/data/run.json";

const run = raw as unknown as LovablePayload;

export const campaigns = run.views.campaign_cards;   // storefront
export const journeys  = run.views.user_journeys;    // "why am I in this group"
export const buckets   = run.views.bucket_summaries; // grouping explainer
export const timeline  = run.views.timeline;         // how it was decided
```

## Path B — live API

```bash
uv run uvicorn sye.main:app --port 8000
```

```ts
import { createSyeClient } from "@/lib/sye";

const sye = createSyeClient(import.meta.env.VITE_SYE_API_URL);

// one-click demo
const run = await sye.runScenario("easy");

// or run the pipeline on requests your users typed
const run = await sye.run({
  users: {
    "john doe": "I need a 27 inch 1440p monitor that charges over USB-C, under €320",
    "jane doe": "At least 27 inches, QHD, for spreadsheets. Around €280",
  },
  offline: false,        // research the live web with Linkup
});

run.views.campaign_cards.forEach((card) => console.log(card.title, card.pricing.group_price));
```

Both `POST /api/v1/demo/runs` and `POST /api/v1/demo/scenarios/{key}/run` accept
`?format=lovable` and return the payload with `views` already attached — one request,
one render.

`users` accepts whichever shape you have:

```jsonc
{"john doe": "...", "jane doe": "..."}          // mapping
[{"user_id": "john doe", "prompt": "..."}]      // objects
["...", "..."]                                  // bare prompts
```

### Two things to get right

**CORS.** Add your Lovable origins to `.env` and restart the API:

```bash
SYE_CORS_ORIGINS=http://localhost:3000,http://localhost:5173,https://your-project.lovable.app
```

**A hosted Lovable page cannot reach `http://localhost:8000`** — it is served over
https, so the browser blocks the request. Either develop against Lovable's local dev
server, or expose the API over https:

```bash
cloudflared tunnel --url http://localhost:8000     # prints an https URL
# or: ngrok http 8000
```

Then set `VITE_SYE_API_URL` to that https URL and add it to `SYE_CORS_ORIGINS`.

### Live progress (optional)

A run takes ~1s offline and ~60s against the live web. For the live case, start it in
the background and stream the audit events:

```ts
const { run_id } = await fetch(`${api}/api/v1/demo/runs`, {
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify({ users, background: true, offline: false }),
}).then((r) => r.json());

const stop = sye.subscribe(run_id, (event) => setLog((l) => [...l, event.message]));
// ...then sye.getRun(run_id) once the stream ends
```

---

## What to build (paste this into Lovable)

> I have a JSON file (`src/data/run.json`) typed by `src/lib/sye.ts` as `LovablePayload`.
> Build a demand-aggregation storefront with four sections:
>
> 1. **Campaigns** — a card grid from `views.campaign_cards`. Each card shows `title`,
>    `product.name`, `pricing.group_price` next to a struck-through
>    `pricing.normal_market_price`, a `pricing.discount_percent` badge, a progress bar of
>    `demand.committed` toward `demand.min_buyers`, `delivery.estimated_days` and
>    `delivery.warranty_months`, and a "Join this group buy" button. Because
>    `pricing.simulated` is true, show a clear "Simulated pricing — not a supplier
>    commitment" label on every card.
> 2. **How the group formed** — from `views.bucket_summaries`: the label, the member
>    list, and `explanation` verbatim. Under it list `requirements`.
> 3. **Your request** — from `views.user_journeys`: for each person show their original
>    `prompt`, the parsed `intent_summary`, and `bucket_explanation` (which says which
>    requirements came from other members). This is the "why am I in this group" view.
> 4. **How it was decided** — `views.timeline` as a vertical stepper of
>    `sequence`/`node`/`message`, warnings in amber.
>
> Money is already a number and timestamps are ISO strings — no parsing needed. Never
> present simulated prices as real offers.

---

## Contract reference

| What | Where |
| --- | --- |
| Types + client | [`sye.ts`](sye.ts) |
| Live JSON Schema | `GET /api/v1/schema/pipeline-run`, `GET /api/v1/schema/campaign` |
| Interactive API docs | `http://localhost:8000/docs` |
| Full endpoint list | [../README.md](../README.md#api) |

The schema endpoints return Pydantic-generated JSON Schema, so you can regenerate
types instead of hand-maintaining them:

```bash
curl -s localhost:8000/api/v1/schema/pipeline-run > pipeline-run.schema.json
npx json-schema-to-typescript pipeline-run.schema.json -o src/lib/sye-generated.d.ts
```

`sye.ts` stays the more ergonomic hand-written surface (it also types `views`, which the
schema of `PipelineRunExport` does not include).
