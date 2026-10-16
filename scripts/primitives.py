"""Harvests the primitives of TeX, e-TeX, pdfTeX and LuaTeX, asking the engines themselves.

Writes `data/primitives.txt`, the table read first: the kernel's tables come after
it and win, so that `\\input`, `\\end` or `\\underline` keep what LaTeX makes of them.

Why from the engines. latex.ltx does not define the primitives, it uses them: the
kernel harvest (`harvest.py`) cannot see `\\relax`, `\\hskip` or `\\kern`, and every one
of them was an unknown command — which stops some checks for the rest of its group.

- The list. LuaTeX gives its own (`tex.extraprimitives`); pdfTeX has no such call,
  so every name in its binary is tried in iniTeX, where nothing but the primitives
  is defined (`\\ifcsname`).
- What LaTeX keeps. Each primitive is written in a paragraph of an article, with the
  engine that has it: one LaTeX does not define (“Undefined control sequence”) is
  left out; one TeX refuses outside math (“Missing $ inserted”) is `math`.
- The arguments. A primitive takes none of LaTeX's kind, except those that read a
  braced text right after them; a few read it after other tokens (`\\write\\@auxout{…}`,
  `\\toks0={…}`): left out, so that what follows them stays unknown, as it was — their
  braces are a file's or a register's content, not text to check.

    python3 scripts/primitives.py
"""

import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

OUTPUT = Path(__file__).parents[1] / "src" / "latexdetok" / "data" / "primitives.txt"

# A braced text right after them: its signature, whether it is typeset is up to
# `parser.UNTYPESET_ARGUMENTS` and `conditions.TEXT_ARGUMENT_COMMANDS`.
ARGUMENTS = {
    "hbox": "m",
    "vbox": "m",
    "vtop": "m",
    "vcenter": "m",
    "vadjust": "m",
    "noalign": "m",
    "uppercase": "m",
    "lowercase": "m",
    "message": "m",
    "errmessage": "m",
    "special": "m",
    "mark": "m",
    "detokenize": "m",
    "unexpanded": "m",
    "expanded": "m",
    "scantokens": "m",
    "scantextokens": "m",
    "showtokens": "m",
    "directlua": "m",
    "latelua": "m",
    "luaescapestring": "m",
    "pdfescapestring": "m",
    "pdfescapename": "m",
    "pdfescapehex": "m",
    "pdfunescapehex": "m",
    "pdffilesize": "m",
    "pdffilemoddate": "m",
    "pdfstrcmp": "m m",
    "discretionary": "m m m",
    "mathchoice": "m m m m",
}
# What they read in braces comes after other tokens, or is a template, a file's or a
# register's content: left unknown. The conditionals are `conditions`' business.
LEFT_OUT = frozenset(
    {
        "write",
        "toks",
        "insert",
        "marks",
        "csname",
        "endcsname",
        "halign",
        "valign",
        "pdfliteral",
        "pdfobj",
        "pdfannot",
        "pdfstartlink",
        "pdfoutline",
        "pdfdest",
        "pdfximage",
        "pdfxform",
        "pdfcolorstack",
        "pdfextension",
        "pdfmatch",
        "pdfmdfivesum",
        "pdfsetmatrix",
        "else",
        "fi",
        "or",
        "unless",
    }
)
# They open math: in a paragraph, what TeX refuses is the math they leave unclosed.
MATH_OPENERS = frozenset({"Ustartmath", "Ustartdisplaymath"})
ERROR = re.compile(r"^! (.*)$", re.MULTILINE)


def luatex_primitives(folder: Path) -> set[str]:
    script = folder / "list.lua"
    script.write_text(
        'local out = io.open("luatex.txt", "w")\n'
        'for _, group in ipairs({"tex", "etex", "pdftex", "luatex", "core", "omega", "aleph"}) do\n'
        "  for _, name in ipairs(tex.extraprimitives(group)) do out:write(name, '\\n') end\n"
        "end\n"
        "out:close()\n",
        encoding="utf-8",
    )
    (folder / "list.tex").write_text(
        '\\catcode`\\{=1 \\catcode`\\}=2\n\\directlua{dofile("list.lua")}\n\\end\n', encoding="utf-8"
    )
    run(["luatex", "-ini", "-interaction=nonstopmode", "list.tex"], folder)
    return set((folder / "luatex.txt").read_text(encoding="utf-8").split())


