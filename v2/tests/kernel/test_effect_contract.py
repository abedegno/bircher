"""An effect must do what its class says, on the object that was authorized.

Round 6, findings C1 and C2 — found independently by the author and by codex.

C1: nothing compared the effect CLASS to the argv, so `_effect comment k -
    gh pr merge 123` journalled a comment and performed a merge. Because the
    class was not `merge`, `revalidate_merge` never ran at all: the
    authority-bearing gate was opt-in by the caller.

C2: even with the class declared honestly, the argv was caller-supplied and
    unchecked. A run legitimately authorized to merge its own artifact
    executed `gh pr merge 9999 --repo attacker/other --admin`.

Both are the same shape as every other finding in this programme: a real,
correctly implemented check that constrains something the caller chooses.
"""

import pytest

from kernel.artifacts import put_artifact
from kernel.authz import NotAuthorized
from kernel.commands import Command, submit
from kernel.dispatch import Role, dispatch
from kernel.effects import EffectClass, perform
from kernel.ids import Clock
from kernel.store import Store

BASE, HEAD, BUNDLE = "c" * 40, "d" * 40, "e" * 64
REPO, PR = "abedegno/muesli", 42


def _sub(s, name, key, actor, role, **p):
    return submit(s, Command(
        name=name, run_id="r", expected_version=s.run_version("r"),
        idempotency_key=key,
        generation=dispatch(s, "r", actor=actor, role=role).generation, payload=p))


def _authorized():
    s = Store.open(":memory:", clock=Clock(start_us=1))
    s.create_run(run_id="r", base_repo="o/r", base_sha=BASE)
    spec = put_artifact(s, b"# spec")
    _sub(s, "submit_spec", "k1", "claude", Role.IMPLEMENTER, spec_sha256=spec)
    _sub(s, "submit_plan", "k2", "claude", Role.IMPLEMENTER, plan_sha256=spec)
    _sub(s, "start_implementation", "k3", "claude", Role.IMPLEMENTER)
    out = put_artifact(s, b"diff v1")
    _sub(s, "record_implementation_output", "k4", "claude", Role.IMPLEMENTER,
         artifact_hash=out)
    _sub(s, "record_ci_observation", "k5", "claude", Role.IMPLEMENTER,
         status="success", head_git_sha=HEAD)
    _sub(s, "record_review", "k6", "codex", Role.REVIEWER, verdict="accept",
         artifact_hash=out, base_sha=BASE, context_bundle_hash=BUNDLE,
         policy_version=1)
    _sub(s, "request_merge", "k7", "claude", Role.IMPLEMENTER, head_git_sha=HEAD,
         artifact_hash=out, base_sha=BASE, context_bundle_hash=BUNDLE,
         policy_version=1, pr=PR, repo=REPO)
    return s


def _perform(s, cls, key, argv, run="r"):
    gen = dispatch(s, run, actor="claude", role=Role.IMPLEMENTER).generation
    ran = []
    out = perform(s, run, gen, cls, key, {"argv": argv},
                  lambda c, i, k: ran.append(i["argv"]) or "done")
    return out, ran


# --- C1: the class must match the argv ---------------------------------------

def test_a_merge_labelled_as_a_comment_is_refused():
    """THE BYPASS. Mislabelling skipped revalidate_merge entirely."""
    s = _authorized()
    with pytest.raises(NotAuthorized, match="does not match"):
        _perform(s, EffectClass.COMMENT, "sneaky",
                 ["gh", "pr", "merge", "123"])


def test_a_push_labelled_as_a_comment_is_refused():
    s = _authorized()
    with pytest.raises(NotAuthorized, match="does not match"):
        _perform(s, EffectClass.COMMENT, "sneaky2",
                 ["git", "push", "origin", "HEAD:main"])


def test_an_honest_comment_is_allowed():
    """The control. Refusing everything would pass both tests above."""
    s = _authorized()
    out, ran = _perform(s, EffectClass.COMMENT, "honest",
                        ["gh", "pr", "comment", "7", "--body", "hi"])
    assert out == "done" and ran


def test_an_argv_matching_no_contract_is_refused():
    """Fail closed on a shape the kernel does not recognise, rather than
    passing it through because no rule said no."""
    s = _authorized()
    with pytest.raises(NotAuthorized, match="does not match"):
        _perform(s, EffectClass.COMMENT, "weird", ["curl", "https://evil/"])


# --- C2: the merge must target what was authorized ---------------------------

def test_a_merge_of_a_different_pr_is_refused():
    """THE OTHER HALF. The run is legitimately authorized; the argv is not."""
    s = _authorized()
    with pytest.raises(NotAuthorized, match="authorized"):
        _perform(s, EffectClass.MERGE, "wrong-pr",
                 ["gh", "pr", "merge", "9999", "--repo", REPO])


def test_a_merge_in_a_different_repo_is_refused():
    s = _authorized()
    with pytest.raises(NotAuthorized, match="authorized"):
        _perform(s, EffectClass.MERGE, "wrong-repo",
                 ["gh", "pr", "merge", str(PR), "--repo", "attacker/other"])


