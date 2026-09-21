"""Compact views of a tree: an outline in text, an HTML page.

Why. `repr_hierarchy()` shows every node: more than two lines of output per line
of source, unreadable beyond one page. Of a long document, one wants to see the
frame — sections, environments, boxes, branches of conditionals — and where it
is in the source. The rest is summed up: how many words, formulas and `\\item`
in each block.

Everything starts from a summary of the tree in blocks (`Block`):

- a section runs from its command to the next section of the same rank or of a
  higher one; it is not a group of the tree, but it is how one reads a document;
- an environment, and a verbatim over several lines, are blocks;
- a taken branch of a conditional (`TexBranch`) is text TeX reads: its content
  goes back to the block around it, and every block that comes out of it knows
  which side it is on (`side`). A discarded branch is only a block if it holds
  structure; otherwise it is counted;
- a command one of whose arguments spans several lines too
  (`\\rmqphys{…}`, `\\newcommand{\\x}{…}`): those are the boxes and the
  definitions;
- math, and a math environment, do not open: their content would teach the
  outline nothing;
- everything else is counted in the block that holds it.

Two renderings:

- `outline`: the plan, for the terminal. Neighbouring blocks of the same name
  and of few lines are grouped (`align ×6`), the depth is limited;
- `html_page`: the two views of one document, the compact one (the LaTeX as
  written, shorthands included) and the expanded one (the expanded view, see
  `expansion`), with outline and text. A switch flips the whole document; every
  use of a macro, in the text, and every block, in the outline, expands or folds
  on its own.
"""

import html
import json
import re
from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

from latexdetok.analyse import TexFile
from latexdetok.classes import (
    Position,
    TexBranch,
    TexCommand,
    TexComment,
    TexContainer,
    TexGroup,
    TexVerbatim,
)
from latexdetok.conditions import MATH_ENVIRONMENTS
from latexdetok.diagnostics import Severity, TexDiagnostic
from latexdetok.expansion import ExpandedFile, Expansion, expand

__all__ = ["Block", "blocks", "html_page", "outline"]

SECTION_LEVELS = {
    "part": 0,
    "chapter": 1,
    "section": 2,
    "subsection": 3,
    "subsubsection": 4,
    "paragraph": 5,
    "subparagraph": 6,
}
# A command whose name says something: not a symbol (`\\\\`, `\\{`), not an accent (`\\c`),
# not layout, not a token of a conditional (see `_notable`).
NOTABLE_COMMAND = re.compile(r"[A-Za-z@]{2,}")
LAYOUT_COMMANDS = frozenset(
    {
        "par",
        "relax",
        "strut",
        "noindent",
        "indent",
        "hspace",
        "vspace",
        "hfill",
        "vfill",
        "smallskip",
        "medskip",
        "bigskip",
        "newline",
        "linebreak",
        "centering",
        "protect",
        "leavevmode",
        "ignorespaces",
        "unskip",
        "global",
    }
)
MATH = "$…$"
DISPLAY_MATH = "\\[…\\]"
WORDS = "words"
SKIPPED = "discarded branches"
# The singular of the counted keys. Cutting the final “s” would give “branche”;
# anything absent here is a command name, which keeps its spelling.
SINGULARS = {
    WORDS: "word",
    SKIPPED: "discarded branch",
    "lines": "line",
    "macro uses": "macro use",
    "decided conditionals": "decided conditional",
    "diagnostics": "diagnostic",
}
LABEL_WIDTH = 48
STATS_SHOWN = 4


@dataclass(slots=True)
class Block:
    """A block of the frame: its kind, its name, its lines, its sub-blocks and the count of the rest.

    `kind`: `root`, `section`, `environment`, `math` (a math environment),
    `command` (a box or a definition), `branch`, `verbatim`. `stats` counts what
    the block holds outside its sub-blocks: words, formulas, commands by name.
    `side`: when the block is inside a decided branch, True in a taken branch,
    False in a discarded one.
    """

    kind: str
    label: str
    start: int
    end: int
    children: list["Block"] = field(default_factory=list)
    stats: Counter[str] = field(default_factory=Counter)
    level: int = 0
    side: bool | None = None

    @property
    def lines(self) -> int:
        return self.end - self.start + 1

    def walk(self, depth: int = 0) -> Iterator[tuple["Block", int]]:
        yield self, depth
        for child in self.children:
            yield from child.walk(depth + 1)


