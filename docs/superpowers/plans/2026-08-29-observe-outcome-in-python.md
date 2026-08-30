# `observe_outcome` in Python Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** move the outcome derivation — the 192 lines that decide what every item did — out of `batch/run-queue.sh` and into `v2/coordinator/`, owning its own loops in one long-lived process.

**Architecture:** every rule it needs is already Python (CI classification, verdict reading, PR selection, the poll loop, the effect path). What remains is four I/O helpers and the orchestration itself. `run-queue.sh` invokes it once per item and parses the same seven-field tuple it does today.

**Tech Stack:** Python 3.11+ in `v2/coordinator/`, `gh` for GitHub, the v2 kernel for effects.

**Spec:** `docs/superpowers/specs/2026-08-29-coordinator-effect-path-design.md` (the effect half), and the C8 Phase 2 record for what the derivation must produce.

## Global Constraints

- **The outcome vocabulary is unchanged:** `ready`, `escalated`, `noop`, `failed`, `timeout`, `skipped`.
- **The tuple is seven fields**, in order: `outcome|review|note|head|ci|ci_first|resubmissions`. A caller reading six absorbs the last into its neighbour.
- **Every ported rule keeps a differential test** against the bash it replaces, frozen at replacement time. Two implementations without one is how a port diverges silently.
- **It must own its loops in ONE process.** `_coordinator` wraps calls in `_net_run` at `BIRCHER_KERNEL_TIMEOUT` (5s), which would kill a 1500-second CI watch. This gets its own invocation with the item's own bound.
- **Effects go through `coordinator.effects.perform_effect`**, never a second path. Two entry points already exist; a third would be one too many.
- Mutation-prove every guard: commit first, one mutation at a time, prove it applied, restore with `git checkout`, confirm clean.
- Run `python3 v2/tools/repoint-scar-citations.py` before each commit; `run-queue.sh` shrinking will move every citation below it.

---

## What this plan is NOT allowed to do

**Change behaviour while moving it.** Every difference between the bash and the Python must be either (a) covered by a differential test proving there is none, or (b) written down as a deliberate change with its reason. A port that quietly improves something is a port nobody can review.

The one exception is stated up front so it is not smuggled: the bash posts its comment through `_coordinator effect`, which the Python will call directly. Same `perform()`, same journal — a shorter path to the same place.

---

### Task 1: the two cached/aggregating I/O helpers

**Files:**
- Modify: `v2/coordinator/ci.py`
- Test: `v2/tests/coordinator/test_ci_and_review.py` (append)

**Interfaces:**
- Produces: `required_contexts(repo, *, gh, cache=None) -> str`, `failure_kind(pr, *, gh) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
def test_required_contexts_unions_both_shapes():
    """Branch protection reports required checks under TWO keys, and a repo can
    use either. Reading one would leave the other's checks non-blocking."""
    payload = {"required_status_checks": {
        "contexts": ["build"],
        "checks": [{"context": "test"}, {"context": "build"}]}}
    gh = lambda args: json.dumps(payload)
    assert sorted(required_contexts("o/r", gh=gh).split()) == ["build", "test"]


def test_required_contexts_is_EMPTY_when_the_lookup_fails():
    """Empty means "everything blocks" downstream, which is the conservative
    reading. Inventing a context list would make real checks non-blocking."""
    def boom(args):
        raise GhError("404")
    assert required_contexts("o/r", gh=boom) == ""


def test_required_contexts_is_fetched_once():
    calls = []
    def gh(args):
        calls.append(args)
        return json.dumps({"required_status_checks": {"contexts": ["build"]}})
    cache = {}
    required_contexts("o/r", gh=gh, cache=cache)
    required_contexts("o/r", gh=gh, cache=cache)
    assert len(calls) == 1, "branch protection is fetched once per run"


def test_a_red_run_with_failed_steps_is_genuine():
    def gh(args):
        if args[0] == "pr":
            return "build|https://github.com/o/r/actions/runs/1"
        return json.dumps({"jobs": [{"conclusion": "failure",
                                     "steps": [{"conclusion": "failure"}]}]})
    assert failure_kind("7", gh=gh) == "genuine"


def test_a_red_run_with_NO_failed_step_is_infrastructure():
    """B-5: a runner never acquired, or a cancelled job, fails with zero failed
    steps. Burying those as `failed` buried three green PRs in one incident."""
    def gh(args):
        if args[0] == "pr":
            return "build|https://github.com/o/r/actions/runs/1"
        return json.dumps({"jobs": [{"conclusion": "cancelled", "steps": []}]})
    assert failure_kind("7", gh=gh) == "infra"


def test_an_unreadable_run_is_GENUINE_not_infra():
    """Fails toward NOT re-running. `infra` triggers a re-run that costs CI
    minutes; if we cannot see why a run failed, spending them is a guess."""
    def gh(args):
        if args[0] == "pr":
            return "build|https://github.com/o/r/actions/runs/1"
        raise GhError("boom")
    assert failure_kind("7", gh=gh) == "genuine"
```

