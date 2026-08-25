# M1-1 authority boundary: capability evidence

*The Milestone 1 acceptance evidence, recorded as an artifact rather than left
in a session transcript. `v2/tests/execution/test_boundary_evidence.py` fails
if a check in the probe is missing from this record, or if any recorded check
is not a pass.*

## Why this file exists

M1-1's "Done means" requires the capability suite to pass **with its control
and its planted positive both demonstrated**. It was originally run ad hoc.
The suite was never committed and the result lived only in a conversation, so
by the time anyone came to check it, the evidence covered an image the runner
no longer ran — `sha-54df826`, superseded by a rebuild. An acceptance
criterion whose evidence cannot be reproduced is satisfied by memory.

## Run

| | |
|---|---|
| Date | 2026-08-25 |
| Runner | `omnigent-runner-bircher` on the NAS |
| omnigent | 0.9.0, built 2026-08-25T07:39:56Z |
| Landlock ABI | 6 |
| Bundle | `agents/v2_implementer` at bircher `2332046` (branch `v2`) |
| Probe | `v2/tools/capability_probe.sh` at the same commit |
| Harness | `claude-sdk`, server-hosted session on `http://omnigent:8000` |

Fork patches confirmed present in the running image: `_LANDLOCK_RULE_NET_PORT`
(landlock_sandbox), `_READS_UNRESTRICTED_WHEN_UNSET` (sandbox),
`backend_can_enforce_egress_rules` (datamodel).

## Result

```
=== control: the boundary must not simply be a dead network ===
CHECK fetch_allowed          expect=allow  got=http_200                     PASS
=== denials ===
CHECK push_receive_pack      expect=deny   got=http_403                     PASS
CHECK api_pr_create          expect=deny   got=http_403                     PASS
CHECK api_comment            expect=deny   got=http_403                     PASS
CHECK api_label_edit         expect=deny   got=http_403                     PASS
CHECK api_graphql            expect=deny   got=http_403                     PASS
CHECK unlisted_host          expect=deny   got=no_route_l4                  PASS
CHECK unlisted_repo          expect=deny   got=http_403                     PASS
CHECK redirect_to_denied     expect=deny   got=http_403                     PASS
=== the credential must never enter the session ===
CHECK no_github_credential   expect=deny   got=placeholder_only             PASS
CHECK gh_write_denied        expect=deny   got=refused                      PASS
=== planted positive: the probe must be able to fail ===
CHECK planted_positive       expect=allow  got=http_200                     PASS

SUMMARY pass=12 fail=0 inconclusive=0
```

## The first run failed, and that is the point

The probe's first run reported `pass=10 fail=1 inconclusive=1`. Both were
defects in the probe, not in the boundary, and both were caught by the
structure rather than by inspection:

- **`planted_positive` FAILED.** It requested the bare
  `/repos/abedegno/muesli`, which the rule
  `GET api.github.com/repos/abedegno/muesli/**` does not match — the glob
  requires a segment beneath the repo. The probe was not exercising the
  allowed path at all, and it refused to certify the run. A suite without a
  planted positive would have reported eleven clean denials and been believed.
- **`unlisted_host` was INCONCLUSIVE.** An unlisted host is denied by Landlock
  at L4: there is no allowed TCP route, so the connection never completes. The
  probe treated every non-response as inconclusive, which is right in general
  and wrong here. It now distinguishes the two mechanisms, and counts a
  no-route as a denial **only when the control passed** — without a working
  network the same result would mean nothing.

## What this proves, and what it does not

**Proves.** A session launched from the v2 bundle, on the image the runner is
running now, can fetch the target repository and cannot push to it, open a
pull request, comment, edit a label, or reach the GraphQL API. Denials are
HTTP 403 from the egress proxy or an absent L4 route — not a dead network,
which the passing control rules out. No GitHub credential enters the session;
`gh` holds a host-bound placeholder, and possessing it does not confer write
authority.

**Does not prove.** Landlock's egress enforcement is **TCP only**. UDP, raw
sockets, and any socket connected before restriction are not confined. The
model provider (`api.anthropic.com`) is reachable by necessity, so the domain
is free of *GitHub* credentials, not of all credentials. And this is a
capability probe: it demonstrates the boundary holds against the operations
named here, not that the set is exhaustive. "Every mutation-capable command"
is not an enumerable set — which is why the coordinator-side routing in M1-4
is coverage evidence beside this, and not a substitute for it.
