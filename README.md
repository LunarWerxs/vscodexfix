# vscodexfix

**Make the Codex task list usable.**

The stock ChatGPT / Codex VS Code extension buries every task behind a tiny `View all` flyout, hides recent-chat search, and won't let you rename, pin, or star anything from the sidebar. This fixes all of that — and groups your tasks by workspace while it's at it.

> Current release: **v0.4.0** · sister project: [vsclaudefix](https://github.com/LunarWerxs/vsclaudefix) for the Anthropic Claude Code extension.

---

## Install

```bash
python patch_codex_vsix_rename.py
```

That's it. The script downloads the latest Codex extension from the Marketplace, patches it, and installs the patched version via `code --install-extension`. Reload the VS Code window when it's done.

Pass `--vsix-only` if you'd rather inspect the patched `.vsix` before installing it yourself.

**Requires:** Python 3.9+, the `code` CLI on PATH (ships with VS Code). Node.js optional (used for a post-patch syntax check).

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

| Patch              | What it does                                          |
| ------------------ | ----------------------------------------------------- |
| `rename`           | Right-click rename / pin / star + live refresh        |
| `recent-menu`      | Moves recent-chat search into the profile menu        |
| `workspace-groups` | Grouping, collapsible headers, overflow fixes         |
| `pin-composer`     | Sticks the new-chat composer to the bottom            |

```bash
# Apply only what you want
python patch_codex_vsix_rename.py --patches rename,pin-composer
```

---

## Upgrading

Re-run the script. The marketplace download always pulls the latest extension, and `--install-extension --force` replaces whatever's currently loaded.

## Rollback

Uninstall the patched VSIX from the VS Code Extensions panel and reinstall the stock extension from the Marketplace.

## Compatibility

Anchored on identifiers and structural patterns in `out/extension.js`. The Codex extension ships new bundles regularly. If a patch step errors with *"could not find anchor"*, the bundle has shifted — open an issue with the version and which step failed (the log next to the script points right at it).

---

## Power-user usage

```bash
# Skip auto-install, just write the patched .vsix
python patch_codex_vsix_rename.py --vsix-only

# Patch a specific local .vsix instead of downloading
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

**v0.4.0** — Tracks the latest Codex bundle (26.5527.x) and hardens the patcher against the next ones. The task-row rename / pin / star context now resolves the thread id and title from stable React prop names (`conversationId`, `task`) and the row's inner title component instead of minified locals — which had started *shifting meaning* between builds (a local that was the title in one bundle became the id in the next). The profile-menu "Search Chats" injection no longer assumes a fixed menu-item count, the webview-provider and inline-task anchors key off stable structure rather than churning class strings, and the sticky composer footer — which had silently stopped applying — is fixed. Patched output is byte-for-byte deterministic across fresh runs.

**v0.3.0** — Dynamic patcher refresh for newer Codex bundles. Restores task-row context injection so right-click rename / pin / star receive the actual task id again, adds cache-busting for patched webview assets, and hardens the minified-anchor matching used by the sidebar, workspace grouping, recent-search menu, and sticky composer patches.

**v0.2.0** — Default flow is now download-latest-from-Marketplace → patch → auto-install via `code --install-extension --force`. No more "I patched the wrong installed version" footgun (VS Code can have multiple versions of an extension side-by-side and only loads the highest). Pass `--vsix-only` to opt out of auto-install. The old `--install` flag is kept as a deprecated alias.

**v0.1.0** — First release: right-click rename / pin / star with live refresh, expanded inline task list, workspace grouping with collapsible headers, sticky composer, `Search Chats` in profile menu, horizontal-overflow fixes.

## License

MIT.