def blocks(tex: TexFile) -> Block:
    """The frame of an analysed file (or of an expanded view)."""
    return _Builder(tex).build()


class _Builder:
    def __init__(self, tex: TexFile) -> None:
        self._tex = tex
        self._side: bool | None = None

    def build(self) -> Block:
        root = Block("root", self._tex.name, 1, max(len(self._tex.lines), 1))
        self._fill(root, self._tex.container.content)
        return root

    def _fill(self, parent: Block, nodes: Sequence[TexContainer]) -> None:
        sections: list[Block] = []
        skipped: set[int] = set()
        for index, node in enumerate(nodes):
            if id(node) in skipped:
                continue
            owner = sections[-1] if sections else parent
            start, end = _lines(node, parent.start)
            if (
                isinstance(node, TexCommand)
                and node.base_name in SECTION_LEVELS
                and node.arguments is not None
            ):
                level = SECTION_LEVELS[node.base_name]
                while sections and sections[-1].level >= level:
                    closed = sections.pop()
                    closed.end = max(closed.start, start - 1)
                title = _title(node)
                label = f"{node.base_name} · {title}" if title else node.base_name
                block = Block("section", label, start, parent.end, level=level, side=self._side)
                (sections[-1] if sections else parent).children.append(block)
                sections.append(block)
                skipped.update(id(argument) for argument in node.arguments if argument is not None)
                continue
            if isinstance(node, TexBranch):
                self._branch(node, owner, start, end)
            elif isinstance(node, TexGroup) and node.math:
                owner.stats[DISPLAY_MATH if node.displaymath else MATH] += 1
            elif isinstance(node, TexGroup) and node.env:
                kind = "math" if node.name in MATH_ENVIRONMENTS else "environment"
                block = Block(kind, node.name, start, end, side=self._side)
                owner.children.append(block)
                if kind == "environment":
                    self._fill(block, node.content)
            elif isinstance(node, TexGroup):
                self._fill(owner, node.content)  # braces and brackets: their content goes back to the block
            elif isinstance(node, TexVerbatim):
                if end > start:
                    owner.children.append(Block("verbatim", node.name, start, end, side=self._side))
                else:
                    owner.stats[f"\\{node.name}"] += 1
            elif isinstance(node, TexCommand):
                arguments = _arguments(node, nodes, index)
                if any(_lines(argument, start)[1] > start for argument in arguments):
                    last = _lines(arguments[-1], start)[1]
                    block = Block("command", _command_label(node, arguments), start, last, side=self._side)
                    owner.children.append(block)
                    for argument in arguments:
                        skipped.add(id(argument))
                        self._fill(block, argument.content if isinstance(argument, TexGroup) else [argument])
                elif _notable(node):
                    owner.stats[f"\\{node.base_name}"] += 1
            elif isinstance(node, TexComment):
                continue
            elif node.is_pure_text() and node.content:
                words = len(node.content.split())
                owner.stats[WORDS] += words

    def _branch(self, node: TexBranch, owner: Block, start: int, end: int) -> None:
        test = getattr(node.condition, "test", str(node.condition))
        outer = self._side
        if node.taken:
            self._side = outer if outer is False else True
            self._fill(owner, node.content)
        else:
            self._side = False
            block = Block("branch", f"discarded · {test}", start, end, side=False)
            self._fill(block, node.content)
            if block.children:
                owner.children.append(block)
            else:
                owner.stats[SKIPPED] += 1
        self._side = outer


def _notable(command: TexCommand) -> bool:
    name = command.base_name
    return (
        command.role is None and name not in LAYOUT_COMMANDS and NOTABLE_COMMAND.fullmatch(name) is not None
    )


def _lines(node: TexContainer, default: int) -> tuple[int, int]:
    start = node.start_position[0] if node.start_position is not None else default
    end = node.end_position[0] if node.end_position is not None else start
    return start, max(start, end)


def _arguments(command: TexCommand, nodes: Sequence[TexContainer], index: int) -> list[TexContainer]:
    """Bound arguments, or, for an unknown command, the groups that follow it."""
    if command.arguments is not None:
        return [argument for argument in command.arguments if argument is not None]
    following: list[TexContainer] = []
    for node in nodes[index + 1 :]:
        if not (node.is_bracket_group() or node.is_option_group()):
            break
        following.append(node)
    return following


def _short(text: str, width: int = LABEL_WIDTH) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= width else text[: width - 1] + "…"


