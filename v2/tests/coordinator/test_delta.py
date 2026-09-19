import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from coordinator.delta import canonical_delta, delta_digest

COMPARE_B1 = {"files": [{"filename": "a.txt", "status": "modified",
                         "sha": "blob0000000000000000000000000000000000b1",
                         "patch": "@@ -1 +1 @@\n-old\n+new"}]}
COMPARE_B2 = {"files": [{"filename": "a.txt", "status": "modified",
                         "sha": "blob0000000000000000000000000000000000b2",
                         "patch": "@@ -1 +1 @@\n-old\n+SOMETHING ELSE"}]}
TREE = {"truncated": False, "tree": [{"path": "a.txt", "mode": "100644", "type": "blob"}]}


def _gh_for(compare, tree):
    def gh(args):
        joined = " ".join(args)
        if "/compare/" in joined:
            return json.dumps(compare)
        if "/git/trees/" in joined:
            return json.dumps(tree)
        raise AssertionError(f"unexpected gh call {args}")
    return gh


def test_the_canonical_form_is_sorted_compact_and_carries_mode_and_type():
    text = canonical_delta(COMPARE_B1, TREE)
    assert text == ('[{"entry":"100644|blob","filename":"a.txt","patch":"@@ -1 +1 @@\\n-old\\n+new",'
                    '"previous_filename":null,"sha":"blob0000000000000000000000000000000000b1",'
                    '"status":"modified"}]')


def test_the_digest_is_the_sha256_of_the_canonical_form():
    text = canonical_delta(COMPARE_B1, TREE)
    # +"\n": the retired bash `_pr_delta_digest` piped `jq -cS`'s stdout --
    # always newline-terminated -- straight into `sha256sum`, so that byte is
    # part of every digest it produced. Matched here, not stripped, so this
    # digest agrees with the bash oracle for identical content (see
    # test_the_port_reproduces_the_bash_digest_for_the_fixture below).
    assert delta_digest("o/r", "main", "b1", gh=_gh_for(COMPARE_B1, TREE)) == hashlib.sha256((text + "\n").encode()).hexdigest()


def test_the_port_reproduces_the_bash_digest_for_the_fixture():
    # The frozen answer from the retired `_pr_delta_digest`, captured before
    # its deletion by running it (unmodified) against this exact fixture:
    #   cd .../back-half-closed-loop
    #   tmp=$(mktemp -d); cat > "$tmp/gh" <<'SH' ... SH; chmod +x "$tmp/gh"
    #   PATH="$tmp:$PATH" REPO=o/r bash -c '<source _sha256 + _pr_delta_digest
    #     from batch/run-queue.sh>; _pr_delta_digest main b1'
    # -> d87efb25c38472efc67874cce8fe3f4a7efde9c89324e6577112556031e323e9
    assert delta_digest("o/r", "main", "b1", gh=_gh_for(COMPARE_B1, TREE)) == \
        "d87efb25c38472efc67874cce8fe3f4a7efde9c89324e6577112556031e323e9"


def test_a_changed_patch_changes_the_digest():
    assert delta_digest("o/r", "main", "b1", gh=_gh_for(COMPARE_B1, TREE)) != \
        delta_digest("o/r", "main", "b2", gh=_gh_for(COMPARE_B2, TREE))


def test_a_path_absent_from_the_tree_is_marked_absent():
    assert '"entry":"ABSENT"' in canonical_delta(COMPARE_B1, {"truncated": False, "tree": []})


@pytest.mark.parametrize("compare, tree", [
    ({"files": []}, TREE),                                                   # 0 files
    ({"files": [dict(COMPARE_B1["files"][0]) for _ in range(300)]}, TREE),   # GitHub's cap
    ({"files": [{"filename": "a.txt", "status": "modified", "sha": "x"}]}, TREE),  # patch withheld
    (COMPARE_B1, {"truncated": True, "tree": []}),                           # truncated tree
    (COMPARE_B1, {"tree": []}),                                              # truncated absent, not false
])
def test_every_unprovable_case_yields_no_digest(compare, tree):
    assert canonical_delta(compare, tree) is None
    assert delta_digest("o/r", "main", "b1", gh=_gh_for(compare, tree)) == ""


def test_a_failed_lookup_is_unprovable_not_a_crash():
    def gh(args):
        raise RuntimeError("boom")
    assert delta_digest("o/r", "main", "b1", gh=gh) == ""


def test_the_cli_prints_the_digest_or_nothing(tmp_path, monkeypatch):
    # A fake gh on PATH, answering by URL shape as the self-test's does.
    fake = tmp_path / "gh"
    fake.write_text('#!/bin/bash\ncase "$*" in *"/compare/"*) printf %s \'' + json.dumps(COMPARE_B1) +
                    '\' ;; *"/git/trees/"*) printf %s \'' + json.dumps(TREE) + '\' ;; *) exit 1 ;; esac\n')
    fake.chmod(0o755)
    env = {"PATH": f"{tmp_path}:/usr/bin:/bin", "BIRCHER_GH_REPO": "o/r"}
    r = subprocess.run(["python3", "-m", "coordinator.cli", "delta", "--repo", "o/r", "--base", "main", "--ref", "b1"],
                       capture_output=True, text=True, env=env, cwd=str(Path(__file__).resolve().parents[2]))
    assert r.returncode == 0
    assert r.stdout.strip() == delta_digest("o/r", "main", "b1", gh=_gh_for(COMPARE_B1, TREE))
