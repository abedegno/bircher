# v2/kernel/slices.py
"""The slice plan: one grammar, one parser, one set of renderers (shaping
spec §2 *The artefact*, §3 *The shaping round*, §3 *Filing the children*).

Every reader of a plan -- the submit refusals, the outcome guard, the filing
step, the effect binding, the child renderer, the proof -- goes through
`parse`, so there is one reading of a plan and not five. Nothing here reads
the store or a payload; these are pure functions over bytes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: The definition of coarse, verbatim from spec §1. The shape author's brief
#: and the slice reviewer's brief both render it from here.
COARSE = (
    "A slice is a coarse, coherent, independently reviewable capability: a few "
    "related files, on the order of one to four hundred lines, that deliver a "
    "working sub-feature a reviewer can see working end to end. It is not an "
    "atomic plan-step — not a migration, a single struct, one helper, one store "
    "method or one endpoint on its own. Every slice is a full pipeline cycle, "
    "an author, a reviewer, CI and a merge, and the cross-vendor review is "
    "weaker on a lone fragment than on a slice that does something. Aggregate by "
    "layer or capability, three to five slices to an epic; a plan that sliced an "
    "issue into its plan-steps has recreated the problem."
)

MIN_SLICES, MAX_SLICES = 2, 5
MIN_SENTENCES, MAX_SENTENCES = 3, 6
RULING_LINE = "Ruling: one piece"
#: The reserved model_ruling question_id (spec §2): only record_one_piece
#: writes it, and record_model_question/record_model_ruling refuse it.
SHAPE_QUESTION = "shape"
#: The labels a child inherits from its parent (spec §1): the policy labels,
#: never the runner's or the filing step's.
POLICY_LABELS = ("bircher:autonomous", "bircher:gate-plan", "bircher:grill")

_HEADING = re.compile(r"^## Slice (\d+): (.+?)\s*$")
_LABELS = ("Scope:", "Non-goals:", "Depends on:")
_RULING_LABELS = ("Reasoning:", "Cost if wrong:")


class PlanError(ValueError):
    """The bytes are not a slice plan the kernel admits; the message names why."""


@dataclass(frozen=True)
class Slice:
    number: int
    title: str
    scope: str
    non_goals: str
    depends_on: tuple[int, ...]


@dataclass(frozen=True)
class Plan:
    title: str
    preamble: str
    slices: tuple[Slice, ...]

    def by_number(self) -> dict[int, Slice]:
        return {s.number: s for s in self.slices}

    def edges(self) -> list[tuple[int, int]]:
        """`(slice, blocker)` pairs, in slice order."""
        return [(s.number, b) for s in self.slices for b in s.depends_on]


@dataclass(frozen=True)
class Ruling:
    reasoning: str
    cost_if_wrong: str


def sentences(text: str) -> int:
    """Full stops that end a sentence: a `.` followed by whitespace or the end
    of the text. `3.5` does not count; a final `done.` does."""
    return len(re.findall(r"\.(?=\s|$)", text.strip()))


def parse(data: bytes) -> Plan:
    """The slice-plan grammar (spec §2 *The artefact*).

    Before the first `## Slice N: <title>` heading, every line -- including
    one that itself starts with `## ` -- is preamble. After it, a `## `
    line that is not a slice heading ends the slices: everything from it to
    the end of the file is a TRAILING SECTION and is not parsed. This is
    what lets a revision's artefact carry `## Dispositions` (the author's
    brief demands one, spec §2/§3) after its last slice -- a slice plan is
    not free text, and without this a reviewer's own findings section
    would be read as more of the last slice's `Depends on:` field, and
    refused as one (kernel authz ruling, fix round following Task 7).

    A line that is not a slice heading and does not start with `## ` where
    one is expected is still refused, as before: only a `## ` line reads as
    the start of a trailing section, everything else is malformed.
    """
    lines = data.decode("utf-8", "replace").splitlines()
    title, pre, i = "", [], 0
    while i < len(lines) and not _HEADING.match(lines[i]):
        if not title and lines[i].startswith("# "):
            title = lines[i][2:].strip()
        else:
            pre.append(lines[i])
        i += 1
    blocks: list[tuple[int, str, list[str]]] = []
    while i < len(lines):
        m = _HEADING.match(lines[i])
        if not m:
            if lines[i].startswith("## "):
                break
            raise PlanError(f"line {i + 1}: expected a `## Slice N: <title>` heading, got {lines[i]!r}")
        n, t = int(m.group(1)), m.group(2).strip()
        i += 1
        body: list[str] = []
        while i < len(lines) and not lines[i].startswith("## "):
            body.append(lines[i])
            i += 1
        blocks.append((n, t, body))
    if not blocks:
        raise PlanError("no `## Slice N: <title>` heading: not a slice plan")
    numbers = [n for n, _, _ in blocks]
    if numbers != list(range(1, len(blocks) + 1)):
        raise PlanError(f"slice numbers must be 1..N in order with no gaps, got {numbers}")
    if not MIN_SLICES <= len(blocks) <= MAX_SLICES:
        raise PlanError(f"{len(blocks)} slices: a plan has {MIN_SLICES} to {MAX_SLICES}")
    out = []
    for n, t, body in blocks:
        if not t:
            raise PlanError(f"slice {n} has no title")
        fields = _fields(n, body)
        count = sentences(fields["Scope:"])
        if not MIN_SENTENCES <= count <= MAX_SENTENCES:
            raise PlanError(f"slice {n}: Scope has {count} sentence(s); "
                            f"{MIN_SENTENCES} to {MAX_SENTENCES} by full stops")
        if not fields["Non-goals:"]:
            raise PlanError(f"slice {n}: Non-goals is empty")
        out.append(Slice(n, t, fields["Scope:"], fields["Non-goals:"], _deps(n, fields["Depends on:"])))
    plan = Plan(title, "\n".join(pre).strip(), tuple(out))
    for s in plan.slices:
        for b in s.depends_on:
            if b == s.number:
                raise PlanError(f"slice {s.number} depends on itself")
            if b not in numbers:
                raise PlanError(f"slice {s.number} depends on slice {b}, which is not in the plan")
    topo_order(plan)  # raises on a cycle
    return plan


def _fields(n: int, body: list[str]) -> dict[str, str]:
    found: dict[str, list[str]] = {}
    current = None
    for line in body:
        hit = next((lab for lab in _LABELS if line.startswith(lab)), None)
        if hit is not None:
            if hit in found:
                raise PlanError(f"slice {n}: `{hit}` appears twice")
            found[hit] = [line[len(hit):].strip()]
            current = hit
        elif current is not None and line.strip():
            found[current].append(line.strip())
    missing = [lab for lab in _LABELS if lab not in found]
    if missing:
        raise PlanError(f"slice {n}: missing {missing}")
    return {k: " ".join(x for x in v if x) for k, v in found.items()}


def _deps(n: int, value: str) -> tuple[int, ...]:
    if value.strip().lower() == "none":
        return ()
    out = []
    for tok in value.split(","):
        tok = tok.strip()
        if not tok.isdigit():
            raise PlanError(f"slice {n}: Depends on must be `none` or slice numbers, got {value!r}")
        out.append(int(tok))
    return tuple(out)


def topo_order(plan: Plan) -> list[int]:
    """Dependency order, ties by slice number (spec §3 pass 1); PlanError on
    a cycle."""
    deps = {s.number: set(s.depends_on) for s in plan.slices}
    order: list[int] = []
    while deps:
        ready = sorted(n for n, d in deps.items() if not d - set(order))
        if not ready:
            raise PlanError(f"dependency cycle among slices {sorted(deps)}")
        order.append(ready[0])
        del deps[ready[0]]
    return order


def parse_ruling(data: bytes) -> Ruling | None:
    """`None` when the file is not a ruling (spec §3 *The ruling file's
    grammar*): the first non-blank line is exactly `Ruling: one piece`;
    `Reasoning:` and `Cost if wrong:` each open a block that runs to the next
    label or the end; both non-empty after trimming; leading whitespace is
    ignored; any other non-blank line before the first label, a missing
    label, or a repeated label makes the file not a ruling."""
    body = [ln.strip() for ln in data.decode("utf-8", "replace").splitlines() if ln.strip()]
    if not body or body[0] != RULING_LINE:
        return None
    blocks: dict[str, list[str]] = {}
    current = None
    for line in body[1:]:
        label = next((lab for lab in _RULING_LABELS if line.startswith(lab)), None)
        if label is not None:
            if label in blocks:
                return None
            current = label
            blocks[label] = [line[len(label):].strip()]
        elif current is None:
            return None
        else:
            blocks[current].append(line)
    reasoning = " ".join(x for x in blocks.get("Reasoning:", []) if x).strip()
    cost = " ".join(x for x in blocks.get("Cost if wrong:", []) if x).strip()
    if not reasoning or not cost:
        return None
    return Ruling(reasoning, cost)


def render_child(s: Slice, parent: int, hash8: str, filed: dict[int, int]) -> tuple[str, str]:
    """`(title, body)` of the child issue for *s* (spec §3 pass 1). *filed*
    maps slice number -> issue number for the siblings already filed. STRICT:
    a dependency with no filed number raises rather than rendering `none`
    (round 11, finding 3), so an out-of-order create cannot produce a body
    the binding admits."""
    missing = [b for b in s.depends_on if b not in filed]
    if missing:
        raise PlanError(f"slice {s.number}: blocker(s) {missing} have no filed issue")
    deps = ", ".join(f"#{filed[b]}" for b in s.depends_on) or "none"
    body = (f"Slice {s.number} of #{parent} (bircher shaping {hash8})\n\n"
            f"{s.scope}\n\nNon-goals: {s.non_goals}\n\nDepends on: {deps}\n")
    return s.title, body


def umbrella_body(hash8: str, plan: Plan, filed: dict[int, int]) -> str:
    lines = [f"- #{filed[s.number]} Slice {s.number}: {s.title}" for s in plan.slices]
    return f"bircher: sliced {hash8}\n\n" + "\n".join(lines) + "\n"


def completion_body(plan: Plan, filed: dict[int, int], states: dict[int, str]) -> str:
    lines = [f"- #{filed[s.number]} Slice {s.number}: {s.title} — {states[s.number]}" for s in plan.slices]
    return "bircher: sliced complete\n\n" + "\n".join(lines) + "\n"


def inherited_labels(labels) -> list[str]:
    return sorted(lab for lab in set(labels) if lab in POLICY_LABELS)