def _title(command: TexCommand) -> str:
    groups = [argument for argument in command.arguments or [] if isinstance(argument, TexGroup)]
    return _short(groups[-1].arg()) if groups else ""


def _command_label(command: TexCommand, arguments: Sequence[TexContainer]) -> str:
    first = arguments[0] if arguments else None
    single_line = (
        isinstance(first, TexGroup)
        and first.start_position is not None
        and first.end_position is not None
        and first.start_position[0] == first.end_position[0]
    )
    if isinstance(first, TexGroup) and single_line and len(first.arg()) <= LABEL_WIDTH:
        return f"{command.base_name} · {_short(first.arg())}"
    return command.base_name


def _stats(stats: Counter[str], limit: int = STATS_SHOWN) -> str:
    """`340 words · 12 \\item · 9 $…$`: the words first, then the most frequent."""
    items = [(key, count) for key, count in stats.most_common() if key != WORDS][: limit - 1]
    parts = [_counted(stats[WORDS], WORDS)] if stats[WORDS] else []
    parts += [_counted(count, key) for key, count in items]
    return " · ".join(parts)


def _counted(count: int, key: str) -> str:
    """`1 word`, `2 words`: the keys of `SINGULARS` agree; command names stay as they are."""
    return f"{count} {SINGULARS[key] if count == 1 and key in SINGULARS else key}"


# Outline in text

TREE_BRANCH, TREE_LAST, TREE_PIPE, TREE_SPACE = "├ ", "└ ", "│ ", "  "
ANSI = {
    "section": "\033[1;34m",
    "environment": "\033[36m",
    "math": "\033[33m",
    "command": "\033[35m",
    "verbatim": "\033[33m",
    "taken": "\033[32m",
    "skipped": "\033[31m",
    "dim": "\033[2m",
    "reset": "\033[0m",
}


def _problems(tex: TexFile) -> list[TexDiagnostic]:
    """The diagnostics to show: not the infos, which report valid LaTeX."""
    return [diagnostic for diagnostic in tex.diagnostics if diagnostic.severity is not Severity.INFO]


def outline(
    tex: TexFile, depth: int | None = None, min_lines: int = 3, width: int = 110, color: bool = False
) -> str:
    """The outline of the document: one block per line, short neighbours grouped, depth limited."""
    root = blocks(tex)
    count = sum(1 for _ in tex.container.iter())
    problems = len(_problems(tex))
    header = f"{tex.name} · {len(tex.lines)} lines · {count} nodes · {problems} diagnostic" + (
        "s" if problems > 1 else ""
    )
    rows = [header]
    if root.stats:
        rows.append(f"{'':>6}  {_paint(_stats(root.stats), 'dim', color)}")
    _outline_children(root, "", 1, depth, min_lines, width, color, rows)
    return "\n".join(rows)


def _outline_children(
    block: Block,
    prefix: str,
    level: int,
    depth: int | None,
    min_lines: int,
    width: int,
    color: bool,
    rows: list[str],
) -> None:
    items = _grouped(block.children, min_lines)
    for position, (child, repeat, last_end) in enumerate(items):
        is_last = position == len(items) - 1
        glyph = TREE_LAST if is_last else TREE_BRANCH
        name = child.label + (f" ×{repeat}" if repeat > 1 else "")
        hidden = depth is not None and level >= depth and bool(child.children)
        stats = _stats(_with_hidden(child) if hidden else child.stats)
        tree = prefix + glyph
        span = f"{child.start}–{last_end}" if last_end > child.start else f"{child.start}"
        tail = f"  {stats}" if stats else ""
        text = _short(name + tail, width - len(tree) - len(span) - 10)
        padding = " " * max(1, width - 8 - len(tree) - len(text) - len(span))
        cut = min(len(name), len(text))
        painted = _paint(text[:cut], _style(child), color) + _paint(text[cut:], "dim", color)
        rows.append(f"{child.start:>6}  {tree}{painted}{padding}{_paint(span, 'dim', color)}")
        if repeat == 1 and not hidden:
            _outline_children(
                child,
                prefix + (TREE_SPACE if is_last else TREE_PIPE),
                level + 1,
                depth,
                min_lines,
                width,
                color,
                rows,
            )


def _grouped(children: list[Block], min_lines: int) -> list[tuple[Block, int, int]]:
    """Short neighbours of the same name in one entry: (first, count, last line)."""
    items: list[tuple[Block, int, int]] = []
    for child in children:
        short = child.lines < min_lines and not child.children and child.kind != "section"
        if items and short:
            first, repeat, _ = items[-1]
            if first.label == child.label and first.lines < min_lines and not first.children:
                items[-1] = (first, repeat + 1, child.end)
                continue
        items.append((child, 1, child.end))
    return items


