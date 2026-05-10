"""User state inference from full conversation history.

Stateless API: every /chat call carries the entire message list, and we re-derive the
user state from scratch. No DB, no session storage.

Approach: regex/keyword extraction. Cheap, deterministic, easy to debug. We add an LLM
extraction pass only if rules underperform on the public traces.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from api.schemas import Message

Intent = Literal["clarify", "recommend", "refine", "compare", "refuse"]


@dataclass
class UserState:
    role: str | None = None
    seniority: str | None = None  # junior | mid | senior | executive
    skills: list[str] = field(default_factory=list)
    test_types_wanted: list[str] = field(default_factory=list)  # ["K","P"]
    test_types_excluded: list[str] = field(default_factory=list)
    duration_max_min: int | None = None
    language: str | None = None
    current_shortlist_names: list[str] = field(default_factory=list)
    intent: Intent = "clarify"
    clarify_count: int = 0
    compare_targets: list[str] = field(default_factory=list)
    last_user_msg: str = ""
    closure_signal: bool = False  # user said "thanks", "perfect", etc.


# --- Vocabulary -----------------------------------------------------------------

SENIORITY_PATTERNS: dict[str, list[str]] = {
    "junior": [
        r"\bentry[- ]level\b", r"\bgraduate\b", r"\bjunior\b", r"\bintern\b",
        r"\bfresher\b", r"\bnew grad\b", r"\bgrad\b",
    ],
    "mid": [
        r"\bmid[- ]level\b", r"\bmid[- ]professional\b", r"\b3[- ]?5 years\b",
        r"\b4 years\b", r"\b5 years\b", r"\bsenior IC\b", r"\bsenior individual contributor\b",
    ],
    "senior": [
        r"\bsenior\b", r"\bmanager\b", r"\bsupervisor\b", r"\btech lead\b",
        r"\b7\+? years\b", r"\b10\+? years\b",
    ],
    "executive": [
        r"\bdirector\b", r"\bexecutive\b", r"\bCXO\b", r"\bC-?suite\b", r"\bVP\b",
        r"\bhead of\b", r"\b15\+? years\b",
    ],
}

# Trace-driven role keywords. Not exhaustive; extend as we encounter new persona patterns.
ROLE_KEYWORDS = [
    "java developer", "java engineer", "rust engineer", "rust developer",
    "full-stack engineer", "fullstack engineer", "backend engineer",
    "frontend engineer", "financial analyst", "plant operator",
    "contact centre agent", "contact center agent", "call centre agent",
    "healthcare admin", "admin assistant", "admin assistants",
    "sales", "graduate management trainee", "graduate management trainees",
    "trainee", "leadership", "CXO",
    "data analyst", "customer service rep", "office administrator",
    "medical assistant", "nurse", "manufacturing operator",
]

SKILL_KEYWORDS = [
    "Java", "Spring", "REST", "RESTful", "Angular", "React", "SQL", "AWS", "Docker",
    "Kubernetes", "Python", "Linux", "C++", "C#", ".NET", "Rust", "Go", "Node",
    "Networking", "Microservices",
    "Excel", "Word", "PowerPoint", "Outlook", "Office",
    "HIPAA", "Medical Terminology", "Healthcare", "Clinical",
    "stakeholder", "leadership", "management",
    "numerical reasoning", "situational judgement", "situational judgment",
    "personality", "cognitive", "verify", "OPQ", "OPQ32r", "GSA",
    "MQ", "DSI", "Verify G+",
    "Spanish", "English", "bilingual",
    "safety", "dependability", "compliance",
]

LANGUAGE_PATTERNS = {
    "English (USA)": [r"\benglish \(?usa?\)?\b", r"\bus english\b", r"\bus accent\b", r"\bus\b(?=\.| |$)"],
    "English International": [r"\benglish international\b", r"\bUK english\b"],
    "Latin American Spanish": [r"\bspanish\b", r"\blatin american\b"],
    "French": [r"\bfrench\b"],
    "German": [r"\bgerman\b"],
}

# Words that map a phrase to a test_type letter.
TYPE_INTENT = {
    "K": [r"\bknowledge\b", r"\btechnical\b", r"\bskills test\b", r"\bskill test\b"],
    "P": [r"\bpersonality\b", r"\bbehaviou?ral\b", r"\bopq\b"],
    "A": [r"\bcognitive\b", r"\baptitude\b", r"\bability\b", r"\breasoning\b", r"\bnumerical\b", r"\bverbal\b", r"\bverify\b"],
    "S": [r"\bsimulation\b"],
    "B": [r"\bsituational\b", r"\bsjt\b", r"\bbiodata\b"],
    "C": [r"\bcompetenc(y|ies|e)\b"],
    "D": [r"\bdevelopment\b", r"\b360\b"],
}

ADD_PATTERNS = [r"\badd\b", r"\binclude\b", r"\balso\b.{0,20}(test|assessment)", r"\bplus\b"]
DROP_PATTERNS = [r"\bdrop\b", r"\bremove\b", r"\bskip\b", r"\bexclude\b", r"\btake out\b"]
CLOSURE_PATTERNS = [
    r"\bperfect\b", r"\bthanks\b", r"\bthank you\b", r"\bconfirmed\b",
    r"\bthat's it\b", r"\bthat covers it\b", r"\blocking it in\b",
    r"\blooks good\b", r"\bthat works\b", r"\bgood\b", r"\bunderstood\b",
    r"\bkeep .* as[- ]?is\b",
]
COMPARE_PATTERNS = [
    r"\bdifference between\b", r"\bcompare\b", r"\bvs\.?\b", r"\bversus\b",
    r"\bdifferent from\b", r"\bsame as\b", r"\bvs\b",
]
REFUSAL_TRIGGERS = [
    r"\blegal\b.{0,30}\b(require|obligation|comply)\b",
    r"\bregulator\b", r"\bcompliance\b.{0,30}\brequire\b",
    r"\bweather\b", r"\bstock price\b", r"\bjoke\b",
    # Prompt injection signatures
    r"ignore (all )?(previous|prior) instructions",
    r"you are now\b", r"pretend you are\b", r"system prompt\b",
    r"reveal (your|the) (prompt|instructions)",
]


# --- Extraction helpers ---------------------------------------------------------

def _find_first(text: str, patterns: list[str]) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def _seniority_from(text: str) -> str | None:
    for level, patterns in SENIORITY_PATTERNS.items():
        if _find_first(text, patterns):
            return level
    return None


def _skills_from(text: str) -> list[str]:
    found = []
    for skill in SKILL_KEYWORDS:
        if re.search(rf"\b{re.escape(skill)}\b", text, re.IGNORECASE):
            found.append(skill)
    return found


def _role_from(text: str) -> str | None:
    text_lc = text.lower()
    for role in ROLE_KEYWORDS:
        if role.lower() in text_lc:
            return role
    return None


def _language_from(text: str) -> str | None:
    for lang, patterns in LANGUAGE_PATTERNS.items():
        if _find_first(text, patterns):
            return lang
    return None


def _type_intents_from(text: str) -> list[str]:
    found = []
    for letter, patterns in TYPE_INTENT.items():
        if _find_first(text, patterns):
            found.append(letter)
    return found


def _last_assistant_recommendations(
    messages: list[Message], catalog_names: list[str] | None = None
) -> list[str]:
    """Best-effort extract of catalog item names from the most recent assistant turn.

    The /chat API only carries `content` strings, not structured `recommendations`,
    across turns. So we scan the last assistant `reply` for catalog item names that
    appear as substrings. With ~377 items this is fast (single linear pass).

    Two passes:
      1. Markdown table rows / bullets containing a catalog URL — pull the second cell.
      2. Substring match of any known catalog name in the prose itself (covers replies
         like "Recommended: Java 8 (New), Spring (New), SQL (New).").
    """
    for msg in reversed(messages):
        if msg.role != "assistant":
            continue
        names: list[str] = []
        seen: set[str] = set()

        # Pass 1: markdown table rows
        for line in msg.content.splitlines():
            if "|" in line and "shl.com" in line.lower():
                cells = [c.strip() for c in line.split("|")]
                non_empty = [c for c in cells if c]
                if len(non_empty) >= 2 and non_empty[1] not in seen:
                    seen.add(non_empty[1])
                    names.append(non_empty[1])

        # Pass 2: substring match of catalog names in prose. Match longest names
        # first so we don't double-count a shorter name that's a prefix of a longer one
        # (e.g. "Smart Interview Live" inside "Smart Interview Live Coding").
        if catalog_names:
            content = msg.content
            for cname in sorted(catalog_names, key=len, reverse=True):
                if not cname or cname in seen:
                    continue
                if cname in content:
                    seen.add(cname)
                    names.append(cname)

        # Final dedupe: drop any name that is a strict substring of another in the list.
        deduped: list[str] = []
        for n in names:
            if any(n != other and n in other for other in names):
                continue
            deduped.append(n)
        return deduped
    return []


# --- Public entrypoint ----------------------------------------------------------

def _load_catalog_names() -> list[str]:
    """Lazy import to avoid a circular dependency with api.dependencies at module load."""
    from api.dependencies import get_catalog
    return [item["name"] for item in get_catalog()]


def infer_state(messages: list[Message]) -> UserState:
    """Re-derive user state from the entire history."""
    state = UserState()
    user_turns: list[str] = []
    for msg in messages:
        if msg.role == "user":
            user_turns.append(msg.content)

    if not user_turns:
        return state

    state.last_user_msg = user_turns[-1]
    full_text = " \n ".join(user_turns)

    # Take role from earliest mention; refinement messages should not overwrite role.
    for ut in user_turns:
        r = _role_from(ut)
        if r:
            state.role = r
            break

    # Seniority and language: take latest non-null mention (user can correct themselves).
    for ut in reversed(user_turns):
        if state.seniority is None:
            state.seniority = _seniority_from(ut)
        if state.language is None:
            state.language = _language_from(ut)
        if state.seniority and state.language:
            break

    # Skills are additive across turns; dedupe preserving order.
    seen = set()
    for ut in user_turns:
        for s in _skills_from(ut):
            if s.lower() not in seen:
                seen.add(s.lower())
                state.skills.append(s)

    # Test-type wants: union across turns. Excluded: per-turn drop signal.
    type_intents = _type_intents_from(full_text)
    state.test_types_wanted = list(dict.fromkeys(type_intents))

    last_msg = state.last_user_msg
    if _find_first(last_msg, DROP_PATTERNS):
        # Heuristic: which type letter sits near a drop word in the last msg?
        for letter, patterns in TYPE_INTENT.items():
            for p in patterns:
                if re.search(rf"({'|'.join(DROP_PATTERNS)}).{{0,40}}{p}", last_msg, re.IGNORECASE):
                    if letter not in state.test_types_excluded:
                        state.test_types_excluded.append(letter)

    # Closure signal — drives end_of_conversation.
    state.closure_signal = _find_first(last_msg, CLOSURE_PATTERNS)

    # Refusal signal — checked first because it short-circuits routing.
    if _find_first(last_msg, REFUSAL_TRIGGERS):
        state.intent = "refuse"
    elif _find_first(last_msg, COMPARE_PATTERNS):
        state.intent = "compare"
    else:
        # Detect prior shortlist and infer refine vs recommend vs clarify.
        try:
            catalog_names = _load_catalog_names()
        except Exception:
            catalog_names = []
        state.current_shortlist_names = _last_assistant_recommendations(
            messages, catalog_names=catalog_names
        )
        has_prior_shortlist = bool(state.current_shortlist_names)
        wants_edit = _find_first(last_msg, ADD_PATTERNS + DROP_PATTERNS)

        if has_prior_shortlist and wants_edit:
            state.intent = "refine"
        elif state.role and (state.seniority or state.skills or state.test_types_wanted):
            state.intent = "recommend"
        else:
            # Count clarify turns the assistant has already asked.
            clarify_turns = sum(
                1 for m in messages if m.role == "assistant" and "?" in m.content
            )
            state.clarify_count = clarify_turns
            # Auto-recommend after 2 clarifies to honor turn cap.
            if clarify_turns >= 2 and state.role:
                state.intent = "recommend"
            else:
                state.intent = "clarify"

    return state
