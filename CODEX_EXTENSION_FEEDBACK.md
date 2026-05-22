# Codex VS Code Extension Feedback

## Part 1: TLDR for the ChatGPT/Codex VS Code Team

The Codex task list in the VS Code extension is close to being very useful, but a few task-management basics are missing or awkward:

- Right-clicking a task should expose `Rename Task`, `Pin Task`, and `Star Task`.
- Renaming should update the visible sidebar immediately without reloading the webview or interrupting active Codex tasks.
- Pinning should keep a task at the top of its workspace group.
- Starring should visibly mark a task, either with first-class metadata or by prefixing the task title with a star.
- The current `View all` task flyout should be replaced by a taller inline task list that uses the available sidebar height.
- Recent-chat search should move into the settings/profile menu as `Search Chats`.
- `Search Chats` should open reliably and not instantly close because of the click event that opened the menu.
- The new-chat composer/input should stay pinned to the bottom while the task list scrolls above it.
- Tasks should be grouped by workspace/project with collapsible workspace headers.
- The task list should never show a horizontal scrollbar.

Why this matters:

Users often have many Codex tasks running across different workspaces. The sidebar has enough vertical space to show and organize those tasks directly. Hiding most of the list behind a cramped flyout makes it harder to monitor work, jump between projects, or preserve important chats.

Please implement this natively in the extension source before bundling the VSIX rather than relying on a post-build patch.

## Part 2: Implementation Instructions for an AI Code Agent

### Goal

Implement task-list ergonomics in the Codex VS Code extension:

1. Add task row context menu actions for rename, pin, and star.
2. Make rename update the visible sidebar live.
3. Keep active chats/tasks running during rename, pin, and star operations.
4. Replace the small `View all` flyout as the primary browser.
5. Move full recent-chat search to the settings/profile menu as `Search Chats`.
6. Keep the composer pinned to the bottom.
7. Group tasks by workspace/project.
8. Make workspace groups collapsible.
9. Remove persistent horizontal overflow.

Do this in source if available. If working from a built VSIX, the relevant bundled output is currently in `out/extension.js` plus the webview assets.

### Relevant Existing Code Concepts

The current bundled VSIX has these conceptual areas:

- `package.json`
  - Contributes commands, menus, and webview context menu items.
- `out/extension.js`
  - Registers extension host commands.
  - Contains the Codex chat/session provider logic.
- `codex.chatSessionProvider`
  - Existing provider for visible Codex task/session items.
- `renameChatSessionItem`
  - Existing or partial rename path.
- App-server route equivalent to `thread/name/set`
  - Existing backend route used to persist a thread/task title.
- Codex webview task row components
  - Local task rows and cloud task rows.
  - In bundled output these may appear as helpers/components similar to `sidebarThreadRow`.
- Recent task list/search UI
  - In the current bundle this is identifiable near functions similar to `Ql(e)` and `xu(e)`.
- Settings/profile menu
  - The menu that already contains items like `Codex Settings`, keyboard shortcuts, and logout.
- New thread page/composer layout
  - In the bundle this is near `NewThreadPanelPage` and `thread-footer-overlap`.

Use the real source names if they differ from these minified/bundled names.

### Task Row Context Menu

Each Codex task row should provide VS Code webview context data so extension commands know which task was clicked.

Add context data to every task row that should support rename/pin/star:

```tsx
<button
  className={styles.taskRow}
  data-vscode-context={JSON.stringify({
    webviewSection: 'codex-task',
    codexTaskId: task.id,
    codexTaskTitle: task.title,
    codexWorkspaceKey: workspaceKey,
    preventDefaultContextMenuItems: true,
  })}
>
  {task.title}
</button>
```

Then contribute menu items in `package.json`:

```json
{
  "contributes": {
    "commands": [
      {
        "command": "chatgpt.renameTask",
        "title": "Rename Task"
      },
      {
        "command": "chatgpt.pinTask",
        "title": "Pin Task"
      },
      {
        "command": "chatgpt.starTask",
        "title": "Star Task"
      }
    ],
    "menus": {
      "webview/context": [
        {
          "command": "chatgpt.renameTask",
          "when": "webviewSection == 'codex-task'",
          "group": "navigation@1"
        },
        {
          "command": "chatgpt.pinTask",
          "when": "webviewSection == 'codex-task'",
          "group": "navigation@2"
        },
        {
          "command": "chatgpt.starTask",
          "when": "webviewSection == 'codex-task'",
          "group": "navigation@3"
        }
      ]
    }
  }
}
```

If the command ids differ in the codebase, keep the existing naming convention.

### Rename Behavior

Rename should not reload the whole webview. It should not stop active Codex tasks.

Expected flow:

1. User right-clicks a task row.
2. User chooses `Rename Task`.
3. Extension command receives the clicked task id and title from webview context.
4. Extension asks for the new title.
5. Extension persists the title through the existing backend route, currently equivalent to `thread/name/set`.
6. Extension updates local in-memory task/session state immediately.
7. Extension fires the provider/webview refresh event.
8. Sidebar row text updates live.

Avoid:

```ts
vscode.commands.executeCommand('workbench.action.webview.reloadWebviewAction');
```

Reloading the webview is too disruptive because active chats/tasks can be running.

Source-level pseudocode:

```ts
context.subscriptions.push(
  vscode.commands.registerCommand('chatgpt.renameTask', async (webviewContext) => {
    const taskId = webviewContext?.codexTaskId;
    const oldTitle = webviewContext?.codexTaskTitle ?? '';

    if (!taskId) {
      vscode.window.showErrorMessage('Codex could not determine which task to rename.');
      return;
    }

    const nextTitle = await vscode.window.showInputBox({
      title: 'Rename Codex task',
      value: oldTitle,
      prompt: 'Enter a new task name',
      ignoreFocusOut: true,
    });

    if (!nextTitle || nextTitle.trim() === oldTitle.trim()) return;

    await codexClient.setThreadName(taskId, nextTitle.trim());
    chatSessionProvider.updateCachedTitle(taskId, nextTitle.trim());
    chatSessionProvider.refresh();
    webviewPanels.broadcastTaskTitleChanged(taskId, nextTitle.trim());
  }),
);
```

The exact refresh mechanism should match the extension architecture. The important part is that title state is updated and rendered live without a full webview reload.

### Pin and Star Behavior

Preferred implementation:

- Store pin/star metadata separately from the task title.
- Use VS Code `globalState`, extension storage, or the extension's existing task metadata layer.
- Key metadata by stable task/thread id.
- Keep pin scoped globally or per workspace, whichever matches existing task identity behavior.

Fallback implementation:

- Star can prefix the title with `⭐ ` if there is no better metadata field.
- Pin can be stored in local extension state keyed by task id.
- If absolutely necessary, pin can also be represented by a title prefix, but metadata is cleaner.

Recommended metadata shape:

```ts
type CodexTaskUiState = {
  pinnedTaskIds: string[];
  starredTaskIds: string[];
};
```

Pin command:

```ts
async function pinTask(taskId: string) {
  const pinned = new Set(await getPinnedTaskIds());
  if (pinned.has(taskId)) {
    pinned.delete(taskId);
  } else {
    pinned.add(taskId);
  }

  await savePinnedTaskIds([...pinned]);
  chatSessionProvider.refresh();
  webviewPanels.broadcastTaskUiStateChanged();
}
```

Star command:

```ts
async function starTask(taskId: string) {
  const starred = new Set(await getStarredTaskIds());
  if (starred.has(taskId)) {
    starred.delete(taskId);
  } else {
    starred.add(taskId);
  }

  await saveStarredTaskIds([...starred]);
  chatSessionProvider.refresh();
  webviewPanels.broadcastTaskUiStateChanged();
}
```

If using the title-prefix fallback for star:

```ts
function starTitle(title: string): string {
  const clean = title.trim() || 'Untitled';
  return clean.startsWith('⭐ ') ? clean : `⭐ ${clean}`;
}
```

Sorting:

```ts
function sortTasks(a: CodexTask, b: CodexTask): number {
  const aPinned = isPinned(a);
  const bPinned = isPinned(b);

  if (aPinned !== bPinned) {
    return aPinned ? -1 : 1;
  }

  return b.lastUpdatedAt - a.lastUpdatedAt;
}
```