def _with_hidden(block: Block) -> Counter[str]:
    total: Counter[str] = Counter(block.stats)
    for child, _ in block.walk():
        if child is not block:
            total.update(child.stats)
            total[child.label.split(" · ")[0]] += 1
    return total


def _style(block: Block) -> str:
    return "skipped" if block.side is False else block.kind


def _paint(text: str, style: str, color: bool) -> str:
    if not color or not text or style not in ANSI:
        return text
    return f"{ANSI[style]}{text}{ANSI['reset']}"


# HTML page: the two views

PAGE_STYLE = """
:root { --ground:#fbfaf7; --ink:#23221f; --muted:#7c776d; --rule:#e3dfd6; --hover:#efe9dc; --flash:#fff1b8;
  --developed:hsla(172,45%,45%,.13); --accent:hsl(172,40%,38%);
  --section:hsl(222,62%,72%); --environment:hsl(190,40%,70%); --math:hsl(38,85%,68%);
  --command:hsl(172,38%,62%); --branch:hsl(130,42%,58%); --skipped:#b9b4ab; --verbatim:hsl(25,25%,65%);
  --root:#b9b4ab; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --ground:#1c1b19; --ink:#e9e6df;
  --muted:#9b958a; --rule:#34322e; --hover:#2b2926; --flash:#4a4221; --developed:hsla(172,45%,55%,.16);
  --accent:hsl(172,45%,62%); } }
:root[data-theme="dark"] { --ground:#1c1b19; --ink:#e9e6df; --muted:#9b958a; --rule:#34322e; --hover:#2b2926;
  --flash:#4a4221; --developed:hsla(172,45%,55%,.16); --accent:hsl(172,45%,62%); }
body { background:var(--ground); color:var(--ink); font:13px/1.45 ui-sans-serif,system-ui,sans-serif; }
header { padding:8px 16px; border-bottom:1px solid var(--rule); display:flex; gap:16px; align-items:center;
  flex-wrap:wrap; }
header h1 { font-size:15px; margin:0; }
.meta, .legend, .hint { color:var(--muted); }
.switch { display:inline-flex; border:1px solid var(--rule); border-radius:6px; overflow:hidden; }
.switch button { font:inherit; color:inherit; background:none; border:0; padding:4px 10px; cursor:pointer; }
.switch button[aria-pressed="true"] { background:var(--accent); color:var(--ground); }
.legend span { display:inline-flex; align-items:center; gap:4px; margin-right:8px; }
.legend i, .kind { width:9px; height:9px; border-radius:2px; display:inline-block; flex:none; }
main { display:grid; grid-template-columns:minmax(280px,1.1fr) minmax(340px,2fr);
  height:calc(100vh - 50px); }
main > * { overflow:auto; min-width:0; }
#outline { padding:8px 8px 40px; border-right:1px solid var(--rule); }
.blk .kids { margin-left:14px; }
.row { display:flex; align-items:center; gap:5px; padding:1px 4px; border-radius:3px; white-space:nowrap; }
.row:hover { background:var(--hover); }
.row .open { width:12px; flex:none; color:var(--muted); cursor:pointer; user-select:none; text-align:center; }
.row .label { cursor:pointer; overflow:hidden; text-overflow:ellipsis; }
.row .span, .row .stats { color:var(--muted); font-size:11px; }
.row .stats { overflow:hidden; text-overflow:ellipsis; }
.row .flip { font:inherit; font-size:11px; border:1px solid var(--rule); background:none; color:var(--muted);
  border-radius:4px; padding:0 5px; cursor:pointer; }
.row .flip:hover { color:var(--accent); border-color:var(--accent); }
.blk.flipped > .row .flip { background:var(--accent); color:var(--ground); border-color:var(--accent); }
.blk.flipped > .kids { border-left:2px solid var(--accent); padding-left:4px; }
#text { margin:0; padding:8px 12px 40px 0; font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;
  white-space:pre; tab-size:4; }
#text .n { display:inline-block; width:4.2em; padding-right:10px; text-align:right; color:var(--muted);
  font-style:normal; user-select:none; }
#text .n.warn { color:#d6453d; font-weight:700; }
#text .flash { background:var(--flash); }
#text .skip { opacity:.42; }
.u > .x { display:none; }
.u > .s { cursor:pointer; border-bottom:1px dotted var(--rule); }
.u > .x { cursor:pointer; background:var(--developed); border-radius:2px; }
.u.open > .s { display:none; } .u.open > .x { display:inline; }
body.dev .u:not(.closed) > .s { display:none; } body.dev .u:not(.closed) > .x { display:inline; }
body.dev .u.closed > .s { display:inline; } body.dev .u.closed > .x { display:none; }
.u.hot > .s, .u.hot > .x { outline:1px solid var(--accent); outline-offset:1px; }
.x:empty::after { content:"∅"; color:var(--muted); }
"""

