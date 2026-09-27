# latexdetok for VS Code

Underlines what is wrong in a LaTeX source while it is being typed: unclosed
braces and environments, math that never closes, an `\item` outside a list, a
missing argument — with the opening at fault, and the fix.

Where the fix is not a guess — an opening that never closes, a brace too many —
**⌥⌘.** applies it.

The extension does almost nothing itself. It starts
[latexdetok](https://github.com/antnardo/latexdetok)'s language server and
hands it the buffer; everything that reads LaTeX happens there, in Python.

## Installing

The server first, in any Python 3.13 environment:

```bash
pip install "latexdetok[lsp]"
```

Then the extension, which is not on the marketplace:

```bash
npm install
npx @vscode/vsce package -o latexdetok.vsix
code --install-extension latexdetok.vsix
```

## Where it applies

Installed this way, the extension belongs to the user, not to a folder: every
window of that VS Code has it, whatever is open. Four things are worth knowing.

- A window **already open** when it is installed keeps the extension host it
  started with. It picks the extension up on the next *Developer: Reload
  Window*, or in a new window.
- VS Code **profiles** carry their own set of extensions: installing it in one
  does not install it in the others.
- In a **dev container, or on a remote** (SSH, WSL), the extension runs on that
  side, because that is where the files it reads live — the neighbours a
  document loads, and the texmf trees. The server has to be installed there
  too, and `latexdetok.serverPath` set in the settings of that remote.
- In a workspace one has **not approved**, it still underlines. Only
  `latexdetok.serverPath` coming from that workspace is ignored, so a folder
  one has just cloned cannot choose what process gets started.

## Settings

| Setting | Default | Role |
| --- | --- | --- |
| `latexdetok.serverPath` | `latexdetok-lsp` | the command that starts the server; give the whole path when it is not on the `PATH` VS Code sees |
| `latexdetok.language` | `en` | the language of the messages, `en` or `fr`; the diagnostic codes never change |
| `latexdetok.followInputs` | `true` | read the definitions of the loaded files (`\input`, `\usepackage`, `% !TEX root`) |
| `latexdetok.expand` | `true` | expand the macros of the document before diagnosing |

The first one is the setting to check when nothing is underlined. `pip install
latexdetok` installs `latexdetok-lsp` whether or not the `lsp` extra came with
it, so the command VS Code finds first on its `PATH` may be one from an
environment without `pygls`: it says so in the output channel rather than
starting. Giving the whole path settles it:

```json
{ "latexdetok.serverPath": "~/Envs/Main/bin/latexdetok-lsp" }
```

Changing any of them restarts the server, and **latexdetok: restart the
server** in the command palette does it by hand.
