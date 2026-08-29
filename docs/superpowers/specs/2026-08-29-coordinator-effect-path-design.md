# The coordinator performs effects from Python

**Status:** design, not yet implemented. Written before the code deliberately —
this is the one migration step where a mistake means effects execute
UNJOURNALLED, which is the failure the whole programme exists to prevent. Every
slice so far moved pure functions, where a mistake shows up in a test.

## The problem

`observe_outcome` posts a comment. That is an effect, and it is the last thing
standing between the derivation and Python. Today every effect goes through
`_effect` in `batch/lib/effect-adapter.sh`, which owns the three-way switch:

    deny     refuse, return RC_DENIED          (the default)
    legacy   run the command directly          (unjournalled, v1 behaviour)
    kernel   python3 -m kernel.cli effect ...  (contract, authz, journal)

**Python knows nothing about this.** `BIRCHER_EFFECT_MODE` appears in
`v2/kernel/mode.py` only inside comments. A Python coordinator that called
`kernel.effects.perform` directly would journal every effect regardless of
mode — including under `deny`, where the operator asked for nothing to happen.

## Why the switch cannot simply move into the kernel

The tempting answer is to let `perform()` consult the mode, so bash and Python
share one implementation. It does not work, for one reason:

**`legacy` exists to run WITHOUT the kernel.** It is the bisecting tool for a
suspected kernel fault. Routing it through `kernel.cli` would make the mode
whose purpose is "do not involve the kernel" depend on the kernel being
reachable. That is not a trade worth making for tidiness.

So the switch belongs at the OUTERMOST caller in each language, and there will
be two entry points during the transition. What must not be duplicated is the
*definition*: the three names, their meanings, and the default.

## The design

**One source of truth for the vocabulary, two entry points.**

`v2/coordinator/effect_mode.py` holds the mode names, the `deny` default, and
the rule that an unrecognised value raises rather than defaulting — mirroring
`kernel/mode.py`'s reasoning for `KERNEL_MODE`. A structural test parses the
`case` in `effect-adapter.sh` and asserts it implements exactly those arms with
exactly that default. Divergence becomes a failing test rather than an
unjournalled mutation.

`v2/coordinator/effects.py` provides the Python entry point:

```python
def perform_effect(effect_class, key, argv, *, timeout=None) -> str:
    mode = effect_mode()
    if mode == DENY:
        raise EffectDenied(f"{effect_class} {key}")
    if mode == LEGACY:
        return _run_unjournalled(argv, timeout)
    return perform(_store(), _run_id(), _generation(),
                   effect_class, key, {"argv": argv}, executor)
```

**It reuses `kernel.cli._executor`, not a copy.** Two executors could differ in
how they resolve a command — and `resolve_command` is what decides which binary
actually runs. A second implementation of that is a second place for an
argv-contract bypass to hide.

`run_id` and `generation` come from `BIRCHER_RUN_ID` / `BIRCHER_GENERATION`,
exactly as the bash adapter reads them, and are REQUIRED in kernel mode: the
adapter uses `${VAR:?}` for both, and a Python default of `None` would journal
an effect against no attempt.

## What this does NOT change

The journal, the argv contract, authorization, idempotency and reconciliation
are untouched. Both entry points call the same `perform()`, write to the same
`BIRCHER_KERNEL_DB`, and bind to the same run and generation — so a run whose
effects come partly from bash and partly from Python produces ONE coherent
journal. That is what makes an incremental migration safe here.

## Acceptance

1. **An effect performed from Python is journalled identically to the same
   effect performed from bash** — same class, same idempotency key, same
   `effect_intended` / `effect_confirmed` pair, same external object id.
   Asserted by performing one of each against one database and comparing the
   facts.
2. **Under `deny`, Python performs nothing and says so.** Not "journals a
   refusal" — the bash adapter does not reach the kernel under `deny`, and a
   Python path that did would put facts in a database the operator asked to
   leave alone.
3. **Under `legacy`, Python journals nothing and the kernel is never opened.**
   Asserted by pointing `BIRCHER_KERNEL_DB` at a path that does not exist and
   requiring the effect to succeed anyway.
4. **The structural test fails if `effect-adapter.sh` and `effect_mode.py`
   disagree** about the arms or the default. Mutation-proved by changing the
   bash default to `legacy` and watching it red.
5. **An unset `BIRCHER_RUN_ID` or `BIRCHER_GENERATION` refuses in kernel mode**,
   rather than journalling against nothing.

## The risk I am not removing

During the transition both entry points exist, and a reader must know which one
a given effect took. The journal does not record it, and I am not adding a
field for it: the fact is derivable from the call site, and a field that exists
only during a migration is a field someone reads afterwards.

What I am doing instead is keeping the count small — `observe_outcome`'s comment
is the ONLY effect that moves in this step. `merge_ready_pr`, the status
publication and the issue writeback stay in bash until their orchestrators move.