def pdftex_primitives(folder: Path) -> set[str]:
    binary = subprocess.run(["which", "pdftex"], capture_output=True, text=True, check=True).stdout.strip()
    strings = subprocess.run(
        ["strings", "-n", "2", binary], capture_output=True, text=True, check=True
    ).stdout
    names = sorted({name for name in strings.split() if re.fullmatch(r"[A-Za-z]{2,40}", name)})
    lines = [
        "\\catcode`\\{=1 \\catcode`\\}=2 \\catcode`\\#=6",
        "\\immediate\\openout1=pdftex.txt",
        "\\def\\test#1{\\ifcsname#1\\endcsname\\immediate\\write1{#1}\\fi}",
        *(f"\\test{{{name}}}" for name in names),
        "\\immediate\\closeout1",
        "\\end",
    ]
    (folder / "test.tex").write_text("\n".join(lines) + "\n", encoding="utf-8")
    run(["pdftex", "-ini", "-etex", "-interaction=batchmode", "test.tex"], folder)
    return set((folder / "pdftex.txt").read_text(encoding="utf-8").split())


def run(command: list[str], folder: Path) -> None:
    subprocess.run(
        command, cwd=folder, capture_output=True, stdin=subprocess.DEVNULL, timeout=120, check=False
    )


def in_latex(name: str, engine: str) -> tuple[str, str | None]:
    """How LaTeX takes `\\name` in a paragraph: None if it does not define it, else its mode."""
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "p.tex"
        path.write_text(
            f"\\documentclass{{article}}\n\\begin{{document}}\nx \\{name}\\ y\n\\end{{document}}\n"
        )
        try:
            subprocess.run(
                [engine, "-interaction=nonstopmode", "p.tex"],
                cwd=folder,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=60,
                check=False,
            )
            log = (Path(folder) / "p.log").read_text(encoding="utf-8", errors="replace")
        except (subprocess.TimeoutExpired, OSError):
            return name, "any"
    errors = ERROR.findall(log)
    first = errors[0] if errors else ""
    if first.startswith("Undefined control sequence"):
        return name, None
    math = first.startswith("Missing $ inserted") and name not in MATH_OPENERS
    return name, "math" if math else "any"


def main() -> None:
    with tempfile.TemporaryDirectory() as folder:
        luatex = luatex_primitives(Path(folder))
        pdftex = pdftex_primitives(Path(folder))
    names = sorted((luatex | pdftex) - LEFT_OUT - {name for name in luatex | pdftex if name.startswith("if")})
    jobs = [(name, "lualatex" if name in luatex else "pdflatex") for name in names]
    with ThreadPoolExecutor(max_workers=6) as pool:
        modes = dict(pool.map(lambda job: in_latex(*job), jobs))
    kept = {name: mode for name, mode in modes.items() if mode is not None}
    lines = [
        "# Harvested by scripts/primitives.py: do not edit, fix it in kernel.txt instead.",
        f"# LuaTeX: {len(luatex)} primitives, pdfTeX: {len(pdftex)}; {len(kept)} defined in LaTeX.",
        "# Read before the other tables, which win: LaTeX redefines some of them.",
        "",
    ]
    lines += [
        f"\\{name:<34} {mode:<5} {ARGUMENTS.get(name, '-')}".rstrip() for name, mode in sorted(kept.items())
    ]
    OUTPUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    left = sorted(name for name, mode in modes.items() if mode is None)
    print(f"{len(kept)} primitives written to {OUTPUT.name}; {len(left)} not in LaTeX", file=sys.stderr)


if __name__ == "__main__":
    main()
