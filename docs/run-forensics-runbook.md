# Bircher run forensics — evaluation runbook

How to reconstruct and evaluate a Bircher batch run after the fact: what to look at,
which APIs/scripts to use, and the pitfalls that will waste your time if you don't know
them. Written from the 2026-07-11 retrospective
([`2026-07-11-overnight-retro.md`](2026-07-11-overnight-retro.md)).

## What you're evaluating (the axes)

1. **Non-clean execution** — halts, reverts, coordinator/sub-agent deaths, CI reruns,
   multi-round reviews, noops.
2. **Bugs / missing tools** — errors in the batch log and, crucially, in the _sub-agent
   transcripts_ (skills/tools that failed to resolve).
3. **Deviations from context** — cross-vendor review actually happening, priority order,
   preflight, any protocol the run was supposed to follow.
4. **Performance** — per-item wall time, inter-item gaps, total span, idle time.

## The three evidence sources (in order of accessibility)

| Source                    | Where                                                                                               | Gives you                                                                                          |
| ------------------------- | --------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| **GitHub** (`gh`)         | issues + PRs + timeline                                                                             | outcomes, timing (labeled→closed), scorecard write-back comments, CI status, merge method          |
| **Batch log + scorecard** | on the runner: `/workspaces/<repo>/docs/agent-runs/scorecard.jsonl` and `/workspaces/bircher-*.log` | per-item `implementer`/`reviewer`, the halt sequence, recovery, notes, wall time                   |
| **omnigent transcripts**  | omnigent REST API (session items)                                                                   | what each coordinator + sub-agent actually did: tool calls, tool/skill errors, assistant narration |

The GitHub layer is self-serve; the other two require reaching the NAS runner and the
omnigent server (below).

## Reaching the runner and the omnigent server

The omnigent server is **not** exposed on the LAN — it's tunnel-only behind an access proxy (browser/Google auth), so plain `curl` from a workstation hits a 302 login wall,
and `<runner-host>:8000` is **the container manager**, not omnigent. The working path is the
ops helper, which authenticates to the container manager once and `docker exec`s inside the
runner container, where `http://omnigent:8000` is reachable on the internal docker
network with no auth:

```bash
# from the ops checkout — runs <cmd> inside the omnigent runner container
./omnigent.sh exec "<shell command>"
```

**Lockout rule:** each `omnigent.sh` invocation re-auths to the container manager, and hammering
the container manager auth locks it out. **Batch everything into as few `exec` calls as possible**
(one big script per call), and never loop `omnigent.sh` per item.

### Scorecard + logs (one call)

```bash
./omnigent.sh exec 'set +e;
  echo "@@SCORECARD@@"; cat /workspaces/muesli/docs/agent-runs/scorecard.jsonl;
  echo "@@LOGS@@";      ls -lt /workspaces/*.log | head -20;
  echo "@@SESSIONS@@";  curl -s "http://omnigent:8000/v1/sessions?limit=200&kind=any"'
```

