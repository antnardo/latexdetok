"""Compiles the cases of `tests/fixtures/refused.json` with LuaLaTeX: does TeX agree?

Every rule of what LaTeX refuses (`semantics`) rests on cases compiled one by one:
the refused ones must stop on an error, the others must compile. The tests check
that latexdetok reports the first and not the second; this script checks the
table itself, against the TeX installed — after an update of TeX Live, or a case
added by hand.

    python3 scripts/oracle.py            # every case
    python3 scripts/oracle.py too-many-columns
"""

import json
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

TABLE = Path(__file__).parents[1] / "tests" / "fixtures" / "refused.json"
ERROR = re.compile(r"^! (.*)$", re.MULTILINE)


def source(case: dict[str, str], template: str) -> str:
    if "full" in case:
        return case["full"]
    return template.replace("{preamble}", case.get("preamble", "")).replace("{body}", case.get("body", ""))


def compile_case(text: str) -> list[str]:
    """The errors TeX stops on, in order."""
    with tempfile.TemporaryDirectory() as folder:
        (Path(folder) / "case.tex").write_text(text, encoding="utf-8")
        subprocess.run(
            ["lualatex", "-interaction=nonstopmode", "case.tex"],
            cwd=folder,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=120,
            check=False,
        )
        log = (Path(folder) / "case.log").read_text(encoding="utf-8", errors="replace")
    return ERROR.findall(log)


def main() -> None:
    table = json.loads(TABLE.read_text(encoding="utf-8"))
    rules = set(sys.argv[1:])
    cases = [case for case in table["cases"] if not rules or case["rule"] in rules]
    with ThreadPoolExecutor(max_workers=6) as pool:
        verdicts = list(pool.map(lambda case: compile_case(source(case, table["template"])), cases))
    wrong = 0
    for case, errors in zip(cases, verdicts, strict=True):
        if bool(errors) != case["refused"]:
            wrong += 1
            body = (case.get("full") or case.get("body", "")).replace("\n", "⏎")
            print(f"{case['rule']:<26} {'refused' if case['refused'] else 'accepted'} expected: {body[:70]}")
            print(f"{'':<26} TeX: {errors[0] if errors else 'compiles'}")
    print(f"{len(cases)} cases, {wrong} where TeX disagrees")
    sys.exit(1 if wrong else 0)


if __name__ == "__main__":
    main()
