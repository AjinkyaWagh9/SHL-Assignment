"""Hybrid retrieval: metadata pre-filter -> BM25 + dense -> RRF fusion -> type boost.

Public API: `Retriever.search(state, query, k=40) -> list[dict]`.

Default-candidate injection: SHL's flagship defaults (OPQ32r personality, Verify G+
cognitive) and role-specific defaults (DSI for safety roles, MS Office for admins,
HIPAA for healthcare) never surface from a role-only query because their vocabulary
doesn't overlap. We append them to the candidate pool so the LLM can choose to
include or skip them. They get a score below all retrieved items so they don't
push out genuine matches.
"""

from __future__ import annotations

from agent.state import UserState
from api.dependencies import get_catalog
from retriever.embeddings import get_index
from retriever.metadata_filters import apply_filters
from retriever.utils import reciprocal_rank_fusion


# Always-include defaults: OPQ32r is added to most shortlists as the personality layer;
# Verify G+ is the cognitive default for IC and graduate roles.
DEFAULT_ALWAYS: tuple[str, ...] = (
    "Occupational Personality Questionnaire OPQ32r",
    "SHL Verify Interactive G+",
)


# Role categories drive default-candidate injection.
#
# Each category has:
#   triggers:      keywords matched against (role + last user message); fires the bucket.
#   curated_names: hand-picked catalog items that are the canonical defaults. MUST resolve.
#   name_patterns: substrings used at module load to discover supplementary catalog items
#                  not in `curated_names` (case-insensitive). Capped per `extra_cap`.
#   extra_cap:     max supplementary items appended beyond the curated list.
#
# Items are discovered programmatically from `catalog.json`, so a catalog rename surfaces
# at boot via the assertion in `_build_default_by_role`, not silently at eval time.
_ROLE_CATEGORIES: tuple[dict, ...] = (
    {
        "triggers": ("admin", "office", "assistant", "secretary", "clerical", "receptionist"),
        "curated_names": (
            "MS Excel (New)",
            "MS Word (New)",
            "Microsoft Excel 365 - Essentials (New)",
            "Microsoft Word 365 - Essentials (New)",
        ),
        "name_patterns": ("MS PowerPoint", "Microsoft Outlook", "Microsoft Word 365", "MS Outlook"),
        "extra_cap": 2,
    },
    {
        "triggers": ("safety", "operator", "industrial", "plant", "manufact", "factory", "warehouse"),
        "curated_names": (
            "Dependability and Safety Instrument (DSI)",
            "Workplace Health and Safety (New)",
            "Manufac. & Indust. - Safety & Dependability 8.0",
        ),
        "name_patterns": ("Manufac. & Indust.",),
        "extra_cap": 1,
    },
    {
        "triggers": ("healthcare", "medical", "clinical", "hipaa", "patient", "hospital", "nurse"),
        "curated_names": (
            "HIPAA (Security)",
            "Medical Terminology (New)",
        ),
        "name_patterns": (),  # narrow bucket; no good supplementary patterns
        "extra_cap": 0,
    },
    {
        "triggers": ("financial", "finance", "analyst", "accountant", "banking", "numerical", "actuary"),
        "curated_names": (
            "SHL Verify Interactive – Numerical Reasoning",
            "Basic Statistics (New)",
        ),
        "name_patterns": ("Financial Accounting", "Financial and Banking", "Verify - Numerical"),
        "extra_cap": 2,
    },
    {
        "triggers": ("contact center", "contact centre", "call center", "call centre", "customer service", "csr"),
        "curated_names": (
            "Contact Center Call Simulation (New)",
            "Entry Level Customer Serv-Retail & Contact Center",
            "Customer Service Phone Simulation",
            "SVAR - Spoken English (US) (New)",
            "SVAR - Spoken Spanish (North American) (New)",
        ),
        "name_patterns": (),
        "extra_cap": 0,
    },
    {
        "triggers": ("sales", "selling", "account executive", "business development", "re-skill", "reskill"),
        "curated_names": (
            "OPQ MQ Sales Report",
            "Sales Transformation 2.0 - Individual Contributor",
            "Global Skills Assessment",
            "Global Skills Development Report",
        ),
        "name_patterns": ("Entry Level Sales", "Sales & Service Phone Simulation"),
        "extra_cap": 2,
    },
    {
        "triggers": ("graduate", "trainee", "intern", "fresher", "new grad", "campus"),
        "curated_names": (
            "Graduate Scenarios",
        ),
        "name_patterns": ("Graduate Scenarios Narrative", "Graduate Scenarios Profile"),
        "extra_cap": 2,
    },
    {
        "triggers": ("rust", "c++", "linux", "devops", "systems programming", "kernel", "embedded", "networking", "sre"),
        "curated_names": (
            "Smart Interview Live Coding",
            "Linux Programming (General)",
            "Networking and Implementation (New)",
        ),
        "name_patterns": ("Linux Administration", "C++ Programming"),
        "extra_cap": 2,
    },
    {
        # Use specific phrases — generic "manager"/"leadership" hits noise like "Adobe Experience Manager".
        "triggers": ("manager", "leadership", "director", "lead", "head of", "vp", "executive"),
        "curated_names": (
            "OPQ Universal Competency Report 2.0",
        ),
        "name_patterns": ("Enterprise Leadership Report", "Managerial Scenarios", "OPQ Leadership"),
        "extra_cap": 3,
    },
)