Scorecard row schema (`json_row` in `run-queue.sh`):
`{ts,item,pr,outcome,ci_pass_first_try,review,rounds,wall_seconds,cost,bound,note}`.
Note **`bound` is a status flag (`ok`), not a session id** — do not try to map items to
sessions through it. Note also there is **no `implementer` field** (that's why
cross-vendor can't be audited from the scorecard — see retro #360). The `note` field
carries recovery detail (`RECOVERED: coordinator reaped…`) and review-round detail.

The batch log (`bircher-overnight*.log`) is the richest single artifact for the halt +
vendor picks. Grep it for:
`_pick_implementer|implementer=|HALT|revert|MAIN CI RED|died|runner_error|preflight|recover`.

## omnigent REST API (session transcripts)

Reference: `omnigent/server/API.md`. Base is `http://omnigent:8000` **from inside the
runner** (via `omnigent.sh exec`). No auth on the internal network. Endpoints that
matter for forensics:

| Call                                       | Use                                                                                                                      |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------ |
| `GET /v1/sessions?limit=200&kind=any`      | list sessions. **`kind=any`** is required to include sub-agent (child) sessions — the default `kind=default` hides them. |
| `GET /v1/sessions/{id}?include_items=true` | session snapshot: `agent_name`, `status`, `labels` (incl. `omnigent.last_task_error_code`/`_message`), and items.        |
| `GET /v1/sessions/{id}/items?limit=1000`   | the clean paginated transcript (`.data[]`) — **use this for text extraction.**                                           |

Session naming makes the run legible without opening each one: each item is a
coordinator titled `IMPLEMENTER VENDOR DIRECTIVE: …` plus two children,
`codex:<item>` (implementer) and `claude_code:review-<item>` (reviewer). The
`labels.omnigent.last_task_error_message` is where a killed session's cause lives
(e.g. the 240s idle-watchdog message).

### Pitfalls (each cost real time here)

- **`limit` max is 1000.** `?limit=2000` returns a `422` validation error with an empty
  `.data` — which silently looks like "no matching content." Use `limit=1000`.
- **Transcript endpoint differs by session kind.** For these session-backed
  conversations, `GET /v1/conversations/{id}/items` returns `not_found`; the items live
  under `GET /v1/sessions/{id}/items` (and the snapshot's `.items`). The `.items` in the
  snapshot store text in a different internal shape than the `/items` endpoint's
  `.data` — **extract from `/items` `.data[]`**, whose messages carry
  `content[].text`, function calls carry `name`/`arguments`, outputs carry `output`.
- **Don't do jq text-extraction _through_ `omnigent.sh exec`.** The
  `exec → the container manager → shell → jq` layering mangles `\"`, `\n`, and `\(...)` and yields
  empty output. **Pull raw JSON out and parse locally** (Python). One `exec` that just
  `curl`s each transcript with an `@@@ID:<id>` delimiter, then a local parser.
- **Filter code-content noise.** Grepping transcripts for `not found` matches the docs
  the agents were _writing_ (`404 not found`). The genuine signals are narrow:
  `skill '…' not found. Available skills: […]`, `No such tool available: …`,
  `<tool_use_error>`.

### Local parse pattern

```python
# split the raw dump on @@@ID: markers, json.loads each, then:
def alltext(data):
    s=[]
    for i in data:
        for c in (i.get('content') or []):
            if c.get('text'): s.append(c['text'])
        if i.get('output') is not None: s.append(str(i['output']))
    return "\n".join(s)
# tool/skill failures worth surfacing:
#   re: skill '([^']+)' not found\. Available skills: (\[[^\]]*\])
#   re: No such tool available: (\w+)
#   function_call .name  -> which tools were actually invoked (and how often)
```

## Cross-checking outcomes on GitHub

```bash
# scorecard write-back per item (the comment Bircher posts):
gh issue view <n> --json comments \
  --jq '[.comments[]|select(.body|startswith("bircher:"))]|.[-1].body'
# per-item timing:
gh api repos/<owner>/<repo>/issues/<n>/timeline \
  --jq '[.[]|select(.event=="labeled" and .label.name=="bircher:running")]|.[-1].created_at'
# how a PR merged + whether it auto-closed its issue:
gh pr view <pr> --json mergedAt,closingIssuesReferences
```

`gh issue list --label` uses an eventually-consistent search index (lags seconds); for
exact current state prefer per-issue `gh issue view`.

## Recipe (minimize NAS round-trips)

1. **GitHub sweep** (self-serve): outcomes, timing, scorecard comments, per-item CI.
2. **One `omnigent.sh exec`**: scorecard.jsonl + `ls /workspaces/*.log` + session list.
3. **One `omnigent.sh exec`**: `cat` both `bircher-overnight*.log` → grep vendor/halt.
4. **One `omnigent.sh exec`**: `curl … /v1/sessions/{id}/items?limit=1000` for every
   run-window session (delimited raw JSON) → parse + grep **locally**.
5. Synthesize against the four axes; file issues; write the retro.

Keep it to ~3–4 `exec` calls total. Everything else is local parsing.
