"""Every routed call site must satisfy its declared class's argv contract.

Round 6 added a contract check to `perform()`. If a routed call site does not
match its class, the coordinator breaks in kernel mode — and it would break at
the moment the effect fires, in production, rather than here.

This closes the loop between three artifacts that previously only agreed by
inspection: the inventory says which class each site is, the adapter passes
the class through, and the contract says what that class may run.
"""

import pathlib
import re
import shlex

import pytest

from kernel.contract import CONTRACTS, ContractViolation, check, signature

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
RUN_QUEUE = REPO_ROOT / "batch" / "run-queue.sh"

#: `_effect <class> <key> <cap> <argv...>`. The key and cap can themselves be
#: command substitutions containing spaces and pipes -- `comment:$pr:$(printf
#: ... | shasum ...)` -- so positional splitting does not work. The command is
#: located instead by finding the first known binary at or after position 3.
CALL = re.compile(r"_effect\s+([a-z_]+)\s+(.*)$")
BINARIES = ("gh", "git", "curl")


def routed_calls():
    from tools.detect_direct_effects import code_lines

    out = []
    for n, line, mask, is_comment in code_lines(str(RUN_QUEUE)):
        if is_comment:
            continue
        m = CALL.search(line)
        # A match that STARTS inside a quoted string is a selftest assertion
        # about the source, not a call -- the same rule the detector uses.
        if not m or mask[m.start()]:
            continue
        cls, rest = m.groups()
        rest = re.split(r"\s+(?:>|2>|\|\||&&|;)", rest)[0]
        try:
            toks = shlex.split(rest)
        except ValueError:
            toks = rest.split()
        argv = next((toks[i:] for i, tk in enumerate(toks) if tk in BINARIES), None)
        if argv is None:
            continue
        out.append((n, cls, argv))
    return out


def test_the_extractor_finds_the_routed_calls():
    """A parser that finds nothing reports total compliance."""
    calls = routed_calls()
    assert len(calls) >= 12, [c[:2] for c in calls]


def test_every_routed_class_is_a_class_the_contract_knows():
    for n, cls, _argv in routed_calls():
        assert cls in CONTRACTS, f"run-queue.sh:{n} routes {cls!r}, which has no contract"


@pytest.mark.parametrize("n,cls,argv", routed_calls(),
                         ids=lambda v: str(v) if not isinstance(v, list) else "argv")
def test_each_routed_call_satisfies_its_contract(n, cls, argv):
    """The one that would otherwise fail in production, at the effect."""
    try:
        check(cls, argv)
    except ContractViolation as exc:
        pytest.fail(
            f"run-queue.sh:{n} routes class {cls!r} with argv whose signature "
            f"is {signature(argv)!r}\n  argv: {argv}\n  {exc}"
        )