def _build_default_by_role(
    catalog: list[dict],
) -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    """Resolve role categories against the catalog at module load.

    For each category: validate every `curated_names` item exists, then append up to
    `extra_cap` supplementary catalog items whose name contains any `name_patterns`
    substring (case-insensitive) and isn't already in the curated list.

    Raises RuntimeError on first unresolved curated name — surfaces catalog drift at
    boot, not silently at eval time.
    """
    by_name = {item["name"]: item for item in catalog}

    # Validate DEFAULT_ALWAYS up front.
    for name in DEFAULT_ALWAYS:
        if name not in by_name:
            raise RuntimeError(
                f"DEFAULT_ALWAYS item not found in catalog: {name!r}. Catalog drift?"
            )

    out: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    for cat in _ROLE_CATEGORIES:
        # Curated names must resolve byte-exact.
        for name in cat["curated_names"]:
            if name not in by_name:
                raise RuntimeError(
                    f"DEFAULT_BY_ROLE curated item not found in catalog: {name!r} "
                    f"(triggers={cat['triggers']}). Catalog drift?"
                )

        # Supplementary items: substring match against catalog names, dedupe vs curated.
        seen = set(cat["curated_names"])
        extras: list[str] = []
        if cat["extra_cap"] > 0 and cat["name_patterns"]:
            patterns_lower = [p.lower() for p in cat["name_patterns"]]
            for item in catalog:
                if len(extras) >= cat["extra_cap"]:
                    break
                name = item["name"]
                if name in seen:
                    continue
                name_lower = name.lower()
                if any(p in name_lower for p in patterns_lower):
                    extras.append(name)
                    seen.add(name)

        out.append((tuple(cat["triggers"]), tuple(cat["curated_names"]) + tuple(extras)))

    return out


# Resolved at module import. Raises if catalog drift breaks any curated name.
DEFAULT_BY_ROLE: list[tuple[tuple[str, ...], tuple[str, ...]]] = _build_default_by_role(
    get_catalog()
)


class Retriever:
    def __init__(self) -> None:
        self.catalog = get_catalog()
        self.by_id = {item["entity_id"]: item for item in self.catalog}
        self.by_name = {item["name"]: item for item in self.catalog}
        self.index = get_index()

    def search(self, state: UserState, query: str, k: int = 40) -> list[dict]:
        # 1. Build query text from state + last user message. Both contribute signal.
        composed = self._compose_query(state, query)

        # 2. Run sparse + dense retrieval. Pull more than k each — fusion narrows down.
        bm25_ids = self.index.bm25_topk(composed, k=120)
        dense_ids = self.index.dense_topk(self.index.encode_query(composed), k=120)

        # 3. RRF fuse.
        fused = reciprocal_rank_fusion([bm25_ids, dense_ids])

        # 4. Hydrate to full items (preserve fused order).
        items = []
        for entity_id, score in fused:
            item = self.by_id.get(entity_id)
            if item:
                items.append({**item, "score": score})

        # 5. Metadata pre-filter (permissive — won't drop everything).
        items = apply_filters(items, state)

        # 6. Type-want boost: nudge items matching wanted test types up the list.
        if state.test_types_wanted:
            wanted = set(state.test_types_wanted)
            for it in items:
                it_letters = set(it.get("test_type", "").split(","))
                if it_letters & wanted:
                    it["score"] += 0.1

            items.sort(key=lambda x: x["score"], reverse=True)

        items = items[:k]

        # 7. Inject defaults the LLM should always have a chance to consider.
        items = self._inject_defaults(items, state, query)
        return items

    def _inject_defaults(
        self, items: list[dict], state: UserState, last_msg: str
    ) -> list[dict]:
        """Append SHL defaults that role-only queries miss; also flag matching items
        that natural retrieval already returned, so the reranker treats them as required.

        Skip on clarify/refuse — those intents don't surface recommendations anyway.
        Trigger detection runs on the **original** user message + state.role, NOT the
        expanded retrieval query — the LLM expander adds adjacent vocabulary
        ("customer service", "manufacturing") that would falsely fire unrelated buckets.
        """
        if state.intent not in ("recommend", "refine"):
            return items

        # Always prefer the unexpanded user message for trigger detection.
        trigger_msg = (state.last_user_msg or last_msg or "").lower()
        signal = f"{(state.role or '').lower()} {trigger_msg}"
        names_to_add: list[str] = list(DEFAULT_ALWAYS)
        for keywords, defaults in DEFAULT_BY_ROLE:
            if any(kw in signal for kw in keywords):
                names_to_add.extend(defaults)

        # Flag items that natural retrieval already returned so they're treated as
        # default-injected by the reranker. Without this, OPQ32r / Verify G+ silently
        # lose their must-include status when BM25/dense rank them naturally.
        target_names = set(names_to_add)
        present_names: set[str] = set()
        for it in items:
            if it["name"] in target_names:
                it["default_injected"] = True
                present_names.add(it["name"])

        # Append defaults missing from retrieval, with a low score so they don't shove
        # genuine retrieval matches off the candidate pool.
        for name in names_to_add:
            if name in present_names:
                continue
            item = self.by_name.get(name)
            if item:
                items.append({**item, "score": -0.1, "default_injected": True})
                present_names.add(name)
        return items

    @staticmethod
    def _compose_query(state: UserState, last_msg: str) -> str:
        """Compose a retrieval query from inferred state + last user message.

        Rule of thumb: the LAST message dominates intent (refinement); the state
        provides standing context (role, skills) that the user has stopped repeating.
        """
        parts = [last_msg]
        if state.role:
            parts.append(state.role)
        if state.skills:
            parts.append(" ".join(state.skills))
        if state.seniority:
            parts.append(state.seniority)
        return " | ".join(parts)


_RETRIEVER: Retriever | None = None


def get_retriever() -> Retriever:
    global _RETRIEVER
    if _RETRIEVER is None:
        _RETRIEVER = Retriever()
    return _RETRIEVER
