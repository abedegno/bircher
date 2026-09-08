"""Every SKILL.md in the bundle carries YAML frontmatter.

The coordinator's own bundle is the whole bircher checkout, `skills/` included,
and the omnigent server validates every SKILL.md it receives. One file without
frontmatter fails the upload with HTTP 400, `main` exits 3, and no wave starts
at all -- so a missing `---` block is not a documentation nit, it is a runner
that cannot run. Found live on 2026-09-08: two skills written for the front
half shipped without it, and the E2 wave died in preflight.

Nothing in the suite uploads to a real server, which is why this is a
file-shaped test of the property the server checks.
"""
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
SKILLS = sorted((REPO_ROOT / "skills").glob("*/SKILL.md"))


def test_there_are_skills_to_check():
    # A glob that matched nothing would make every case below vacuous.
    assert SKILLS, f"no SKILL.md under {REPO_ROOT / 'skills'}"


@pytest.mark.parametrize("path", SKILLS, ids=lambda p: p.parent.name)
def test_skill_has_frontmatter(path):
    text = path.read_text()
    assert text.startswith("---\n"), (
        f"{path.relative_to(REPO_ROOT)} has no YAML frontmatter: the server "
        "refuses the whole bundle upload (HTTP 400) and no wave starts"
    )
    end = text.find("\n---\n", 4)
    assert end != -1, f"{path.relative_to(REPO_ROOT)}: frontmatter block is not closed"
    block = text[4:end]
    keys = {line.split(":", 1)[0].strip() for line in block.splitlines() if ":" in line}
    assert "name" in keys, f"{path.relative_to(REPO_ROOT)}: frontmatter has no name"
    assert "description" in keys, f"{path.relative_to(REPO_ROOT)}: frontmatter has no description"
