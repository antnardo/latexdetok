"""Diagnostics: what is wrong in a source, said better than TeX says it.

Why one can do better than TeX. TeX reports an error where it notices it, often
far from its cause (“Missing $ inserted”, “Runaway argument?”), does not relate
a closing to its opening, and stops at the first error. The tokeniser knows the
position of every opening and never stops: a `TexDiagnostic` carries the span at
fault, the related places (the opening, the likely cause) and, when it is known,
the fix.

Severity. Nothing that may be valid is an error: that is the tokeniser's rule of
tolerance, applied to messages.

- `error`: TeX would refuse, whatever the context;
- `warning`: a likely mistake, but a context we cannot see may make it valid. A
  file with no `\\documentclass` and no `% !TEX root` is a fragment, which its
  master perhaps includes between `\\begin{x}` and `\\end{x}`; an unknown
  environment may belong to a package that reads its own body (beamer's frames
  close with the name of another environment, and compile); the argument of an
  unknown command is perhaps not typeset;
- `info`: valid, but worth knowing. The body of a definition is only matched
  when it expands (`\\def\\beq{\\begin{equation}}`), the argument of
  `\\directlua` goes off to Lua without being typeset.

Grouping. One mistake often makes several, in TeX as in a naive reading: a brace
forgotten before `\\end{itemize}` also leaves `itemize` and `document` open to
the end of the file. The tokeniser therefore notes events (`StructureReport`)
and the report only yields, at the end of the reading, one diagnostic per cause:
the brace, with as a related place the `\\end` it kept from closing; a stray
`\\end{x}` and a `\\begin{y}` never closed become one single crossed environment.

Positions. Lines counted from 1, columns from 0, as in the tree. The renderings
for the eye and for tools (`render_text`, `to_json`) count columns from 1, like
compilers and VS Code.
"""

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import Enum, StrEnum
from typing import Any

from latexdetok.characters import TEX_ROOT, TEX_ROOT_LINES
from latexdetok.classes import Position, TexCommand, TexComment, TexGroup, TexVerbatim
from latexdetok.logger import logger
from latexdetok.messages import say

__all__ = [
    "CATALOGUE",
    "Cause",
    "Context",
    "Related",
    "Severity",
    "StructureReport",
    "TexDiagnostic",
    "dumps",
    "is_fragment",
    "render_text",
    "similar",
    "to_json",
]


