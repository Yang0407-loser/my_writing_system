"""WR0-E offline gold fixture for the Saturday bakery world-runtime chain.

The fixture freezes expected artifacts and their traceability.  It is not a
general runtime compiler or transition validator and is not imported by the
production writing pipeline.
"""

from __future__ import annotations

import hashlib
from typing import Any, Literal

from pydantic import Field, model_validator

from .world_runtime_contracts import (
    CanonicalWorldState,
    FrozenRuntimeModel,
    Lifecycle,
    ProjectWorldConstitution,
    ProvenanceRef,
    RuleScope,
    StatePredicate,
    WorldFact,
    WorldRule,
    canonical_hash,
)
from .world_runtime_event_contracts import (
    EventRequirement,
    EventRuntimeBinding,
    SubsectionEventContract,
)
from .world_runtime_pack_modern_urban import (
    MODERN_URBAN_CN_2020S_PACK_REF,
    build_modern_urban_cn_2020s_candidate_pack,
)


WORLD_RUNTIME_BAKERY_GOLD_VERSION = "world-runtime-bakery-gold-wr0e-v1"
BAKERY_PROJECT_ID = "gold-project:saturday-bakery"

EvidenceSourceType = Literal[
    "user_world_setting",
    "pack_candidate",
    "model_inferred",
    "event_contract",
    "final_text",
]
ChangeKind = Literal["fact_update", "lifecycle_transition", "event_completion"]
GoldOutcome = Literal["valid", "invalid", "unresolved"]


class GoldEvidence(FrozenRuntimeModel):
    evidence_id: str = Field(min_length=1)
    source_type: EvidenceSourceType
    source_id: str = Field(min_length=1)
    source_hash: str = Field(min_length=1)
    excerpt: str | None = None
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=1)
    schema_version: str = WORLD_RUNTIME_BAKERY_GOLD_VERSION

    @model_validator(mode="after")
    def validate_span_shape(self):
        span_values = (self.excerpt, self.start, self.end)
        if any(value is not None for value in span_values) and not all(
            value is not None for value in span_values
        ):
            raise ValueError("evidence excerpt and span must be provided together")
        if self.start is not None and self.end is not None and self.start >= self.end:
            raise ValueError("evidence span must be non-empty")
        if self.source_type == "final_text" and self.excerpt is None:
            raise ValueError("final-text evidence requires an exact span")
        return self


class GoldEventChangeExpectation(FrozenRuntimeModel):
    event_id: str = Field(min_length=1)
    expected_change_ids: tuple[str, ...] = Field(min_length=1)
    schema_version: str = WORLD_RUNTIME_BAKERY_GOLD_VERSION


class ProposedFactChange(FrozenRuntimeModel):
    change_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    kind: ChangeKind
    fact_id: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    before_value: Any = None
    before_epistemic_status: str = Field(min_length=1)
    after_value: Any = None
    after_epistemic_status: str = Field(min_length=1)
    actor: str = Field(min_length=1)
    lifecycle_id: str | None = None
    transition_id: str | None = None
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    schema_version: str = WORLD_RUNTIME_BAKERY_GOLD_VERSION

    @model_validator(mode="after")
    def validate_lifecycle_reference(self):
        if self.kind == "lifecycle_transition" and not (
            self.lifecycle_id and self.transition_id
        ):
            raise ValueError("lifecycle changes require lifecycle and transition IDs")
        if self.kind != "lifecycle_transition" and (
            self.lifecycle_id or self.transition_id
        ):
            raise ValueError("non-lifecycle changes cannot name lifecycle transitions")
        return self


class ProposedStateDelta(FrozenRuntimeModel):
    delta_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    base_revision: int = Field(ge=0)
    output_hash: str = Field(min_length=1)
    changes: tuple[ProposedFactChange, ...] = Field(min_length=1)
    schema_version: str = WORLD_RUNTIME_BAKERY_GOLD_VERSION

    @model_validator(mode="after")
    def reject_duplicate_or_unordered_changes(self):
        ids = [change.change_id for change in self.changes]
        sequences = [change.sequence for change in self.changes]
        if len(ids) != len(set(ids)):
            raise ValueError("proposed change IDs must be unique")
        if len(sequences) != len(set(sequences)):
            raise ValueError("proposed change sequence values must be unique")
        if sequences != sorted(sequences):
            raise ValueError("proposed changes must be ordered by sequence")
        return self

    @property
    def artifact_hash(self) -> str:
        return canonical_hash(self)


class GoldChangeValidation(FrozenRuntimeModel):
    change_id: str = Field(min_length=1)
    outcome: GoldOutcome
    rule_ids: tuple[str, ...] = ()
    lifecycle_transition_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    unresolved_fact_ids: tuple[str, ...] = ()
    reasons: tuple[str, ...] = Field(min_length=1)
    schema_version: str = WORLD_RUNTIME_BAKERY_GOLD_VERSION

    @model_validator(mode="after")
    def require_explainable_non_valid_outcome(self):
        if self.outcome == "invalid" and not self.rule_ids:
            raise ValueError("invalid gold changes require rule IDs")
        if self.outcome == "unresolved" and not self.unresolved_fact_ids:
            raise ValueError("unresolved gold changes require unresolved fact IDs")
        return self