PAGE_SCRIPT = """
const DATA = JSON.parse(document.getElementById('data').textContent);
const body = document.body;
const text = document.getElementById('text');
const outline = document.getElementById('outline');
const uses = [...text.querySelectorAll('.u')].map(n => ({n, a: +n.dataset.a, b: +n.dataset.b}));
const KINDS = {section: 1, environment: 1, math: 1, command: 1, branch: 1, skipped: 1, verbatim: 1};

function range(block) { return [block.a, block.b]; }
function hasUses(a, b) { return uses.some(u => u.a >= a && u.b <= b); }
// The largest blocks of `root` within lines a–b; the block itself, found again in the other view,
// opens onto its sub-blocks.
function within(root, a, b, label) {
  const found = [];
  (function visit(block) {
    for (const child of block.c) {
      const [ca, cb] = range(child);
      if (cb < a || ca > b) continue;
      const same = ca === a && cb === b && child.l === label;
      if (ca >= a && cb <= b && !same) found.push(child);
      else visit(child);
    }
  })(root);
  return found;
}
function developUses(a, b, developed) {
  for (const u of uses) {
    if (u.a < a || u.b > b) continue;
    if (body.classList.contains('dev')) u.n.classList.toggle('closed', !developed);
    else u.n.classList.toggle('open', developed);
  }
}
function show(line) {
  const marker = document.getElementById('L' + line);
  if (!marker) return;
  // A line folded inside an expanded use: we show the use.
  let target = marker;
  while (target && !target.getClientRects().length) target = target.parentElement.closest('.u');
  target = target || marker;
  target.scrollIntoView({block: 'center'});
  target.classList.add('flash');
  setTimeout(() => target.classList.remove('flash'), 1200);
}
function row(block, side, depth) {
  const element = document.createElement('div');
  element.className = 'blk';
  const head = document.createElement('div');
  head.className = 'row';
  const kids = document.createElement('div');
  kids.className = 'kids';
  let opened = depth < 2, flipped = false;
  const other = side === 'source' ? 'view' : 'source';
  const children = () => flipped ? within(DATA[other], block.a, block.b, block.l) : block.c;
  const toggle = document.createElement('span');
  toggle.className = 'open';
  const fill = () => {
    kids.replaceChildren();
    const list = children();
    toggle.textContent = list.length ? (opened ? '▾' : '▸') : '';
    if (opened) for (const child of list) kids.appendChild(row(child, flipped ? other : side, depth + 1));
  };
  toggle.onclick = () => { opened = !opened; fill(); };
  const swatch = document.createElement('i');
  swatch.className = 'kind';
  swatch.style.background = 'var(--' + (KINDS[block.k] ? block.k : 'root') + ')';
  const label = document.createElement('span');
  label.className = 'label';
  label.textContent = block.l;
  label.onclick = () => show(block.a);
  const span = document.createElement('span');
  span.className = 'span';
  span.textContent = block.a === block.b ? block.a : block.a + '–' + block.b;
  const stats = document.createElement('span');
  stats.className = 'stats';
  stats.textContent = block.t;
  head.append(toggle, swatch, label, span);
  if (hasUses(block.a, block.b)) {
    const flip = document.createElement('button');
    flip.className = 'flip';
    flip.title = side === 'source' ? 'Expand this block' : 'Back to the source of this block';
    flip.textContent = '⇄';
    flip.onclick = () => {
      flipped = !flipped;
      element.classList.toggle('flipped', flipped);
      developUses(block.a, block.b, (flipped ? other : side) === 'view');
      opened = true;
      fill();
    };
    head.append(flip);
  }
  head.append(stats);
  element.append(head, kids);
  fill();
  return element;
}
function setMode(mode) {
  body.classList.toggle('dev', mode === 'view');
  for (const u of uses) u.n.classList.remove('open', 'closed');
  for (const button of document.querySelectorAll('.switch button'))
    button.setAttribute('aria-pressed', String(button.dataset.mode === mode));
  outline.replaceChildren(...DATA[mode].c.map(block => row(block, mode, 0)));
  if (location.hash !== '#' + mode) history.replaceState(null, '', '#' + mode);
}
document.querySelector('.switch').addEventListener('click', event => {
  const button = event.target.closest('button');
  if (button) setMode(button.dataset.mode);
});
text.addEventListener('click', event => {
  if (String(window.getSelection())) return;
  const use = event.target.closest('.u');
  if (!use) return;
  use.classList.toggle(body.classList.contains('dev') ? 'closed' : 'open');
});
let hot = null;
text.addEventListener('mouseover', event => {
  const use = event.target.closest('.u');
  if (use === hot) return;
  if (hot) hot.classList.remove('hot');
  hot = use;
  if (hot) hot.classList.add('hot');
});
setMode(location.hash === '#view' ? 'view' : 'source');
"""

