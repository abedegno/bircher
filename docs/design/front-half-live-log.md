# Front half — the live log

What actually ran on the runner, in order, with the ids to look it up by. E1 is the
deployment precondition; E2 to E4 are the three live exercises the design's §8 proof
is run over.

## E1 — the deployment preconditions

*2026-09-08.*

### The codex-under-Landlock probe

The question was whether the codex harness can run at all inside the `linux_landlock`
sandbox the v2 author bundle declares, with only its model provider reachable. If it
cannot, every codex-authored round is impossible and the cross-vendor claim goes with
it, so this is probed before anything is queued.

| | |
|---|---|
| Bundle | `agents/v2_author_codex` (`harness: codex`, `model: gpt-5.6-sol`) |
| Holder session | `6dc645316bc14ae4ae7f62dc1314d100` |
| Agent | `c167fd8944894d77974c435f0b688a7b` |
| Probe session | `d6c30fff3e8441cb9e991346531773df` |
| Workspace | `/workspaces/probe-codex`, a detached worktree of `bircher-smoke` at `25a979e` |
| Instruction | create the directory `bircher`, write the single line `hello` to `bircher/artifact.md`, end the turn |
| Result | the file exists and holds exactly `hello\n`, six bytes; the session went `running` then `idle` |

**No egress rule needed correcting.** The bundle's declared list — `api.openai.com/**`
and `chatgpt.com/**` — was enough for the harness to boot, take its turn and write the
file. The proxy denied nothing, so `agents/v2_author_codex/config.yaml` is unchanged.

The probe also settles a smaller question the design leaned on: a session created
against a worktree path writes into that worktree and nowhere else. The write landed
inside `/workspaces/probe-codex`.

### The turn timeout

`HARNESS_TURN_TIMEOUT_S` on `omnigent-runner-bircher` was 480, an idle watchdog set for
v1's per-item turns. A v2 author or reviewer turn is a whole session the coordinator
waits on and ends itself, under a deadline it records in its own journal. A watchdog
shorter than that deadline ends the turn underneath the coordinator, which then reads a
file the session never finished writing — so the two bounds must not compete, and the
coordinator's cap is the one that should bite.

Raised to 5400, equal to `ITEM_TIMEOUT`, in `abedegno/homelab` at
`docker/omnigent/docker-compose.yml`, and the stack redeployed through Portainer.
Only `omnigent-runner-bircher` was recreated; the server, the second runner, postgres
and cloudflared were untouched. Confirmed from inside the container:

```
HARNESS_TURN_TIMEOUT_S=5400
OMNIGENT_RUNNER_ENV_PASSTHROUGH=…,HARNESS_TURN_TIMEOUT_S,…
```

The second line matters as much as the first: the runner strips any variable not in
that allowlist before it reaches an agent shell, so a raised timeout that is not
threaded through it never reaches the harness that reads it.

### How the branch reached the runner

`/workspaces/bircher-v2` was a clone of the older `v2` branch. It is now on
`feat/front-half`, fetched from a git bundle copied into the container rather than
from GitHub: the branch is unpushed, and deploying a test checkout is not a reason to
publish 53 commits. `/workspaces/bircher` is untouched and still runs v1 from `main`.
