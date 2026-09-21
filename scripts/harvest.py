"""Harvests the signatures that can be read in the kernel sources of TeX Live.

Writes `data/kernel-harvested.txt`, which `data/kernel.txt` completes and
corrects. Only the declarations whose signature reads on the line are taken:

- `\\NewDocumentCommand\\x{spec}` and its variants: the exact signature;
- `\\newcommand\\x[2][d]`, `\\DeclareRobustCommand{\\x}[1]`: the number of
  arguments, the first one optional if it has a default value;
- `\\DeclareMathSymbol`, `…Delimiter` (no argument), `…Accent`, `…Radical` (one
  argument): math mode;
- `\\DeclareTextSymbolDefault`, `\\DeclareTextCommandDefault` (no argument),
  `\\DeclareTextAccentDefault` (one argument): text mode.

A `\\DeclareRobustCommand` without `[n]` may hide an `\\@ifstar` or an
`\\@ifnextchar[`: it is harvested with no argument, and it is up to `kernel.txt`
to say better. Internal names (`@`, `_`, `:`) are left out.

    python3 scripts/harvest.py
"""

import re
import subprocess
from pathlib import Path

SOURCES = ("latex.ltx", "fontmath.ltx")
OUTPUT = Path(__file__).parents[1] / "src" / "latexdetok" / "data" / "kernel-harvested.txt"

# The name defined, bare (`\x`) or between braces (`{\x}`).
TARGET = r"(?P<target>\{\s*\\(?:[A-Za-z]+|[^A-Za-z\s])\s*\}|\\(?:[A-Za-z]+|[^A-Za-z\s]))"

DOCUMENT_COMMAND = re.compile(
    r"\\(?:NewDocumentCommand|DeclareDocumentCommand|NewExpandableDocumentCommand)\s*"
    + TARGET
    + r"\s*\{(?P<spec>(?:[^{}]|\{[^{}]*\})*)\}"
)
ARITY_COMMAND = re.compile(
    r"\\(?:newcommand|renewcommand|providecommand|DeclareRobustCommand)\*?\s*"
    + TARGET
    + r"\s*(?:\[(?P<count>\d)\])?\s*(?P<default>\[)?(?P<rest>.*)"
)
DECLARATIONS = (
    (re.compile(r"\\DeclareMath(?:Symbol|Delimiter)\s*" + TARGET), "math", "-"),
    (re.compile(r"\\DeclareMath(?:Accent|Radical)\s*" + TARGET), "math", "m"),
    (re.compile(r"\\DeclareText(?:Symbol|Command)Default\s*" + TARGET), "text", "-"),
    (re.compile(r"\\DeclareTextAccentDefault\s*" + TARGET), "text", "m"),
)
MATH_BODY = re.compile(r"\\math(?:op|rel|bin|ord|char|punct|inner)\b|\\mkern|\\m@th")


def kernel_source(name: str) -> Path:
    found = subprocess.run(["kpsewhich", name], capture_output=True, text=True, check=True)
    return Path(found.stdout.strip())


def target_name(match: re.Match[str]) -> str:
    return match.group("target").strip("{} \t")[1:]


def harvest(lines: list[str]) -> dict[str, tuple[str, str]]:
    """`name → (mode, signature)`; an explicit signature wins over a derived one."""
    found: dict[str, tuple[str, str]] = {}
    explicit: set[str] = set()

    def keep(name: str, mode: str, spec: str, *, strength: int) -> None:
        # 0: symbol; 1: number of arguments; 2: ltcmd signature.
        if "@" in name or (name in explicit and strength < 2):
            return
        if strength == 0 and name in found:
            return
        found[name] = (mode, spec)
        if strength == 2:
            explicit.add(name)

    for line in lines:
        code = re.split(r"(?<!\\)%", line, maxsplit=1)[0]
        for pattern, mode, spec in DECLARATIONS:
            for match in pattern.finditer(code):
                keep(target_name(match), mode, spec, strength=0)
        for match in ARITY_COMMAND.finditer(code):
            count = int(match.group("count") or 0)
            arguments = ["m"] * count
            if match.group("default") and count:
                arguments[0] = "o"
            mode = "math" if MATH_BODY.search(match.group("rest")) else "any"
            keep(target_name(match), mode, " ".join(arguments) or "-", strength=1)
        for match in DOCUMENT_COMMAND.finditer(code):
            keep(target_name(match), "any", " ".join(match.group("spec").split()) or "-", strength=2)
    return found


def main() -> None:
    sources = [kernel_source(name) for name in SOURCES]
    found: dict[str, tuple[str, str]] = {}
    for path in sources:
        for name, value in harvest(path.read_text(encoding="utf-8", errors="replace").splitlines()).items():
            found.setdefault(name, value)
    width = max(len(name) for name in found) + 1
    header = [
        "# Harvested by scripts/harvest.py: do not edit, fix it in kernel.txt instead.",
        *(f"# source: {path}" for path in sources),
        "",
    ]
    rows = [f"\\{name:<{width}} {mode:<5} {spec}" for name, (mode, spec) in sorted(found.items())]
    OUTPUT.write_text("\n".join(header + rows) + "\n", encoding="utf-8")
    print(f"{len(rows)} signatures written to {OUTPUT}")


if __name__ == "__main__":
    main()
