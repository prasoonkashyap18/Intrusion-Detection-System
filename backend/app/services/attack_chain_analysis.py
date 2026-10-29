"""Attack-chain analysis: finds connected groups of `Alert` rows joined
by one or more `EventCorrelation` edges (Step 38) and persists each
distinct group as an `AttackChain`.

    Alert + EventCorrelation rows
        |
    find_connected_components()  <- pure, deterministic
        |
    classify_chain()             <- CORROBORATED vs HEURISTIC
        |
    persist_chains()
        |
    AttackChain + AttackChainEvent rows

This module performs NO new correlation of its own: its only input is
the `EventCorrelation` graph `app.services.event_correlation` already
persisted. A "chain" here is nothing more than a connected component of
that graph with at least `config.min_chain_size` members — an
**observed grouping**, not a confirmed attack campaign. No MITRE
ATT&CK technique or attack-stage label is ever assigned: nothing in
this codebase implements a mapping from evidence to a technique, and
inventing one would be exactly the kind of unjustified claim Step 39
rules out. `classification` (see `app.models.enums.ChainClassification`)
only ever reports whether at least one STRONG correlation edge connects
the chain's members, or whether every edge is WEAK — the strength
distinction `app.services.event_correlation` already computed, reused
here rather than re-derived.

This module never writes to `Alert.status` or any other alert-lifecycle
field — chain analysis is strictly read-only with respect to Step 37;
an `AttackChain` existing has no automatic effect on any alert.

Idempotency: `member_signature` (a SHA-256 hex digest of the sorted
member alert ids, joined by `,`) is the entire chain-level deduplication
strategy — re-analyzing the same underlying evidence always recomputes
the same signature for the same component, and `AttackChain`'s
`UniqueConstraint("member_signature")` rejects a second insert for it.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.alert import Alert
from app.models.attack_chain import AttackChain, AttackChainEvent
from app.models.enums import ChainClassification, CorrelationStrength
from app.models.event_correlation import EventCorrelation

logger = logging.getLogger("ai_ids")


class AttackChainPersistenceError(Exception):
    """Raised when this module cannot safely persist a set of chains —
    never partially, and never silently."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class AttackChainConfig:
    min_chain_size: int = 2
    """A component with fewer members than this is not a "chain" — a
    single, uncorrelated alert is left as-is, not wrapped in a
    one-member chain. Must be >= 2."""


DEFAULT_ATTACK_CHAIN_CONFIG = AttackChainConfig()


@dataclass(frozen=True)
class ChainComponent:
    """One connected component — the pure-data result of graph
    traversal, before any database write."""

    member_alert_ids: tuple[uuid.UUID, ...]
    """Sorted (by string form) for a deterministic, order-independent
    signature — see `compute_member_signature`."""

    correlation_types: tuple[str, ...]
    """The distinct `EventCorrelation.correlation_type` values found on
    edges *within* this component, sorted."""

    has_strong_edge: bool


def compute_member_signature(member_alert_ids: Sequence[uuid.UUID]) -> str:
    """Deterministic SHA-256 hex digest of the sorted member ids — the
    same component, found again later, always yields the same
    signature regardless of traversal order."""
    sorted_ids = sorted(str(alert_id) for alert_id in member_alert_ids)
    return hashlib.sha256(",".join(sorted_ids).encode("utf-8")).hexdigest()


def find_connected_components(
    alert_ids: Sequence[uuid.UUID],
    edges: Sequence[EventCorrelation],
    config: AttackChainConfig = DEFAULT_ATTACK_CHAIN_CONFIG,
) -> list[ChainComponent]:
    """Pure function: standard union-find connected-components over
    `alert_ids` joined by `edges`, keeping only components with at
    least `config.min_chain_size` members. Deterministic: components
    and their `member_alert_ids` are always returned in the same sorted
    order for the same input.
    """
    parent: dict[uuid.UUID, uuid.UUID] = {alert_id: alert_id for alert_id in alert_ids}

    def find(x: uuid.UUID) -> uuid.UUID:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: uuid.UUID, b: uuid.UUID) -> None:
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            # Deterministic merge direction so the resulting root (and
            # therefore grouping) never depends on edge-processing order.
            if str(root_a) < str(root_b):
                parent[root_b] = root_a
            else:
                parent[root_a] = root_b

    relevant_edges = [e for e in edges if e.first_alert_id in parent and e.second_alert_id in parent]
    for edge in relevant_edges:
        union(edge.first_alert_id, edge.second_alert_id)

    groups: dict[uuid.UUID, list[uuid.UUID]] = {}
    for alert_id in alert_ids:
        groups.setdefault(find(alert_id), []).append(alert_id)

    components = []
    for members in groups.values():
        if len(members) < config.min_chain_size:
            continue
        member_set = set(members)
        component_edges = [
            e for e in relevant_edges if e.first_alert_id in member_set and e.second_alert_id in member_set
        ]
        correlation_types = sorted({e.correlation_type for e in component_edges})
        has_strong_edge = any(e.strength == CorrelationStrength.STRONG for e in component_edges)
        components.append(
            ChainComponent(
                member_alert_ids=tuple(sorted(members, key=str)),
                correlation_types=tuple(correlation_types),
                has_strong_edge=has_strong_edge,
            )
        )

    components.sort(key=lambda c: c.member_alert_ids)
    return components


