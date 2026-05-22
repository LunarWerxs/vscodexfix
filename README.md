# vscodexfix

**Make the Codex task list usable.**

The stock ChatGPT / Codex VS Code extension buries every task behind a tiny `View all` flyout, hides recent-chat search, and won't let you rename, pin, or star anything from the sidebar. This fixes all of that — and groups your tasks by workspace while it's at it.

> Current release: **v0.1.0** · sister project: [vsclaudefix](https://github.com/LunarWerxs/vsclaudefix) for the Anthropic Claude Code extension.

---

## Install

```bash
python patch_codex_vsix_rename.py
```

Downloads the latest Codex VSIX from the Marketplace, patches it, writes `*.tasks-patched.vsix`. Install via **Extensions → "…" → Install from VSIX…** and reload.

Want it done for you? Add `--install` and the script hands the VSIX off to `code --install-extension`.

**Requires:** Python 3.9+. Node.js is optional (used for a post-patch syntax check).

---

## What you get

#### Right-click any task

`Rename Task`, `Pin Task`, `Star Task` — all from the standard VS Code context menu, all live. Rename updates the sidebar in place without reloading the webview or interrupting whichever task is actively running.

#### Pin / Star with workspace-aware sorting

Pinned tasks float to the top of their workspace group (not the top of every workspace combined). Starred tasks are visibly marked. State persists in extension storage where possible, with a title-prefix fallback when the bundle won't cooperate.

#### Real task list, real sidebar

The cramped `View all` flyout is gone. The task list now uses the full sidebar height, with workspace groups and collapsible headers derived from stable metadata — not hardcoded local paths.

#### Sticky composer, no sideways scroll

New-chat input stays pinned to the bottom while the task list scrolls above it. Long titles and workspace labels ellipsize cleanly instead of forcing a horizontal scrollbar.

#### Search Chats moved where it belongs

Recent-chat search lives in the profile menu now, next to Codex Settings. Opens reliably — no more flashing closed on the click that opened it.

---

## Each patch is independent

The patcher splits into four feature patches so a single broken anchor in a new bundle doesn't take the whole run down with it:

| Patch | What it does |
| --- | --- |
| `rename` | Right-click rename / pin / star + live refresh |
| `recent-menu` | Moves recent-chat search into the profile menu |
| `workspace-groups` | Grouping, collapsible headers, overflow fixes |
| `pin-composer` | Sticks the new-chat composer to the bottom |

```bash
# Apply only what you want
python patch_codex_vsix_rename.py --patches rename,pin-composer
```

---

## Rollback

Uninstall the patched VSIX, reinstall the stock extension from the Marketplace.

## Compatibility

The script anchors on identifiers and structural patterns in `out/extension.js`. The Codex extension ships new bundles regularly. If a patch step errors with *"could not find anchor"* the bundle has shifted — open an issue with the version and which step failed (the log next to the script points right at it).

---

## Power-user usage

```bash
# Specific marketplace item / URL / local VSIX
python patch_codex_vsix_rename.py openai.chatgpt
python patch_codex_vsix_rename.py ./openai.chatgpt-X.Y.Z.vsix

# Custom output path
python patch_codex_vsix_rename.py --out ./codex.patched.vsix

# Skip the post-patch verification pass
python patch_codex_vsix_rename.py --no-verify

# Version
python patch_codex_vsix_rename.py --patcher-version
```

The full feature spec sent to the Codex team lives in [CODEX_EXTENSION_FEEDBACK.md](CODEX_EXTENSION_FEEDBACK.md) if you want the long version.

---

## Changelog

**v0.1.0** — First release: right-click rename / pin / star with live refresh, expanded inline task list, workspace grouping with collapsible headers, sticky composer, `Search Chats` in profile menu, horizontal-overflow fixes.

## License

MIT.
