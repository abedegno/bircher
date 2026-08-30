"""Every gh effect argv must name its target repository explicitly.

`gh` resolves an omitted `--repo` from the CURRENT WORKING DIRECTORY's git
remote. For the coordinator that is the bircher checkout, not the repository
under management -- so an effect without `--repo` acts on the WRONG REPOSITORY
and still succeeds.

FOUND IN PRODUCTION, 2026-08-30. The derivation's review comment carried no
`--repo`. Its comments for smoke runs s13 and s14 landed on `abedegno/bircher`
issues #17 and #18 instead of `abedegno/bircher-smoke`, and the kernel
journalled `effect_confirmed` for both -- correctly, because the command DID
succeed. The journal recorded a true fact about a command and a false
impression about the world.

On a muesli run this would have posted cross-vendor review findings into an
unrelated repository, and left the muesli PR with no review record at all.

SECOND INSTANCE OF THE SHAPE. `publish_cmd` ran `git push` in the
coordinator's cwd, where `origin` resolved to bircher; that was fixed with a
subshell `cd` and nothing generalised the lesson. Hence an ENUMERATION rather
than one more spot fix: this fails when effect site N+1 forgets.
"""
import ast
import pathlib

COORDINATOR = pathlib.Path(__file__).resolve().parents[2] / "coordinator"

#: Effect-performing call names. An argv inside one of these acts on the world.
EFFECT_CALLS = frozenset({"perform_effect", "effect"})

#: Subcommands that DO NOT take `--repo`, with the reason. `gh api` is
#: noun-then-URL: the repository is already inside the path, so a `--repo`
#: would be rejected -- the same fact a live run taught us on 2026-08-30.
NO_REPO_FLAG = {("api",): "the repository is part of the URL path"}


def _effect_argvs():
    """Every string-literal argv passed to an effect call."""
    out = []
    for path in sorted(COORDINATOR.glob("*.py")):
        tree = ast.parse(path.read_text(), str(path))
        parent = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parent[child] = node
        for node in ast.walk(tree):
            if not isinstance(node, ast.List):
                continue
            strs = [e.value for e in node.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            if not strs or strs[0] != "gh":
                continue
            cursor, inside = node, False
            while cursor in parent:
                cursor = parent[cursor]
                if isinstance(cursor, ast.Call):
                    fn = cursor.func
                    name = (fn.attr if isinstance(fn, ast.Attribute)
                            else getattr(fn, "id", ""))
                    if name in EFFECT_CALLS:
                        inside = True
                        break
            if inside:
                out.append((f"coordinator/{path.name}", node.lineno, strs))
    return out


def test_every_effect_argv_names_its_repo():
    missing = [(f, n, " ".join(a[:3])) for f, n, a in _effect_argvs()
               if "--repo" not in a and tuple(a[1:2]) not in NO_REPO_FLAG]
    assert not missing, (
        "these effect argvs omit --repo, so `gh` will resolve the target from "
        "the coordinator's own working directory and act on the WRONG "
        f"repository while still succeeding: {missing}")


def test_the_enumeration_can_still_see_the_effect_sites():
    """A guard that finds nothing is indistinguishable from a clean tree.

    Anchored to the two effect argvs present on 2026-08-30 (the PR comment and
    the superseded-sibling close). A refactor that drops this to zero has
    blinded the guard, not cleaned the code.
    """
    assert len(_effect_argvs()) >= 2