def test_the_authorized_merge_executes():
    """The control."""
    s = _authorized()
    out, ran = _perform(s, EffectClass.MERGE, "right",
                        ["gh", "pr", "merge", str(PR), "--repo", REPO])
    assert out == "done"
    assert ran[0][:4] == ["gh", "pr", "merge", str(PR)]


def test_request_merge_must_name_its_pr_and_repo():
    """The authorization has to record a target, or there is nothing for the
    effect to be bound to."""
    s = Store.open(":memory:", clock=Clock(start_us=1))
    s.create_run(run_id="r", base_repo="o/r", base_sha=BASE)
    spec = put_artifact(s, b"# spec")
    _sub(s, "submit_spec", "k1", "claude", Role.IMPLEMENTER, spec_sha256=spec)
    _sub(s, "submit_plan", "k2", "claude", Role.IMPLEMENTER, plan_sha256=spec)
    _sub(s, "start_implementation", "k3", "claude", Role.IMPLEMENTER)
    out = put_artifact(s, b"diff")
    _sub(s, "record_implementation_output", "k4", "claude", Role.IMPLEMENTER,
         artifact_hash=out)
    _sub(s, "record_ci_observation", "k5", "claude", Role.IMPLEMENTER,
         status="success", head_git_sha=HEAD)
    _sub(s, "record_review", "k6", "codex", Role.REVIEWER, verdict="accept",
         artifact_hash=out, base_sha=BASE, context_bundle_hash=BUNDLE,
         policy_version=1)
    with pytest.raises(NotAuthorized, match="pr"):
        _sub(s, "request_merge", "k7", "claude", Role.IMPLEMENTER,
             head_git_sha=HEAD, artifact_hash=out, base_sha=BASE,
             context_bundle_hash=BUNDLE, policy_version=1)


def test_the_authorization_fact_records_the_target():
    s = _authorized()
    from kernel.authz import latest_merge_authorization
    a = latest_merge_authorization(s, "r")
    assert a["pr"] == PR and a["repo"] == REPO


# --- the contract table itself ------------------------------------------------

def test_a_class_with_no_contract_is_refused():
    """T4. Nothing exercised the missing-contract path, because all nine
    classes have one -- so a class added without a contract would have passed
    silently, which is the fail-open direction."""
    from kernel.contract import ContractViolation, check

    with pytest.raises(ContractViolation, match="declares no argv contract"):
        check("some_future_class", ["gh", "pr", "merge", "1"])


def test_every_effect_class_has_a_contract():
    """Adding a class must force adding a contract, rather than leaving one
    class unconstrained until someone notices."""
    from kernel.contract import CONTRACTS

    missing = sorted(EffectClass.ALL - set(CONTRACTS))
    assert not missing, f"effect classes with no argv contract: {missing}"


# --- round 7: the contract must constrain more than the verb ------------------

DANGEROUS = [
    ("delete the branch",        EffectClass.REF_UPDATE,
     ["git", "push", "origin", ":main"]),
    ("force-push over main",     EffectClass.REF_UPDATE,
     ["git", "push", "--force", "origin", "HEAD:main"]),
    ("delete a ref via gh api",  EffectClass.REF_UPDATE,
     ["gh", "api", "repos/o/r/git/refs/heads/main", "-X", "DELETE",
      "-f", "note=update-branch"]),
    ("wrong method on statuses", EffectClass.STATUS_CHECK,
     ["gh", "api", "repos/o/r/statuses/abc", "-X", "DELETE"]),
    ("body from a file",         EffectClass.COMMENT,
     ["gh", "pr", "comment", "7", "--body-file", "/etc/passwd"]),
    ("assign a collaborator",    EffectClass.ISSUE_OR_LABEL,
     ["gh", "issue", "edit", "7", "--add-assignee", "attacker"]),
    ("mirror-push",              EffectClass.REF_UPDATE,
     ["git", "push", "--mirror", "origin"]),
]


@pytest.mark.parametrize("name,cls,argv", DANGEROUS, ids=[d[0] for d in DANGEROUS])
def test_the_contract_constrains_more_than_the_verb(name, cls, argv):
    """ROUND 7. The first repair matched only the command's leading words, and
    `signature()` stops at the first flag -- so everything after it went
    unexamined. Every one of these passed."""
    from kernel.contract import ContractViolation, check

    with pytest.raises(ContractViolation):
        check(cls, argv)


def test_the_url_marker_must_be_in_the_url_not_anywhere_in_the_argv():
    """The `gh api` rules identify an endpoint. Searching the joined argv let
    the marker be smuggled into an unrelated field -- `-f note=update-branch`
    satisfied a check meant to identify the endpoint. Round 7 then showed the
    URL-operand version was still a substring test, so the marker could sit in
    the QUERY; it is now a regex over the PATH."""
    from kernel.contract import ContractViolation, check

    with pytest.raises(ContractViolation, match="url path"):
        check(EffectClass.REF_UPDATE,
              ["gh", "api", "repos/o/r/git/refs/heads/main", "-X", "PUT",
               "-f", "note=update-branch"])