Pinned tasks should sort to the top of their workspace group, not necessarily the top of every workspace combined.

### Sidebar Task List Layout

The current task list should use the available vertical sidebar space.

Desired layout:

```tsx
<section className={styles.codexSidebar}>
  <header className={styles.taskHeader}>
    <span>Tasks</span>
    {/* existing controls */}
  </header>

  <div className={styles.taskListScroller}>
    <WorkspaceTaskGroups tasks={tasks} />
  </div>

  <footer className={styles.composer}>
    <NewChatComposer />
  </footer>
</section>
```

CSS guidance:

```css
.codexSidebar {
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
  overflow: hidden;
}

.taskHeader {
  flex: 0 0 auto;
}

.taskListScroller {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
}

.composer {
  flex: 0 0 auto;
  position: sticky;
  bottom: 0;
  background: var(--vscode-sideBar-background);
  z-index: 2;
}
```

Important:

- The task list scrolls.
- The whole sidebar should not need to scroll just to reach the composer.
- Do not require hovering directly over the scrollbar to scroll.
- Avoid `overflow: auto` on multiple nested wrappers unless each layer has a clear reason.
- Set `min-height: 0` on flex children that need to scroll.
- Set `overflow-x: hidden` on the task list and row wrappers.

### Replace `View All` Flyout

The `View all` button should not open the primary task browser. The sidebar itself should be tall enough to browse tasks directly.

Recommended changes:

- Remove or de-emphasize `View all` from the inline task list.
- Keep recent-chat search available as `Search Chats` in the settings/profile menu.
- Reuse the existing search UI if possible.
- Ensure opening search from the menu does not close immediately.

Common bug to avoid:

```ts
setSearchOpen(true);
setSettingsMenuOpen(false);
```

If the same click event bubbles into a document-level outside-click handler, the search can flash open and instantly close. Prevent this by stopping propagation, deferring the open by one tick, or making the outside-click handler ignore the opening event.

Example:

```ts
function handleSearchChatsClick(event: React.MouseEvent) {
  event.preventDefault();
  event.stopPropagation();

  setSettingsMenuOpen(false);

  requestAnimationFrame(() => {
    setSearchChatsOpen(true);
  });
}
```

### Settings/Profile Menu

Add `Search Chats` to the existing settings/profile menu near items like:

- `Codex Settings`
- Keyboard shortcuts
- Log out

Behavior:

- Clicking `Search Chats` opens the existing full recent-chat search UI.
- It should focus the search input.
- It should not flash and close.
- It should not depend on the old `View all` button.

### Workspace Grouping

Group tasks by workspace/project.

Use stable metadata instead of hardcoded local paths.

Candidate grouping keys:

- Workspace root path.
- Task cwd.
- Project/environment label from cloud task metadata.
- Repository name if that is the clearest stable label.
- Fallback: `Other` or `No workspace`.

Do not hardcode one user's paths.

Example:

```ts
function workspaceKeyForTask(task: CodexTask): string {
  return (
    task.workspaceRoot ||
    task.cwd ||
    task.projectName ||
    task.environmentName ||
    'No workspace'
  );
}
```

Render collapsible groups:

```tsx
function WorkspaceGroup({ group }: { group: TaskGroup }) {
  const [collapsed, setCollapsed] = useCollapsedState(group.key);

  return (
    <section className={styles.workspaceGroup}>
      <button
        className={styles.workspaceHeader}
        onClick={() => setCollapsed(!collapsed)}
        aria-expanded={!collapsed}
      >
        <Chevron className={collapsed ? styles.collapsed : styles.expanded} />
        <span>{group.label}</span>
        <span>{group.tasks.length}</span>
      </button>

      {!collapsed && group.tasks.map((task) => (
        <TaskRow key={task.id} task={task} />
      ))}
    </section>
  );
}
```

Persist collapsed state in local component state or VS Code extension storage:

```ts
type CollapsedWorkspaceState = Record<string, boolean>;
```

### Horizontal Overflow