- [ ] **Step 2: Run and watch them fail** — `ImportError: cannot import name 'required_contexts'`.

- [ ] **Step 3: Implement**

```python
# `os` and `json` are already imported at the top of ci.py.
def required_contexts(repo: str, *, gh=_gh, cache=None) -> str:
    """The contexts branch protection requires, newline separated.

    UNIONS both shapes. Protection reports required checks under
    `contexts` (legacy) and `checks[].context` (current), and a repo may use
    either; reading one would leave the other's checks non-blocking.

    EMPTY on any failure, which downstream means "everything blocks" -- the
    conservative reading. Inventing a list would silently make real checks
    non-blocking, which is the failure that cannot be noticed.
    """
    if cache is not None and "v" in cache:
        return cache["v"]
    try:
        branch = (os.environ.get("MAIN_BRANCH") or "main")
        d = json.loads(gh(["api", f"repos/{repo}/branches/{branch}/protection"]))
        rsc = d.get("required_status_checks") or {}
        names = set(rsc.get("contexts") or [])
        names |= {c.get("context") for c in (rsc.get("checks") or []) if c.get("context")}
        out = "\n".join(sorted(n for n in names if n))
    except (GhError, ValueError, AttributeError):
        out = ""
    if cache is not None:
        cache["v"] = out
    return out


def failure_kind(pr: str, *, gh=_gh) -> str:
    """`genuine` | `infra` for a RED pr, by counting failed STEPS.

    Fails toward `genuine`: `infra` triggers a re-run that costs CI minutes, so
    an unreadable run must not spend them on a guess.
    """
    try:
        links = gh(["pr", "checks", str(pr), "--json", "name,link",
                    "-q", r'.[] | "\(.name)|\(.link)"'])
    except GhError:
        return "genuine"
    ids = run_ids_from_links(links)
    if not ids:
        return "genuine"
    total = 0
    for rid in ids:
        try:
            jobs = json.loads(gh(["run", "view", rid, "--json", "jobs"])).get("jobs") or []
        except (GhError, ValueError, AttributeError):
            return "genuine"
        for job in jobs:
            if job.get("conclusion") in ("failure", "cancelled"):
                total += sum(1 for s in (job.get("steps") or [])
                             if s.get("conclusion") == "failure")
    return classify_failure(total)
```

- [ ] **Step 4: Run the tests** — expect PASS, and the full suite green.

- [ ] **Step 5: Commit.**

- [ ] **Step 6: Mutate**

| mutation | must red |
|---|---|
| read only `contexts`, not `checks[].context` | `test_required_contexts_unions_both_shapes` |
| return a non-empty default on lookup failure | `test_required_contexts_is_EMPTY_when_the_lookup_fails` |
| drop the cache | `test_required_contexts_is_fetched_once` |
| count failed JOBS instead of failed STEPS | `test_a_red_run_with_NO_failed_step_is_infrastructure` |
| return `infra` on an unreadable run | `test_an_unreadable_run_is_GENUINE_not_infra` |

---

### Task 2: discovery by issue linkage

**Files:**
- Create: `v2/coordinator/discovery.py`
- Test: `v2/tests/coordinator/test_discovery.py` (create)

**Interfaces:**
- Consumes: `matches_code` from `pr_selection`.
- Produces: `closes_issue(body, issue) -> bool`, `by_issue(repo, issue, *, gh) -> list[str]`, `by_code(repo, code, *, gh) -> list[str]`.