class Severity(StrEnum):
    """The severity of a diagnostic, from the strongest to the weakest."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"

    @property
    def rank(self) -> int:
        """0 for an error, 2 for an info: for sorting and filtering."""
        return list(Severity).index(self)

    @property
    def text(self) -> str:
        """The word to display, in the language in force (see `messages`)."""
        return say(f"severity.{self.value}")

    def counted(self, count: int) -> str:
        """“2 errors”, “1 warning”: the word at the right number."""
        return f"{count} {say(f'severity.{self.value}' + ('s' if count > 1 else ''))}"


@dataclass(frozen=True, slots=True)
class Related:
    """Another place at fault: the opening of a group, what cut it short, the likely cause."""

    start: Position
    end: Position
    message: str


@dataclass(frozen=True, slots=True)
class TexDiagnostic:
    """What is wrong, where, and what goes with it.

    `code` is stable (see `CATALOGUE`): it is what one filters on, not the
    message. `start` and `end`: the span at fault, end excluded. `related`: the
    other useful places, in the order to read them; `suggestion`: the fix, when
    it is known.
    """

    code: str
    severity: Severity
    message: str
    start: Position
    end: Position
    related: tuple[Related, ...] = ()
    suggestion: str | None = None

    def __str__(self) -> str:
        line, col = self.start
        return f"{line}:{col + 1}: {self.severity.text} [{self.code}] {self.message}"


# Stable codes, and what they report.
CATALOGUE: dict[str, str] = {
    "unclosed-brace": "“{” never closed",
    "extra-brace": "“}” with no “{” to open",
    "unclosed-bracket": "optional argument “[” never closed",
    "unclosed-environment": "“\\begin{x}” never closed",
    "end-without-begin": "“\\end{x}” without “\\begin{x}”",
    "crossed-environment": "“\\begin{x}” closed by “\\end{y}”",
    "crossed-environments": "“\\end{x}” before the end of the environment opened inside “x”",
    "unclosed-math": "math never closed, or cut by a blank line",
    "dollar-in-math": "“$” inside math opened by “\\[”, “\\(” or “$$”",
    "math-in-math": "“\\[” or “\\(” inside math already open",
    "stray-math-close": "“\\]” or “\\)” with no math open",
    "unclosed-verbatim": "verbatim never closed",
    "verb-across-lines": "“\\verb” not closed on its line",
    "verbatim-braces": "unbalanced braces in the argument of a verbatim command",
}


class Cause(Enum):
    """What put an end to an opening that had not closed."""

    END_OF_FILE = "end of file"
    BLANK_LINE = "blank line"
    BRACE = "brace"
    END = "end of environment"
    MATH = "end of math"
    BRANCH = "discarded branch"


class Context(Enum):
    """Where the event happens: what decides its severity."""

    NORMAL = "normal"
    DEFINITION = "definition"  # the body of a definition, or an argument that is not typeset (`\\directlua`)
    UNKNOWN_ARGUMENT = "unknown argument"  # inside the argument of an unknown command


@dataclass(frozen=True, slots=True)
class _Unclosed:
    group: TexGroup
    cause: Cause
    # What cut it short: `}`, `\end{x}`, `\]`, the blank line; nothing for the end of the file.
    closer: str
    at: tuple[Position, Position] | None
    context: Context
    owner: TexCommand | None
    known: bool


@dataclass(frozen=True, slots=True)
class _StrayEnd:
    name: str
    start: Position
    end: Position
    context: Context
    known: bool
    # An environment `name` opened beyond unclosed braces: it, and those braces.
    target: TexGroup | None
    braces: tuple[tuple[TexGroup, TexCommand | None], ...]
    # Otherwise, the nearest open environment, which this `\end` was perhaps closing by mistake.
    environment: TexGroup | None
    environment_known: bool


@dataclass(slots=True)
class StructureReport:
    """The structure events of one reading, and the diagnostics drawn from them.

    The tokeniser calls one method per event, where it notices it; `finish`
    groups what shares a cause and yields the diagnostics in the order of the
    text.
    """

    name: str = "<source>"
    document: bool = False
    _unclosed: list[_Unclosed] = field(default_factory=list)
    _strays: list[_StrayEnd] = field(default_factory=list)
    _extra: list[tuple[Position, TexGroup | None, Context]] = field(default_factory=list)
    _direct: list[TexDiagnostic] = field(default_factory=list)

    # Events

    def unclosed(
        self,
        group: TexGroup,
        cause: Cause,
        context: Context,
        closer: str = "",
        at: tuple[Position, Position] | None = None,
        owner: TexCommand | None = None,
        known: bool = True,
    ) -> None:
        """`group` will not close: `closer`, read at `at`, closes what surrounds it."""
        if cause is Cause.BRANCH:
            return  # read apart: what stays open there is a trick of TeX, not a visible mistake
        if group.option and not known:
            return  # a bracket opened by heuristic, with no signature expecting it: perhaps text
        self._unclosed.append(_Unclosed(group, cause, closer, at, context, owner, known))

    def stray_end(
        self,
        name: str,
        start: Position,
        end: Position,
        context: Context,
        known: bool,
        target: TexGroup | None = None,
        braces: Sequence[tuple[TexGroup, TexCommand | None]] = (),
        environment: TexGroup | None = None,
        environment_known: bool = False,
    ) -> None:
        self._strays.append(
            _StrayEnd(name, start, end, context, known, target, tuple(braces), environment, environment_known)
        )

    def extra_brace(self, position: Position, previous: TexGroup | None, context: Context) -> None:
        """A `}` that closes nothing; `previous`, the last brace closed before it at the same level."""
        self._extra.append((position, previous, context))

    def dollar_in_math(self, start: Position, width: int, math: TexGroup, context: Context) -> None:
        opening, closing = math.enclosures()
        dollars = "$" * width
        related = _opening_related(math, say("related.math-opened-here"))
        self._add(
            "dollar-in-math",
            _severity(Severity.ERROR, context),
            say("dollar-in-math", dollars=dollars, opening=opening),
            start,
            (start[0], start[1] + width),
            related,
            suggestion=say("dollar-in-math.fix", closing=closing),
        )

    def math_in_math(
        self, start: Position, end: Position, control: str, math: TexGroup, context: Context
    ) -> None:
        opening = math.enclosures()[0]
        self._add(
            "math-in-math",
            _severity(Severity.ERROR, context),
            say("math-in-math", control=control, opening=opening),
            start,
            end,
            _opening_related(math, say("related.math-opened-here")),
        )

    def stray_math_close(self, start: Position, end: Position, control: str, context: Context) -> None:
        opening = {"\\]": "\\[", "\\)": "\\("}[control]
        self._add(
            "stray-math-close",
            _severity(Severity.ERROR, context),
            say("stray-math-close", control=control, opening=opening),
            start,
            end,
        )

    def unclosed_verbatim(self, verbatim: TexVerbatim, context: Context) -> None:
        assert verbatim.start_position is not None
        self._add(
            "unclosed-verbatim",
            _severity(Severity.ERROR, context),
            say("unclosed-verbatim", opening=verbatim.opening),
            verbatim.start_position,
            _after_opening(verbatim),
            suggestion=say("unclosed-verbatim.fix", closing=verbatim.verbatim_exit_phrase),
        )

    def verb_line_end(self, verbatim: TexVerbatim, context: Context) -> None:
        assert verbatim.start_position is not None
        self._add(
            "verb-across-lines",
            _severity(Severity.ERROR, context),
            say("verb-across-lines", opening=verbatim.opening),
            verbatim.start_position,
            _after_opening(verbatim),
            suggestion=say("verb-across-lines.fix", closing=verbatim.verbatim_exit_phrase),
        )

    def verbatim_braces(self, verbatim: TexVerbatim, context: Context) -> None:
        assert verbatim.start_position is not None
        self._add(
            "verbatim-braces",
            _severity(Severity.WARNING, context),
            say("verbatim-braces", opening=verbatim.opening),
            verbatim.start_position,
            _after_opening(verbatim),
        )

    def _add(
        self,
        code: str,
        severity: Severity,
        message: str,
        start: Position,
        end: Position,
        related: tuple[Related, ...] = (),
        suggestion: str | None = None,
    ) -> None:
        self._direct.append(TexDiagnostic(code, severity, message, start, end, related, suggestion))

    # Grouping

    def finish(self, document: bool) -> list[TexDiagnostic]:
        """One diagnostic per cause, in the order of the text.

        `document`: the file is a document or a declared chapter, not a fragment
        another one includes (see `is_fragment`).
        """
        self.document = document
        explained: set[int] = set()
        diagnostics = list(self._direct)
        diagnostics.extend(self._extra_brace(*extra) for extra in self._extra)
        unclosed = {id(event.group): event for event in self._unclosed}
        for stray in self._strays:
            diagnostics.extend(self._stray(stray, unclosed, explained))
        for event in self._unclosed:
            if id(event.group) not in explained:
                diagnostics.append(self._unclosed_diagnostic(event))
        diagnostics.sort(key=lambda diagnostic: (diagnostic.start, diagnostic.end, diagnostic.code))
        for diagnostic in diagnostics:
            if diagnostic.severity is Severity.INFO:
                logger.debug("%s - %s", self.name, diagnostic)
            else:
                logger.warning("%s - %s", self.name, diagnostic)
        return diagnostics

    def _stray(
        self, stray: _StrayEnd, unclosed: dict[int, _Unclosed], explained: set[int]
    ) -> list[TexDiagnostic]:
        closing = f"\\end{{{stray.name}}}"
        if stray.braces:
            # The environment is open, but beyond braces: those are what is missing.
            if stray.target is not None:
                explained.add(id(stray.target))
            found = []
            for brace, owner in stray.braces:
                if id(brace) in explained:
                    continue
                explained.add(id(brace))
                event = unclosed.get(id(brace))
                context = event.context if event is not None else stray.context
                reason = Related(stray.start, stray.end, say("related.end-before-closing", closing=closing))
                found.append(self._brace(brace, owner, context, (reason,), document=True))
            return found
        crossed = next(
            (
                event
                for event in reversed(self._unclosed)
                if event.cause is Cause.END
                and event.group.env
                and event.group.name == stray.name
                and id(event.group) not in explained
                and event.group.start_position is not None
                and event.group.start_position < stray.start
                and event.at is not None
                and event.at[0] < stray.start
            ),
            None,
        )
        if crossed is not None:
            explained.add(id(crossed.group))
            assert crossed.at is not None
            inner = crossed.group.enclosures()[0]
            severity = Severity.ERROR if crossed.known and stray.known else Severity.WARNING
            return [
                TexDiagnostic(
                    "crossed-environments",
                    _severity(severity, stray.context),
                    say("crossed-environments", closer=crossed.closer, closing=closing),
                    crossed.at[0],
                    crossed.at[1],
                    (
                        *_opening_related(crossed.group, say("related.opened-here.named", opening=inner)),
                        Related(stray.start, stray.end, say("related.closed-too-late")),
                    ),
                    say("crossed-environments.fix", closing=closing, closer=crossed.closer),
                )
            ]
        environment = stray.environment
        if environment is not None and id(environment) in unclosed and id(environment) not in explained:
            explained.add(id(environment))
            opening = environment.enclosures()[0]
            typo = similar(environment.name, stray.name)
            # Closing a known environment through a typo: no package makes that valid.
            # Two unknown neighbouring names (`beamersubsection*`, `beamersubsection`) may be.
            known = stray.known and (typo or stray.environment_known)
            severity = Severity.ERROR if known else Severity.WARNING
            suggestion = None
            if typo:
                suggestion = say(
                    "crossed-environment.fix",
                    end=f"\\end{{{environment.name}}}",
                    begin=f"\\begin{{{stray.name}}}",
                )
            return [
                TexDiagnostic(
                    "crossed-environment",
                    _severity(severity, stray.context),
                    say("crossed-environment", opening=opening, closing=closing),
                    stray.start,
                    stray.end,
                    _opening_related(environment, say("related.opened-here")),
                    suggestion,
                )
            ]
        doubtful = not self.document and environment is None
        return [
            TexDiagnostic(
                "end-without-begin",
                _severity(Severity.ERROR, stray.context, doubtful=doubtful),
                say("end-without-begin", closing=closing, opening=f"\\begin{{{stray.name}}}"),
                stray.start,
                stray.end,
            )
        ]

    def _extra_brace(self, position: Position, previous: TexGroup | None, context: Context) -> TexDiagnostic:
        related: tuple[Related, ...] = ()
        if previous is not None and previous.start_position is not None and previous.end_position is not None:
            opened, closed = previous.start_position[0], previous.end_position[0]
            key = "same-line" if opened == closed else "opened"
            where = say(f"extra-brace.previous.{key}", line=opened)
            line, col = previous.end_position
            related = (Related((line, col - 1), (line, col), say("extra-brace.previous", where=where)),)
        return TexDiagnostic(
            "extra-brace",
            _severity(Severity.ERROR, context, doubtful=not self.document),
            say("extra-brace"),
            position,
            (position[0], position[1] + 1),
            related,
            say("extra-brace.fix"),
        )

    def _unclosed_diagnostic(self, event: _Unclosed) -> TexDiagnostic:
        group = event.group
        reasons: tuple[Related, ...] = ()
        if event.at is not None and event.cause is not Cause.END_OF_FILE:
            reasons = (Related(*event.at, _cut_by(event)),)
        at_end = event.cause is Cause.END_OF_FILE
        if group.bracket:
            return self._brace(
                group, event.owner, event.context, reasons, document=self.document or not at_end
            )
        opening = group.enclosures()[0]
        doubtful = at_end and not self.document
        if group.option:
            return TexDiagnostic(
                "unclosed-bracket",
                _severity(Severity.ERROR, event.context, doubtful=doubtful),
                _owned("unclosed-bracket", event.owner),
                *_opening_span(group),
                reasons,
                say("unclosed-bracket.fix"),
            )
        if group.math:
            closing = group.enclosures()[1]
            blank = ".blank-line" if event.cause is Cause.BLANK_LINE else ""
            return TexDiagnostic(
                "unclosed-math",
                _severity(Severity.ERROR, event.context, doubtful=doubtful),
                say("unclosed-math", opening=opening),
                *_opening_span(group),
                reasons,
                say(f"unclosed-math.fix{blank}", closing=closing),
            )
        return TexDiagnostic(
            "unclosed-environment",
            _severity(Severity.ERROR, event.context, doubtful=doubtful),
            say("unclosed-environment", opening=opening),
            *_opening_span(group),
            reasons,
            say("unclosed-environment.fix", closing=f"\\end{{{group.name}}}"),
        )

    def _brace(
        self,
        group: TexGroup,
        owner: TexCommand | None,
        context: Context,
        reasons: tuple[Related, ...],
        document: bool,
    ) -> TexDiagnostic:
        return TexDiagnostic(
            "unclosed-brace",
            _severity(Severity.ERROR, context, doubtful=not document),
            _owned("unclosed-brace", owner),
            *_opening_span(group),
            tuple(sorted((*reasons, *_brace_clues(group)), key=lambda item: item.start)),
            say("unclosed-brace.fix"),
        )


def _owned(key: str, owner: TexCommand | None) -> str:
    """“{” never closed, or “{” of “\\emph” never closed: the command when we know it."""
    if owner is None:
        return say(key)
    return say(f"{key}.of", command=f"\\{owner.content}")


def _severity(base: Severity, context: Context, doubtful: bool = False) -> Severity:
    if context is Context.DEFINITION:
        return Severity.INFO
    if (doubtful or context is Context.UNKNOWN_ARGUMENT) and base is Severity.ERROR:
        return Severity.WARNING
    return base


def _opening_span(group: TexGroup) -> tuple[Position, Position]:
    assert group.start_position is not None
    return group.start_position, group.inner_start or group.start_position


def _opening_related(group: TexGroup, message: str) -> tuple[Related, ...]:
    if group.start_position is None:
        return ()
    return (Related(*_opening_span(group), message),)


def _after_opening(verbatim: TexVerbatim) -> Position:
    assert verbatim.start_position is not None
    line, col = verbatim.start_position
    return line, col + len(verbatim.opening)


def _cut_by(event: _Unclosed) -> str:
    if event.cause is Cause.BLANK_LINE:
        return say("related.blank-line-cuts-math" if event.group.math else "related.blank-line-cuts-group")
    what = "environment" if event.cause is Cause.END else "math" if event.cause is Cause.MATH else "group"
    return say(f"related.cut-by.{what}", closer=event.closer)


def _brace_clues(group: TexGroup) -> tuple[Related, ...]:
    """Where the brace is probably missing: a comment that closes one, the first blank line."""
    clues: list[Related] = []
    comment = next(
        (node for node in group.content if isinstance(node, TexComment) and _closes_more(node.content)), None
    )
    if comment is not None and comment.start_position is not None and comment.end_position is not None:
        clues.append(
            Related(
                comment.start_position,
                comment.end_position,
                say("related.brace-in-comment", line=comment.start_position[0]),
            )
        )
    paragraph = next((node for node in group.content if node.is_par()), None)
    if paragraph is not None and paragraph.start_position is not None:
        line = paragraph.start_position[0]
        clues.append(
            Related(
                paragraph.start_position, paragraph.start_position, say("related.first-blank-line", line=line)
            )
        )
    return tuple(clues)


def _closes_more(text: str) -> bool:
    return text.count("}") > text.count("{")


def is_fragment(lines: Sequence[str], document: bool) -> bool:
    """A file with no `\\documentclass`, no `\\begin{document}` and no `% !TEX root`: included by another."""
    if document:
        return False
    return not any(TEX_ROOT.match(line) for line in lines[:TEX_ROOT_LINES])


def similar(first: str, second: str, most: int | None = None) -> bool:
    """Two names one or two typos apart (transposition included).

    Without `most`, one typo under six letters, two beyond.
    """
    if first == second or min(len(first), len(second)) < 3:
        return False
    limit = most if most is not None else 1 if max(len(first), len(second)) < 6 else 2
    return _distance(first, second) <= limit


def _distance(first: str, second: str) -> int:
    """Restricted Damerau-Levenshtein distance: insertion, deletion, substitution, transposition."""
    previous_previous: list[int] = []
    previous = list(range(len(second) + 1))
    for i, a in enumerate(first, start=1):
        current = [i] + [0] * len(second)
        for j, b in enumerate(second, start=1):
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (a != b))
            if i > 1 and j > 1 and a == second[j - 2] and first[i - 2] == b:
                current[j] = min(current[j], previous_previous[j - 2] + 1)
        previous_previous, previous = previous, current
    return previous[-1]


# Renderings


def render_text(
    diagnostics: Iterable[TexDiagnostic], lines: Sequence[str], name: str, color: bool = False
) -> str:
    """Compiler-style: `name:line:column: severity [code] message`, then the underlined excerpts."""
    blocks = []
    for diagnostic in diagnostics:
        line, col = diagnostic.start
        header = f"{name}:{line}:{col + 1}: {_paint(diagnostic.severity.text, diagnostic.severity, color)}"
        rows = [f"{header} [{diagnostic.code}] {diagnostic.message}"]
        rows.extend(_excerpt(lines, diagnostic.start, diagnostic.end, "", color))
        for related in diagnostic.related:
            rows.extend(_excerpt(lines, related.start, related.end, related.message, color))
        if diagnostic.suggestion:
            rows.append(f"{'':>6} = {diagnostic.suggestion}")
        blocks.append("\n".join(rows))
    return "\n\n".join(blocks)


EXCERPT_WIDTH = 100
_COLORS = {Severity.ERROR: "\033[1;31m", Severity.WARNING: "\033[1;33m", Severity.INFO: "\033[1;36m"}


def _paint(text: str, severity: Severity, color: bool) -> str:
    return f"{_COLORS[severity]}{text}\033[0m" if color else text


def _excerpt(lines: Sequence[str], start: Position, end: Position, message: str, color: bool) -> list[str]:
    line, col = start
    if not 1 <= line <= len(lines):
        return [f"{'':>6} │ {message}"] if message else []
    text = lines[line - 1].rstrip("\r\n").replace("\t", " ")
    width = max((end[1] if end[0] == line else len(text)) - col, 1)
    # A long line is shown around the column: a one-line paragraph runs to a thousand characters.
    offset = max(0, min(col - EXCERPT_WIDTH // 3, len(text) - EXCERPT_WIDTH))
    shown = text[offset : offset + EXCERPT_WIDTH]
    prefix = "…" if offset else ""
    suffix = "…" if offset + EXCERPT_WIDTH < len(text) else ""
    marker = " " * (col - offset + len(prefix)) + "^" + "~" * (min(width, EXCERPT_WIDTH) - 1)
    note = f" {message}" if message else ""
    return [f"{line:>6} │ {prefix}{shown}{suffix}", f"{'':>6} │ {marker}{note}"]


def to_json(diagnostics: Iterable[TexDiagnostic], name: str) -> list[dict[str, Any]]:
    """Objects ready for `json.dumps`: columns counted from 1, end excluded."""

    def span(start: Position, end: Position) -> dict[str, int]:
        return {"line": start[0], "column": start[1] + 1, "end_line": end[0], "end_column": end[1] + 1}

    return [
        {
            "file": name,
            **span(diagnostic.start, diagnostic.end),
            "severity": str(diagnostic.severity),
            "code": diagnostic.code,
            "message": diagnostic.message,
            "related": [{**span(r.start, r.end), "message": r.message} for r in diagnostic.related],
            "suggestion": diagnostic.suggestion,
        }
        for diagnostic in diagnostics
    ]


def dumps(diagnostics: Iterable[TexDiagnostic], name: str) -> str:
    """`to_json` as text."""
    return json.dumps(to_json(diagnostics, name), ensure_ascii=False, indent=1)
