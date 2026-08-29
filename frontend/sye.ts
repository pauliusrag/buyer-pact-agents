/**
 * SYE frontend contract.
 *
 * Drop this file into a Lovable project (e.g. `src/lib/sye.ts`). It types the JSON
 * the backend produces and gives you a small client for both ways of consuming it:
 *
 *   1. Static  — import a `lovable_payload.json` exported from a run. No backend.
 *   2. Live    — call the API, optionally starting a fresh run.
 *
 * Everything here is plain JSON: numbers for money, ISO-8601 strings for time,
 * plain strings for enums. No decoding needed.
 */

/* -------------------------------------------------------------------------- */
/* Views — what a storefront actually renders                                  */
/* -------------------------------------------------------------------------- */

/** One group-buy campaign, denormalised and ready to render. */
export interface CampaignCard {
  campaign_id: string;
  title: string;
  short_description: string;
  why_this_product: string;
  status: "draft" | "simulation_ready" | "ready_for_review";
  /** "simulated" in demo mode — always label these prices as simulated in the UI. */
  data_origin: DataOrigin;
  product: {
    product_id: string;
    name: string | null;
    brand: string | null;
    /** Canonical keys, e.g. "display.size_in", "connectivity.usb_c_power_delivery". */
    attributes: Record<string, string | number | boolean | null>;
    listing_url: string | null;
    data_origin: DataOrigin | null;
  };
  supplier: {
    supplier_id: string;
    name: string | null;
    type: SupplierType | null;
    website: string | null;
  };
  pricing: {
    currency: string;
    group_price: number;
    normal_market_price: number | null;
    discount_amount: number | null;
    discount_percent: number | null;
    /** Always true in demo mode. */
    simulated: boolean;
  };
  demand: {
    committed: number;
    min_buyers: number;
    max_buyers: number | null;
    member_user_ids: string[];
  };
  delivery: {
    estimated_days: number | null;
    warranty_months: number | null;
    returns: string | null;
  };
  /** Human-readable requirement lines, e.g. `screen size ≥ 27" (required by 4/5 buyers)`. */
  requirements: string[];
  terms: string[];
  bucket_label: string | null;
  /** Source URLs for anything learned from the web. */
  sources: string[];
  starts_at: string;
  ends_at: string;
}

/** One person's path through the system — the "why am I in this group" view. */
export interface UserJourney {
  user_id: string;
  prompt: string;
  intent_summary: string | null;
  hard_requirements: string[];
  max_budget: number | null;
  bucket_id: string | null;
  bucket_label: string | null;
  bucket_explanation: string | null;
  campaign_id: string | null;
  outcome: "campaign" | "no_campaign";
}

export interface BucketSummary {
  bucket_id: string;
  label: string;
  members: string[];
  demand_quantity: number;
  price_ceiling: number | null;
  status:
    | "open"
    | "no_viable_product"
    | "no_supplier"
    | "no_qualifying_offer"
    | "campaign_created";
  explanation: string;
  requirements: string[];
  intent_ids: string[];
}

export interface TimelineEntry {
  sequence: number;
  node: string;
  status: "started" | "completed" | "warning" | "failed";
  message: string;
  timestamp: string;
  duration_ms: number | null;
}

/* -------------------------------------------------------------------------- */
/* Core objects — everything the views are derived from                        */
/* -------------------------------------------------------------------------- */

export type DataOrigin =
  | "user"
  | "llm_inferred"
  | "web_research"
  | "supplier"
  | "simulated"
  | "system";

export type SupplierType =
  | "manufacturer"
  | "distributor"
  | "retailer"
  | "marketplace_seller"
  | "unknown";

export type MatchClassification = "qualified" | "negotiable_gap" | "rejected";

export type EvaluationResult = "pass" | "fail" | "unknown" | "negotiable";

export interface EvidenceSource {
  title: string | null;
  url: string;
  snippet: string | null;
  retrieved_at: string;
  provider: string;
}

export interface RequirementConstraint {
  key: string;
  operator: "eq" | "gte" | "lte" | "in" | "contains_any" | "contains_all" | "boolean";
  value: unknown;
  unit: string | null;
  importance: "hard" | "soft";
  weight: number;
  source_text: string | null;
  confidence: number;
  /** Whose request produced this. A single member's requirement binds the group. */
  required_by_user_ids: string[];
}

export interface UserRequest {
  user_id: string;
  request_id: string;
  prompt: string;
  market: string;
  currency: string;
  created_at: string;
}

export interface UserIntent {
  intent_id: string;
  user_id: string;
  request_id: string;
  category: string;
  category_confidence: number;
  constraints: RequirementConstraint[];
  max_budget: number | null;
  target_budget: number | null;
  currency: string;
  named_brands: string[];
  excluded_brands: string[];
  clarification_needed: boolean;
  clarification_questions: string[];
  extraction_summary: string;
  extraction_confidence: number;
  /** "llm:anthropic" or "heuristic" — which engine produced this. */
  extracted_by: string;
  data_origin: DataOrigin;
}

