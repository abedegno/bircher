# Bircher run forensics — evaluation runbook

How to reconstruct and evaluate a Bircher batch run after the fact: what to look at,
which APIs and scripts to use, and the pitfalls that waste time if you don't know them.

## What you're evaluating (the axes)

1. **Non-clean execution** — halts, reverts, coordinator/sub-agent deaths, CI reruns,
   multi-round reviews, noops.
2. **Bugs / missing tools**: errors in the batch log, and — the ones people miss — errors
   in the _sub-agent transcripts_, where skills or tools failed to resolve.
3. **Deviations from context** — cross-vendor review actually happening, priority order,
   preflight, any protocol the run was supposed to follow.
4. **Performance** — per-item wall time, inter-item gaps, total span, idle time.

## The three evidence sources (in order of accessibility)

| Source                    | Where                                                                                               | Gives you                                                                                          |
| ------------------------- | --------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| **GitHub** (`gh`)         | issues + PRs + timeline                                                                             | outcomes, timing (labeled→closed), scorecard write-back comments, CI status, merge method          |
| **Batch log + scorecard** | on the runner: `$BUNDLE_DIR/.run/scorecard.jsonl` and the run log (`run.log` by default)             | per-item `implementer`/`reviewer`, the halt sequence, recovery, notes, wall time                   |
| **omnigent transcripts**  | omnigent REST API (session items)                                                                   | what each coordinator + sub-agent actually did: tool calls, tool/skill errors, assistant narration |

The GitHub layer is self-serve; the other two require reaching the runner host and the
omnigent server (below).

## Reaching the runner and the omnigent server

Two of the three evidence sources live on the runner, so you need a way to run commands
inside the runner container. How you get there depends on your deployment.

If your omnigent server sits behind an authenticating proxy, a plain `curl` from a
workstation will hit a login redirect rather than the API. The reliable route is to
execute inside the runner container, where the server is reachable on the internal
network without going through the proxy at all:

```bash
# whatever your deployment uses to run a command inside the runner container
<your-exec-helper> "<shell command>"

# from in there, the server is just:
curl -s "$OMNIGENT_SERVER/v1/sessions?limit=200&kind=any"
```

**Batch your commands.** If your exec helper re-authenticates on every invocation — a
container manager API, for instance — calling it in a loop can trip rate limiting and
lock you out for a while. Put everything into as few calls as possible: one script per
call, never one call per item. This is the single most common way to turn a ten-minute
investigation into an hour.

### Scorecard + logs (one call)

```bash
<your-exec-helper> 'set +e;
  echo "@@SCORECARD@@"; cat "$BUNDLE_DIR/.run/scorecard.jsonl";
  echo "@@LOGS@@";      ls -lt "$BUNDLE_DIR"/*.log | head -20;
  echo "@@SESSIONS@@";  curl -s "$OMNIGENT_SERVER/v1/sessions?limit=200&kind=any"'
```

Scorecard row schema (`json_row` in `run-queue.sh`):
`{ts,item,pr,outcome,ci_pass_first_try,review,rounds,wall_seconds,cost,bound,note,implementer}`.

Two things to know about it. **`bound` is a status flag (`ok`), not a session id**, so
don't try to map items to sessions through it. And `implementer` records the vendor that
wrote the code, which together with `review` (`<vendor>:<verdict>`) is what makes the
cross-vendor pairing auditable from the scorecard alone. It is `null` on rows written
before an implementer was chosen.

The `note` field carries recovery detail (`RECOVERED: coordinator reaped…`) and
review-round detail.

The run log is the richest single artifact for the halt and the vendor picks. Grep it
for:
`_pick_implementer|implementer=|HALT|revert|MAIN CI RED|died|runner_error|preflight|recover`.

## omnigent REST API (session transcripts)

Reference: `omnigent/server/API.md`. Base is `http://omnigent:8000` **from inside the
runner** (via your exec helper). No auth on the internal network. Endpoints that
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
- **Don't do jq text-extraction _through_ the exec helper.** The
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

## Recipe (minimise round-trips to the runner)

1. **GitHub sweep** (self-serve): outcomes, timing, scorecard comments, per-item CI.
2. **One exec call**: scorecard.jsonl + `ls "$BUNDLE_DIR"/*.log` + session list.
3. **One exec call**: `cat` the run log(s) → grep vendor/halt.
4. **One exec call**: `curl … /v1/sessions/{id}/items?limit=1000` for every
   run-window session (delimited raw JSON) → parse + grep **locally**.
5. Synthesize against the four axes; file issues; write the retro.

Keep it to ~3–4 `exec` calls total. Everything else is local parsing.
