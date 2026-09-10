"""Task 1: the slice-plan grammar, the ruling grammar and the renderers
(spec §2 *The artefact*, §3 *The shaping round*, §3 *Filing the children*)."""
import pytest

from kernel import slices
from kernel.events import SCHEMA_VERSIONS, EventKind
from kernel.store import Store

GOOD = b"""# Slicing the epic

Why three pieces: the store, the API, the client.

## Slice 1: The store
Scope: Add the table. Add the migration. Add the store reads.
Non-goals: the API.
Depends on: none

## Slice 2: The API
Scope: Add the endpoint. Wire it to the store. Cover it with a handler test.
Non-goals: the client.
Depends on: 1

## Slice 3: The client
Scope: Add the page. Call the endpoint. Show the result. Cover it with a component test.
Non-goals: styling.
Depends on: 1, 2
"""


def _plan(**edits) -> bytes:
    text = GOOD.decode()
    for old, new in edits.items():
        text = text.replace(old.replace("_", " "), new)
    return text.encode()


def test_new_kinds_declared_with_schema_version():
    for attr, value in {"ARTIFACT_ADVANCED": "artifact_advanced", "SLICE_FILED": "slice_filed",
                        "FILING_COMPLETE": "filing_complete", "SLICE_CLOSED": "slice_closed",
                        "SLICE_REOPENED": "slice_reopened",
                        "CHILDREN_OBSERVED_CLOSED": "children_observed_closed"}.items():
        assert getattr(EventKind, attr) == value
        assert SCHEMA_VERSIONS[value] == 1


def test_good_plan_parses():
    p = slices.parse(GOOD)
    assert p.title == "Slicing the epic"
    assert p.preamble.startswith("Why three pieces")
    assert [s.number for s in p.slices] == [1, 2, 3]
    assert p.slices[1].depends_on == (1,) and p.slices[2].depends_on == (1, 2)
    assert p.slices[0].non_goals == "the API."
    assert p.edges() == [(2, 1), (3, 1), (3, 2)]
    assert slices.topo_order(p) == [1, 2, 3]
    # A blocker named twice is ONE blocker: `Depends on: 1, 1` rendered
    # `Depends on: #40, #40` and yielded two identical `slice_dependency`
    # obligations (final review, finding 5).
    assert slices.parse(GOOD.replace(b"Depends on: 1, 2", b"Depends on: 1, 1")).slices[2].depends_on == (1,)


def test_a_trailing_section_after_the_last_slice_is_not_parsed():
    """The author's brief demands `## Dispositions` at the end of a revised
    artefact; the parser must not read it as more of the last slice's
    `Depends on:` field (fix round following Task 7)."""
    p = slices.parse(GOOD + b"\n## Dispositions\n\n1. accepted - merged.\n")
    assert [s.number for s in p.slices] == [1, 2, 3]
    assert p.slices[2].depends_on == (1, 2)


def test_a_non_slice_heading_before_the_first_slice_stays_preamble():
    """`## Notes` is not `## Slice N: <title>`; before the first real slice
    heading it is preamble, same as any other line."""
    pre, sep, rest = GOOD.partition(b"## Slice 1")
    p = slices.parse(pre + b"## Notes\n\nSome notes.\n\n" + sep + rest)
    assert "## Notes" in p.preamble and "Some notes." in p.preamble
    assert [s.number for s in p.slices] == [1, 2, 3]


def test_sentences_count_full_stops_that_end_a_sentence():
    assert slices.sentences("One. Two. Three.") == 3
    assert slices.sentences("Use this. Then 3.5 of that. Done.") == 3
    assert slices.sentences("No stop") == 0