export interface DemandBucket {
  bucket_id: string;
  category: string;
  label: string;
  member_user_ids: string[];
  member_intent_ids: string[];
  demand_quantity: number;
  shared_hard_constraints: RequirementConstraint[];
  compatible_soft_constraints: RequirementConstraint[];
  price_ceiling: number | null;
  target_price: number | null;
  currency: string;
  compatibility_score: number;
  compatibility_explanation: string;
  conflicts: string[];
  created_at: string;
}

export interface ProductCandidate {
  product_id: string;
  category: string;
  brand: string;
  model: string;
  canonical_name: string;
  attributes: Record<string, string | number | boolean | null>;
  normal_market_price: number | null;
  currency: string | null;
  merchant_or_listing_name: string | null;
  listing_url: string | null;
  availability: string | null;
  sources: EvidenceSource[];
  data_origin: DataOrigin;
  researched_at: string;
  bucket_id: string | null;
  verified: boolean;
}

export interface ConstraintEvaluation {
  constraint_key: string;
  result: EvaluationResult;
  expected: unknown;
  observed: unknown;
  explanation: string;
  importance: "hard" | "soft";
  required_by_user_ids: string[];
}

export interface ProductMatch {
  match_id: string;
  bucket_id: string;
  product_id: string;
  product_name: string;
  classification: MatchClassification;
  hard_constraint_results: ConstraintEvaluation[];
  soft_constraint_results: ConstraintEvaluation[];
  soft_constraint_score: number;
  overall_score: number;
  negotiable_gaps: string[];
  rejection_reasons: string[];
  unknown_specs: string[];
  explanation: string;
}

export interface SupplierCandidate {
  supplier_id: string;
  name: string;
  supplier_type: SupplierType;
  website: string | null;
  market: string | null;
  evidence: EvidenceSource[];
  data_origin: DataOrigin;
  product_ids: string[];
  bucket_id: string | null;
  authorization_claimed: boolean;
}

export interface SupplierOffer {
  offer_id: string;
  rfq_id: string;
  supplier_id: string;
  product_id: string;
  unit_price: number;
  currency: string;
  max_quantity: number | null;
  shipping_cost_total: number | null;
  estimated_delivery_days: number | null;
  warranty_months: number | null;
  returns_policy_summary: string | null;
  expires_at: string | null;
  conditions: string[];
  negotiation_round: number;
  data_origin: DataOrigin;
  source_reference: string | null;
}

export interface OfferEvaluation {
  offer_id: string;
  bucket_id: string | null;
  landed_unit_cost: number;
  price_score: number;
  fulfillment_score: number;
  warranty_score: number;
  terms_score: number;
  overall_score: number;
  qualifies: boolean;
  disqualification_reasons: string[];
  negotiation_round: number;
}

export interface Campaign {
  campaign_id: string;
  bucket_id: string;
  winning_offer_id: string;
  product_id: string;
  supplier_id: string;
  title: string;
  short_description: string;
  why_this_product: string;
  currency: string;
  normal_market_price: number | null;
  group_price: number;
  discount_amount: number | null;
  discount_percent: number | null;
  committed_demand: number;
  min_buyers: number;
  max_buyers: number | null;
  starts_at: string;
  ends_at: string;
  terms_summary: string[];
  requirement_match_summary: string[];
  member_user_ids: string[];
  sources: EvidenceSource[];
  status: "draft" | "simulation_ready" | "ready_for_review";
  data_origin: DataOrigin;
  run_id: string | null;
  disclaimer: string;
}

export interface AuditEvent {
  event_id: string;
  run_id: string;
  sequence: number;
  timestamp: string;
  node: string;
  event_type: string;
  status: "started" | "completed" | "warning" | "failed";
  input_refs: string[];
  output_refs: string[];
  message: string;
  decision: string | null;
  confidence: number | null;
  sources: EvidenceSource[];
  duration_ms: number | null;
  metadata: Record<string, unknown>;
}

export interface RunMetrics {
  users: number;
  demand_buckets: number;
  products_researched: number;
  products_qualified: number;
  products_rejected: number;
  suppliers_researched: number;
  simulated_offers: number;
  negotiation_rounds: number;
  campaigns_created: number;
  linkup_calls: number;
  llm_calls: number;
  reasoning_engine: string;
  initial_best_offer: number | null;
  final_best_offer: number | null;
  simulated_negotiation_improvement_percent: number;
  total_simulated_group_value: number;
  total_duration_ms: number;
  [key: string]: unknown;
}