def classify_chain(component: ChainComponent) -> ChainClassification:
    """Pure function: `CORROBORATED` iff at least one STRONG edge
    connects the component's members, `HEURISTIC` otherwise."""
    return ChainClassification.CORROBORATED if component.has_strong_edge else ChainClassification.HEURISTIC


def persist_chains(
    db: Session,
    components: Sequence[ChainComponent],
    alerts_by_id: dict[uuid.UUID, Alert],
) -> list[AttackChain]:
    """Persists one `AttackChain` + its `AttackChainEvent` rows per
    component not already represented by an existing chain (checked by
    `member_signature` before insert). Returns `[]` without writing
    anything for an empty or fully-duplicate sequence. Member ordering
    within a chain is deterministic: ascending `(created_at, id)` over
    the component's own alerts — a real chronological order, never an
    assumed or inferred one.
    """
    if not components:
        return []

    signatures = [compute_member_signature(c.member_alert_ids) for c in components]
    existing_signatures = set(
        db.scalars(select(AttackChain.member_signature).where(AttackChain.member_signature.in_(signatures))).all()
    )

    new_chains: list[AttackChain] = []
    for component, signature in zip(components, signatures):
        if signature in existing_signatures:
            continue

        ordered_alerts = sorted(
            (alerts_by_id[alert_id] for alert_id in component.member_alert_ids),
            key=lambda alert: (alert.created_at, str(alert.id)),
        )

        chain = AttackChain(
            member_signature=signature,
            classification=classify_chain(component),
            evidence_summary={
                "correlation_types": list(component.correlation_types),
                "member_count": len(component.member_alert_ids),
            },
        )
        chain.events = [
            AttackChainEvent(alert_id=alert.id, sequence_index=index)
            for index, alert in enumerate(ordered_alerts)
        ]
        new_chains.append(chain)

    if not new_chains:
        return []

    try:
        db.add_all(new_chains)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Failed to persist attack chains")
        raise AttackChainPersistenceError("Unable to persist attack chains.") from None

    return new_chains


def build_and_persist_chains_for_batch(
    db: Session,
    batch_id: uuid.UUID,
    config: AttackChainConfig = DEFAULT_ATTACK_CHAIN_CONFIG,
) -> list[AttackChain]:
    """Orchestration: starting from this batch's own `Alert` rows,
    expands to every alert reachable through at least one
    `EventCorrelation` edge (so a chain spanning multiple batches is
    still found and persisted as one chain, not split), finds connected
    components, and persists whatever new chains result. Returns `[]`
    when this batch produced no alerts.
    """
    seed_alerts = list(db.scalars(select(Alert).where(Alert.batch_id == batch_id)).all())
    if not seed_alerts:
        return []

    seed_ids = {alert.id for alert in seed_alerts}
    frontier = set(seed_ids)
    all_alert_ids: set[uuid.UUID] = set(seed_ids)
    all_edges: list[EventCorrelation] = []
    seen_edge_ids: set[uuid.UUID] = set()

    # Breadth-first expansion over the correlation graph so a chain is
    # never cut off at this batch's own alerts.
    while frontier:
        edges = list(
            db.scalars(
                select(EventCorrelation).where(
                    or_(
                        EventCorrelation.first_alert_id.in_(frontier),
                        EventCorrelation.second_alert_id.in_(frontier),
                    )
                )
            ).all()
        )
        new_ids: set[uuid.UUID] = set()
        for edge in edges:
            if edge.id not in seen_edge_ids:
                seen_edge_ids.add(edge.id)
                all_edges.append(edge)
            for alert_id in (edge.first_alert_id, edge.second_alert_id):
                if alert_id not in all_alert_ids:
                    all_alert_ids.add(alert_id)
                    new_ids.add(alert_id)
        frontier = new_ids

    alerts = list(db.scalars(select(Alert).where(Alert.id.in_(all_alert_ids))).all())
    alerts_by_id = {alert.id: alert for alert in alerts}

    components = find_connected_components(list(all_alert_ids), all_edges, config)
    return persist_chains(db, components, alerts_by_id)


__all__ = [
    "AttackChainPersistenceError",
    "AttackChainConfig",
    "DEFAULT_ATTACK_CHAIN_CONFIG",
    "ChainComponent",
    "compute_member_signature",
    "find_connected_components",
    "classify_chain",
    "persist_chains",
    "build_and_persist_chains_for_batch",
]
