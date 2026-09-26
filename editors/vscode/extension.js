// The client: it starts the server and hands it the documents. Everything that
// reads LaTeX happens on the other side, in Python — this file only carries
// the settings across and restarts the server when they change.

const vscode = require("vscode");
const { LanguageClient, TransportKind } = require("vscode-languageclient/node");

let client;

function options() {
  const settings = vscode.workspace.getConfiguration("latexdetok");
  return {
    // `~` is the shell's, not a program's: VS Code starts the server itself.
    command: settings.get("serverPath").replace(/^~(?=$|\/)/, process.env.HOME),
    initializationOptions: {
      language: settings.get("language"),
      followInputs: settings.get("followInputs"),
      expand: settings.get("expand"),
    },
  };
}

async function start() {
  const { command, initializationOptions } = options();
  client = new LanguageClient(
    "latexdetok",
    "latexdetok",
    { command, args: [], transport: TransportKind.stdio },
    {
      documentSelector: [{ scheme: "file", language: "latex" }],
      initializationOptions,
    },
  );
  try {
    await client.start();
  } catch (error) {
    client = undefined;
    vscode.window.showErrorMessage(
      `latexdetok: ${command} did not start (${error.message}). Install it with ` +
        "`pip install latexdetok[lsp]`, or set latexdetok.serverPath to the whole path.",
    );
  }
}

async function stop() {
  const running = client;
  client = undefined;
  if (running) {
    await running.stop();
  }
}

function activate(context) {
  start();
  context.subscriptions.push(
    // The settings go to the server when it starts: changing them restarts it.
    vscode.workspace.onDidChangeConfiguration(async (event) => {
      if (event.affectsConfiguration("latexdetok")) {
        await stop();
        await start();
      }
    }),
    vscode.commands.registerCommand("latexdetok.restart", async () => {
      await stop();
      await start();
    }),
  );
}

module.exports = { activate, deactivate: stop };