def test_the_real_update_branch_call_still_passes():
    """The control for the rule above."""
    from kernel.contract import check

    check(EffectClass.REF_UPDATE,
          ["gh", "api", "repos/o/r/pulls/7/update-branch", "-X", "PUT"])


def test_a_flag_value_is_not_mistaken_for_an_operand():
    """`--max-time 120` made `120` the URL operand. Beyond breaking the real
    calls, it means an operand could hide behind any value-taking flag."""
    from kernel.contract import check

    check(EffectClass.SESSION_CONTROL,
          ["curl", "-sf", "--max-time", "120", "-X", "POST",
           "http://srv/v1/sessions/1/events", "-H", "content-type: application/json",
           "-d", "{}"])


def test_a_repo_given_as_an_equals_form_is_still_read():
    """`merge_target` looked for the exact token `--repo`, so `--repo=x` read
    as None. That happened to fail closed; it is now read properly rather than
    relying on the accident."""
    from kernel.contract import merge_target

    assert merge_target(["gh", "pr", "merge", "42", "--repo=o/r"]) == ("42", "o/r")


# --- round 7, codex: twelve shapes the verb/flag contract still admitted ------

CODEX_R7 = [
    ("R7-1 gh auth token prints the kernel credential",
     EffectClass.CREDENTIAL_LIFECYCLE, ["gh", "auth", "token", "--hostname", "github.com"]),
    ("R7-2 status marker in a query, on a comments endpoint",
     EffectClass.STATUS_CHECK,
     ["gh", "api", "repos/o/r/issues/1/comments?marker=/statuses/", "-X", "POST",
      "-f", "body=x"]),
    ("R7-3 no method flag; gh defaults to POST when -f is present",
     EffectClass.REF_UPDATE,
     ["gh", "api", "repos/o/r/pulls/1/update-branch", "-f", "expected_head_sha=0"]),
    ("R7-4 file:// scheme", EffectClass.SESSION_CONTROL,
     ["curl", "-s", "-X", "POST", "file:///etc/hosts/v1/sessions"]),
    ("R7-5 a second URL operand", EffectClass.SESSION_CONTROL,
     ["curl", "-s", "-X", "POST", "http://srv/v1/sessions", "file:///etc/hosts"]),
    ("R7-6 +refspec forces a push with no force flag", EffectClass.REF_UPDATE,
     ["git", "push", "origin", "+HEAD:main"]),
    ("R7-7 two refspecs under one journalled effect", EffectClass.REF_UPDATE,
     ["git", "push", "origin", "HEAD:main", "HEAD:other"]),
    ("R7-9 -w %output{} writes an arbitrary local file",
     EffectClass.SESSION_CONTROL,
     ["curl", "-s", "-X", "POST", "http://srv/v1/sessions", "-w", "%output{/tmp/x}HI"]),
    ("R7-12 ref marker in a query, on the contents endpoint",
     EffectClass.REF_UPDATE,
     ["gh", "api", "repos/o/r/contents/x.txt?marker=update-branch", "-X", "PUT",
      "-f", "content=eA=="]),
]


@pytest.mark.parametrize("name,cls,argv", CODEX_R7, ids=[c[0][:5] for c in CODEX_R7])
def test_codex_round7_shapes_are_refused(name, cls, argv):
    """Every one of these was ADMITTED by the verb-and-flag contract, and each
    was demonstrated against the real tool by the reviewer -- `+HEAD:main`
    rewrote a branch in a scratch repository, `-w %output{}` created a file,
    `gh auth token` printed a token that the executor would have returned to
    the caller as an external object id."""
    from kernel.contract import ContractViolation, check

    with pytest.raises(ContractViolation):
        check(cls, argv)


def test_the_credential_class_permits_nothing_and_says_so():
    """Distinguished from a class with no entry at all: this one was decided."""
    from kernel.contract import CONTRACTS, ContractViolation, check

    assert CONTRACTS[EffectClass.CREDENTIAL_LIFECYCLE] == []
    with pytest.raises(ContractViolation, match="empty contract"):
        check(EffectClass.CREDENTIAL_LIFECYCLE, ["gh", "auth", "status"])


def test_merge_target_reads_flags_before_the_positional():
    """R7-11. `gh pr merge --repo o/r 42` is valid and the old reading gave
    pr=None, refusing a legitimate merge. It failed closed, but the helper
    claimed to model what the command would act on."""
    from kernel.contract import merge_target

    assert merge_target(
        ["gh", "pr", "merge", "--repo", "o/r", "42", "--squash"]) == ("42", "o/r")


def test_the_real_calls_still_pass_after_all_of_it():
    """The control for the whole round: hardening that broke the coordinator
    would be a different failure, not a fix."""
    from kernel.contract import check

    check(EffectClass.REF_UPDATE, ["git", "push", "origin", "HEAD:main", "-q"])
    check(EffectClass.STATUS_CHECK,
          ["gh", "api", "repos/o/r/statuses/abc", "-X", "POST", "-f", "state=success"])
    check(EffectClass.SESSION_CONTROL,
          ["curl", "-sf", "--max-time", "15", "-X", "DELETE", "http://srv/v1/sessions/1"])
