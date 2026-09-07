"""The frozen per-run policy (spec §1).

Derived by the kernel inside `create_run`'s transaction from the issue's
labels and the omnigent Project config; recorded once as `policy_frozen`.
Every guard reads the fact, never a caller's claim.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from kernel.canon import canonical_hash
from kernel.events import EventKind

GRILLS = ("human", "model")
PHASES = ("spec", "plan")
ROUNDS = range(1, 6)      # 1..5
SEATS = range(4, 41)      # 4..40


class PolicyFrozen(Exception):
    """A run's policy is written once, in its creation transaction."""


@dataclass(frozen=True)
class Policy:
    grill: str = "model"
    gates: frozenset[str] = frozenset({"spec"})
    max_rounds: int = 3
    max_seats: int = 16


def _int(cfg: dict, key: str, default: int, allowed: range) -> int:
    value = cfg.get(key, default)
    if type(value) is not int or value not in allowed:
        raise ValueError(f"policy {key} must be an int in {allowed}, got {value!r}")
    return value


def derive(labels: Iterable[str], project_config: dict) -> Policy:
    # Only `None` means "unset". A falsy non-dict (`[]`, `""`, `0`, `False`)
    # is a caller error, not an empty config -- `project_config or {}` would
    # coerce it to `{}` and silently accept a wrong type, and only a truthy
    # wrong type would ever reach the isinstance check below.
    if project_config is None:
        cfg = {}
    elif not isinstance(project_config, dict):
        raise ValueError("project config must be an object")
    else:
        cfg = project_config
    grill = cfg.get("grill", "model")
    if grill not in GRILLS:
        raise ValueError(f"policy grill must be one of {GRILLS}, got {grill!r}")
    raw_gates = cfg.get("gates", ["spec"])
    if not isinstance(raw_gates, (list, tuple, set, frozenset)):
        raise ValueError("policy gates must be a list")
    gates = set(raw_gates)
    if not gates <= set(PHASES):
        raise ValueError(f"policy gates must be within {PHASES}, got {sorted(gates)}")
    max_rounds = _int(cfg, "max_rounds", 3, ROUNDS)
    max_seats = _int(cfg, "max_seats", 16, SEATS)

    present = set(labels)
    if "bircher:grill" in present:
        grill = "human"
    if "bircher:gate-plan" in present:
        gates.add("plan")
    # Ruling 5: cleared last, so `bircher:autonomous` wins over `bircher:gate-plan`.
    if "bircher:autonomous" in present:
        gates = set()
    return Policy(grill=grill, gates=frozenset(gates),
                  max_rounds=max_rounds, max_seats=max_seats)


def to_payload(p: Policy) -> dict:
    return {"grill": p.grill, "gates": sorted(p.gates),
            "max_rounds": p.max_rounds, "max_seats": p.max_seats}


def from_payload(d: dict) -> Policy:
    return Policy(grill=d["grill"], gates=frozenset(d["gates"]),
                  max_rounds=d["max_rounds"], max_seats=d["max_seats"])


def freeze(store, run_id: str, *, labels: Iterable[str], project_config: dict) -> Policy:
    """Write `policy_frozen`. Called inside `create_run`'s transaction only."""
    if store.newest_fact(run_id, EventKind.POLICY_FROZEN) is not None:
        raise PolicyFrozen(f"run {run_id} already has a policy_frozen fact")
    labels = sorted(set(labels))
    p = derive(labels, project_config)  # raises ValueError before any hash is taken
    cfg_hash = canonical_hash({} if project_config is None else project_config)
    store.append_fact(
        run_id=run_id, kind=EventKind.POLICY_FROZEN, actor="kernel",
        causal_command_id=None,
        payload={"policy": to_payload(p), "labels": labels,
                 "project_config_hash": cfg_hash},
    )
    return p


def policy_of(store, run_id: str) -> Policy:
    """Ruling 6: a run with no `policy_frozen` (a v1 run) has the defaults."""
    fact = store.newest_fact(run_id, EventKind.POLICY_FROZEN)
    return Policy() if fact is None else from_payload(fact.payload["policy"])


def policy_version(store, run_id: str) -> int | None:
    """The journal `seq` of the run's `policy_frozen` fact; None for a v1 run."""
    fact = store.newest_fact(run_id, EventKind.POLICY_FROZEN)
    return None if fact is None else fact.seq