LEGEND = (
    ("section", "section"),
    ("environment", "environment"),
    ("math", "math"),
    ("command", "box or definition"),
    ("branch", "discarded branch (block)"),
    ("verbatim", "verbatim"),
)


def html_page(tex: TexFile, view: ExpandedFile | None = None) -> str:
    """A standalone page on the two views of a document: the compact one and the expanded one.

    `tex` is the analysed file; `view`, its expanded view, computed if it is not
    given (a view passed as `tex` serves as the view, its source as the file).
    The text shows the source: every use of a macro there also carries the text
    it becomes, shown on a click; the switch shows everything expanded, and a
    click then folds the use. The outline follows the chosen view, and the ⇄
    button of a block gives it the sub-blocks of the other one, expanding or
    folding its uses in the text.
    """
    if isinstance(tex, ExpandedFile):
        view = tex
        tex = view.source
    if view is None:
        view = expand(tex)
    view_lines = _view_to_source_lines(view)
    data = {
        "source": _block_json(blocks(tex), None),
        "view": _block_json(blocks(view), view_lines),
    }
    faces = _TwoFaces(tex, view)
    legend = "".join(f'<span><i style="background:var(--{key})"></i>{name}</span>' for key, name in LEGEND)
    expanded = sum(1 for expansion in view.expansions if expansion.parent is None)
    return (
        '<meta charset="utf-8">\n'
        f"<title>{html.escape(tex.name)} — frame</title>\n<style>{PAGE_STYLE}</style>\n"
        f"<header><h1>{html.escape(tex.name)}</h1>"
        '<span class="switch"><button data-mode="source">Compact</button>'
        '<button data-mode="view">Expanded</button></span>'
        f'<span class="meta">{_counted(len(tex.lines), "lines")}'
        f" · {_counted(expanded, 'macro uses')}"
        f" · {_counted(len(view.conditions), 'decided conditionals')}"
        f" · {_counted(len(_problems(tex)), 'diagnostics')}</span>"
        f'<span class="legend">{legend}</span>'
        '<span class="hint">click a use to expand or fold it</span></header>\n'
        '<main><nav id="outline"></nav>'
        f'<pre id="text">{faces.render()}</pre></main>\n'
        f'<script type="application/json" id="data">{_script_json(data)}</script>\n'
        f"<script>{PAGE_SCRIPT}</script>\n"
    )


def _script_json(data: object) -> str:
    """JSON that is safe in a `<script>` tag: no `</script>` and no HTML comment."""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")


def _block_json(block: Block, lines: Callable[[int, int], tuple[int, int]] | None) -> dict[str, object]:
    start, end = (block.start, block.end) if lines is None else lines(block.start, block.end)
    return {
        "k": _style(block),
        "l": block.label,
        "a": start,
        "b": end,
        "t": _stats(block.stats),
        "c": [_block_json(child, lines) for child in block.children],
    }


def _view_to_source_lines(view: ExpandedFile) -> Callable[[int, int], tuple[int, int]]:
    """Source lines the lines of the expanded view come from."""
    source_map = view.source_map
    target = source_map.target
    total = len(view.lines)

    def lines(start: int, end: int) -> tuple[int, int]:
        start, end = max(1, min(start, total)), max(1, min(end, total))
        first = source_map.source_start(target.offset((start, 0)))[0]
        last = source_map.source_end(target.line_end(end))[0]
        return first, max(first, last)

    return lines