The closing-keyword pattern is the scar: it recovers the run #24 class where branch AND signal used the wrong code but the body write-back was right.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("body,expected", [
    ("Closes #711", True), ("closes #711", True), ("Closed #711", True),
    ("Fixes #711", True), ("fixed #711", True), ("Resolves #711", True),
    ("resolve #711", True), ("Closes: #711", True), ("Closes:#711", True),
    ("Closes #7110", False),          # boundary
    ("Closes #71", False),
    ("mentions #711 in passing", False),
    ("Related to #711", False),       # not a closing keyword
    ("", False),
])
def test_only_a_closing_keyword_links_an_issue(body, expected):
    assert closes_issue(body, "711") is expected


def test_the_issue_number_is_not_a_pattern():
    assert closes_issue("Closes #7x1", "7.1") is False
```

- [ ] **Step 2: Run and watch it fail.**

- [ ] **Step 3: Implement**

```python
_CLOSES = r"(?i)\b(close[sd]?|fix(e[sd])?|resolve[sd]?)\s*:?\s*#"


def closes_issue(body: str, issue: str) -> bool:
    """Does this PR body CLOSE the issue, not merely mention it?

    "Related to #711" is not a link. Treating a mention as one would adopt any
    PR that referenced the issue in passing.
    """
    if not issue:
        return False
    return bool(re.search(_CLOSES + re.escape(issue) + r"\b", body or ""))
```

- [ ] **Step 4-6:** run, commit, mutate.

| mutation | must red |
|---|---|
| drop `\b` after the issue number | the `#7110` boundary case |
| add `relate[sd]?` to the keyword set | `test_only_a_closing_keyword_links_an_issue[Related to #711-False]` |
| drop `re.escape` | `test_the_issue_number_is_not_a_pattern` |

---

### Task 3: sibling reconciliation

**Files:**
- Modify: `v2/coordinator/discovery.py`
- Test: `v2/tests/coordinator/test_discovery.py` (append)

**Interfaces:**
- Consumes: `perform_effect`, `poll`/`normalize`, `matches_code`.
- Produces: `reconcile(repo, code, tracked, *, gh, ci_of, close) -> str`.

**This task performs effects** — it closes superseded PRs. `close` is injected so tests assert what it closed without touching GitHub.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_single_match_is_returned_untouched():
    closed = []
    assert reconcile("o/r", "i23", "5", gh=_listing(["5"]),
                     ci_of=lambda n: "green", close=closed.append) == "5"
    assert closed == []


def test_a_ci_GREEN_sibling_is_adopted_and_the_others_closed():
    """Run #20 #141: a CI-red retry opened a second PR before the coordinator
    died. Adopting the green one is the recovery; leaving the red one open is
    a second PR nobody closes."""
    closed = []
    got = reconcile("o/r", "i23", "5", gh=_listing(["5", "6"]),
                    ci_of=lambda n: "green" if n == "6" else "red",
                    close=closed.append)
    assert got == "6"
    assert closed == ["5"]


def test_with_no_green_sibling_the_tracked_pr_is_kept_and_nothing_is_closed():
    """Closing on no evidence would destroy the only candidate."""
    closed = []
    got = reconcile("o/r", "i23", "5", gh=_listing(["5", "6"]),
                    ci_of=lambda n: "red", close=closed.append)
    assert got == "5" and closed == []


def test_the_adopted_pr_is_never_closed():
    closed = []
    reconcile("o/r", "i23", "5", gh=_listing(["5", "6"]),
              ci_of=lambda n: "green", close=closed.append)
    assert "5" not in closed or "6" not in closed
    assert len(closed) == 1
```

- [ ] **Steps 2-6:** watch fail, implement, run, commit, mutate.

| mutation | must red |
|---|---|
| close every match including the adopted one | `test_the_adopted_pr_is_never_closed` |
| adopt the first match rather than the green one | `test_a_ci_GREEN_sibling_is_adopted_and_the_others_closed` |
| close siblings even when none is green | `test_with_no_green_sibling_...` |

---

### Task 4: the derivation itself

**Files:**
- Create: `v2/coordinator/outcome.py`
- Test: `v2/tests/coordinator/test_outcome.py` (create)

**Interfaces:**
- Consumes: everything above, plus `observe.classify`, `ci.poll`, `review.extract_verdict`, `effects.perform_effect`.
- Produces: `derive(item, code, pr, issue, *, deps) -> Derived` with the seven fields.

**This is the task that changes the working path.** Everything before it is additive.

- [ ] **Step 1: Write the failing tests**

Every dependency arrives in one injected object, so each test drives the real
`derive` rather than a rearrangement of it.

```python
@dataclass
class _Deps:
    """Everything derive() reaches the world through."""
    checks: callable          # pr -> bucket text
    head_of: callable         # pr -> sha
    review: callable          # (pr, sha) -> "PASS" | "FAIL" | None
    effect: callable          # (cls, key, argv) -> str
    failure_kind: callable = lambda pr: "genuine"
    rerun: callable = lambda pr: "red"
    history: callable = lambda br: ("true", 0)
    branch_of: callable = lambda pr: "feat-x"
    pr_state: callable = lambda pr: ("OPEN", "")
    discover: callable = lambda code, issue: []
    required: str = ""


def _deps(**over):
    posted = []
    base = dict(checks=lambda pr: "build|pass",
                head_of=lambda pr: "a" * 40,
                review=lambda pr, sha: "PASS",
                effect=lambda c, k, a: posted.append((c, k, a)) or "ok")
    base.update(over)
    d = _Deps(**base)
    d.posted = posted
    return d


def test_the_tuple_has_seven_fields_in_order():
    r = derive("i1", "i1", "7", "", deps=_deps())
    assert len(r.as_tuple()) == 7
    assert r.as_tuple()[0] == "ready"
    assert r.as_tuple()[5:] == ("true", 0)


def test_a_red_pr_never_reaches_the_reviewer():
    """CI is checked BEFORE the verdict. A PASS on a red PR must not merge,
    and dispatching a reviewer at all wastes a run on a foregone answer."""
    asked = []
    d = _deps(checks=lambda pr: "build|fail",
              review=lambda pr, sha: asked.append(pr) or "PASS")
    r = derive("i1", "i1", "7", "", deps=d)
    assert r.outcome == "failed" and asked == []


def test_the_reviewed_sha_is_captured_BEFORE_the_review_is_dispatched():
    """#66. The head travels INTO the reviewer; it is never re-read afterwards.
    A push landing mid-review would otherwise be blessed as reviewed."""
    order = []
    d = _deps(head_of=lambda pr: order.append("head") or "b" * 40,
              review=lambda pr, sha: order.append(f"review:{sha}") or "PASS")
    r = derive("i1", "i1", "7", "", deps=d)
    assert order == ["head", "review:" + "b" * 40]
    assert r.sha == "b" * 40


def test_a_non_40_hex_head_yields_an_EMPTY_sha_field():
    """An unpinnable head must not authorise a merge. Empty means "cannot
    pin", which the caller reads as "cannot auto-merge"."""
    r = derive("i1", "i1", "7", "", deps=_deps(head_of=lambda pr: "nope"))
    assert r.sha == ""


def test_an_infra_red_is_re_run_at_most_the_configured_number_of_times():
    tries = []
    d = _deps(checks=lambda pr: "build|fail",
              failure_kind=lambda pr: "infra",
              rerun=lambda pr: tries.append(pr) or "red")
    derive("i1", "i1", "7", "", deps=d, rerun_max=2)
    assert len(tries) == 2, tries


def test_the_comment_is_posted_through_the_effect_path():
    d = _deps()
    derive("i1", "i1", "7", "", deps=d)
    assert [c for c, _k, _a in d.posted] == ["comment"]


def test_the_comment_carries_no_bircher_status_line():
    d = _deps()
    derive("i1", "i1", "7", "", deps=d)
    body = " ".join(str(a) for _c, _k, a in d.posted)
    assert "bircher-status" not in body


def test_an_abandoned_tracked_pr_is_dropped_and_rediscovered():
    d = _deps(pr_state=lambda pr: ("CLOSED", "") if pr == "7" else ("OPEN", ""),
              discover=lambda code, issue: ["9"])
    assert derive("i1", "i1", "7", "", deps=d).pr == "9"


def test_no_pr_anywhere_is_a_timeout():
    d = _deps(discover=lambda code, issue: [])
    r = derive("i1", "i1", "", "", deps=d)
    assert (r.outcome, r.ci) == ("timeout", "na")
