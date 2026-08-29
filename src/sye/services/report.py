"""Operator-friendly run report.

Generated entirely from the structured export — it contains no hidden reasoning,
only what the pipeline actually recorded.
"""

from __future__ import annotations

from sye.domain.enums import MatchClassification
from sye.domain.models import PipelineRunExport


def render_report(export: PipelineRunExport) -> str:
    products = {p.product_id: p for p in export.products}
    suppliers = {s.supplier_id: s for s in export.suppliers}
    offers = {o.offer_id: o for o in export.offers}
    evaluations = {e.offer_id: e for e in export.offer_evaluations}
    buckets = {b.bucket_id: b for b in export.buckets}

    lines: list[str] = []
    add = lines.append

    add(f"# SYE demo run — {export.run_id}")
    add("")
    add(
        f"*{export.scenario_name or 'unnamed scenario'}* · mode **{export.mode.value}** · "
        f"status **{export.status.value}** · market {export.market} · {export.currency}"
    )
    add("")

    # 1. Scenario ----------------------------------------------------------- #
    add("## 1. Scenario")
    add("")
    add(f"- Started: {export.started_at.isoformat()}")
    add(f"- Completed: {export.completed_at.isoformat() if export.completed_at else '—'}")
    add(
        f"- Users: {len(export.user_requests)} · Buckets: {len(export.buckets)} · "
        f"Campaigns: {len(export.campaigns)}"
    )
    add(
        f"- Research provider: {export.metrics.get('linkup_calls', 0)} research call(s); "
        f"reasoning engine: {export.metrics.get('reasoning_engine', 'unknown')}"
    )
    add("")

    # 2. Users -------------------------------------------------------------- #
    add("## 2. Users")
    add("")
    add("| User | Request |")
    add("| --- | --- |")
    for request in export.user_requests:
        add(f"| `{request.user_id}` | {request.prompt} |")
    add("")

    # 3. Parsed intents ------------------------------------------------------ #
    add("## 3. Parsed intents")
    add("")
    for intent in export.intents:
        add(
            f"### `{intent.user_id}` → {intent.category} "
            f"(confidence {intent.extraction_confidence:.2f}, via {intent.extracted_by})"
        )
        add("")
        add(f"{intent.extraction_summary}")
        add("")
        add("| Constraint | Operator | Value | Importance | Confidence | Source text |")
        add("| --- | --- | --- | --- | --- | --- |")
        for constraint in intent.constraints:
            add(
                f"| `{constraint.key}` | {constraint.operator.value} | {constraint.value} | "
                f"{constraint.importance.value} | {constraint.confidence:.2f} | "
                f"{(constraint.source_text or '').strip()[:60]} |"
            )
        if intent.clarification_questions:
            add("")
            add("Open questions: " + "; ".join(intent.clarification_questions))
        add("")

    # 4. Demand buckets ------------------------------------------------------ #
    add("## 4. Demand buckets")
    add("")
    for bucket in export.buckets:
        add(f"### {bucket.label} (`{bucket.bucket_id}`)")
        add("")
        add(f"- Members: {', '.join(bucket.member_user_ids)}")
        add(f"- Demand quantity: {bucket.demand_quantity}")
        add(
            f"- Price ceiling: {bucket.price_ceiling} {bucket.currency} · "
            f"target {bucket.target_price} {bucket.currency}"
        )
        add(f"- Compatibility score: {bucket.compatibility_score:.2f}")
        add(f"- {bucket.compatibility_explanation}")
        add("")
        for membership in export.bucket_memberships:
            if membership.bucket_id != bucket.bucket_id:
                continue
            marker = "joined" if membership.joined else "not joined"
            add(f"  - **{membership.user_id}** ({marker}): {membership.explanation}")
        add("")

    # 5 & 6. Products and verdicts ------------------------------------------- #
    add("## 5. Product candidates")
    add("")
    add("| Product | Brand | Price | Origin | Bucket | Listing |")
    add("| --- | --- | --- | --- | --- | --- |")
    for product in export.products:
        add(
            f"| {product.canonical_name} | {product.brand} | "
            f"{product.normal_market_price} {product.currency or ''} | "
            f"{product.data_origin.value} | `{product.bucket_id}` | "
            f"{product.listing_url or '—'} |"
        )
    add("")

    add("## 6. Why candidates passed or failed")
    add("")
    for match in export.matches:
        product = products.get(match.product_id)
        name = product.canonical_name if product else match.product_id
        add(f"### {name} → **{match.classification.value}** (score {match.overall_score:.2f})")
        add("")
        add(f"{match.explanation}")
        add("")
        add("| Requirement | Result | Expected | Observed |")
        add("| --- | --- | --- | --- |")
        for evaluation in match.hard_constraint_results:
            add(
                f"| `{evaluation.constraint_key}` | {evaluation.result.value} | "
                f"{evaluation.expected} | {evaluation.observed} |"
            )
        add("")

    # 7. Suppliers ------------------------------------------------------------ #
    add("## 7. Suppliers")
    add("")
    add("| Supplier | Type | Market | Origin | Website |")
    add("| --- | --- | --- | --- | --- |")
    for supplier in export.suppliers:
        add(
            f"| {supplier.name} | {supplier.supplier_type} | {supplier.market or '—'} | "
            f"{supplier.data_origin.value} | {supplier.website or '—'} |"
        )
    add("")

    # 8. Offer rounds --------------------------------------------------------- #
    add("## 8. Offer rounds (simulated)")
    add("")
    add(
        "| Round | Supplier | Unit price | Shipping | Landed unit cost | Delivery | Warranty | Qualifies |"
    )
    add("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for offer in sorted(export.offers, key=lambda o: (o.negotiation_round, o.offer_id)):
        supplier = suppliers.get(offer.supplier_id)
        evaluation = evaluations.get(offer.offer_id)
        add(
            f"| {offer.negotiation_round} | {supplier.name if supplier else offer.supplier_id} | "
            f"{offer.unit_price} {offer.currency} | {offer.shipping_cost_total} | "
            f"{evaluation.landed_unit_cost if evaluation else '—'} | "
            f"{offer.estimated_delivery_days} d | {offer.warranty_months} mo | "
            f"{'yes' if evaluation and evaluation.qualifies else 'no'} |"
        )
    add("")
    if export.negotiation_actions:
        add("Negotiation actions (drafted, never sent):")
        add("")
        for action in export.negotiation_actions:
            supplier = suppliers.get(action.supplier_id)
            add(
                f"- Round {action.round} · {supplier.name if supplier else action.supplier_id} · "
                f"**{action.action}**"
                + (f" at {action.proposed_unit_price}" if action.proposed_unit_price else "")
                + f" — {action.rationale_summary}"
            )
        add("")

    # 9 & 10. Winner and campaign --------------------------------------------- #
    add("## 9. Winning offers")
    add("")
    for campaign in export.campaigns:
        offer = offers.get(campaign.winning_offer_id)
        evaluation = evaluations.get(campaign.winning_offer_id)
        supplier = suppliers.get(campaign.supplier_id)
        add(
            f"- `{campaign.bucket_id}`: {supplier.name if supplier else campaign.supplier_id} at "
            f"{offer.unit_price if offer else '—'} {campaign.currency} "
            f"(landed {evaluation.landed_unit_cost if evaluation else '—'}, "
            f"score {evaluation.overall_score if evaluation else '—'})"
        )
    if not export.campaigns:
        add("- No qualifying offer produced a campaign in this run.")
    add("")

    add("## 10. Campaigns")
    add("")
    for campaign in export.campaigns:
        bucket = buckets.get(campaign.bucket_id)
        add(f"### {campaign.title}")
        add("")
        add(f"{campaign.short_description}")
        add("")
        add(f"- Why this product: {campaign.why_this_product}")
        add(
            f"- Group price: **{campaign.group_price} {campaign.currency}** "
            f"(reference {campaign.normal_market_price} {campaign.currency}, "
            f"discount {campaign.discount_percent}%) — simulated"
        )
        add(f"- Buyers: {campaign.committed_demand} committed · minimum {campaign.min_buyers}")
        add(f"- Window: {campaign.starts_at.date()} → {campaign.ends_at.date()}")
        add(f"- Bucket: {bucket.label if bucket else campaign.bucket_id}")
        add("- Requirements satisfied:")
        for line in campaign.requirement_match_summary:
            add(f"  - {line}")
        add("- Terms:")
        for line in campaign.terms_summary:
            add(f"  - {line}")
        add("")

    # 11. Sources -------------------------------------------------------------- #
    add("## 11. Sources")
    add("")
    seen: set[str] = set()
    for product in export.products:
        for source in product.sources:
            if source.url in seen:
                continue
            seen.add(source.url)
            add(
                f"- [{source.title or source.url}]({source.url}) — {product.canonical_name} "
                f"({source.provider})"
            )
    for supplier in export.suppliers:
        for source in supplier.evidence:
            if source.url in seen:
                continue
            seen.add(source.url)
            add(
                f"- [{source.title or source.url}]({source.url}) — {supplier.name} "
                f"({source.provider})"
            )
    if not seen:
        add("- No external sources were used in this run.")
    add("")

    # 12-14. Metrics, warnings, disclaimer ------------------------------------- #
    add("## 12. Metrics")
    add("")
    add("| Metric | Value |")
    add("| --- | --- |")
    for key, value in export.metrics.items():
        if key == "node_durations_ms":
            continue
        add(f"| {key} | {value} |")
    add("")
    durations = export.metrics.get("node_durations_ms") or {}
    if durations:
        add("Node durations (ms): " + ", ".join(f"{k}={v}" for k, v in durations.items()))
        add("")

    add("## 13. Warnings")
    add("")
    if export.warnings:
        for warning in export.warnings:
            add(f"- {warning}")
    else:
        add("- None.")
    add("")

    add("## 14. Simulation disclaimer")
    add("")
    add(export.disclaimer)
    add("")
    add(
        "No supplier was contacted, no message was sent, no order was placed and no payment "
        "was processed during this run."
    )
    add("")
    qualified = sum(1 for m in export.matches if m.classification == MatchClassification.QUALIFIED)
    add(
        f"_Generated from {len(export.audit_events)} audit events; {qualified} qualified "
        f"product match(es)._"
    )
    return "\n".join(lines)
