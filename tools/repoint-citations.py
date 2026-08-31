"""Repoint by CONTENT, taken from the last commit where the citations held.

The inventory's Call column is a rendering of a site, not a substring of it, so
it cannot anchor a search. The line's own text at the last green commit can:
read what line N used to be, find that text in the file now. A line whose old
text is absent or ambiguous is reported, never guessed -- repointing a row onto
a different site is precisely the false claim these tests check for.
"""
import pathlib, re, subprocess, sys

ROOT = pathlib.Path("/Users/jonw/bircher")
BASE = "HEAD"  # the docs at HEAD are consistent with the source at HEAD


def old_lines(path):
    return subprocess.run(["git", "-C", str(ROOT), "show", f"{BASE}:{path}"],
                          capture_output=True, text=True, check=True
                          ).stdout.splitlines()


def new_lines(path):
    return (ROOT / path).read_text().splitlines()


FILES = {"run-queue.sh": "batch/run-queue.sh",
         "kernel-client.sh": "batch/lib/kernel-client.sh",
         "effect-adapter.sh": "batch/lib/effect-adapter.sh"}
OLD = {k: old_lines(v) for k, v in FILES.items()}
NEW = {k: new_lines(v) for k, v in FILES.items()}
problems = []


def remap(fname, old):
    o, n = OLD[fname], NEW[fname]
    if not (0 < old <= len(o)):
        problems.append(f"{fname}:{old} out of range at {BASE}")
        return old
    if 0 < old <= len(n) and n[old - 1] == o[old - 1]:
        return old                      # unmoved
    text = o[old - 1]
    hits = [i for i in range(1, len(n) + 1) if n[i - 1] == text]
    if len(hits) == 1:
        return hits[0]
    # Disambiguate with the neighbouring lines, which a duplicated line rarely
    # shares. Widen until unique or exhausted.
    for w in (1, 2, 3, 5):
        ctx = o[max(0, old - 1 - w): old + w]
        hits = [i for i in range(1, len(n) + 1)
                if n[max(0, i - 1 - w): i + w] == ctx]
        if len(hits) == 1:
            return hits[0]
    problems.append(f"{fname}:{old} {text.strip()[:60]!r} -> {len(hits)} hits")
    return old


# --- the scar/effect matrix -------------------------------------------------
mp = ROOT / "docs/design/scar-effect-matrix.md"
t = mp.read_text()
SRC = re.compile(r"`([\w.-]+):(\d+)`")
t2 = SRC.sub(lambda m: f"`{m.group(1)}:{remap(m.group(1), int(m.group(2)))}`"
             if m.group(1) in FILES else m.group(0), t)
if t2 != t:
    mp.write_text(t2); print("scar-effect-matrix.md repointed")

# --- the effect-site inventory ----------------------------------------------
ip = ROOT / "docs/design/effect-site-inventory.md"
it = ip.read_text()
out = []
for line in it.splitlines():
    m = re.match(r"^\| (\d+) \|", line)
    out.append(re.sub(r"^\| \d+ \|", f"| {remap('run-queue.sh', int(m.group(1)))} |", line)
               if m else line)
ni = "\n".join(out) + ("\n" if it.endswith("\n") else "")
if ni != it:
    ip.write_text(ni); print("effect-site-inventory.md repointed")

for p in problems:
    print("UNRESOLVED:", p)
sys.exit(1 if problems else 0)
