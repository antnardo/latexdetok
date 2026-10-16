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

Then, weakest of all, what the kernel defines in plain TeX: `\\def\\x#1#2{` (as many
arguments as undelimited parameters; a delimited one, `#1.`, says nothing we can
read and is left out), `\\let\\x\\y`, `\\chardef\\x`, the registers (`\\newcount`…).
Without them, `\\leavevmode`, `\\strut` or `\\bgroup` were unknown commands. The
primitives themselves come from `primitives.py`.

    python3 scripts/harvest.py
"""

import re
import subprocess
import sys
import tempfile
from pathlib import Path

# Its neighbour, wherever this script is loaded from (the tests load it by its path).
sys.path.insert(0, str(Path(__file__).parent))
from primitives import LEFT_OUT
from primitives import OUTPUT as PRIMITIVES

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
# Plain TeX definitions: `\def\x#1#2{`, `\let\x\y`, `\chardef\x`, the registers.
PREFIXES = r"(?:\\(?:long|protected|outer|global)\s*)*"
PLAIN_DEF = re.compile(
    PREFIXES + r"\\[gex]?def\s*\\(?P<name>[A-Za-z]+)(?![@A-Za-z])(?P<parameters>[^{]*)\{(?P<rest>.*)"
)
PLAIN_LET = re.compile(PREFIXES + r"\\let\s*\\(?P<name>[A-Za-z]+)(?![@A-Za-z])")
PLAIN_REGISTER = re.compile(
    r"\\(?:chardef|mathchardef|countdef|dimendef|skipdef|toksdef|"
    r"newcount|newdimen|newskip|newmuskip|newtoks|newbox|newlength|newsavebox)\s*\\(?P<name>[A-Za-z]+)(?![@A-Za-z])"
)
UNDELIMITED = re.compile(r"(?:#[1-9])*")


def kernel_source(name: str) -> Path:
    found = subprocess.run(["kpsewhich", name], capture_output=True, text=True, check=True)
    return Path(found.stdout.strip())


def target_name(match: re.Match[str]) -> str:
    return match.group("target").strip("{} \t")[1:]


def harvest(lines: list[str]) -> dict[str, tuple[str, str]]:
    """`name → (mode, signature)`; an explicit signature wins over a derived one."""
    return {name: (mode, spec) for name, (mode, spec, _) in harvest_strengths(lines).items()}


def harvest_strengths(lines: list[str]) -> dict[str, tuple[str, str, int]]:
    """`name → (mode, signature, strength)`: the strength of the form it was read in."""
    found: dict[str, tuple[str, str, int]] = {}
    explicit: set[str] = set()
    plain: list[str] = []

    def keep(name: str, mode: str, spec: str, *, strength: int) -> None:
        # -1: plain TeX definition; 0: symbol; 1: number of arguments; 2: ltcmd signature.
        if "@" in name or (name in explicit and strength < 2):
            return
        if strength <= 0 and name in found:
            return
        found[name] = (mode, spec, strength)
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
        plain.append(code)
    # Last, so that any declaration above wins over a `\def` of the same name.
    for code in plain:
        definition = PLAIN_DEF.search(code)
        if definition is not None:
            parameters = definition.group("parameters").strip()
            if UNDELIMITED.fullmatch(parameters):
                mode = "math" if MATH_BODY.search(definition.group("rest")) else "any"
                keep(
                    definition.group("name"),
                    mode,
                    " ".join(["m"] * (len(parameters) // 2)) or "-",
                    strength=-1,
                )
            continue
        for pattern in (PLAIN_LET, PLAIN_REGISTER):
            match = pattern.search(code)
            if match is not None:
                keep(match.group("name"), "any", "-", strength=-1)
    return found


def primitive_names() -> set[str]:
    """The primitives, those of `primitives.txt` and those `primitives.py` leaves out on purpose."""
    names = set(LEFT_OUT)
    for line in PRIMITIVES.read_text(encoding="utf-8").splitlines():
        if line.startswith("\\"):
            names.add(line.split()[0][1:])
    return names


def defined_in_latex(names: set[str]) -> set[str]:
    """Those of `names` a document still has after `\\begin{document}`.

    The kernel uses some of its `\\def` as scratch while it loads (`\\x`, `\\next`), and
    undefines them after: they are no commands of a document.
    """
    with tempfile.TemporaryDirectory() as folder:
        lines = [
            "\\documentclass{article}",
            "\\begin{document}",
            "\\newwrite\\found \\immediate\\openout\\found=defined.txt",
            "\\def\\test#1{\\ifcsname#1\\endcsname\\immediate\\write\\found{#1}\\fi}",
            *(f"\\test{{{name}}}" for name in sorted(names)),
            "\\immediate\\closeout\\found",
            "\\end{document}",
        ]
        (Path(folder) / "defined.tex").write_text("\n".join(lines) + "\n", encoding="utf-8")
        subprocess.run(
            ["lualatex", "-interaction=nonstopmode", "defined.tex"],
            cwd=folder,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            check=False,
        )
        return set((Path(folder) / "defined.txt").read_text(encoding="utf-8").split())


def main() -> None:
    sources = [kernel_source(name) for name in SOURCES]
    strongest: dict[str, tuple[str, str, int]] = {}
    for path in sources:
        for name, value in harvest_strengths(
            path.read_text(encoding="utf-8", errors="replace").splitlines()
        ).items():
            # The strongest form wins across the files too: the `\let\alpha` of latex.ltx
            # does not hide the `\DeclareMathSymbol{\alpha}` of fontmath.ltx.
            if name not in strongest or value[2] > strongest[name][2]:
                strongest[name] = value
    # A primitive is the business of `primitives.py`, even one the kernel lets locally
    # (`\\let\\write\\@gobbletwo`): left out there, it stays out here.
    primitives = primitive_names()
    plain = {name for name, (_, _, strength) in strongest.items() if strength < 0 and name not in primitives}
    strongest = {name: value for name, value in strongest.items() if value[2] >= 0 or name in plain}
    alive = defined_in_latex(plain)
    found = {
        name: (mode, spec)
        for name, (mode, spec, strength) in strongest.items()
        if strength >= 0 or name in alive
    }
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
