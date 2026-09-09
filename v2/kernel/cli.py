"""bircher-effect -- journal and perform one effect from the shell.

The coordinator keeps its orchestration; this is where its authority goes.

The executor runs the real command from the KERNEL's credential domain. That
is the whole point of the seam: no model process holds credentials for a
kernel-owned effect, implementers included.

There is deliberately NO timeout here. The bound stays in `_net_run`, wrapping
this process, because #62's scar is `timeout -k` specifically -- plain SIGTERM
is not a bound against a push stuck in credential negotiation -- and a Python
reimplementation would be a second bound to keep correct. If this process is
killed mid-effect the journal holds `intended`, which M1-3 already treats as
uncertain rather than as a completed replay.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

from kernel.authz import NotAuthorized
from kernel.effects import (
    EffectClass, Resolution, UncertainEffect, is_halted, pending_reconciliation,
    perform, reconcile, reconcile_many, reconcile_typed,
)
# Re-exported so `create_body` / `parse` are the SAME functions `check` uses
# to admit an argv in the first place, not a second reading of it. Tasks 3
# and 4 call these as `create_body(...)` / `parse(...)` from this module.
from kernel.contract import create_body, parse
from kernel.ownership import OwnershipLost
from kernel.store import Store

#: Directories a kernel tool may live in. The resolved path must be inside
#: one of these -- not merely "first on PATH".
TOOL_DIRS = ("/usr/local/bin", "/usr/bin", "/bin", "/opt/homebrew/bin",
             "/usr/local/sbin", "/opt/bin")

#: Tools the kernel performs effects with. An allowlist of TOOLS as well as
#: directories: without it the directory list would be the only limit, and
#: /usr/bin holds a great deal more than three programs.
TOOLS = frozenset({"gh", "git", "curl"})

#: curl reads its config file BEFORE its command line, so `.curlrc` can add
#: URLs and options no contract ever sees. `-q` suppresses that, and it only
#: works as the FIRST argument -- so the kernel inserts it rather than asking
#: every call site to remember.
_CONFIG_SUPPRESS = {"curl": "-q"}


class UnresolvableTool(Exception):
    """The command names something the kernel will not run."""


def resolve_command(argv: list[str]) -> list[str]:
    """Turn a validated argv into an absolute, unambiguous command.

    `contract.check` validates the NAME `gh`. `subprocess.run` then re-resolves
    that name through PATH, so a `gh` earlier in PATH would execute instead --
    in the kernel's credential domain. Validating a command name is not
    validating an executable.
    """
    if not argv:
        raise UnresolvableTool("empty command")
    tool = argv[0]
    if tool not in TOOLS:
        raise UnresolvableTool(
            f"{tool!r} is not a tool the kernel runs; expected one of "
            f"{sorted(TOOLS)}"
        )
    for d in TOOL_DIRS:
        candidate = os.path.join(d, tool)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            rest = argv[1:]
            suppress = _CONFIG_SUPPRESS.get(tool)
            if suppress and suppress not in rest:
                rest = [suppress] + rest
            return [candidate] + rest
    raise UnresolvableTool(
        f"{tool!r} not found in any allowlisted directory: {list(TOOL_DIRS)}"
    )


RC_OK = 0
RC_USAGE = 2
RC_REFUSED = 87
RC_FENCED = 88
RC_UNCERTAIN = 89
RC_FAILED = 90


SESSION_PROJECTION_KEYS = ("id", "agent_id", "agent_name", "host_id", "workspace", "title")


def session_projection(snap: dict) -> dict:
    """The six fields the guards read; a session that has run carries its
    whole transcript under `items`, which the journal does not want."""
    return {k: snap.get(k) for k in SESSION_PROJECTION_KEYS}


def validate_create_response(stdout: str, body: dict) -> str:
    """A confirmed create holds a snapshot by construction (spec §3)."""
    try:
        snap = json.loads(stdout)
    except ValueError as exc:
        raise RuntimeError(f"sess-create returned non-JSON: {stdout[:200]!r}") from exc
    if not isinstance(snap, dict) or not snap.get("id") or not snap.get("agent_name"):
        raise RuntimeError(f"sess-create response is not a session snapshot: {stdout[:200]!r}")
    if snap.get("agent_id") != body.get("agent_id"):
        raise RuntimeError(
            f"sess-create bound agent {snap.get('agent_id')!r}, requested {body.get('agent_id')!r}")
    if "title" in body and snap.get("title") != body["title"]:
        raise RuntimeError(
            f"sess-create titled {snap.get('title')!r}, requested {body['title']!r}")
    return json.dumps(session_projection(snap), sort_keys=True)


def compose_event(store, body: dict) -> bytes:
    """The events-endpoint JSON for an intent body. Read from the journal:
    the prompt text is the artefact the intent names, never an argv string."""
    if "artifact" in body:
        data = store.read_blob(body["artifact"])
        if data is None:
            raise RuntimeError(f"artifact {body['artifact']} is not held")
        event = {"type": "message",
                 "data": {"role": "user",
                          "content": [{"type": "input_text", "text": data.decode("utf-8")}]}}
        return json.dumps(event).encode("utf-8")
    return json.dumps({"type": "stop_session"}).encode("utf-8")


def _write_event_file(store, body: dict) -> str:
    fh = tempfile.NamedTemporaryFile(
        dir=os.path.dirname(os.path.abspath(store.path)), prefix="event-",
        suffix=".json", delete=False)
    with fh:
        os.chmod(fh.name, 0o600)
        fh.write(compose_event(store, body))
    return fh.name


def _write_raw_file(store, body: dict) -> str:
    """The artefact the intent names, raw: an issue body (planning ruling 1)."""
    data = store.read_blob(body["artifact"])
    if data is None:
        raise RuntimeError(f"artifact {body['artifact']} is not held")
    fh = tempfile.NamedTemporaryFile(
        dir=os.path.dirname(os.path.abspath(store.path)), prefix="body-",
        suffix=".md", delete=False)
    with fh:
        os.chmod(fh.name, 0o600)
        fh.write(data)
    return fh.name


_ISSUE_URL = re.compile(r"/issues/(\d+)/?$")


def _read_back_issue(argv: list[str], url: str) -> str:
    """The created issue's number from its URL and its database id from a
    read-back (planning ruling 6), as {url, number, id} JSON."""
    m = _ISSUE_URL.search(url.strip())
    if not m:
        raise RuntimeError(f"issue_create returned no issue URL: {url[:200]!r}")
    repo = argv[argv.index("--repo") + 1]
    r = subprocess.run(resolve_command(["gh", "api", f"repos/{repo}/issues/{m.group(1)}", "--jq", ".id"]),
                       capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip().isdigit():
        raise RuntimeError(f"issue_create read-back failed rc={r.returncode}: {r.stderr.strip()[:200]}")
    return json.dumps({"url": url.strip(), "number": int(m.group(1)), "id": int(r.stdout.strip())}, sort_keys=True)


def make_executor(store):
    """The real executor, closed over the store the body is composed from.

    `check=False` plus an explicit raise: the raised message carries the
    command's rc and stderr, which the journal records as the halt's detail.
    Every executor failure -- a clean non-zero exit as much as a crash --
    becomes `effect_uncertain` and halts the run (spec §7, *Session creation
    fails*).
    """
    from kernel.contract import check

    def executor(effect_class, intent, idempotency_key):
        argv = list(intent["argv"])
        rule = check(effect_class, argv)
        body = intent.get("body")
        extra: list[str] = []
        path = None
        try:
            if body is not None:
                if store is None:
                    raise RuntimeError("an intent with a body needs the store it is composed from")
                if effect_class == EffectClass.ISSUE_CREATE:
                    path = _write_raw_file(store, body)
                    extra = ["--body-file", path]
                else:
                    path = _write_event_file(store, body)
                    extra = ["--data-binary", f"@{path}"]
            r = subprocess.run(resolve_command(argv + extra), capture_output=True, text=True)
        finally:
            if path is not None:
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass
        if r.returncode != 0:
            raise RuntimeError(
                f"{effect_class} failed rc={r.returncode}: {r.stderr.strip()[:200]}")
        out = r.stdout.strip()
        if rule.name == "sess-create":
            return validate_create_response(out, create_body(argv))
        if effect_class == EffectClass.ISSUE_CREATE:
            return _read_back_issue(argv, out)
        return out or "ok"

    return executor


_executor = make_executor(None)


def _add_common(p):
    p.add_argument("--db", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--generation", type=int, required=True)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="bircher-kernel")
    subs = p.add_subparsers(dest="mode", required=True)

    e = subs.add_parser("effect")
    _add_common(e)
    e.add_argument("--class", dest="effect_class", required=True,
                   choices=sorted(EffectClass.ALL))
    e.add_argument("--idempotency-key", required=True)
    e.add_argument("cmd", nargs=argparse.REMAINDER)

    c = subs.add_parser("command")
    _add_common(c)
    c.add_argument("--name", required=True)
    c.add_argument("--payload-json", default="{}")
    c.add_argument("--idempotency-key", default=None)

    # `pending` and `reconcile` are the halt's two halves. An uncertain effect
    # halts its run and `kernel.effects.reconcile` has always been able to
    # resolve it -- but nothing outside Python could ASK. A live merge came
    # back uncertain and the coordinator had no route to the resolution, so the
    # run could not be advanced by any path it had. The capability existed and
    # the door did not.
    n = subs.add_parser("pending")
    n.add_argument("--db", required=True)
    n.add_argument("--run-id", required=True)

    r = subs.add_parser("reconcile")
    r.add_argument("--db", required=True)
    r.add_argument("--run-id", required=True)
    # Repeatable: several uncertain effects are resolved under ONE CAS, in one
    # transaction. Resolving them one call at a time cannot be made safe from
    # outside -- a CAS cannot tell the caller's own bump from a foreign one.
    # Not `required` any more: this is the LEGACY form, for keys that carry no
    # obligation (spec §3). The typed form below (--delivered/--not-delivered)
    # is the other, and _do_reconcile refuses a call that mixes them.
    r.add_argument("--idempotency-key", action="append", default=[])
    r.add_argument("--resolution")
    # Supplied by the caller, not read here: the CAS exists so a resolution
    # derived from an observation at version N is refused when the run has
    # moved since. Reading the version inside this command would compare it
    # against itself and check nothing.
    r.add_argument("--expected-version", type=int, required=True)
    # Typed form: one RESULT per obligation-bearing key, not one free-text
    # resolution for the whole batch. `KEY=VALUE` is what a delivered
    # obligation returned (a session snapshot, a prompt item id, a publish
    # URL); a bare `KEY` is a sess-stop, which takes none.
    r.add_argument("--delivered", action="append", default=[], metavar="KEY[=VALUE]")
    r.add_argument("--not-delivered", action="append", default=[], metavar="KEY")

    v = subs.add_parser("verify-nomination")
    v.add_argument("--db", required=True)
    v.add_argument("--run-id", required=True)
    v.add_argument("--worktree", required=True)
    v.add_argument("--branch", required=True)
    # OPTIONAL, and it may only agree with what the kernel reads at --branch.
    # It is never a tiebreak and never a fallback: the observation decides.
    v.add_argument("--claimed-oid", default=None)

    a = p.parse_args(argv)
    if a.mode == "pending":
        return _do_pending(a)
    if a.mode == "reconcile":
        return _do_reconcile(a)
    if a.mode == "verify-nomination":
        return _do_verify_nomination(a)
    return _do_effect(a) if a.mode == "effect" else _do_command(a)


def _do_verify_nomination(a) -> int:
    from kernel.nomination import NotPublishable, verify_nomination

    store = Store.open(a.db)
    try:
        print(verify_nomination(store, a.run_id, a.worktree, a.branch,
                                a.claimed_oid))
    except NotPublishable as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return RC_REFUSED
    return RC_OK


def _do_pending(a) -> int:
    """What is unresolved, and the version any resolution must be derived from."""
    store = Store.open(a.db)
    # `state` rides along because every caller that asks "is this halted"
    # also needs "how far has this run got". Without it a recovery re-drives
    # the whole lifecycle blindly and the kernel refuses each stage the run
    # has already passed -- four advisory refusals per run, all correct, all
    # noise, and all indistinguishable in the shadow report from refusals that
    # mean something.
    print(json.dumps({
        "version": store.run_version(a.run_id),
        "state": store.run_state(a.run_id),
        "halted": is_halted(store, a.run_id),
        "pending": pending_reconciliation(store, a.run_id),
    }))
    return RC_OK


def _do_reconcile(a) -> int:
    from kernel.commands import StaleVersion

    # Exactly one form. Mixing them would let a caller name a typed key
    # through the legacy, unvalidated path (or vice versa), and "neither" is
    # as much a usage error as "both" -- there is no default reconciliation.
    typed = bool(a.delivered or a.not_delivered)
    legacy = bool(a.idempotency_key or a.resolution)
    if typed == legacy:
        print("reconcile takes either --delivered/--not-delivered or"
              " --idempotency-key/--resolution, not both and not neither", file=sys.stderr)
        return RC_USAGE

    store = Store.open(a.db)
    try:
        if typed:
            items = []
            for spec in a.delivered:
                key, sep, value = spec.partition("=")
                if sep and value.startswith("@"):
                    # The runner hands a session snapshot this way (Task 20):
                    # the shell composes it to a file rather than a command
                    # line, which has its own length limit and quoting rules.
                    path = value[1:]
                    try:
                        value = open(path).read()
                    except OSError as exc:
                        # Missing, unreadable or a directory -- all OSError,
                        # none of them ValueError, so left uncaught this
                        # crashed main() with a traceback instead of the
                        # RC_REFUSED every other reconcile refusal returns.
                        raise ValueError(f"{key}: cannot read {path!r}: {exc}") from exc
                items.append(Resolution(key, True, value if sep else None))
            items += [Resolution(k, False) for k in a.not_delivered]
            reconcile_typed(store, a.run_id, items, a.expected_version)
        else:
            reconcile_many(store, a.run_id, a.idempotency_key, a.resolution,
                           a.expected_version)
    except StaleVersion as exc:
        print(f"stale: {exc}", file=sys.stderr)
        return RC_FAILED
    except ValueError as exc:
        # Not an uncertain effect: an unknown or already-resolved key. Refused
        # rather than applied, because mark_effect is a bare UPDATE and would
        # otherwise clear the halt for something that was never in doubt.
        print(f"refused: {exc}", file=sys.stderr)
        return RC_REFUSED
    print(json.dumps({"halted": is_halted(store, a.run_id),
                      "pending": pending_reconciliation(store, a.run_id)}))
    return RC_OK


def _do_effect(a) -> int:
    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    if not cmd:
        print("no command given", file=sys.stderr)
        return RC_USAGE

    store = Store.open(a.db)
    try:
        print(perform(store, a.run_id, a.generation, a.effect_class,
                      a.idempotency_key, {"argv": cmd}, make_executor(store)))
        return RC_OK
    except UnresolvableTool as e:
        print(f"unresolvable: {e}", file=sys.stderr)
        return RC_REFUSED
    except NotAuthorized as e:
        # Includes an undispatched generation and a merge whose authorization
        # no longer holds. Distinct from FENCED: the caller may be the current
        # owner and still have no authority for THIS effect.
        print(f"refused: {e}", file=sys.stderr)
        return RC_REFUSED
    except OwnershipLost as e:
        print(f"fenced: {e}", file=sys.stderr)
        return RC_FENCED
    except UncertainEffect as e:
        print(f"uncertain, run halted: {e}", file=sys.stderr)
        return RC_UNCERTAIN
    except RuntimeError as e:
        print(f"failed: {e}", file=sys.stderr)
        return RC_FAILED


def _do_command(a) -> int:
    """Submit one typed command and translate its outcome to an exit code.

    Exit codes: 0 accepted or replayed, 2 usage (unknown command name,
    unparseable or non-object payload), 87 refused -- either ENFORCE raising
    (NotAuthorized, a stale aggregate version, or any other command-level
    rejection) OR SHADOW recording a refusal and returning normally instead
    of raising (`submit()` gives back `Result(accepted=False, ...)`). Under
    BIRCHER_KERNEL_MODE's real default (`shadow`, unset), most refusals take
    this second path -- `r.accepted` must be checked, not just whether
    `submit()` raised, or a shadow-refused command prints "accepted" and
    exits 0. The bash wrapper is advisory and never branches on 87 vs. any
    other nonzero code, but an operator reading the output must not be told
    a refusal succeeded just because shadow mode did not raise for it.
    88 fenced (superseded generation), 90 failed (a run halted pending
    reconciliation -- the same RC _do_effect returns for its own
    RuntimeError, so the two subcommands agree). 89 (uncertain) does not
    apply here: only an effect executor can leave an outcome unconfirmed.
    """
    from kernel.commands import COMMAND_NAMES, Command, StaleVersion, submit

    if a.name not in COMMAND_NAMES:
        print(f"unknown command: {a.name}", file=sys.stderr)
        return RC_USAGE
    try:
        payload = json.loads(a.payload_json)
    except ValueError as exc:
        print(f"payload is not JSON: {exc}", file=sys.stderr)
        return RC_USAGE
    if not isinstance(payload, dict):
        print("payload must be a JSON object", file=sys.stderr)
        return RC_USAGE

    store = Store.open(a.db)
    # A stable default so a retry of the same stage REPLAYS. Without it every
    # retry is a new command and the same stage records twice.
    key = a.idempotency_key or f"{a.run_id}:{a.name}:{a.generation}"
    try:
        r = submit(store, Command(name=a.name, run_id=a.run_id,
                                  expected_version=store.run_version(a.run_id),
                                  idempotency_key=key, generation=a.generation,
                                  payload=payload))
        if r.replayed:
            print("replayed")
            return RC_OK
        if r.accepted:
            print("accepted")
            return RC_OK
        # SHADOW evaluated this and refused it, but submit() returned instead
        # of raising: the refusal is recorded (command_rejected,
        # shadow_rejected) and nothing was applied. Must not print "accepted"
        # or return RC_OK for the same reason ENFORCE's raise below does not.
        print(f"shadow-refused: {a.name} would have been refused by "
              "enforcement; the kernel recorded it and applied nothing",
              file=sys.stderr)
        return RC_REFUSED
    except NotAuthorized as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return RC_REFUSED
    except OwnershipLost as exc:
        print(f"fenced: {exc}", file=sys.stderr)
        return RC_FENCED
    except (StaleVersion, ValueError) as exc:
        print(f"rejected: {exc}", file=sys.stderr)
        return RC_REFUSED
    except RuntimeError as exc:
        # A halted run is an ordinary reachable state -- any failed effect
        # halts its run unconditionally (kernel.effects, on the first
        # execution failure). submit() raises a bare RuntimeError for it
        # (kernel.commands), same as _do_effect's halt-on-retry path, so both
        # subcommands map it to the same exit code rather than one of them
        # leaking an uncaught traceback.
        print(f"failed: {exc}", file=sys.stderr)
        return RC_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