```

The sha test is #66 and must not be dropped: the reviewed head is captured from
the ref handed to the reviewer, never re-read afterwards, or a push landing
mid-review would be blessed as reviewed.

- [ ] **Step 2: Run and watch them fail.**

- [ ] **Step 3: Implement**, porting the bash structure unchanged. Dependencies arrive in one injected object so every test drives the real function.

- [ ] **Step 4: Run the tests and the full suite.**

- [ ] **Step 5: Commit.**

- [ ] **Step 6: Mutate**

| mutation | must red |
|---|---|
| emit six fields | `test_the_tuple_has_seven_fields_in_order` |
| dispatch the reviewer on a red PR | `test_a_red_pr_never_reaches_the_reviewer` |
| re-read the head AFTER the review | `test_the_reviewed_sha_is_captured_BEFORE...` |
| accept a short head as the sha | `test_a_non_40_hex_head_yields_an_EMPTY_sha_field` |
| classify without waiting on pending CI | `test_a_PENDING_ci_is_waited_on_before_classifying` |
| drop the re-run cap | `test_an_infra_red_is_re_run_at_most...` |

---

### Task 5: invoke it from `run-queue.sh`

**Files:**
- Modify: `v2/coordinator/cli.py`, `batch/run-queue.sh`
- Test: `v2/tests/execution/test_observe_execution.py` (rewrite)

- [ ] **Step 1:** add `derive --item --code --pr --issue`, printing the seven-field tuple.

- [ ] **Step 2:** replace `observe_outcome`'s body with a call that does **not** go through `_coordinator`:

```bash
observe_outcome() {
  # NOT via `_coordinator`: that wraps every call in `_net_run` at
  # BIRCHER_KERNEL_TIMEOUT (5s), and this legitimately runs as long as CI does.
  # Bounded by the item's own budget instead.
  PYTHONPATH="$(_kernel_pythonpath)" \
    _net_run "${BIRCHER_DERIVE_TIMEOUT:-1800}" \
    "${BIRCHER_PY:-python3}" -m coordinator.cli derive \
      --item "$1" --code "$2" --pr "${3:-}" --issue "${4:-}"
}
```

- [ ] **Step 3:** delete the bash bodies of `_discover_pr_by_issue`, `_reconcile_item_pr`, `_required_contexts`, `_ci_failure_kind`, `_wait_ci`, `_poll_ci`, `_rerun_and_wait_ci`, `_ci_run_ids`, `_run_ids_from_check_links`, `_drop_non_ci_checkruns` — **and name each one's replacement in the commit message.** A deleted test is a coverage change, not a cleanup.

- [ ] **Step 4:** run the full suite and `--self-test`. Expect the self-test to shrink: several of its blocks test functions that no longer exist.

- [ ] **Step 5: Commit** with the replacement table.

---

### Task 6: prove it on the throwaway repo

Operational, on `abedegno/bircher-smoke`. **Must not run against `abedegno/muesli`.**

- [ ] **Step 1:** deploy, queue one trivial item, run `batch/launch.sh --source queue`.
- [ ] **Step 2:** assert it merges, with no `bircher-status:` anywhere.
- [ ] **Step 3:** assert the scorecard row has all seven fields populated as before, and compare it field by field against `s08`'s row — the last item derived by the bash. **A difference is either explained or a defect.**
- [ ] **Step 4:** read the kernel journal and confirm the comment effect was performed from Python (`effect_confirmed`, class `comment`), and that command/effect counts match `s08`'s.
- [ ] **Step 5:** record it in the acceptance record, including the wall-clock against `s08`'s 318s.

---

## Done means

`observe_outcome` is Python, owns its loops in one process, and `run-queue.sh`
calls it once per item. Ten bash functions are deleted with their replacements
named. The seven-field tuple is unchanged, proved by comparing a live scorecard
row against `s08`'s field by field. Every ported rule has a differential test
against the bash it replaced, and every guard introduced has a mutation that
reds it.

**Not delivered, and stated so nobody infers otherwise:** `merge_ready_pr` and
the status publication stay in bash. They are the next orchestrator, and they
share `_required_contexts` and the CI helpers this plan moves — so they will
call Python for those and keep their own loops until they move too.