@dataclass(slots=True)
class _Written:
    """A use written in the source: its span, and that of the text it becomes in the view."""

    start: int
    end: int
    name: str
    parent: "_Written | None" = None
    children: list["_Written"] = field(default_factory=list)
    ranges: list[tuple[int, int]] = field(default_factory=list)
    view_start: int | None = None
    view_end: int | None = None

    def cover(self, start: int, end: int) -> None:
        self.ranges.append((start, end))

    def settle(self) -> None:
        """Keep the first occurrence: the spans contiguous from the first one on.

        A use substituted twice (`\\deux{\\gras{x}}`) has two occurrences; the one
        shown in its place is the first, the other stays in the parent's slice.
        """
        for start, end in sorted(self.ranges):
            if self.view_start is None or self.view_end is None:
                self.view_start, self.view_end = start, end
            elif start <= self.view_end:
                self.view_end = max(self.view_end, end)
            else:
                break


class _TwoFaces:
    """The text of the source, where every use also carries the text it becomes.

    Every piece of the expanded view is attached to the written use it comes
    from: written by a body, or an argument copied into a body, to the use that
    opens its chain of expansions; copied as it stands from the source, to the
    innermost use that holds what it copies. The text of a use is the span of the
    view that covers its pieces and those of the uses it holds.
    """

    def __init__(self, tex: TexFile, view: ExpandedFile) -> None:
        self._view = view
        self._source = view.source_map.source
        self._target = view.source_map.target
        self._warned = {diagnostic.start[0] for diagnostic in _problems(tex)}
        self.roots, flat = self._uses()
        self._boundaries = sorted({point for use in flat for point in (use.start, use.end)})
        self._owners = [self._innermost(point) for point in self._boundaries]
        self._cover(flat)
        self._source_skips = self._skips_in_source()
        self._view_skips = _merged(
            (self._target.offset(region.start), self._target.offset(region.end))
            for region in view.branch_regions
            if not region.taken
        )

    def _uses(self) -> tuple[list[_Written], list[_Written]]:
        # A use also covers what the expansions it spawns read: `\\rmqphys*` delegates to
        # `\\@rmqphys`, which reads the argument written after the command.
        extents: dict[tuple[Position, Position, str], tuple[int, int]] = {}
        for expansion in self._view.expansions:
            root = _root(expansion)
            key = (root.start, root.end, root.name)
            start, end = self._source.offset(expansion.start), self._source.offset(expansion.end)
            known = extents.get(key)
            extents[key] = (start, end) if known is None else (min(known[0], start), max(known[1], end))
        spans: dict[tuple[int, int], str] = {}
        self._root_spans: dict[tuple[Position, Position, str], tuple[int, int]] = {}
        for (root_start, root_end, name), (start, end) in extents.items():
            if end > start:
                spans.setdefault((start, end), name)
                self._root_spans[root_start, root_end, name] = (start, end)
        roots: list[_Written] = []
        flat: list[_Written] = []
        stack: list[_Written] = []
        for start, end in sorted(spans, key=lambda span: (span[0], -span[1])):
            while stack and stack[-1].end <= start:
                stack.pop()
            if stack and end > stack[-1].end:
                continue  # overlaps a use without nesting into it: we leave it alone
            use = _Written(start, end, spans[start, end], stack[-1] if stack else None)
            (stack[-1].children if stack else roots).append(use)
            flat.append(use)
            stack.append(use)
        self._by_span = {(use.start, use.end): use for use in flat}
        return roots, flat

    def _innermost(self, offset: int) -> _Written | None:
        found = None
        level = self.roots
        while True:
            inside = next((use for use in level if use.start <= offset < use.end), None)
            if inside is None:
                return found
            found, level = inside, inside.children

    def _cover(self, flat: list[_Written]) -> None:
        # Text written by a body: its occurrences per use, with the context each one was written in.
        occurrences: dict[int, list[tuple[_Written | None, int, int]]] = {}
        uses = {id(use): use for use in flat}
        for piece in self._view.source_map.pieces:
            context = self._written(piece.within)
            if piece.expansion is not None:
                owner = self._written(piece.expansion)
                if owner is None:
                    owner, context = context, None
                if owner is not None:
                    occurrences.setdefault(id(owner), []).append((context, piece.start, piece.end))
                continue
            if piece.source is None:
                continue
            start, end = piece.source, piece.source + piece.end - piece.start
            first, last = bisect_right(self._boundaries, start), bisect_left(self._boundaries, end)
            for low, high in pairwise([start, *self._boundaries[first:last], end]):
                index = bisect_right(self._boundaries, low) - 1
                owner = self._owners[index] if index >= 0 else None
                if owner is not None and not owner.start <= low < owner.end:
                    owner = None
                # Copied by a use that does not hold its text (the end code of an environment
                # takes up the argument of the `\\begin`), it goes back to that use.
                if context is not None and (owner is None or not _encloses(context, owner)):
                    owner = context
                if owner is not None:
                    owner.cover(piece.start + low - start, piece.start + high - start)
        for key, found in occurrences.items():
            use = uses[key]
            for context, start, end in found:
                # Substituted by a use that holds it, it stays with it; elsewhere (an argument taken up
                # by the end code of an environment), it goes back to whoever substituted it.
                (use if context is None or _encloses(context, use) else context).cover(start, end)
        for use in reversed(flat):  # children before parents: the order of the starts, backwards
            for child in use.children:
                # Every occurrence of a child is text of the parent, not only the one it keeps.
                use.ranges.extend(child.ranges)
            use.settle()

    def _written(self, expansion: Expansion | None) -> "_Written | None":
        """The written use that opens the chain of `expansion`."""
        if expansion is None:
            return None
        root = _root(expansion)
        span = self._root_spans.get((root.start, root.end, root.name))
        return self._by_span.get(span) if span is not None else None

    def _skips_in_source(self) -> list[tuple[int, int]]:
        ranges = []
        for piece in self._view.source_map.pieces:
            if piece.source is not None and any(not taken for _, taken, _ in piece.branches):
                ranges.append((piece.source, piece.source + piece.end - piece.start))
        return _merged(ranges)

    def render(self) -> str:
        return self._source_range(0, len(self._source.text), self.roots)

    def _source_range(self, start: int, end: int, uses: list[_Written]) -> str:
        parts = []
        cursor = start
        for use in uses:
            parts.append(self._chunk(self._source, cursor, use.start, self._source_skips, numbered=True))
            view = ""
            if use.view_start is not None and use.view_end is not None:
                view = self._chunk(
                    self._target, use.view_start, use.view_end, self._view_skips, numbered=False
                )
            first = self._source.position(use.start)[0]
            last = self._source.position(max(use.start, use.end - 1))[0]
            parts.append(
                f'<span class="u" data-a="{first}" data-b="{last}" title="\\{html.escape(use.name)}">'
                f'<span class="s">{self._source_range(use.start, use.end, use.children)}</span>'
                f'<span class="x">{view}</span></span>'
            )
            cursor = use.end
        parts.append(self._chunk(self._source, cursor, end, self._source_skips, numbered=True))
        return "".join(parts)

    def _chunk(self, offsets: Any, start: int, end: int, skips: list[tuple[int, int]], numbered: bool) -> str:
        """The text from `start` to `end`, lines marked and discarded branches dimmed."""
        if start >= end:
            return ""
        starts = offsets.starts
        lines = starts[bisect_left(starts, start) : bisect_left(starts, end)]
        edges = [edge for skip in skips for edge in skip if start < edge < end]
        points = sorted({start, end, *lines, *edges})
        line_numbers = {offset: index for index, offset in enumerate(starts, start=1)} if lines else {}
        parts = []
        for low, high in pairwise(points):
            if low in line_numbers:
                parts.append(self._marker(line_numbers[low]) if numbered else '<i class="n"></i>')
            chunk = html.escape(offsets.text[low:high])
            position = bisect_right(skips, (low, float("inf"))) - 1
            if position >= 0 and skips[position][0] <= low < skips[position][1]:
                chunk = f'<span class="skip">{chunk}</span>'
            parts.append(chunk)
        return "".join(parts)

    def _marker(self, line: int) -> str:
        warn = " warn" if line in self._warned else ""
        return f'<i class="n{warn}" id="L{line}">{line}</i>'


def _encloses(outer: _Written, inner: _Written) -> bool:
    """`outer` is `inner` or one of the uses that hold it."""
    current: _Written | None = inner
    while current is not None:
        if current is outer:
            return True
        current = current.parent
    return False


def _root(expansion: Expansion) -> Expansion:
    while expansion.parent is not None:
        expansion = expansion.parent
    return expansion


def _merged(ranges: Iterator[tuple[int, int]] | list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged
