# v2/tests/kernel/test_contract_sessions.py
"""Task 2: the SESSION_CONTROL contract is three anchored rules over one parse."""
import pytest

import kernel.cli
import kernel.contract
import kernel.effects
from kernel.contract import ContractViolation, check, create_body, parse
from kernel.effects import EffectClass

SC = EffectClass.SESSION_CONTROL
CREATE = '{"agent_id": "ag_1", "host_id": "host_h1", "workspace": "/w", "title": "k1"}'


def _create(*extra, method="POST", url="http://srv/v1/sessions", d=CREATE):
    argv = ["curl", "-sSf", "-X", method, "-H", "content-type: application/json"]
    if d is not None:
        argv += ["-d", d]
    return argv + list(extra) + [url]


def _events(*extra, url="http://srv/v1/sessions/s1/events"):
    return ["curl", "-sSf", "-X", "POST", "-H", "content-type: application/json",
            *extra, url]


def _delete(url="http://srv/v1/sessions/s1", fail="-sSf"):
    return ["curl", fail, "-X", "DELETE", url]


def test_three_rules_match_by_name():
    assert check(SC, _create()).name == "sess-create"
    assert check(SC, _events()).name == "sess-events"
    assert check(SC, _delete()).name == "sess-delete"


def test_contract_d_twice_refused():
    with pytest.raises(ContractViolation):
        check(SC, _create("-d", CREATE))
    with pytest.raises(ContractViolation):
        check(SC, _events("-d", "{}", "-d", "{}"))


def test_contract_d_equals_refused():
    argv = ["curl", "-sSf", "-X", "POST", f"-d={CREATE}", "http://srv/v1/sessions"]
    with pytest.raises(ContractViolation):
        check(SC, argv)
    with pytest.raises(ContractViolation):
        check(SC, _events("-d={}"))


def test_create_requires_exactly_one_d():
    with pytest.raises(ContractViolation):
        check(SC, _create(d=None))


def test_d_file_value_refused_both_endpoints():
    with pytest.raises(ContractViolation):
        check(SC, _create(d="@/tmp/body.json"))
    with pytest.raises(ContractViolation):
        check(SC, _events("-d", "@/tmp/body.json"))


def test_events_accepts_one_d_from_runner_templates():
    # Ruling 1: the runner's _send_prompt/_stop_session keep their inline -d.
    assert check(SC, _events("-d", '{"type": "stop_session"}')).name == "sess-events"


def test_data_binary_is_unlisted_from_every_caller():
    with pytest.raises(ContractViolation):
        check(SC, _events("--data-binary", "@/x"))
    with pytest.raises(ContractViolation):
        check(SC, _create("--data-binary", "@/x"))


def test_failing_curl_mode_required():
    with pytest.raises(ContractViolation):
        check(SC, ["curl", "-s", "-X", "POST", "-d", CREATE, "http://srv/v1/sessions"])
    assert check(SC, ["curl", "-sf", "-X", "POST", "-d", CREATE,
                      "http://srv/v1/sessions"]).name == "sess-create"
    assert check(SC, ["curl", "-s", "-f", "-X", "POST", "-d", CREATE,
                      "http://srv/v1/sessions"]).name == "sess-create"
    with pytest.raises(ContractViolation):
        check(SC, _delete(fail="-s"))


@pytest.mark.parametrize("argv", [
    ["curl", "-sSf", "-X", "POST", "http://srv/v1/sessions/s1/switch-agent"],
    ["curl", "-sSf", "-X", "POST", "http://srv/v1/sessions/s1/fork"],
    ["curl", "-sSf", "-X", "PATCH", "http://srv/v1/sessions/s1"],
    ["curl", "-sSf", "-X", "DELETE", "http://srv/v1/sessions"],
    ["curl", "-sSf", "-X", "POST", "http://srv/v1/sessions/s1"],
    ["curl", "-sSf", "-X", "POST", "-d", CREATE, "http://srv/v1/sessions/"],
    ["curl", "-sSf", "-X", "POST", "-F", "a=b", "http://srv/v1/sessions"],
    ["curl", "-sSf", "-X", "DELETE", "-d", "{}", "http://srv/v1/sessions/s1"],
])
def test_unanchored_shapes_refused(argv):
    with pytest.raises(ContractViolation):
        check(SC, argv)


def test_parse_reports_values_and_joined():
    p = parse(["curl", "-sSf", "-X", "POST", "-d", "a", "-H", "h: 1", "--max-time=9", "u"],
              frozenset({"-X", "-d", "-H", "--max-time"}))
    assert p.flags == frozenset({"-sSf", "-X", "-d", "-H", "--max-time"})
    assert p.methods == frozenset({"POST"})
    assert p.operands == ("curl", "u")
    assert p.values["-d"] == ["a"]
    assert p.values["-H"] == ["h: 1"]
    assert p.joined == frozenset({"--max-time"})


def test_method_flag_always_takes_a_value_even_if_not_listed_as_valued():
    # `_flags_and_operands`'s original behaviour: a `_METHOD_FLAGS` entry
    # (`-X`, `--method`) always consumes its next token as the method, even
    # when a rule names it in `methods` but forgets to also list it in
    # `valued`. Otherwise the verb leaks into `operands` -- the same shape as
    # the `--max-time 120` finding the module docstring already fixed for
    # ordinary valued flags.
    p = parse(["--method", "DELETE", "http://srv/x"], frozenset())
    assert p.methods == frozenset({"DELETE"})
    assert p.operands == ("http://srv/x",)
    assert "--method" not in p.values


def test_create_body_decodes_single_d():
    assert create_body(_create())["agent_id"] == "ag_1"
    with pytest.raises(ContractViolation):
        create_body(_create(d="[1]"))
    with pytest.raises(ContractViolation):
        create_body(_create(d="not json"))


def test_one_parse_three_readers():
    # One parser (contract.parse) is what the contract, the executor and the
    # reconciler read; none has a second reading of argv.
    assert kernel.cli.create_body is kernel.contract.create_body
    assert kernel.effects.create_body is kernel.contract.create_body
    assert kernel.cli.parse is kernel.contract.parse
    assert kernel.effects.parse is kernel.contract.parse
    assert kernel.contract._flags_and_operands.__wrapped_by__ == "parse"
