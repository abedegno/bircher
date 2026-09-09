"""The review brief: a kernel artefact rendered from objects the kernel holds
(spec §2 *Brief*). Pure: the same inputs and template give the same bytes,
which is what the §8 proof re-renders and compares."""
from __future__ import annotations

from kernel.canon import content_hash
from kernel.policy import Policy, to_payload

#: Which template `issue_review_brief` renders. 2 is the spec's brief since
#: 2026-09-09: severities, a verdict rule that PASSes when nothing high or
#: medium remains, and the previous round's findings attached with an
#: instruction not to relitigate what the author's Dispositions section
#: resolved. It began as an experiment on 2026-09-08 and became the default
#: after two converged runs against three that did not. The fact records
#: which template was used, so the proof re-renders the right one.
TEMPLATE_VERSION = 2
#: The spec's original brief, renderable so the proof can re-render the
#: briefs of runs that were issued under it; never issued any more.
LEGACY_TEMPLATE_VERSION = 1
#: Relative to the reviewer's worktree root. A convention stated in the
#: brief, not an environment variable (spec §3 Author round).
REVIEW_OUT = "bircher/review.md"

_TEMPLATES = {1: """# Bircher review brief (template v{template})

You are an INDEPENDENT, READ-ONLY reviewer of a {phase} for the issue below.
Do not edit files outside `{review_out}`, do not run git write commands, do
not push, do not open or comment on anything. You have no credentials.

## What to do

1. Read the issue snapshot, {what_to_read}.
2. Write your findings to `{review_out}` in your worktree: numbered,
   most severe first, each naming the section it concerns and what is wrong.
3. As the file's LAST non-blank line write exactly one of (`VERDICT: PASS|FAIL {hash8}`):

       VERDICT: PASS {hash8}
       VERDICT: FAIL {hash8}

   `{hash8}` is the first eight hex digits of the artefact's sha256 and
   names what you reviewed; a verdict without it, or with another hash,
   counts as no verdict. Findings come BEFORE the verdict line, never after.
4. End your turn.

PASS means the artefact is ready for the next phase as it stands. FAIL means
the author must revise; your findings are its brief.

## Policy

    grill: {grill}
    gates: {gates}
    max_rounds: {max_rounds}
    max_seats: {max_seats}
    base_sha: {base_sha}

## Issue snapshot (bundle {bundle_hash})

```json
{bundle}
```
{spec_section}
## The {phase} under review (sha256 {artifact_hash})

{artefact}
""",
2: """# Bircher review brief (template v{template})

You are an INDEPENDENT, READ-ONLY reviewer of a {phase} for the issue below.
Do not edit files outside `{review_out}`, do not run git write commands, do
not push, do not open or comment on anything. You have no credentials.

## What to do

1. Read the issue snapshot, {what_to_read}.
2. Write your findings to `{review_out}` in your worktree: numbered,
   most severe first, each opening with a severity tag -- `[high]`, `[medium]`
   or `[low]` -- then the section it concerns and what is wrong.
   - `[high]`: the artefact is wrong, unsafe, or contradicts the issue, the
     spec or itself; implementing it as written would produce a defect.
   - `[medium]`: a real gap or ambiguity an implementer would have to guess
     at, or a decision the artefact makes that the spec did not.
   - `[low]`: wording, ordering, redundancy, or a preference. Record it;
     it does not block.
3. As the file's LAST non-blank line write exactly one of (`VERDICT: PASS|FAIL {hash8}`):

       VERDICT: PASS {hash8}
       VERDICT: FAIL {hash8}

   `{hash8}` is the first eight hex digits of the artefact's sha256 and
   names what you reviewed; a verdict without it, or with another hash,
   counts as no verdict. Findings come BEFORE the verdict line, never after.
4. End your turn.

**The verdict rule.** FAIL only if at least one `[high]` or `[medium]` finding
remains. If everything you found is `[low]`, the verdict is PASS and the low
findings travel with it as advice. Do not invent a finding to justify a FAIL,
and do not withhold a PASS because a document could always be improved.
{prior_section}
## Policy

    grill: {grill}
    gates: {gates}
    max_rounds: {max_rounds}
    max_seats: {max_seats}
    base_sha: {base_sha}

## Issue snapshot (bundle {bundle_hash})

```json
{bundle}
```
{spec_section}
## The {phase} under review (sha256 {artifact_hash})

{artefact}
"""}

_SPEC_SECTION = """
## The accepted spec this plan implements (sha256 {spec_hash})

{spec}
"""

_PRIOR_SECTION = """
## The previous round's findings (sha256 {prior_hash})

This artefact is a revision. Its `## Dispositions` section answers each
finding below by number: *accepted*, with what changed, or *rejected*, with
why. Read the dispositions before you review.

- A finding the author accepted and addressed is resolved. Do not raise it
  again unless the revision fails to do what the disposition claims.
- A finding the author rejected stands only if you have NEW evidence the
  rejection did not consider. Restating the original objection is not new
  evidence.
- A finding with no disposition at all is unresolved: raise it, at its
  original severity.

Your job this round is what is wrong NOW, not whether you would have written
it differently.

{prior}
"""


def hash8(artifact_hash: str) -> str:
    return artifact_hash[:8]


def render(*, phase: str, artefact: bytes, bundle: bytes, spec: bytes | None,
           policy: Policy, base_sha: str, template: int,
           prior_findings: bytes | None = None) -> bytes:
    if template not in _TEMPLATES:
        raise ValueError(f"unknown brief template version {template}")
    if phase not in ("spec", "plan"):
        raise ValueError(f"brief phase must be spec or plan, got {phase!r}")
    if phase == "plan" and spec is None:
        raise ValueError("a plan brief carries the run's current spec")
    if phase == "spec" and spec is not None:
        raise ValueError("a spec brief carries no spec")
    if template == 1 and prior_findings is not None:
        # Purity: the template the spec fixes renders the same bytes for the
        # same named objects, and it names no prior round.
        raise ValueError("template 1 carries no prior findings")
    p = to_payload(policy)
    artifact_hash = content_hash(artefact)
    spec_section = ""
    what = "then the spec"
    if phase == "plan":
        spec_section = _SPEC_SECTION.format(spec_hash=content_hash(spec),
                                            spec=spec.decode("utf-8", "replace"))
        what = "then the accepted spec, then the plan"
    prior_section = ""
    if prior_findings is not None:
        prior_section = _PRIOR_SECTION.format(prior_hash=content_hash(prior_findings),
                                              prior=prior_findings.decode("utf-8", "replace"))
    text = _TEMPLATES[template].format(
        template=template, phase=phase, review_out=REVIEW_OUT, what_to_read=what,
        prior_section=prior_section,
        hash8=hash8(artifact_hash), grill=p["grill"], gates=", ".join(p["gates"]) or "(none)",
        max_rounds=p["max_rounds"], max_seats=p["max_seats"], base_sha=base_sha,
        bundle_hash=content_hash(bundle), bundle=bundle.decode("utf-8", "replace"),
        spec_section=spec_section, artifact_hash=artifact_hash,
        artefact=artefact.decode("utf-8", "replace"),
    )
    return text.encode("utf-8")
