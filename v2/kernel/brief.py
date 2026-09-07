"""The review brief: a kernel artefact rendered from objects the kernel holds
(spec §2 *Brief*). Pure: the same inputs and template give the same bytes,
which is what the §8 proof re-renders and compares."""
from __future__ import annotations

from kernel.canon import content_hash
from kernel.policy import Policy, to_payload

TEMPLATE_VERSION = 1
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
"""}

_SPEC_SECTION = """
## The accepted spec this plan implements (sha256 {spec_hash})

{spec}
"""


def hash8(artifact_hash: str) -> str:
    return artifact_hash[:8]


def render(*, phase: str, artefact: bytes, bundle: bytes, spec: bytes | None,
           policy: Policy, base_sha: str, template: int) -> bytes:
    if template not in _TEMPLATES:
        raise ValueError(f"unknown brief template version {template}")
    if phase not in ("spec", "plan"):
        raise ValueError(f"brief phase must be spec or plan, got {phase!r}")
    if phase == "plan" and spec is None:
        raise ValueError("a plan brief carries the run's current spec")
    if phase == "spec" and spec is not None:
        raise ValueError("a spec brief carries no spec")
    p = to_payload(policy)
    artifact_hash = content_hash(artefact)
    spec_section = ""
    what = "then the spec"
    if phase == "plan":
        spec_section = _SPEC_SECTION.format(spec_hash=content_hash(spec),
                                            spec=spec.decode("utf-8", "replace"))
        what = "then the accepted spec, then the plan"
    text = _TEMPLATES[template].format(
        template=template, phase=phase, review_out=REVIEW_OUT, what_to_read=what,
        hash8=hash8(artifact_hash), grill=p["grill"], gates=", ".join(p["gates"]) or "(none)",
        max_rounds=p["max_rounds"], max_seats=p["max_seats"], base_sha=base_sha,
        bundle_hash=content_hash(bundle), bundle=bundle.decode("utf-8", "replace"),
        spec_section=spec_section, artifact_hash=artifact_hash,
        artefact=artefact.decode("utf-8", "replace"),
    )
    return text.encode("utf-8")
