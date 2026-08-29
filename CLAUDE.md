# CLAUDE.md — working in this repository

Context for future sessions. Read this before changing anything.

## What this is

An MVP of **demand-first group buying**: people describe what they need in free text,
the system groups compatible demand, researches products and suppliers, negotiates in
simulation, and produces campaigns plus a complete audit trail. Demo category: monitors.

The most important deliverable is **demo mode**: one command must produce a complete,
inspectable, explainable end-to-end run.

## Non-negotiable principles

1. **Typed data between every stage.** Node boundaries carry Pydantic models from
   `src/sye/domain/models.py`, never prose. There is exactly one definition of product,
   intent, offer and campaign.
2. **Deterministic where determinism is possible.** Prices, ranges, feasibility, scores,
   thresholds, IDs and winner selection are plain Python (`src/sye/services/`). LLMs are
   for language: interpretation, semantic tie-breaks, explanations, negotiation copy.
   *No LLM call lives in `src/sye/services/`.*
3. **Evidence and traceability.** Any fact learned from the web keeps its source URLs.
   Any AI decision records node, input/output IDs, timestamp, a concise reason and a
   confidence. Never store or stream chain-of-thought.
4. **Demo safety.** Demo mode never sends an email, submits a form, places an order or
   charges anything, and never presents a simulated value as real. Simulated objects are
   `data_origin="simulated"`; web-derived objects are `"web_research"`; offline fixtures
   are `"system"` with a `fixture://` source URL.
5. **Fixtures are never a silent fallback for failed live research.** A failing live
   branch produces a warning and a `partial` run.

## Layout

| Path | Role |
| --- | --- |
| `src/sye/domain/` | canonical models, enums, audit events, graph state, monitor vocabulary |
| `src/sye/services/` | deterministic logic: constraints, bucketing, matching, scoring, offers, simulation, exports, report |
| `src/sye/agents/` | one agent per phase of autonomous work (see below) |
| `src/sye/agents/tools/` | single-purpose helpers the agents use |
| `src/sye/integrations/` | LLM provider, Linkup/fixture research, supplier gateways |
| `src/sye/graph/` | LangGraph nodes, routing and assembly |
| `src/sye/api/` | FastAPI routes and the in-process run manager |
| `src/sye/persistence/` | SQLite tables, repositories, LangGraph checkpointer |
| `data/fixtures/` | the offline catalogue (a project asset, not run output) |
| `examples/` | scenario files |

## The agents

| Agent | Owns |
| --- | --- |
| `IntentAgent` | ingestion: free text → validated `UserIntent` |
| `MarketResearchAgent` | group bucketing + web research + match evaluation |
| `SourcingAgent` | supplier discovery + RFQ drafting |
| `NegotiationAgent` | offers, normalisation, counter rounds, stop policy |
| `CampaignAgent` | winner selection + campaign publication |

Rules for agents:

- An agent receives typed input and returns a typed `AgentResult`; never prose across
  a boundary.
- An agent owns its decisions, including how often to use a tool and when to stop.
  `MarketResearchAgent` decides to re-search with a broadened query; `NegotiationAgent`
  decides when to stop countering (`should_continue`, which the graph edge consults).
- An agent knows nothing about LangGraph, FastAPI or the database. Everything it needs
  arrives in `AgentContext`, which is what makes it runnable standalone.
- Graph nodes are thin adapters: call the agent, merge the typed result into state,
  write the snapshot. Put no reasoning in a node.
- Each agent method opens its own `audit.step`, so a standalone run produces the same
  audit trail as a pipeline run.
- Add a new phase by adding an agent, not by growing an existing one.

## Conventions

- Every agent must work **without** an API key: LLM path first, deterministic fallback
  second, and the audit event records which engine produced the object.
- Constraints are generic `key / operator / value` triples. Monitor-specific knowledge
  lives only in `domain/vocabulary.py`; adding a category means adding a vocabulary, not
  new agents.
- Bucket constraints are *binding for the whole group*: a requirement held by one member
  still constrains every candidate product (`shared_hard_constraints`).
- Unknown specs never pass. `unknown` is a distinct evaluation result and blocks
  qualification. This includes a **missing price** and a price quoted in **another
  currency** — 218.99 GBP is not comparable with a €440 ceiling, so it is `unknown`,
  not a pass. Both conditions also mark a candidate as needing verification.
- Live web results are messier than fixtures: expect missing prices, foreign
  currencies and near-duplicate listings. The research agent spends its verification
  budget (`max_verifications_per_bucket`) on the candidates that are actually thin,
  and asks for prices in the group's own currency in the query itself.
- Simulation is seeded from **stable natural keys** (supplier and product names), never
  from run-scoped IDs, so `--seed 42` reproduces the same economics across runs. Never
  use `hash()` for seeding — it is salted per process; use `hashlib`.
- Graph nodes return only the objects they created; state lists use additive reducers.
- Money is `Decimal` internally and serialises as a JSON number (`Money` in
  `domain/primitives.py`).

## Commands

```bash
uv sync
uv run python scripts/run_demo.py examples/demo_easy.json --verbose
uv run pytest
uv run ruff check src tests scripts && uv run ruff format --check src tests scripts
uv run uvicorn sye.main:app --reload
```

## Non-goals

No authentication, payments, real supplier outreach, CRM, inventory reservation,
shipping, returns, legal engine, vector database, arbitrary-category ontology,
multi-region tax, or production frontend. Create seams, not implementations.