class GoldValidationResult(FrozenRuntimeModel):
    validation_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    delta_id: str = Field(min_length=1)
    base_revision: int = Field(ge=0)
    output_hash: str = Field(min_length=1)
    items: tuple[GoldChangeValidation, ...] = Field(min_length=1)
    accepted_change_ids: tuple[str, ...] = ()
    rejected_change_ids: tuple[str, ...] = ()
    unresolved_change_ids: tuple[str, ...] = ()
    schema_version: str = WORLD_RUNTIME_BAKERY_GOLD_VERSION

    @model_validator(mode="after")
    def validate_partitions(self):
        item_ids = [item.change_id for item in self.items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("validation item change IDs must be unique")
        expected = {
            "valid": set(self.accepted_change_ids),
            "invalid": set(self.rejected_change_ids),
            "unresolved": set(self.unresolved_change_ids),
        }
        if any(expected[left] & expected[right] for left, right in (
            ("valid", "invalid"),
            ("valid", "unresolved"),
            ("invalid", "unresolved"),
        )):
            raise ValueError("validation partitions must not overlap")
        for outcome, ids in expected.items():
            actual = {item.change_id for item in self.items if item.outcome == outcome}
            if actual != ids:
                raise ValueError(f"{outcome} validation partition mismatch")
        return self

    @property
    def artifact_hash(self) -> str:
        return canonical_hash(self)


class GoldCommittedStateDelta(FrozenRuntimeModel):
    commit_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    delta_id: str = Field(min_length=1)
    validation_id: str = Field(min_length=1)
    base_revision: int = Field(ge=0)
    after_revision: int = Field(ge=1)
    output_hash: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    changes: tuple[ProposedFactChange, ...] = Field(min_length=1)
    schema_version: str = WORLD_RUNTIME_BAKERY_GOLD_VERSION

    @model_validator(mode="after")
    def enforce_single_revision_commit(self):
        if self.after_revision != self.base_revision + 1:
            raise ValueError("gold commit must advance exactly one revision")
        return self

    @property
    def artifact_hash(self) -> str:
        return canonical_hash(self)


class SaturdayBakeryGoldFixture(FrozenRuntimeModel):
    fixture_id: str = "world-runtime-wr0e:saturday-bakery:v1"
    candidate_pack_hash: str = Field(min_length=1)
    constitution: ProjectWorldConstitution
    state_before: CanonicalWorldState
    model_inferred_candidates: tuple[WorldFact, ...]
    event_contract: SubsectionEventContract
    event_change_expectations: tuple[GoldEventChangeExpectation, ...]
    final_text: str = Field(min_length=1)
    output_hash: str = Field(min_length=1)
    evidence: tuple[GoldEvidence, ...] = Field(min_length=1)
    proposed_delta: ProposedStateDelta
    validation_result: GoldValidationResult
    committed_delta: GoldCommittedStateDelta
    state_after: CanonicalWorldState
    handover_projection: str | None = None
    schema_version: str = WORLD_RUNTIME_BAKERY_GOLD_VERSION

    @model_validator(mode="after")
    def audit_closed_gold_chain(self):
        if (
            self.candidate_pack_hash
            != build_modern_urban_cn_2020s_candidate_pack().artifact_hash
        ):
            raise ValueError("gold fixture candidate pack hash mismatch")
        project_ids = {
            self.constitution.project_id,
            self.state_before.project_id,
            self.event_contract.project_id,
            self.proposed_delta.project_id,
            self.validation_result.project_id,
            self.committed_delta.project_id,
            self.state_after.project_id,
        }
        if project_ids != {BAKERY_PROJECT_ID}:
            raise ValueError("gold artifacts must belong to the bakery project")
        if _sha256(self.final_text) != self.output_hash:
            raise ValueError("final text hash mismatch")
        if not (
            self.proposed_delta.output_hash
            == self.validation_result.output_hash
            == self.committed_delta.output_hash
            == self.output_hash
        ):
            raise ValueError("delta, validation and commit must bind final output hash")
        if not (
            self.proposed_delta.base_revision
            == self.validation_result.base_revision
            == self.committed_delta.base_revision
            == self.state_before.revision
        ):
            raise ValueError("before revision binding mismatch")
        if self.committed_delta.after_revision != self.state_after.revision:
            raise ValueError("after revision binding mismatch")
        if self.proposed_delta.delta_id != self.validation_result.delta_id:
            raise ValueError("validation must bind proposed delta")
        if self.committed_delta.delta_id != self.proposed_delta.delta_id:
            raise ValueError("commit must bind proposed delta")
        if self.committed_delta.validation_id != self.validation_result.validation_id:
            raise ValueError("commit must bind validation")

        evidence_by_id = {item.evidence_id: item for item in self.evidence}
        if len(evidence_by_id) != len(self.evidence):
            raise ValueError("gold evidence IDs must be unique")
        for item in self.evidence:
            if item.source_type == "final_text":
                if item.start is None or item.end is None:
                    raise ValueError("final-text evidence span is incomplete")
                if self.final_text[item.start:item.end] != item.excerpt:
                    raise ValueError("final-text evidence span mismatch")
                if item.source_hash != self.output_hash:
                    raise ValueError("final-text evidence must bind output hash")
        referenced_evidence = {
            evidence_id
            for change in self.proposed_delta.changes
            for evidence_id in change.evidence_ids
        } | {
            evidence_id
            for item in self.validation_result.items
            for evidence_id in item.evidence_ids
        }
        if not referenced_evidence.issubset(evidence_by_id):
            raise ValueError("delta or validation references missing evidence")

        proposed_ids = {item.change_id for item in self.proposed_delta.changes}
        validated_ids = {item.change_id for item in self.validation_result.items}
        if proposed_ids != validated_ids:
            raise ValueError("every proposed change requires one gold validation")
        accepted = set(self.validation_result.accepted_change_ids)
        committed = {item.change_id for item in self.committed_delta.changes}
        if committed != accepted:
            raise ValueError("commit may contain accepted changes only")
        if not set(self.validation_result.rejected_change_ids).isdisjoint(committed):
            raise ValueError("rejected changes cannot enter commit")
        event_ids = {
            requirement.event_id for requirement in self.event_contract.requirements
        }
        expectation_ids = {
            expectation.event_id for expectation in self.event_change_expectations
        }
        if event_ids != expectation_ids:
            raise ValueError("gold event/change expectations must cover event contract")
        for expectation in self.event_change_expectations:
            if not set(expectation.expected_change_ids).issubset(accepted):
                raise ValueError("required event is not satisfied by accepted changes")

        canonical_fact_ids = {fact.fact_id for fact in self.state_before.facts}
        for candidate in self.model_inferred_candidates:
            if (
                candidate.authority != "model_inferred"
                or candidate.epistemic_status != "proposed"
            ):
                raise ValueError("model-inferred fixture facts must remain proposed")
            if candidate.fact_id in canonical_fact_ids:
                raise ValueError("model-inferred candidates cannot enter canonical state")

        active_rule_ids = {rule.rule_id for rule in self.constitution.rules}
        active_transitions = {
            transition.transition_id: (lifecycle.lifecycle_id, transition)
            for lifecycle in self.constitution.lifecycles
            for transition in lifecycle.transitions
        }
        proposed_by_id = {
            change.change_id: change for change in self.proposed_delta.changes
        }
        for item in self.validation_result.items:
            if not set(item.rule_ids).issubset(active_rule_ids):
                raise ValueError("validation references a rule outside constitution")
            if not set(item.lifecycle_transition_ids).issubset(active_transitions):
                raise ValueError("validation references an unknown lifecycle transition")
            change = proposed_by_id[item.change_id]
            if item.outcome == "valid" and change.kind == "lifecycle_transition":
                lifecycle_transition = active_transitions.get(change.transition_id)
                if lifecycle_transition is None:
                    raise ValueError("valid lifecycle change lacks a declared transition")
                lifecycle_id, transition = lifecycle_transition
                if (
                    lifecycle_id != change.lifecycle_id
                    or transition.from_state != change.before_value
                    or transition.to_state != change.after_value
                ):
                    raise ValueError("valid lifecycle change does not match transition")

        expected_after = _apply_changes(
            self.state_before,
            self.committed_delta.changes,
            revision=self.committed_delta.after_revision,
            commit_id=self.committed_delta.commit_id,
        )
        if expected_after != self.state_after:
            raise ValueError("state after is not the accepted-delta projection")
        return self

    @property
    def artifact_hash(self) -> str:
        return canonical_hash(self)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _provenance(source_id: str, source_type: str, payload: Any) -> ProvenanceRef:
    return ProvenanceRef(
        source_id=source_id,
        source_type=source_type,
        source_hash=canonical_hash(payload),
        producer="build_saturday_bakery_gold_fixture",
    )


def _activate_rule(candidate: WorldRule) -> WorldRule:
    payload = candidate.model_dump()
    payload.update(
        rule_id=f"bakery.confirmed.{candidate.rule_id}",
        authority="project_explicit",
        enforcement=candidate.activation_enforcement,
        activation_enforcement=None,
        scope=RuleScope(project_id=BAKERY_PROJECT_ID),
        provenance=_provenance(
            f"confirmation:{candidate.rule_id}",
            "user_confirmation",
            {"candidate_hash": canonical_hash(candidate), "decision": "confirmed"},
        ),
        version="1",
    )
    return WorldRule(**payload)


def _activate_lifecycle(candidate: Lifecycle) -> Lifecycle:
    payload = candidate.model_dump()
    payload.update(
        lifecycle_id=f"bakery.confirmed.{candidate.lifecycle_id}",
        authority="project_explicit",
        enforcement=candidate.activation_enforcement,
        activation_enforcement=None,
        scope=RuleScope(project_id=BAKERY_PROJECT_ID),
        provenance=_provenance(
            f"confirmation:{candidate.lifecycle_id}",
            "user_confirmation",
            {"candidate_hash": canonical_hash(candidate), "decision": "confirmed"},
        ),
        version="1",
    )
    transition_payloads = []
    for transition in candidate.transitions:
        transition_data = transition.model_dump()
        transition_data["transition_id"] = f"bakery.confirmed.{transition.transition_id}"
        transition_payloads.append(transition_data)
    payload["transitions"] = tuple(transition_payloads)
    return Lifecycle(**payload)


def _fact(
    fact_id: str,
    subject: str,
    predicate: str,
    value: Any,
    *,
    status: str = "confirmed_true",
    authority: str = "project_explicit",
    revision: int = 7,
    source_type: str = "user_world_setting",
) -> WorldFact:
    return WorldFact(
        fact_id=fact_id,
        subject=subject,
        predicate=predicate,
        value=value,
        epistemic_status=status,
        authority=authority,
        provenance=_provenance(
            f"{source_type}:{fact_id}",
            source_type,
            {"subject": subject, "predicate": predicate, "value": value},
        ),
        revision=revision,
    )


def _build_constitution() -> ProjectWorldConstitution:
    pack = build_modern_urban_cn_2020s_candidate_pack()
    active_rules = tuple(_activate_rule(rule) for rule in pack.rules)
    schedule_rule = WorldRule(
        rule_id="bakery.explicit.storefront-schedule",
        semantic_key="bakery.storefront.schedule",
        kind="precondition",
        authority="project_explicit",
        enforcement="block",
        scope=RuleScope(
            project_id=BAKERY_PROJECT_ID,
            entity_ids=("bakery:wild-bread",),
            location_ids=("bakery:wild-bread:storefront",),
        ),
        prerequisites=(
            StatePredicate(
                subject="bakery:wild-bread",
                predicate="schedule_allows_public_opening",
                operator="equals",
                expected=True,
            ),
        ),
        provenance=_provenance(
            "user-setting:bakery-hours",
            "user_world_setting",
            {"open_days": ["saturday"], "opens_at": "06:00", "production": "03:30"},
        ),
        version="1",
    )
    return ProjectWorldConstitution(
        project_id=BAKERY_PROJECT_ID,
        version="1",
        rules=tuple(sorted((*active_rules, schedule_rule), key=lambda item: item.rule_id)),
        lifecycles=tuple(
            sorted(
                (_activate_lifecycle(item) for item in pack.lifecycles),
                key=lambda item: item.lifecycle_id,
            )
        ),
        bound_candidate_packs=(MODERN_URBAN_CN_2020S_PACK_REF,),
    )


def _build_state_before() -> CanonicalWorldState:
    facts = (
        _fact("fact:clock:weekday", "world_clock", "weekday", "saturday"),
        _fact("fact:clock:time", "world_clock", "time", "04:20"),
        _fact("fact:bakery:open-days", "bakery:wild-bread", "open_days", ["saturday"]),
        _fact("fact:bakery:opens-at", "bakery:wild-bread", "opens_at", "06:00"),
        _fact("fact:bakery:production-start", "bakery:wild-bread", "production_starts_at", "03:30"),
        _fact("fact:bakery:storefront", "bakery:wild-bread:storefront", "operation_state", "closed"),
        _fact("fact:bakery:workshop-access", "bakery:wild-bread:workshop", "access_state", "closed"),
        _fact("fact:bakery:workshop-light", "bakery:wild-bread:workshop", "light", "on"),
        _fact("fact:zhouye:location", "character:zhou-ye", "location", "bakery:wild-bread:workshop"),
        _fact("fact:article:status", "article:lin-wan", "publication_state", "draft"),
        _fact("fact:article:comment-count", "article:lin-wan", "public_comment_count", 0),
        _fact("fact:jiqing:article-knowledge", "character:ji-qing", "article_knowledge", "unknown"),
        _fact("fact:coworker:article-knowledge", "character:coworker", "article_knowledge", None, status="unknown"),
        _fact("fact:resignation:state", "resignation:lin-wan", "lifecycle_state", "private_draft"),
        _fact("fact:company:acknowledgement", "company:lin-wan", "resignation_acknowledged", None, status="unknown"),
        _fact("fact:employment:state", "employment:lin-wan", "status", "employed"),
    )
    return CanonicalWorldState(
        project_id=BAKERY_PROJECT_ID,
        revision=7,
        facts=tuple(sorted(facts, key=lambda item: item.fact_id)),
    )


def _span_evidence(evidence_id: str, text: str, excerpt: str) -> GoldEvidence:
    start = text.index(excerpt)
    return GoldEvidence(
        evidence_id=evidence_id,
        source_type="final_text",
        source_id="final-output:section-2:subsection-1",
        source_hash=_sha256(text),
        excerpt=excerpt,
        start=start,
        end=start + len(excerpt),
    )


def _apply_changes(
    state: CanonicalWorldState,
    changes: tuple[ProposedFactChange, ...],
    *,
    revision: int,
    commit_id: str,
) -> CanonicalWorldState:
    facts = {fact.fact_id: fact for fact in state.facts}
    for change in sorted(changes, key=lambda item: item.sequence):
        prior = facts[change.fact_id]
        if prior.value != change.before_value:
            raise ValueError(f"change {change.change_id} before value mismatch")
        if prior.epistemic_status != change.before_epistemic_status:
            raise ValueError(f"change {change.change_id} before status mismatch")
        facts[change.fact_id] = prior.model_copy(
            update={
                "value": change.after_value,
                "epistemic_status": change.after_epistemic_status,
                "revision": revision,
                "provenance": _provenance(
                    f"{commit_id}:{change.change_id}",
                    "accepted_state_delta",
                    {"change": change.model_dump(), "revision": revision},
                ),
            }
        )
    return CanonicalWorldState(
        project_id=state.project_id,
        revision=revision,
        facts=tuple(sorted(facts.values(), key=lambda item: item.fact_id)),
    )


def build_saturday_bakery_gold_fixture(
    *, include_handover_projection: bool = True
) -> SaturdayBakeryGoldFixture:
    """Build the deterministic WR0-E fixture without reading production state."""

    pack = build_modern_urban_cn_2020s_candidate_pack()
    constitution = _build_constitution()
    state_before = _build_state_before()
    final_text = (
        "周六凌晨四点二十分，野面包的操作间亮着灯，临街店门仍锁着。"
        "周野从里面打开操作间侧门，让林晚进来，店铺没有对外营业。"
        "林晚在手机上点下提交。后台很快显示审核通过，文章状态变为已发布。"
        "她把文章链接发给季晴，季晴点开链接读完，私信回了“收到”。"
        "随后，林晚把辞职通知发送到公司人事邮箱；页面只显示投递成功，还没有公司确认。"
    )
    output_hash = _sha256(final_text)
    evidence = (
        _span_evidence("ev:text:workshop-door", final_text, "打开操作间侧门"),
        _span_evidence("ev:text:article-submit", final_text, "点下提交"),
        _span_evidence("ev:text:article-publish", final_text, "审核通过，文章状态变为已发布"),
        _span_evidence("ev:text:link-send", final_text, "把文章链接发给季晴"),
        _span_evidence("ev:text:jiqing-read", final_text, "季晴点开链接读完"),
        _span_evidence("ev:text:private-reply", final_text, "私信回了“收到”"),
        _span_evidence("ev:text:resignation-deliver", final_text, "把辞职通知发送到公司人事邮箱"),
        GoldEvidence(
            evidence_id="ev:user:bakery-hours",
            source_type="user_world_setting",
            source_id="user-setting:bakery-hours",
            source_hash=canonical_hash(
                {"open_days": ["saturday"], "opens_at": "06:00", "production": "03:30"}
            ),
        ),
        GoldEvidence(
            evidence_id="ev:pack:modern-urban",
            source_type="pack_candidate",
            source_id=MODERN_URBAN_CN_2020S_PACK_REF,
            source_hash=pack.artifact_hash,
        ),
        GoldEvidence(
            evidence_id="ev:model:workshop-permission",
            source_type="model_inferred",
            source_id="model-candidate:workshop-permission",
            source_hash=canonical_hash("周野可能允许林晚进入操作间"),
        ),
    )

    publication_lifecycle = "bakery.confirmed.modern-urban.lifecycle.publication"
    knowledge_lifecycle = "bakery.confirmed.modern-urban.lifecycle.knowledge-transmission"
    resignation_lifecycle = "bakery.confirmed.modern-urban.lifecycle.resignation"
    changes = (
        ProposedFactChange(
            change_id="change:workshop-access-open",
            sequence=1,
            kind="fact_update",
            fact_id="fact:bakery:workshop-access",
            subject="bakery:wild-bread:workshop",
            predicate="access_state",
            before_value="closed",
            before_epistemic_status="confirmed_true",
            after_value="open",
            after_epistemic_status="confirmed_true",
            actor="character:zhou-ye",
            evidence_ids=("ev:text:workshop-door",),
        ),
        ProposedFactChange(
            change_id="change:article-submit",
            sequence=2,
            kind="lifecycle_transition",
            fact_id="fact:article:status",
            subject="article:lin-wan",
            predicate="publication_state",
            before_value="draft",
            before_epistemic_status="confirmed_true",
            after_value="submitted",
            after_epistemic_status="confirmed_true",
            actor="character:lin-wan",
            lifecycle_id=publication_lifecycle,
            transition_id="bakery.confirmed.modern-urban.lifecycle.publication.submit",
            evidence_ids=("ev:text:article-submit",),
        ),
        ProposedFactChange(
            change_id="change:article-publish",
            sequence=3,
            kind="lifecycle_transition",
            fact_id="fact:article:status",
            subject="article:lin-wan",
            predicate="publication_state",
            before_value="submitted",
            before_epistemic_status="confirmed_true",
            after_value="published",
            after_epistemic_status="confirmed_true",
            actor="publishing-platform",
            lifecycle_id=publication_lifecycle,
            transition_id="bakery.confirmed.modern-urban.lifecycle.publication.publish",
            evidence_ids=("ev:text:article-publish",),
        ),
        ProposedFactChange(
            change_id="change:jiqing-link-available",
            sequence=4,
            kind="lifecycle_transition",
            fact_id="fact:jiqing:article-knowledge",
            subject="character:ji-qing",
            predicate="article_knowledge",
            before_value="unknown",
            before_epistemic_status="confirmed_true",
            after_value="available",
            after_epistemic_status="confirmed_true",
            actor="publishing-platform",
            lifecycle_id=knowledge_lifecycle,
            transition_id="bakery.confirmed.modern-urban.lifecycle.knowledge-transmission.make-available",
            evidence_ids=("ev:text:article-publish",),
        ),
        ProposedFactChange(
            change_id="change:jiqing-link-reached",
            sequence=5,
            kind="lifecycle_transition",
            fact_id="fact:jiqing:article-knowledge",
            subject="character:ji-qing",
            predicate="article_knowledge",
            before_value="available",
            before_epistemic_status="confirmed_true",
            after_value="reached",
            after_epistemic_status="confirmed_true",
            actor="character:lin-wan",
            lifecycle_id=knowledge_lifecycle,
            transition_id="bakery.confirmed.modern-urban.lifecycle.knowledge-transmission.reach",
            evidence_ids=("ev:text:link-send",),
        ),
        ProposedFactChange(
            change_id="change:jiqing-link-perceived",
            sequence=6,
            kind="lifecycle_transition",
            fact_id="fact:jiqing:article-knowledge",
            subject="character:ji-qing",
            predicate="article_knowledge",
            before_value="reached",
            before_epistemic_status="confirmed_true",
            after_value="perceived",
            after_epistemic_status="confirmed_true",
            actor="character:ji-qing",
            lifecycle_id=knowledge_lifecycle,
            transition_id="bakery.confirmed.modern-urban.lifecycle.knowledge-transmission.perceive",
            evidence_ids=("ev:text:jiqing-read",),
        ),
        ProposedFactChange(
            change_id="change:resignation-delivered",
            sequence=7,
            kind="lifecycle_transition",
            fact_id="fact:resignation:state",
            subject="resignation:lin-wan",
            predicate="lifecycle_state",
            before_value="private_draft",
            before_epistemic_status="confirmed_true",
            after_value="delivered",
            after_epistemic_status="confirmed_true",
            actor="character:lin-wan",
            lifecycle_id=resignation_lifecycle,
            transition_id="bakery.confirmed.modern-urban.lifecycle.resignation.deliver",
            evidence_ids=("ev:text:resignation-deliver",),
        ),
        ProposedFactChange(
            change_id="change:storefront-public-open",
            sequence=8,
            kind="fact_update",
            fact_id="fact:bakery:storefront",
            subject="bakery:wild-bread:storefront",
            predicate="operation_state",
            before_value="closed",
            before_epistemic_status="confirmed_true",
            after_value="open_to_public",
            after_epistemic_status="confirmed_true",
            actor="character:zhou-ye",
            evidence_ids=("ev:text:workshop-door", "ev:user:bakery-hours"),
        ),
        ProposedFactChange(
            change_id="change:public-comment-increment",
            sequence=9,
            kind="fact_update",
            fact_id="fact:article:comment-count",
            subject="article:lin-wan",
            predicate="public_comment_count",
            before_value=0,
            before_epistemic_status="confirmed_true",
            after_value=1,
            after_epistemic_status="confirmed_true",
            actor="character:ji-qing",
            evidence_ids=("ev:text:private-reply",),
        ),
        ProposedFactChange(
            change_id="change:employment-terminated",
            sequence=10,
            kind="fact_update",
            fact_id="fact:employment:state",
            subject="employment:lin-wan",
            predicate="status",
            before_value="employed",
            before_epistemic_status="confirmed_true",
            after_value="terminated",
            after_epistemic_status="confirmed_true",
            actor="company:lin-wan",
            evidence_ids=("ev:text:resignation-deliver",),
        ),
        ProposedFactChange(
            change_id="change:coworker-knows-article",
            sequence=11,
            kind="fact_update",
            fact_id="fact:coworker:article-knowledge",
            subject="character:coworker",
            predicate="article_knowledge",
            before_value=None,
            before_epistemic_status="unknown",
            after_value="understood",
            after_epistemic_status="confirmed_true",
            actor="character:coworker",
            evidence_ids=("ev:text:link-send",),
        ),
    )
    proposed = ProposedStateDelta(
        delta_id="delta:saturday-bakery:s2.1",
        project_id=BAKERY_PROJECT_ID,
        base_revision=7,
        output_hash=output_hash,
        changes=changes,
    )

    rule_ids = {rule.semantic_key: rule.rule_id for rule in constitution.rules}
    validations = (
        GoldChangeValidation(change_id="change:workshop-access-open", outcome="valid", evidence_ids=("ev:text:workshop-door",), reasons=("周野明确打开操作间侧门；仅改变内部入口状态",)),
        GoldChangeValidation(change_id="change:article-submit", outcome="valid", lifecycle_transition_ids=("bakery.confirmed.modern-urban.lifecycle.publication.submit",), evidence_ids=("ev:text:article-submit",), reasons=("正文明确发生提交动作",)),
        GoldChangeValidation(change_id="change:article-publish", outcome="valid", rule_ids=(rule_ids["publication.public_visibility.prerequisite"],), lifecycle_transition_ids=("bakery.confirmed.modern-urban.lifecycle.publication.publish",), evidence_ids=("ev:text:article-publish",), reasons=("提交后出现审核通过和已发布状态",)),
        GoldChangeValidation(change_id="change:jiqing-link-available", outcome="valid", lifecycle_transition_ids=("bakery.confirmed.modern-urban.lifecycle.knowledge-transmission.make-available",), evidence_ids=("ev:text:article-publish",), reasons=("文章发布后成为可传播信息",)),
        GoldChangeValidation(change_id="change:jiqing-link-reached", outcome="valid", rule_ids=(rule_ids["publication.public_reaction.reach"],), lifecycle_transition_ids=("bakery.confirmed.modern-urban.lifecycle.knowledge-transmission.reach",), evidence_ids=("ev:text:link-send",), reasons=("林晚向季晴发送已发布文章链接",)),
        GoldChangeValidation(change_id="change:jiqing-link-perceived", outcome="valid", rule_ids=(rule_ids["publication.public_reaction.reach"],), lifecycle_transition_ids=("bakery.confirmed.modern-urban.lifecycle.knowledge-transmission.perceive",), evidence_ids=("ev:text:jiqing-read",), reasons=("季晴明确点开并读完链接",)),
        GoldChangeValidation(change_id="change:resignation-delivered", outcome="valid", rule_ids=(rule_ids["employment.resignation.private_draft_delivery"],), lifecycle_transition_ids=("bakery.confirmed.modern-urban.lifecycle.resignation.deliver",), evidence_ids=("ev:text:resignation-deliver",), reasons=("辞职通知发送至公司人事邮箱，只推进到delivered",)),
        GoldChangeValidation(change_id="change:storefront-public-open", outcome="invalid", rule_ids=("bakery.explicit.storefront-schedule", rule_ids["storefront.internal_activity.public_opening"], rule_ids["storefront.public_opening.schedule"]), evidence_ids=("ev:text:workshop-door", "ev:user:bakery-hours"), reasons=("04:20早于06:00对外营业时间", "打开操作间侧门不等于临街店铺对公众营业")),
        GoldChangeValidation(change_id="change:public-comment-increment", outcome="invalid", rule_ids=(rule_ids["publication.public_reaction.reach"],), evidence_ids=("ev:text:private-reply",), reasons=("私信回复不是文章公共评论",)),
        GoldChangeValidation(change_id="change:employment-terminated", outcome="invalid", rule_ids=(rule_ids["employment.termination.prerequisite"], rule_ids["employment.resignation.private_draft_delivery"]), evidence_ids=("ev:text:resignation-deliver",), reasons=("辞职仅到delivered且公司尚未确认，不能跳到terminated",)),
        GoldChangeValidation(change_id="change:coworker-knows-article", outcome="invalid", rule_ids=(rule_ids["publication.public_reaction.reach"],), evidence_ids=("ev:text:link-send",), reasons=("发送对象是季晴，没有同事的传播或感知路径",)),
    )
    accepted_ids = tuple(item.change_id for item in validations if item.outcome == "valid")
    rejected_ids = tuple(item.change_id for item in validations if item.outcome == "invalid")
    validation = GoldValidationResult(
        validation_id="validation:saturday-bakery:s2.1",
        project_id=BAKERY_PROJECT_ID,
        delta_id=proposed.delta_id,
        base_revision=7,
        output_hash=output_hash,
        items=validations,
        accepted_change_ids=accepted_ids,
        rejected_change_ids=rejected_ids,
    )
    committed_changes = tuple(
        change for change in changes if change.change_id in set(accepted_ids)
    )
    committed = GoldCommittedStateDelta(
        commit_id="commit:saturday-bakery:s2.1:r8",
        project_id=BAKERY_PROJECT_ID,
        delta_id=proposed.delta_id,
        validation_id=validation.validation_id,
        base_revision=7,
        after_revision=8,
        output_hash=output_hash,
        idempotency_key=f"{BAKERY_PROJECT_ID}:7:{output_hash}",
        changes=committed_changes,
    )
    state_after = _apply_changes(
        state_before,
        committed_changes,
        revision=8,
        commit_id=committed.commit_id,
    )
    inferred = (
        _fact(
            "candidate:workshop-permission",
            "character:lin-wan",
            "has_workshop_access_permission",
            "possible",
            status="proposed",
            authority="model_inferred",
            source_type="model_inferred",
        ),
    )
    event_contract = SubsectionEventContract(
        contract_id="event-contract:saturday-bakery:s2.1",
        project_id=BAKERY_PROJECT_ID,
        section=2,
        subsection=1,
        requirements=(
            EventRequirement(
                event_id="event:enter-workshop",
                description="周野打开操作间侧门让林晚进入",
                runtime_binding=EventRuntimeBinding(
                    fact_ids=(
                        "fact:clock:weekday",
                        "fact:clock:time",
                        "fact:bakery:open-days",
                        "fact:bakery:opens-at",
                        "fact:bakery:production-start",
                        "fact:bakery:storefront",
                        "fact:bakery:workshop-access",
                        "fact:bakery:workshop-light",
                        "fact:zhouye:location",
                    ),
                    semantic_domains=("bakery", "calendar", "storefront"),
                ),
            ),
            EventRequirement(
                event_id="event:publish-article",
                description="林晚提交并使文章发布",
                runtime_binding=EventRuntimeBinding(
                    fact_ids=(
                        "fact:article:status",
                        "fact:article:comment-count",
                    ),
                    semantic_domains=("publication",),
                    lifecycle_id=publication_lifecycle,
                    lifecycle_state_fact_id="fact:article:status",
                    required_transition_ids=(
                        "bakery.confirmed.modern-urban.lifecycle.publication.submit",
                        "bakery.confirmed.modern-urban.lifecycle.publication.publish",
                    ),
                ),
            ),
            EventRequirement(
                event_id="event:share-with-jiqing",
                description="林晚把已发布文章发给季晴并被她阅读",
                runtime_binding=EventRuntimeBinding(
                    fact_ids=(
                        "fact:article:status",
                        "fact:jiqing:article-knowledge",
                    ),
                    semantic_domains=("communication", "publication"),
                    lifecycle_id=knowledge_lifecycle,
                    lifecycle_state_fact_id="fact:jiqing:article-knowledge",
                    required_transition_ids=(
                        "bakery.confirmed.modern-urban.lifecycle.knowledge-transmission.make-available",
                        "bakery.confirmed.modern-urban.lifecycle.knowledge-transmission.reach",
                        "bakery.confirmed.modern-urban.lifecycle.knowledge-transmission.perceive",
                    ),
                ),
            ),
            EventRequirement(
                event_id="event:deliver-resignation",
                description="林晚把辞职通知送达公司渠道",
                runtime_binding=EventRuntimeBinding(
                    fact_ids=(
                        "fact:resignation:state",
                        "fact:company:acknowledgement",
                        "fact:employment:state",
                    ),
                    semantic_domains=("employment",),
                    lifecycle_id=resignation_lifecycle,
                    lifecycle_state_fact_id="fact:resignation:state",
                    required_transition_ids=(
                        "bakery.confirmed.modern-urban.lifecycle.resignation.deliver",
                    ),
                ),
            ),
        ),
        provenance=_provenance(
            "outline:event-contract:s2.1",
            "event_contract",
            {"section": 2, "subsection": 1, "must_events": 4},
        ),
    )
    return SaturdayBakeryGoldFixture(
        candidate_pack_hash=pack.artifact_hash,
        constitution=constitution,
        state_before=state_before,
        model_inferred_candidates=inferred,
        event_contract=event_contract,
        event_change_expectations=(
            GoldEventChangeExpectation(
                event_id="event:enter-workshop",
                expected_change_ids=("change:workshop-access-open",),
            ),
            GoldEventChangeExpectation(
                event_id="event:publish-article",
                expected_change_ids=(
                    "change:article-submit",
                    "change:article-publish",
                ),
            ),
            GoldEventChangeExpectation(
                event_id="event:share-with-jiqing",
                expected_change_ids=(
                    "change:jiqing-link-available",
                    "change:jiqing-link-reached",
                    "change:jiqing-link-perceived",
                ),
            ),
            GoldEventChangeExpectation(
                event_id="event:deliver-resignation",
                expected_change_ids=("change:resignation-delivered",),
            ),
        ),
        final_text=final_text,
        output_hash=output_hash,
        evidence=evidence,
        proposed_delta=proposed,
        validation_result=validation,
        committed_delta=committed,
        state_after=state_after,
        handover_projection=(
            "周六凌晨，林晚进入野面包操作间；文章已发布并发给季晴；辞职通知已投递，尚未获公司确认。"
            if include_handover_projection
            else None
        ),
    )