No horizontal scrollbar should appear in the task sidebar.

Apply this defensively:

```css
.taskListScroller,
.workspaceGroup,
.workspaceHeader,
.taskRow {
  min-width: 0;
  max-width: 100%;
  overflow-x: hidden;
}

.taskTitle,
.workspaceTitle {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
```

Common causes:

- A task id or environment id rendered inline without shrinking.
- A flex child missing `min-width: 0`.
- A nested list width set to `100vw`.
- Absolute-positioned controls extending outside the row.
- A row using `width: max-content`.

### Live Update Requirements

After rename, pin, or star:

- The visible task list should update without a reload.
- The active chat should continue running.
- The current chat should not be closed or replaced.
- Other chats should continue running.
- The provider/tree/webview should receive the smallest refresh needed.

Recommended event shape:

```ts
type CodexTaskUiEvent =
  | { type: 'taskTitleChanged'; taskId: string; title: string }
  | { type: 'taskPinnedChanged'; taskId: string; pinned: boolean }
  | { type: 'taskStarredChanged'; taskId: string; starred: boolean }
  | { type: 'taskListRefreshRequested' };
```

The webview can optimistically update local state, then reconcile with the provider/server response.

### Acceptance Criteria

The change is complete when:

- Right-clicking a Codex task row shows `Rename Task`, `Pin Task`, and `Star Task`.
- Rename opens a simple rename input and persists the title.
- Rename updates the visible sidebar title within a second.
- Rename does not reload the webview.
- Rename does not interrupt active Codex tasks.
- Pinning moves the task to the top of its workspace group.
- Starring visibly marks the task.
- The task list uses the available sidebar height.
- The composer stays pinned to the bottom.
- `Search Chats` exists in the settings/profile menu.
- `Search Chats` opens reliably and focuses search.
- The old cramped `View all` flyout is not the primary way to browse tasks.
- Tasks are grouped by workspace/project.
- Workspace headers collapse and expand.
- No horizontal scrollbar appears.

### Test Plan

Manual tests:

1. Open the Codex sidebar.
2. Confirm the task list uses the available vertical space.
3. Confirm the composer is visible at the bottom without scrolling the whole sidebar.
4. Scroll the task list with the mouse wheel while hovering over rows, not only the scrollbar.
5. Confirm no horizontal scrollbar appears.
6. Right-click a task row.
7. Confirm `Rename Task`, `Pin Task`, and `Star Task` appear.
8. Rename a task.
9. Confirm the sidebar updates live without reloading.
10. Start or leave another Codex task running.
11. Rename/pin/star a different task.
12. Confirm the running task continues.
13. Pin a task.
14. Confirm it moves to the top of its workspace group.
15. Star a task.
16. Confirm it is visibly starred.
17. Open settings/profile menu.
18. Click `Search Chats`.
19. Confirm search opens and stays open.
20. Confirm the search input is focused.
21. Expand and collapse workspace headers.
22. Confirm grouping is correct on a second machine or workspace and does not depend on hardcoded local paths.

Regression tests:

- Existing task click behavior still opens the correct chat.
- Existing new-chat behavior still works.
- Existing settings menu items still work.
- Cloud tasks still render.
- Local tasks still render.
- Empty states still render.
- Long task names are ellipsized.
- Long workspace names are ellipsized.
- Keyboard navigation and focus styles still work.
- Active task state/status still renders correctly.

### Patch Script Expectations

If maintaining an external VSIX patcher, keep each feature patch separate so future Marketplace updates are easier to troubleshoot:

- task row context/actions
- rename/live refresh
- pin/star metadata or fallback title markers
- expanded sidebar/recent-list layout
- settings menu `Search Chats`
- workspace grouping and collapse state
- composer pinning and overflow fixes

The patcher should:

- Download the latest Marketplace VSIX.
- Extract it to a temporary directory.
- Apply each patch independently.
- Log each patch start, result, and verification status.
- Verify required markers after patching.
- Syntax-check `out/extension.js` with Node when available.
- Write a patched VSIX.
- Write a clear log file next to the patcher output.
- Fail loudly if the expected bundle anchors are missing.

