"""Binding the arguments: which elements belong to a known command.

The tokeniser opens a binding behind every command (or environment) whose
signature has arguments, then offers it the following elements of the same
group, in order. Binding moves nothing in the tree: it notes each element it
keeps in `arguments`.

The rules follow TeX's own reading of arguments:

- blanks and comments do not count (TeX skips them), nor does a branch of a
  condition without braces (see `TexBranch`): TeX reads what follows as if the
  taken branch were written there, and never read the other one;
- a missing optional argument `o`, `d()`, `t*` lets the element through to the
  next one;
- a mandatory argument `m` takes the element whatever it is, a single character
  if it is text (`\\frac12`); a blank line leaves it missing, as with
  `Paragraph ended before…` in TeX, except for a long argument `+m`, which
  takes it;
- an `l` argument takes everything up to the next braced group: that is the
  parameter text of `\\def\\x#1#2{…}`. It may span several elements
  (`#1\\relax#2`); the binding only keeps the first, and whoever needs to know
  whether it stood alone compares its end with the start of the group;
- an unbound type (`e`, `u`, `b`…) stops the binding: better to bind nothing
  than to bind wrong.
"""

from latexdetok.classes import TexBranch, TexCommand, TexContainer, TexGroup
from latexdetok.signatures import ArgumentSpec

__all__ = ["ArgumentBinding"]


class ArgumentBinding:
    """Arguments a command or an environment expects, filled in as reading goes on."""

    def __init__(self, target: TexCommand | TexGroup, specs: tuple[ArgumentSpec, ...]) -> None:
        self.target = target
        self._specs = specs
        self._arguments: list[TexContainer | None] = []
        # First element of the `l` argument under way, filed when the brace comes.
        self._pending: TexContainer | None = None
        target.arguments = self._arguments

    @property
    def _index(self) -> int:
        return len(self._arguments)

    @property
    def done(self) -> bool:
        return self._index >= len(self._specs)

    def _remaining(self) -> tuple[ArgumentSpec, ...]:
        return self._specs[self._index :]

    def accepts_opener(self, char: str) -> bool:
        """Does this character open an expected optional argument in brackets?"""
        for spec in self._remaining():
            if not spec.bound or spec.kind not in "oOdDt":
                return False
            if spec.kind != "t" and spec.opener == char:
                return char == "["
        return False

    def wants_character(self, char: str) -> str | None:
        """What a text character would do: `"token"`, `"delimited"`, or nothing."""
        for spec in self._remaining():
            if not spec.bound:
                return None
            if spec.kind in "oO":
                continue
            if spec.kind == "t":
                if spec.delimiters == char:
                    return "token"
                continue
            if spec.kind in "dDrR":
                if spec.opener == char:
                    return "delimited"
                if spec.kind in "dD":
                    continue
                return None
            return "token" if spec.kind == "m" else None
        return None

    def wants_command(self) -> bool:
        """Would a command read now be a plain token of the binding?

        An `m` argument without braces, or a token of the `l` parameter text.
        """
        for spec in self._remaining():
            if spec.bound and spec.kind in "oOdDt":
                continue
            return spec.kind in "ml"
        return False

    def consume(self, node: TexContainer) -> bool:
        """Keep `node` if it belongs to the binding; otherwise the binding is over."""
        if node.is_comment() or (isinstance(node, TexBranch) and not node.braced):
            return True
        arguments = self._arguments
        for spec in self._remaining():
            if not spec.bound:
                return False
            if spec.kind in "oO":
                if node.is_option_group():
                    arguments.append(node)
                    return True
            elif spec.kind == "t":
                if node.is_pure_text() and node.content == spec.delimiters:
                    arguments.append(node)
                    return True
            elif spec.kind in "dDrR":
                if node.is_pure_text() and _delimited_by(node.content, spec):
                    arguments.append(node)
                    return True
                if spec.kind in "rR":
                    return False
            elif spec.kind == "m":
                if node.is_par() and not spec.long:
                    return False
                arguments.append(node)
                return True
            elif spec.kind == "l":
                if not node.is_bracket_group():
                    if node.is_par():
                        return False
                    if self._pending is None:
                        self._pending = node
                    return True
                arguments.append(self._pending)
                self._pending = None
                continue
            else:
                return False
            arguments.append(None)
        return False

    def close(self) -> None:
        """What was not found is absent (optional) or missing (mandatory)."""
        self._arguments.extend([None] * (len(self._specs) - self._index))


def _delimited_by(content: str, spec: ArgumentSpec) -> bool:
    return len(content) >= 2 and content[0] == spec.opener and content[-1] == spec.closer