@pytest.mark.parametrize("bad, why", [
    (GOOD.replace(b"Scope: Add the table. Add the migration. Add the store reads.\n", b""), "missing"),
    (GOOD.replace(b"Add the table. Add the migration. Add the store reads.", b"Add the table. Add it."), "2 sentence"),
    (GOOD.replace(b"Add the table. Add the migration. Add the store reads.",
                  b"A. B. C. D. E. F. G."), "7 sentence"),
    (GOOD.replace(b"Non-goals: the API.\n", b"Non-goals:\n"), "Non-goals is empty"),
    (GOOD.replace(b"Non-goals: the API.\n", b""), "missing"),
    (GOOD.replace(b"Depends on: 1, 2", b"Depends on: 9"), "not in the plan"),
    (GOOD.replace(b"Depends on: 1, 2", b"Depends on: 3"), "depends on itself"),
    (GOOD.replace(b"## Slice 1: The store\nScope: Add the table. Add the migration. Add the store reads.\nNon-goals: the API.\nDepends on: none",
                  b"## Slice 1: The store\nScope: Add the table. Add the migration. Add the store reads.\nNon-goals: the API.\nDepends on: 3"), "cycle"),
    (GOOD.replace(b"## Slice 2: The API", b"## Slice 5: The API"), "1..N"),
    (b"# T\n\n## Slice 1: only\nScope: A. B. C.\nNon-goals: x.\nDepends on: none\n", "1 slices"),
    (GOOD + b"".join(b"\n## Slice %d: more\nScope: A. B. C.\nNon-goals: x.\nDepends on: none\n" % n for n in (4, 5, 6)), "6 slices"),
    (b"# T\n\nno headings at all\n", "not a slice plan"),
    (GOOD.replace(b"## Slice 3: The client", b"## Slice3: The client"), "malformed slice heading"),
    (GOOD.replace(b"## Slice 3: The client", b"## Slice 3 - The client"), "malformed slice heading"),
    (GOOD.replace(b"## Slice 3: The client", b"## slice 3: The client"), "malformed slice heading"),
    (GOOD.replace(b"Depends on: 1, 2", b"Depends on: two"), "slice numbers"),
])
def test_refusals_name_why(bad, why):
    with pytest.raises(slices.PlanError, match=why):
        slices.parse(bad)


def test_ruling_grammar():
    ok = b"Ruling: one piece\nReasoning: one coherent change\nto one file.\nCost if wrong: a spec round.\n"
    r = slices.parse_ruling(ok)
    assert r == slices.Ruling("one coherent change to one file.", "a spec round.")
    assert slices.parse_ruling(b"  \n  Ruling: one piece\n  Reasoning: r\n  Cost if wrong: c\n") is not None
    assert slices.parse_ruling(b"Ruling: one piece\nReasoning: r\n") is None            # no cost
    assert slices.parse_ruling(b"Ruling: one piece\nReasoning:\nCost if wrong: c\n") is None   # empty block
    assert slices.parse_ruling(b"preface\nRuling: one piece\nReasoning: r\nCost if wrong: c\n") is None
    assert slices.parse_ruling(b"Ruling: two pieces\nReasoning: r\nCost if wrong: c\n") is None
    assert slices.parse_ruling(b"Ruling: one piece\nReasoning: r\nReasoning: again\nCost if wrong: c\n") is None
    assert slices.parse_ruling(b"") is None


def test_render_child_is_exact_and_strict():
    p = slices.parse(GOOD)
    title, body = slices.render_child(p.slices[2], 12, "abcd1234", {1: 40, 2: 41})
    assert title == "The client"
    assert body == ("Slice 3 of #12 (bircher shaping abcd1234)\n\n"
                    "Add the page. Call the endpoint. Show the result. Cover it with a component test.\n\n"
                    "Non-goals: styling.\n\nDepends on: #40, #41\n")
    _, first = slices.render_child(p.slices[0], 12, "abcd1234", {})
    assert first.endswith("Depends on: none\n")
    with pytest.raises(slices.PlanError, match="no filed issue"):
        slices.render_child(p.slices[2], 12, "abcd1234", {1: 40})


def test_umbrella_and_completion_bodies():
    p = slices.parse(GOOD)
    filed = {1: 40, 2: 41, 3: 42}
    assert slices.umbrella_body("abcd1234", p, filed) == (
        "bircher: sliced abcd1234\n\n- #40 Slice 1: The store\n- #41 Slice 2: The API\n- #42 Slice 3: The client\n")
    assert slices.completion_body(p, filed, {1: "merged", 2: "merged", 3: "closed"}) == (
        "bircher: sliced complete\n\n- #40 Slice 1: The store — merged\n- #41 Slice 2: The API — merged\n"
        "- #42 Slice 3: The client — closed\n")


def test_inherited_labels_are_the_policy_labels_only():
    assert slices.inherited_labels(["bircher:queued", "bircher:grill", "priority:p1", "bircher:autonomous",
                                    "bircher:running", "bircher:gate-plan"]) == [
        "bircher:autonomous", "bircher:gate-plan", "bircher:grill"]


def test_coarse_is_the_specs_definition():
    assert slices.COARSE.startswith("A slice is a coarse, coherent, independently reviewable capability")
    assert "three to five slices to an epic" in slices.COARSE


def test_store_create_run_takes_a_state(tmp_path):
    s = Store.open(tmp_path / "k.db")
    s.create_run(run_id="r-1", base_repo="o/r", base_sha="0" * 40, state="shaping")
    assert s.run_state("r-1") == "shaping"
    assert s.newest_fact("r-1", EventKind.RUN_STARTED).payload["state"] == "shaping"
    assert s.run_base_repo("r-1") == "o/r"
    s.create_run(run_id="r-2", base_repo="o/r", base_sha="0" * 40)
    assert s.run_state("r-2") == "queued"
