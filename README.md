# VSCode Codex Extension Improvement's Patch

> Current version: **v0.1.0**

A post-build patcher for the **ChatGPT / Codex VS Code extension** that fixes the cramped task-list ergonomics — adds right-click task actions, an inline expanded task list that uses the whole sidebar, workspace grouping with collapsible headers, a *Search Chats* entry in the profile menu, a sticky composer, and several layout fixes.

This is a community patch, not an official OpenAI project. It edits the installed extension's bundled `out/extension.js` and webview assets in place (or a downloaded `.vsix`).

> **Sister project:** [vsclaudefix](https://github.com/LunarWerxs/vsclaudefix) — the same idea for the **Anthropic Claude Code** VS Code extension (persistent session pane, pin/star, status indicators).

## What it does

- **Right-click task actions** — `Rename Task`, `Pin Task`, `Star Task` on every task row, contributed through the standard VS Code webview context menu.
- **Live rename** — renaming updates the visible sidebar without reloading the webview or interrupting active Codex tasks.
- **Pin / Star** — pinned tasks sort to the top of their workspace group; starred tasks are visibly marked. State is persisted in extension storage where possible (title-prefix fallback otherwise).
- **Expanded inline task list** — replaces the cramped `View all` flyout. The task list now uses the available sidebar height.
- **Search Chats in the profile menu** — moves recent-chat search out of the small flyout. Opens reliably and doesn't flash closed on the click that opened it.
- **Sticky composer** — the new-chat input stays pinned to the bottom while the task list scrolls above it.
- **Workspace grouping** — tasks are grouped by workspace / project with collapsible headers, derived from stable metadata (not hardcoded local paths).
- **No more horizontal scrollbar** — long task titles and workspace labels ellipsize cleanly.

See [CODEX_EXTENSION_FEEDBACK.md](CODEX_EXTENSION_FEEDBACK.md) for the full feature spec sent to the Codex team.

## Requirements

- Python 3.9+
- Node.js (optional — used for a `node --check` syntax pass after patching; the patch still applies if Node is missing)
- The ChatGPT / Codex extension installed in VS Code (`openai.chatgpt`)

## Usage

```bash
# Download + patch the latest from the Marketplace, write a patched .vsix
python patch_codex_vsix_rename.py

# Or pass an explicit Marketplace itemName / Marketplace URL / local .vsix path
python patch_codex_vsix_rename.py openai.chatgpt
python patch_codex_vsix_rename.py "https://marketplace.visualstudio.com/items?itemName=openai.chatgpt"
python patch_codex_vsix_rename.py ./openai.chatgpt-X.Y.Z.vsix

# Apply only specific patches
python patch_codex_vsix_rename.py --patches rename,pin-composer

# Install the patched VSIX automatically (uses code --install-extension)
python patch_codex_vsix_rename.py --install

# Print patcher version
python patch_codex_vsix_rename.py --patcher-version
```

Available patch names for `--patches`:

- `rename` — task row context data + `Rename Task` / `Pin Task` / `Star Task` commands and live-refresh wiring.
- `recent-menu` — moves recent-chat search into the profile menu as `Search Chats`.
- `workspace-groups` — workspace grouping with collapsible headers + horizontal-overflow fixes.
- `pin-composer` — sticks the new-chat composer to the bottom of the sidebar.

Default is `all`.

After patching, install the resulting `*.tasks-patched.vsix` via **Extensions → "..." menu → Install from VSIX...** (or use `--install`), then reload the VS Code window.

## Rollback

Uninstall the patched VSIX and reinstall the stock extension from the Marketplace.

## Version compatibility

The patch script anchors on specific identifiers and structural patterns in the bundled `out/extension.js`. The Codex extension ships new bundles regularly and those anchors can shift without notice. If a patch step raises a "could not find anchor" / "missing marker" error, the bundle has shifted and the anchor needs updating.

If the patch fails on a newer version, please open an issue with the extension version and which patch step failed (the log file written next to the patcher output points at the culprit).

## Why a runtime patch instead of a fork

The Codex extension is closed-source. The bundled VSIX is the only artifact available to modify. The patch is intentionally split into independently-toggleable feature patches so a single broken anchor doesn't take down the whole run — you can disable a failing patch via `--patches` while the others still apply.

The goal is for the Codex team to eventually implement these ergonomics natively; until then this fills the gap.

## Changelog

### v0.1.0

- Initial public release: right-click rename / pin / star, live rename without reload, expanded inline task list, workspace grouping with collapsible headers, sticky composer, `Search Chats` in profile menu, horizontal overflow fixes.

## License

MIT.