/** The whole run. Every view in the UI is derivable from this one object. */
export interface PipelineRunExport {
  schema_version: "1.0";
  run_id: string;
  mode: "demo" | "live";
  status: "running" | "completed" | "partial" | "failed";
  scenario_name: string | null;
  market: string;
  currency: string;
  started_at: string;
  completed_at: string | null;
  user_requests: UserRequest[];
  intents: UserIntent[];
  buckets: DemandBucket[];
  bucket_memberships: BucketMembership[];
  bucket_outcomes: BucketOutcome[];
  products: ProductCandidate[];
  matches: ProductMatch[];
  suppliers: SupplierCandidate[];
  rfqs: unknown[];
  offers: SupplierOffer[];
  offer_evaluations: OfferEvaluation[];
  negotiation_actions: NegotiationAction[];
  campaigns: Campaign[];
  audit_events: AuditEvent[];
  metrics: RunMetrics;
  warnings: string[];
  disclaimer: string;
}

export interface BucketMembership {
  user_id: string;
  bucket_id: string;
  joined: boolean;
  common_requirements: string[];
  individual_requirements_preserved: string[];
  conflicts: string[];
  explanation: string;
}

export interface BucketOutcome {
  bucket_id: string;
  status: BucketSummary["status"];
  reason: string;
  campaign_id: string | null;
}

export interface NegotiationAction {
  offer_id: string;
  supplier_id: string;
  round: number;
  action: "accept" | "counter" | "reject";
  proposed_unit_price: number | null;
  supplier_message: string;
  rationale_summary: string;
  /** Always false in demo mode: nothing is ever sent. */
  delivered: boolean;
  authored_by: string;
}

/** `PipelineRunExport` plus the denormalised views. This is what `format=lovable` returns. */
export interface LovablePayload extends PipelineRunExport {
  views: {
    campaign_cards: CampaignCard[];
    user_journeys: UserJourney[];
    timeline: TimelineEntry[];
    bucket_summaries: BucketSummary[];
  };
}

/* -------------------------------------------------------------------------- */
/* Client                                                                      */
/* -------------------------------------------------------------------------- */

export interface RunOptions {
  /** `{"john doe": "I need a 27 inch monitor..."}` or a list of prompts/objects. */
  users: Record<string, string> | string[] | { user_id?: string; prompt: string }[];
  scenarioName?: string;
  market?: string;
  currency?: string;
  /** false = research the live web with Linkup. Defaults to the server's setting. */
  offline?: boolean;
  seed?: number;
  signal?: AbortSignal;
}

export function createSyeClient(baseUrl: string) {
  const root = baseUrl.replace(/\/$/, "");

  async function json<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(`${root}${path}`, {
      headers: { "content-type": "application/json" },
      ...init,
    });
    if (!response.ok) {
      throw new Error(`SYE ${path} failed: ${response.status} ${await response.text()}`);
    }
    return (await response.json()) as T;
  }

  return {
    health: () =>
      json<{ status: string; linkup_configured: boolean; llm_configured: boolean }>("/health"),

    /** Run the pipeline on your own users and get card-ready JSON back. */
    run: ({ users, scenarioName, market, currency, offline, seed, signal }: RunOptions) =>
      json<LovablePayload>("/api/v1/demo/runs?format=lovable", {
        method: "POST",
        body: JSON.stringify({
          scenario_name: scenarioName ?? "Lovable request",
          market: market ?? "SE",
          currency: currency ?? "EUR",
          users,
          offline,
          seed,
        }),
        signal,
      }),

    /** One-click demo: run a packaged scenario ("easy", "edge-cases", "scale"). */
    runScenario: (key: string, options?: { offline?: boolean; seed?: number }) => {
      const params = new URLSearchParams({ format: "lovable" });
      if (options?.offline !== undefined) params.set("offline", String(options.offline));
      if (options?.seed !== undefined) params.set("seed", String(options.seed));
      return json<LovablePayload>(`/api/v1/demo/scenarios/${key}/run?${params}`, {
        method: "POST",
      });
    },

    scenarios: () =>
      json<{ key: string; scenario_name: string | null; users: number }[]>(
        "/api/v1/demo/scenarios",
      ),

    getRun: (runId: string) => json<LovablePayload>(`/api/v1/demo/runs/${runId}/lovable`),
    getEvents: (runId: string) => json<AuditEvent[]>(`/api/v1/demo/runs/${runId}/events`),
    listRuns: () => json<{ run_id: string; status: string; campaigns: number }[]>("/api/v1/demo/runs"),
    listCampaigns: () => json<Campaign[]>("/api/v1/campaigns"),
    getCampaign: (id: string) => json<Campaign>(`/api/v1/campaigns/${id}`),

    /**
     * Live progress for a background run. Start one with
     * `run({ ..., background: true })` server-side, then subscribe here.
     */
    subscribe(runId: string, onEvent: (event: AuditEvent) => void): () => void {
      const source = new EventSource(`${root}/api/v1/demo/runs/${runId}/stream`);
      source.addEventListener("audit", (e) => onEvent(JSON.parse((e as MessageEvent).data)));
      source.addEventListener("end", () => source.close());
      return () => source.close();
    },
  };
}

export type SyeClient = ReturnType<typeof createSyeClient>;
