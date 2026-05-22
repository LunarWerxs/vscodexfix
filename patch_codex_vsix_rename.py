#!/usr/bin/env python
"""VSCode Codex Extension Improvement's Patch.

Post-build patcher for the ChatGPT/Codex VS Code extension. Adds right-click
task actions (rename / pin / star), an inline expanded task list, workspace
grouping with collapsible headers, a Search Chats entry in the profile menu,
a sticky composer, and horizontal-overflow fixes. See README.md.
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import importlib.util
import json
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

__version__ = "0.3.0"

COMMAND_ID = "chatgpt.renameTask"
PIN_COMMAND_ID = "chatgpt.pinTask"
UNPIN_COMMAND_ID = "chatgpt.unpinTask"
STAR_COMMAND_ID = "chatgpt.starTask"
UNSTAR_COMMAND_ID = "chatgpt.unstarTask"
DEFAULT_MARKETPLACE_ITEM = "openai.chatgpt"
MARKETPLACE_QUERY_URL = (
    "https://marketplace.visualstudio.com/_apis/public/gallery/extensionquery?api-version=7.2-preview.1"
)
PATCH_RENAME = "rename"
PATCH_RECENT_MENU = "recent-menu"
PATCH_WORKSPACE_GROUPS = "workspace-groups"
PATCH_PIN_COMPOSER = "pin-composer"
DEFAULT_PATCHES = (PATCH_RENAME, PATCH_RECENT_MENU, PATCH_WORKSPACE_GROUPS, PATCH_PIN_COMPOSER)
LOG_PATH: Path | None = None


def log(message: str) -> None:
    line = f"[{dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    print(line, flush=True)
    if LOG_PATH is not None:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def check_python_dependencies(path: Path) -> None:
    py_files = [path]
    local_modules = {path.stem for path in py_files}
    missing: set[str] = set()

    for path in py_files:
        try:
            tree = ast.parse(read(path), filename=str(path))
        except SyntaxError as exc:
            raise RuntimeError(f"Could not parse {path.name}: {exc}") from exc
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".", 1)[0]]
            for name in names:
                if name == "__future__" or name in local_modules:
                    continue
                if importlib.util.find_spec(name) is None:
                    missing.add(name)

    if missing:
        packages = " ".join(sorted(missing))
        raise RuntimeError(
            "Missing Python package(s): " f"{packages}\nInstall them with: python -m pip install {packages}"
        )
    log(f"Python dependency check passed ({len(py_files)} file(s) scanned)")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Minified-anchor utilities
#
# The Codex extension bundle is minified with single/short letter identifiers
# that change every release. Rather than hard-coding those identifiers, we
# anchor patches on the strings that DON'T change between releases (literal
# message ids, CSS class names, VS Code API method names) and capture the
# minified identifiers via regex named groups when we need them.
# ---------------------------------------------------------------------------

# Pattern fragment for a minified JS identifier (e.g. `e`, `Nee`, `_Ye`).
JS_ID = r"[A-Za-z_$][\w$]*"


def marketplace_item_from_target(target: str) -> str | None:
    if target.startswith(("http://", "https://")):
        parsed = urllib.parse.urlparse(target)
        query = urllib.parse.parse_qs(parsed.query)
        item = query.get("itemName", [""])[0].strip()
        return item or None
    if re.fullmatch(r"[A-Za-z0-9_.-]+\.[A-Za-z0-9_.-]+", target):
        return target
    return None


def download_marketplace_vsix(item: str, dest_dir: Path, version: str | None = None) -> Path:
    body = {"filters": [{"criteria": [{"filterType": 7, "value": item}]}], "flags": 914}
    req = urllib.request.Request(
        MARKETPLACE_QUERY_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json;api-version=7.2-preview.1",
            "User-Agent": "codex-vsix-patcher",
        },
    )
    with urllib.request.urlopen(req, timeout=45) as response:
        data = json.load(response)
    try:
        extension = data["results"][0]["extensions"][0]
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"Marketplace extension not found: {item}") from exc
    selected = next((v for v in extension.get("versions", []) if not version or v.get("version") == version), None)
    if not selected:
        raise RuntimeError(f"Version {version or 'latest'} was not found for {item}")
    package = next((f for f in selected.get("files", []) if f.get("assetType", "").endswith("VSIXPackage")), None)
    if not package:
        raise RuntimeError(f"Marketplace response for {item} did not include a VSIX package URL")

    publisher = extension.get("publisher", {}).get("publisherName", item.split(".")[0])
    name = extension.get("extensionName", item.split(".")[-1])
    vsix_version = selected.get("version", "latest")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{publisher}.{name}-{vsix_version}.vsix"
    log(f"Downloading {publisher}.{name} {vsix_version} to {dest}")
    urllib.request.urlretrieve(package["source"], dest)
    log(f"Downloaded VSIX: {dest}")
    log(f"Downloaded VSIX size: {dest.stat().st_size} bytes")
    return dest


def patch_package_json(package_json: Path) -> bool:
    data = json.loads(read(package_json))
    contributes = data.setdefault("contributes", {})
    commands = contributes.setdefault("commands", [])
    changed = False

    for command_id, title, icon in (
        (COMMAND_ID, "Rename Task", "$(edit)"),
        (PIN_COMMAND_ID, "Pin Task", "$(pinned)"),
        (UNPIN_COMMAND_ID, "Unpin Task", "$(pinned-dirty)"),
        (STAR_COMMAND_ID, "Star Task", "$(star-full)"),
        (UNSTAR_COMMAND_ID, "Unstar Task", "$(star-empty)"),
    ):
        if not any(command.get("command") == command_id for command in commands):
            commands.append({"command": command_id, "title": title, "category": "Codex", "icon": icon})
            changed = True

    menus = contributes.setdefault("menus", {})
    webview_context_menu = menus.setdefault("webview/context", [])
    webview_when = "(webviewId == 'chatgpt.sidebarView' || webviewId == 'chatgpt.sidebarSecondaryView') && codexTask == true"
    for command_id, group, when in (
        (COMMAND_ID, "navigation@1", webview_when),
        (PIN_COMMAND_ID, "navigation@2", f"{webview_when} && !codexPinned"),
        (UNPIN_COMMAND_ID, "navigation@2", f"{webview_when} && codexPinned == true"),
        (STAR_COMMAND_ID, "navigation@3", f"{webview_when} && !codexStarred"),
        (UNSTAR_COMMAND_ID, "navigation@3", f"{webview_when} && codexStarred == true"),
    ):
        webview_item = next((item for item in webview_context_menu if item.get("command") == command_id), None)
        if webview_item is None:
            webview_context_menu.insert(0, {"command": command_id, "group": group, "when": when})
            changed = True
        else:
            if webview_item.get("when") != when:
                webview_item["when"] = when
                changed = True
            if webview_item.get("group") != group:
                webview_item["group"] = group
                changed = True

    chat_sessions_menu = menus.setdefault("chat/chatSessions", [])
    if not any(item.get("command") == COMMAND_ID for item in chat_sessions_menu):
        chat_sessions_menu.append(
            {
                "command": COMMAND_ID,
                "group": "inline@50",
                "when": "chatSessionType == openai-codex",
            }
        )
        changed = True

    if changed:
        package_json.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return changed


RENAME_HELPER_JS = r"""
var codexLastTaskContext=null;
var codexTaskContextResolvers=new Map,codexTaskContextRequestSeq=0;
function codexRememberTaskContext(t){if(t&&typeof t.codexThreadId=="string"&&t.codexThreadId)codexLastTaskContext={codexThreadId:t.codexThreadId,codexThreadTitle:typeof t.codexThreadTitle=="string"?t.codexThreadTitle:"",__ts:Date.now()}}
function codexResolveTaskContext(t){codexRememberTaskContext(t);let e=t&&t.requestId!=null?String(t.requestId):null;if(e){let r=codexTaskContextResolvers.get(e);r&&(codexTaskContextResolvers.delete(e),r(codexRecentTaskContext()))}}
function codexRecentTaskContext(){return codexLastTaskContext&&Date.now()-codexLastTaskContext.__ts<6e4?codexLastTaskContext:null}
function codexDelay(t){return new Promise(e=>setTimeout(e,t))}
async function codexRequestTaskContext(t){try{let e=t?.sidebarView?.webview;if(!e||typeof t.postMessageToWebview!="function")return codexRecentTaskContext();let r=String(++codexTaskContextRequestSeq),n=new Promise(o=>{codexTaskContextResolvers.set(r,o),setTimeout(()=>{codexTaskContextResolvers.has(r)&&(codexTaskContextResolvers.delete(r),o(codexRecentTaskContext()))},500)});return t.postMessageToWebview(e,{type:"codex-request-task-context",requestId:r}),await n}catch{return codexRecentTaskContext()}}
async function codexWithTaskContext(t,e){if(codexRenameThreadIdDirect(t)&&codexRenameThreadTitleDirect(t))return t;let r=(await codexRequestTaskContext(e))??codexRecentTaskContext();return r?{...t,...r}:t}
function codexRenameThreadIdDirect(t){if(t&&typeof t.id=="string")return t.id;if(t&&typeof t.codexThreadId=="string")return t.codexThreadId;if(t&&typeof t["data-codex-thread-id"]=="string")return t["data-codex-thread-id"];if(t&&t.resource){let e=typeof t.resource.toString=="function"?t.resource.toString():String(t.resource),r=/\/local\/([^/?#]+)/.exec(e);if(r)return decodeURIComponent(r[1])}return null}
function codexRenameThreadId(t){let e=codexRenameThreadIdDirect(t);return e??codexRenameThreadIdDirect(codexRecentTaskContext())}
function codexRenameThreadTitleDirect(t){return typeof t?.label=="string"?t.label:typeof t?.codexThreadTitle=="string"?t.codexThreadTitle:typeof t?.["data-codex-thread-title"]=="string"?t["data-codex-thread-title"]:""}
function codexRenameThreadTitle(t){let e=codexRenameThreadTitleDirect(t);return e||codexRenameThreadTitleDirect(codexRecentTaskContext())}
async function codexEnsureTaskContext(t){let e=codexRenameThreadId(t);if(e)return t;await codexDelay(250);return codexRenameThreadId(t)?t:codexRecentTaskContext()}
async function codexSetTaskTitle(t,e,r,n,o){let i=await codexEnsureTaskContext(t),a=codexRenameThreadId(i);if(!a){e.window.showErrorMessage("Right-click a Codex task row first.");return!1}if(typeof r!="function")throw new Error("Codex app-server rename route is unavailable");await r(a,n),o&&e.window.showInformationMessage(o);return!0}
async function codexRenameTask(t,e,r){let n=await codexEnsureTaskContext(t),o=codexRenameThreadId(n);if(!o){e.window.showErrorMessage("Right-click a Codex task row to rename it.");return!1}let i=codexRenameThreadTitle(n),a=await e.window.showInputBox({title:"Rename Codex Task",prompt:"Enter a new task name",value:i,ignoreFocusOut:!0,validateInput:s=>s.trim().length===0?"Task name cannot be empty":void 0});if(a==null)return!1;let c=a.replace(/\s+/g," ").trim();return!c||c===i?!1:codexSetTaskTitle(n,e,r,c,"Codex task renamed.")}
async function codexStarTask(t,e,r){let n=await codexEnsureTaskContext(t),o=codexRenameThreadTitle(n).trim();if(!o){e.window.showErrorMessage("Could not read Codex task title.");return!1}let i=o.startsWith("⭐ "),a=i?o.slice(2).trimStart():`⭐ ${o}`;return a===o?!1:codexSetTaskTitle(n,e,r,a,i?"Codex task unstarred.":"Codex task starred.")}
async function codexPinTask(t,e,r){let n=await codexEnsureTaskContext(t),o=codexRenameThreadTitle(n).trim();if(!o){e.window.showErrorMessage("Could not read Codex task title.");return!1}let i=o.startsWith("⭐ "),a=i?o.slice(2).trimStart():o,c,d;if(a.startsWith("📌 "))c=a.slice(2).trimStart(),d=i?`⭐ ${c}`:c;else d=i?`⭐ 📌 ${a}`:`📌 ${a}`;return d===o?!1:codexSetTaskTitle(n,e,r,d,a.startsWith("📌 ")?"Codex task unpinned.":"Codex task pinned.")}
"""

OLD_RENAME_HELPER_START = 'var codexRenameFs=require("fs"),codexRenameCp=require("child_process"),codexRenameOs=require("os"),codexRenamePath=require("path");'


def _strip_existing_rename_helpers(text: str) -> tuple[str, bool]:
    """Remove any previously-injected rename helpers so we can re-inject cleanly.

    Returns (new_text, changed).
    """
    changed = False

    # Old-style fs/cp helpers run an external process. If they're present, drop
    # them entirely so the modern helper takes over.
    if OLD_RENAME_HELPER_START in text:
        start = text.index(OLD_RENAME_HELPER_START)
        # Strip up to but not including the next "var " or "function " that
        # isn't part of the helpers. The current helpers' last function is
        # codexPinTask, so look for the end of its body — that's a `}` followed
        # by `var ` or `function ` at top level. Pragmatic heuristic: find the
        # provider-decl anchor and cut up to there.
        m = re.search(rf'var\s+{JS_ID}="codex\.chatSessionProvider"', text)
        if m:
            text = text[:start] + text[m.start() :]
            changed = True

    # Strip any previous codex* helper block so we re-inject from scratch.
    m_first = re.search(
        r"(?:var codexLastTaskContext=null;|function codexRememberTaskContext\(|function codexRename(?:ThreadId|ThreadTitle)(?:Direct)?\()",
        text,
    )
    if m_first:
        m_last = None
        for name in ("codexPinTask", "codexStarTask", "codexRenameTask", "codexSetTaskTitle"):
            for m in re.finditer(rf"function\s+{name}\([^)]*\)\{{", text):
                m_last = m
        if m_last:
            # Find the matching closing brace of m_last's function body
            depth = 0
            i = m_last.end() - 1  # at the '{'
            end_idx = -1
            while i < len(text):
                c = text[i]
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        end_idx = i + 1
                        break
                i += 1
            if end_idx != -1:
                text = text[: m_first.start()] + text[end_idx:]
                changed = True

    return text, changed


def patch_extension_js(extension_js: Path) -> bool:
    text = read(extension_js)
    changed = False

    # ------------------------------------------------------------------
    # 1) Discover the minified identifiers we need.
    # ------------------------------------------------------------------
    m_provider = re.search(
        rf'var\s+(?P<provider>{JS_ID})="codex\.chatSessionProvider",\s*'
        rf'(?P<observer>{JS_ID})="codex\.chatSessionObserver",\s*'
        rf'(?P<agent>{JS_ID})="Codex Agent",\s*'
        rf"(?P<truncate>{JS_ID})=\d+;",
        text,
    )
    if m_provider is None:
        raise RuntimeError("Could not find codex.chatSessionProvider declaration in extension.js")
    provider_id = m_provider.group("provider")

    # The vscode-namespace alias used inside the chat-session class. We grab it
    # from the trackTabIfNeeded method, which destructures TabInputCustom.
    m_track = re.search(
        rf"async\s+trackTabIfNeeded\(e\)\{{let r=e\.input;if\(!\(r instanceof\s+(?P<ns>{JS_ID})\.TabInputCustom\)\)return;",
        text,
    )
    if m_track is None:
        raise RuntimeError("Could not find trackTabIfNeeded anchor in extension.js")
    vscode_ns = m_track.group("ns")

    m_webview_provider = re.search(rf"let\s+(?P<provider>{JS_ID})=new\s+ml\(", text)
    if m_webview_provider is None:
        raise RuntimeError("Could not find Codex webview provider variable in extension.js")
    webview_provider_var = m_webview_provider.group("provider")

    # ------------------------------------------------------------------
    # 2) Inject codex* helper functions just before the chatSessionProvider
    #    declaration (drop any previous versions first).
    #
    # If the file already contains the canonical helper block verbatim, we
    # skip the strip/re-inject cycle so the patch is a no-op.
    # ------------------------------------------------------------------
    if RENAME_HELPER_JS.strip() not in text:
        text, stripped = _strip_existing_rename_helpers(text)
        if stripped:
            changed = True

    if "function codexRenameTask(" not in text:
        # Re-locate the anchor in case the strip changed offsets.
        m_provider = re.search(
            rf'var\s+{JS_ID}="codex\.chatSessionProvider"',
            text,
        )
        if m_provider is None:
            raise RuntimeError("Lost codex.chatSessionProvider anchor while injecting helpers")
        insert_at = m_provider.start()
        text = text[:insert_at] + RENAME_HELPER_JS + text[insert_at:]
        changed = True

    # The VS Code webview context menu shows based on data-vscode-context, but
    # this build does not pass that context object as the command argument.
    # Remember the last right-clicked row when the webview reports it.
    context_case = 'case"codex-task-context":{codexRememberTaskContext(r);break}case"codex-task-context-response":{codexResolveTaskContext(r);break}'
    if context_case not in text:
        switch_anchor = 'switch(r.type){case"ready":break;'
        if switch_anchor not in text:
            raise RuntimeError("Could not find webview message switch for task context bridge")
        old_context_case = 'case"codex-task-context":{codexRememberTaskContext(r);break}'
        if old_context_case in text:
            text = text.replace(old_context_case, context_case, 1)
        else:
            text = text.replace(switch_anchor, f"switch(r.type){{{context_case}case\"ready\":break;", 1)
        changed = True

    # ------------------------------------------------------------------
    # 3) Inject rename/pin/star methods into the chat-session class.
    #
    # We always splice them in just before `async trackTabIfNeeded(`. The
    # vscode-namespace alias is captured from the trackTabIfNeeded signature
    # so the methods reference the right alias regardless of build.
    # ------------------------------------------------------------------
    if "async renameChatSessionItem(e)" not in text:
        rename_body = (
            f"async renameChatSessionItem(e){{let r=await codexRenameTask(e,{vscode_ns},"
            f"(n,o)=>this.conversationLoader.requestThreadNameSet(n,o));"
            f"return r&&(this.pendingConversations.delete(codexRenameThreadId(e)),"
            f"this.onDidChangeChatSessionItemsEmitter.fire()),r}}"
        )
        pin_body = (
            f"async pinChatSessionItem(e){{let r=await codexPinTask(e,{vscode_ns},"
            f"(n,o)=>this.conversationLoader.requestThreadNameSet(n,o));"
            f"return r&&(this.pendingConversations.delete(codexRenameThreadId(e)),"
            f"this.onDidChangeChatSessionItemsEmitter.fire()),r}}"
        )
        star_body = (
            f"async starChatSessionItem(e){{let r=await codexStarTask(e,{vscode_ns},"
            f"(n,o)=>this.conversationLoader.requestThreadNameSet(n,o));"
            f"return r&&(this.pendingConversations.delete(codexRenameThreadId(e)),"
            f"this.onDidChangeChatSessionItemsEmitter.fire()),r}}"
        )
        injection = rename_body + pin_body + star_body
        track_signature = (
            f"async trackTabIfNeeded(e){{let r=e.input;if(!(r instanceof {vscode_ns}.TabInputCustom))return;"
        )
        if track_signature not in text:
            raise RuntimeError("trackTabIfNeeded anchor moved between detection and injection")
        text = text.replace(track_signature, injection + track_signature, 1)
        changed = True
    elif "async pinChatSessionItem(e)" not in text or "async starChatSessionItem(e)" not in text:
        # Older patched copy had only renameChatSessionItem. Replace it with
        # the full triplet.
        m_rename = re.search(
            r"async renameChatSessionItem\(e\)\{[^}]*?codexRenameTask\([^)]*\)[^}]*?\}",
            text,
            re.DOTALL,
        )
        if m_rename is None:
            raise RuntimeError("Found renameChatSessionItem but could not bracket it for replacement")
        rename_body = (
            f"async renameChatSessionItem(e){{let r=await codexRenameTask(e,{vscode_ns},"
            f"(n,o)=>this.conversationLoader.requestThreadNameSet(n,o));"
            f"return r&&(this.pendingConversations.delete(codexRenameThreadId(e)),"
            f"this.onDidChangeChatSessionItemsEmitter.fire()),r}}"
        )
        pin_body = (
            f"async pinChatSessionItem(e){{let r=await codexPinTask(e,{vscode_ns},"
            f"(n,o)=>this.conversationLoader.requestThreadNameSet(n,o));"
            f"return r&&(this.pendingConversations.delete(codexRenameThreadId(e)),"
            f"this.onDidChangeChatSessionItemsEmitter.fire()),r}}"
        )
        star_body = (
            f"async starChatSessionItem(e){{let r=await codexStarTask(e,{vscode_ns},"
            f"(n,o)=>this.conversationLoader.requestThreadNameSet(n,o));"
            f"return r&&(this.pendingConversations.delete(codexRenameThreadId(e)),"
            f"this.onDidChangeChatSessionItemsEmitter.fire()),r}}"
        )
        text = text[: m_rename.start()] + rename_body + pin_body + star_body + text[m_rename.end() :]
        changed = True

    # ------------------------------------------------------------------
    # 4) Inject `requestThreadNameSet` next to `requestThreadList` on the
    #    conversation-loader class.
    # ------------------------------------------------------------------
    if "requestThreadNameSet(e,r)" not in text:
        # The conversation-loader class wraps requestThreadList and is the one
        # that sends to *provider_id*. There can be several requestThreadList
        # methods across classes; we want the one whose sendRequest uses our
        # captured provider id. We accept any body shape — the only invariant
        # is the method name, the sendRequest call to *provider_id*, and the
        # trailing `,n}};` (return promise + close method + close class body).
        m_list = re.search(
            rf'(requestThreadList\(e\)\{{.*?sendRequest\({provider_id},r,"thread/list",.*?,n\}})\}};',
            text,
            re.DOTALL,
        )
        if m_list is None:
            raise RuntimeError("Could not find requestThreadList method bound to the chat-session-provider id")
        list_method = m_list.group(1)
        name_set_method = (
            f"requestThreadNameSet(e,r){{let n=String(this.nextRequestId++),"
            f"o=new Promise((i,s)=>{{this.requestToCallback.set(n,a=>{{"
            f"if(a.error){{s(new Error(a.error.message));return}}i(a.result)}})}});"
            f'return this.codexAppServer.sendRequest({provider_id},n,"thread/name/set",'
            f"{{threadId:e,name:r}}),o}}"
        )
        replacement = list_method + name_set_method + "};"
        text = text[: m_list.start()] + replacement + text[m_list.end() :]
        changed = True

    # ------------------------------------------------------------------
    # 5) Register the rename/pin/star commands during activation.
    #
    # We anchor on the `triggerNewChatViaWebview()` call, which is stable
    # across builds, and inject right after the existing `e.push(...)` for it.
    # ------------------------------------------------------------------
    if 'registerCommand("chatgpt.renameTask"' not in text:
        m_activate = re.search(
            rf"e\.push\((?P<vscNs>{JS_ID})\.commands\.registerCommand\((?P<cmdId>{JS_ID}),"
            rf"async\(\)=>\{{await\s+(?P<authFn>{JS_ID})\(\),\s*(?P<sidebar>{JS_ID})\.triggerNewChatViaWebview\(\)\}}\)\)",
            text,
        )
        if m_activate is None:
            raise RuntimeError("Could not find triggerNewChatViaWebview command registration in extension.js")
        vsc_ns = m_activate.group("vscNs")

        def mk_register(command_id: str, action_label: str, method_name: str) -> str:
            return (
                f'e.push({vsc_ns}.commands.registerCommand("{command_id}",async J=>{{'
                f"try{{await g?.{method_name}(await codexWithTaskContext(J,{webview_provider_var}))}}"
                f"catch(xe){{{vsc_ns}.window.showErrorMessage("
                f"`Failed to {action_label} Codex task: ${{xe instanceof Error?xe.message:String(xe)}}`)}}}}))"
            )

        registrations = ",".join(
            (
                mk_register("chatgpt.renameTask", "rename", "renameChatSessionItem"),
                mk_register("chatgpt.pinTask", "pin", "pinChatSessionItem"),
                mk_register("chatgpt.unpinTask", "unpin", "pinChatSessionItem"),
                mk_register("chatgpt.starTask", "star", "starChatSessionItem"),
                mk_register("chatgpt.unstarTask", "unstar", "starChatSessionItem"),
            )
        )
        text = text[: m_activate.end()] + "," + registrations + text[m_activate.end() :]
        changed = True
    elif (
        'registerCommand("chatgpt.pinTask"' not in text
        or 'registerCommand("chatgpt.unpinTask"' not in text
        or 'registerCommand("chatgpt.starTask"' not in text
        or 'registerCommand("chatgpt.unstarTask"' not in text
    ):
        # Older patched extension only had rename. Replace just the rename
        # entry with all three registrations.
        m_rename_reg = re.search(
            r'e\.push\(([A-Za-z_$][\w$]*)\.commands\.registerCommand\("chatgpt\.renameTask",[^)]*?\}\)\)',
            text,
            re.DOTALL,
        )
        if m_rename_reg is None:
            raise RuntimeError("Found chatgpt.renameTask registration but could not bracket it")
        vsc_ns = m_rename_reg.group(1)

        def mk_register(command_id: str, action_label: str, method_name: str) -> str:
            return (
                f'e.push({vsc_ns}.commands.registerCommand("{command_id}",async J=>{{'
                f"try{{await g?.{method_name}(await codexWithTaskContext(J,{webview_provider_var}))}}"
                f"catch(xe){{{vsc_ns}.window.showErrorMessage("
                f"`Failed to {action_label} Codex task: ${{xe instanceof Error?xe.message:String(xe)}}`)}}}}))"
            )

        registrations = ",".join(
            (
                mk_register("chatgpt.renameTask", "rename", "renameChatSessionItem"),
                mk_register("chatgpt.pinTask", "pin", "pinChatSessionItem"),
                mk_register("chatgpt.unpinTask", "unpin", "pinChatSessionItem"),
                mk_register("chatgpt.starTask", "star", "starChatSessionItem"),
                mk_register("chatgpt.unstarTask", "unstar", "starChatSessionItem"),
            )
        )
        text = text[: m_rename_reg.start()] + registrations + text[m_rename_reg.end() :]
        changed = True

    command_context_replacements = (
        ("renameChatSessionItem", "g?.renameChatSessionItem(J)"),
        ("pinChatSessionItem", "g?.pinChatSessionItem(J)"),
        ("starChatSessionItem", "g?.starChatSessionItem(J)"),
    )
    for method_name, old_call in command_context_replacements:
        new_call = f"g?.{method_name}(await codexWithTaskContext(J,{webview_provider_var}))"
        while old_call in text:
            text = text.replace(old_call, new_call, 1)
            changed = True

    if changed:
        write(extension_js, text)
    return changed


def iter_webview_assets(extension_dir: Path):
    assets_dir = extension_dir / "webview" / "assets"
    if not assets_dir.exists():
        return
    yield from assets_dir.glob("*.js")


SIDEBAR_THREAD_ROW_RE = re.compile(
    rf"sidebarThreadRow:\(\{{active:e,hostId:t,id:n,kind:r,pinned:i,title:a\}}\)=>"
    rf"\(\{{\[(?P<ns>{JS_ID})\.sidebarThreadActive\]:String\(e\),"
    rf"\[(?P=ns)\.sidebarThreadHostId\]:t\?\?``,"
    rf"\[(?P=ns)\.sidebarThreadId\]:n,"
    rf"\[(?P=ns)\.sidebarThreadKind\]:r,"
    rf"\[(?P=ns)\.sidebarThreadPinned\]:String\(i\),"
    rf"\[(?P=ns)\.sidebarThreadRow\]:``,"
    rf"\[(?P=ns)\.sidebarThreadTitle\]:a\}}\)"
)

CODEX_CONTEXT_BRIDGE_JS = r"""
;(function(){try{if(typeof document==="undefined"||window.__codexTaskContextBridgeV5)return;window.__codexTaskContextBridgeV5=!0;let l="",u=0;function c(e){let t=e.getAttribute(`data-app-action-sidebar-thread-id`)||``;if(!t)return null;let n=(e.getAttribute(`data-app-action-sidebar-thread-title`)||e.textContent||``).trim(),a=n.replace(/^⭐\s*/,``),r={codexTask:!0,webviewSection:`codex-task`,codexThreadId:t,codexThreadTitle:n,codexStarred:n.startsWith(`⭐ `),codexPinned:a.startsWith(`📌 `),preventDefaultContextMenuItems:!0},o=JSON.stringify(r);e.setAttribute(`data-vscode-context`,o);for(let i of e.querySelectorAll(`*`))i.setAttribute(`data-vscode-context`,o);window.__codexLastTaskContext=r;return r}function p(){return window.__codexLastTaskContext||c(document.querySelector(`[data-app-action-sidebar-thread-row]:hover`))||c(document.querySelector(`[data-app-action-sidebar-thread-row]`))}function d(e,t){if(!t||typeof window.__codexPostMessage!="function")return;let n=Date.now();if(!e||e!==l||n-u>500){l=e,u=n;window.__codexPostMessage(`codex-task-context`,t)}}function s(e){let t=e&&e.target;if(t instanceof Element){let e=t.closest(`[data-app-action-sidebar-thread-row]`);if(e){let t=c(e);t&&d(t.codexThreadId,t)}}}function i(){document.querySelectorAll(`[data-app-action-sidebar-thread-row]`).forEach(c)}window.addEventListener(`message`,function(e){let t=e&&e.data;if(t&&t.type===`codex-request-task-context`){let e=p();typeof window.__codexPostMessage=="function"&&window.__codexPostMessage(`codex-task-context-response`,{...(e||{}),requestId:t.requestId})}},!0);document.addEventListener(`pointerover`,s,!0);document.addEventListener(`mousemove`,s,!0);document.addEventListener(`pointerdown`,s,!0);document.addEventListener(`contextmenu`,s,!0);new MutationObserver(i).observe(document.documentElement,{childList:!0,subtree:!0,attributes:!0,attributeFilter:[`data-app-action-sidebar-thread-id`,`data-app-action-sidebar-thread-title`]});setTimeout(i,0),setTimeout(i,250),setTimeout(i,1000)}catch(e){console.warn(`codex task context bridge failed`,e)}})();
"""

CODEX_CONTEXT_RESPONDER_JS = r"""
;(function(){try{if(typeof document==="undefined"||window.__codexTaskContextResponderV1)return;window.__codexTaskContextResponderV1=!0;function c(e){if(!(e instanceof Element))return null;let t=e.closest(`[data-app-action-sidebar-thread-row]`);if(!t)return null;let n=t.getAttribute(`data-app-action-sidebar-thread-id`)||``;if(!n)return null;let r=(t.getAttribute(`data-app-action-sidebar-thread-title`)||t.textContent||``).trim(),i=r.replace(/^⭐\s*/,``),o={codexTask:!0,webviewSection:`codex-task`,codexThreadId:n,codexThreadTitle:r,codexStarred:r.startsWith(`⭐ `),codexPinned:i.startsWith(`📌 `),preventDefaultContextMenuItems:!0};window.__codexLastTaskContext=o;return o}function s(){return window.__codexLastTaskContext||c(document.querySelector(`[data-app-action-sidebar-thread-row]:hover`))||c(document.querySelector(`[data-app-action-sidebar-thread-row]`))}function p(e){let t=c(e.target);t&&typeof window.__codexPostMessage=="function"&&window.__codexPostMessage(`codex-task-context`,t)}window.addEventListener(`message`,function(e){let t=e&&e.data;if(t&&t.type===`codex-request-task-context`){let e=s();typeof window.__codexPostMessage=="function"&&window.__codexPostMessage(`codex-task-context-response`,{...(e||{}),requestId:t.requestId})}},!0);document.addEventListener(`pointerover`,p,!0);document.addEventListener(`mousemove`,p,!0);document.addEventListener(`pointerdown`,p,!0);document.addEventListener(`contextmenu`,p,!0)}catch(e){console.warn(`codex task context responder failed`,e)}})();
"""


def _patch_sidebar_thread_row(assets_dir: Path) -> bool:
    """Append data-vscode-context to the sidebarThreadRow data-attribute helper.

    The helper appears in one of the webview *.js files; we locate it by
    regex on the stable property names so the namespace alias may be any
    minified identifier.
    """
    changed = False
    found_helper = False
    already_patched = False
    for path in assets_dir.glob("*.js"):
        text = read(path)
        stripped = re.sub(
            r";\(function\(\)\{try\{if\(typeof document===`undefined`\|\|window\.__codexTaskContextBridgeV\d+\).*?\}\)\(\);",
            "",
            text,
        )
        if stripped != text:
            text = stripped
            changed = True
        m = SIDEBAR_THREAD_ROW_RE.search(text)
        if not m:
            if "webviewSection:`codex-task`,codexThreadId:n,codexThreadTitle:a" in text:
                already_patched = True
            continue
        found_helper = True
        # Already patched if the JSON.stringify call is right after the title slot.
        tail = text[m.end() : m.end() + 200]
        if "webviewSection:`codex-task`,codexThreadId:n,codexThreadTitle:a" in text[m.start() : m.end() + 200]:
            already_patched = True
            continue
        ns = m.group("ns")
        replacement = (
            f"sidebarThreadRow:({{active:e,hostId:t,id:n,kind:r,pinned:i,title:a}})=>"
            f"({{[{ns}.sidebarThreadActive]:String(e),"
            f"[{ns}.sidebarThreadHostId]:t??``,"
            f"[{ns}.sidebarThreadId]:n,"
            f"[{ns}.sidebarThreadKind]:r,"
            f"[{ns}.sidebarThreadPinned]:String(i),"
            f"[{ns}.sidebarThreadRow]:``,"
            f"[{ns}.sidebarThreadTitle]:a,"
            f'"data-vscode-context":JSON.stringify({{codexTask:!0,webviewSection:`codex-task`,'
            f"codexThreadId:n,codexThreadTitle:a,codexStarred:String(a??``).trim().startsWith(`⭐ `),"
            f"codexPinned:String(a??``).trim().replace(/^⭐\\s*/,``).startsWith(`📌 `),"
            f"preventDefaultContextMenuItems:!0}})}})"
        )
        text = text[: m.start()] + replacement + text[m.end() :]
        if "__codexTaskContextBridgeV5" not in text:
            text += CODEX_CONTEXT_BRIDGE_JS
        write(path, text)
        changed = True
        break  # only one file contains it

    if not changed and already_patched:
        for path in assets_dir.glob("*.js"):
            text = read(path)
            if "webviewSection:`codex-task`,codexThreadId:n,codexThreadTitle:a" not in text:
                continue
            if "__codexTaskContextBridgeV5" in text:
                break
            write(path, text + CODEX_CONTEXT_BRIDGE_JS)
            changed = True
            break

    if not changed and not (found_helper or already_patched):
        raise RuntimeError("Could not find sidebarThreadRow data-attributes helper in webview assets")
    return changed


def _patch_vscode_post_message_bridge(assets_dir: Path) -> bool:
    """Expose the existing VS Code API object for our right-click bridge."""
    changed = False
    found = False
    for path in assets_dir.glob("*.js"):
        text = read(path)
        if "acquireVsCodeApi()" not in text:
            continue
        found = True
        if "__codexPostMessage" in text:
            continue
        m = re.search(rf"var\s+(?P<api>{JS_ID})=acquireVsCodeApi\(\),(?P<cls>{JS_ID})=class", text)
        if m is None:
            continue
        api = m.group("api")
        cls = m.group("cls")
        replacement = (
            f"var {api}=acquireVsCodeApi();"
            f"globalThis.__codexPostMessage=function(e,t){{{api}.postMessage({{...t,type:e}})}};"
            f"var {cls}=class"
        )
        text = text[: m.start()] + replacement + text[m.end() :]
        if "__codexTaskContextResponderV1" not in text:
            text += CODEX_CONTEXT_RESPONDER_JS
        write(path, text)
        changed = True
        break
    if not (changed or any("__codexPostMessage" in read(path) for path in assets_dir.glob("*.js"))):
        if found:
            raise RuntimeError("Found acquireVsCodeApi but could not expose Codex postMessage bridge")
        raise RuntimeError("Could not find acquireVsCodeApi in webview assets")
    if not any("__codexTaskContextResponderV1" in read(path) for path in assets_dir.glob("*.js")):
        for path in assets_dir.glob("*.js"):
            text = read(path)
            if "__codexPostMessage" not in text:
                continue
            write(path, text + CODEX_CONTEXT_RESPONDER_JS)
            changed = True
            break
    return changed


def _patch_task_row_data_attrs(
    task_row_text: str,
    archive_string: str,
    title_pref_vars: tuple[str, ...] = (),
    *,
    thread_id_expr: str | None = None,
    title_expr_override: str | None = None,
    search_back_window: int = 0,
    search_window: int = 4000,
) -> tuple[str, bool]:
    """Splice codexRenameContext into a task-row React-memo block.

    *archive_string* is the stable literal (e.g. ``"codex.localTaskRow.archiveTask"``)
    that scopes the search to one row. *title_pref_vars* is a hint for variables
    that may carry the row title (used to populate `codexThreadTitle` in the
    injected JSON context); when none are present, falls back to an empty
    string. *thread_id_expr* and *title_expr_override* let callers provide the
    row's actual minified variables when the upstream dataAttributes object
    does not carry the Codex thread id. *search_window* and
    *search_back_window* bound the search around the archive_string anchor so
    we never accidentally match code belonging to a different row.

    Returns (new_text, changed).
    """
    text = task_row_text

    anchor_idx = text.find(archive_string)
    if anchor_idx == -1:
        raise RuntimeError(f"Could not find task-row anchor: {archive_string!r}")

    window_start = max(0, anchor_idx - search_back_window)
    window_end = min(len(text), anchor_idx + search_window)

    # Look forward (within the window only) for either:
    #   dataAttributes:<var>,archiveAriaLabel
    # or
    #   archiveAriaLabel:<var>,...,dataAttributes:<var>
    # The two orderings show up across the local- vs cloud-task rows.
    attrs_re = re.compile(rf"dataAttributes:(?P<v>{JS_ID})(?:,archiveAriaLabel|\}}\))")
    attr_matches = list(attrs_re.finditer(text, window_start, window_end))
    if search_back_window:
        attr_matches = [m for m in attr_matches if m.start() < anchor_idx]
    else:
        attr_matches = [m for m in attr_matches if m.start() >= anchor_idx]
    m_attrs = attr_matches[-1] if search_back_window else (attr_matches[0] if attr_matches else None)
    if m_attrs is None:
        raise RuntimeError(f"Could not find dataAttributes argument for row near {archive_string!r}")
    data_var = m_attrs.group("v")
    if data_var == "codexRenameContext":
        return text, False

    # Identify the React-memo slot for *data_var*: a `t[N]=<data_var>,` or
    # `t[N]=<data_var>` (no trailing comma if last in the assigns) that
    # appears within the search window after dataAttributes.
    m_slot = re.compile(rf"t\[(?P<idx>\d+)\]={re.escape(data_var)}(?=[,)])").search(text, m_attrs.end(), window_end)
    if m_slot is None:
        raise RuntimeError(f"Could not find React-memo slot for var {data_var!r} near {archive_string!r}")
    slot = m_slot.group("idx")
    slot_check_re = re.compile(rf"t\[{slot}\]!=={re.escape(data_var)}\|\|")

    if title_expr_override is not None:
        title_expr = title_expr_override
    else:
        # Build a title fallback from candidate vars if they exist near the anchor.
        available = []
        window_text = text[window_start:window_end]
        for v in title_pref_vars:
            if re.search(rf"(?:[,{{]){re.escape(v)}[,}}]|\b{re.escape(v)}={JS_ID}", window_text):
                available.append(v)
        if available:
            title_expr = "||".join(f'(typeof {v}=="string"?{v}:"")' for v in available) + '||""'
        else:
            title_expr = '""'
    id_expr = thread_id_expr or f"{data_var}?.codexThreadId"

    rename_context_decl = (
        f'let codexRenameContext={{...{data_var},"data-vscode-context":JSON.stringify({{'
        f'codexTask:!0,webviewSection:`codex-task`,codexThreadId:String({id_expr}??""),'
        f"codexThreadTitle:{title_expr},codexStarred:String({title_expr}??``).trim().startsWith(`⭐ `),"
        f"codexPinned:String({title_expr}??``).trim().replace(/^⭐\\s*/,``).startsWith(`📌 `),"
        f"preventDefaultContextMenuItems:!0}})}};"
    )

    # The let-prefix that starts the React-memo chain containing our slot.
    # Strategy: find the slot check, then scan backwards over the chain to
    # find the FIRST `t[k]!==<x>||` in this chain (the chain starts after a
    # `;` or `return ` boundary). Inject the codexRenameContext declaration
    # just before that boundary.
    slot_check_match = slot_check_re.search(text, window_start, m_attrs.start())
    if slot_check_match is None:
        raise RuntimeError(f"Could not find slot check t[{slot}]!=={data_var} near {archive_string!r}")

    # Walk backwards from slot_check_match.start() to find the chain boundary.
    # Each link of the chain is `t[<n>]!==<var>||`. Tracking the leftmost
    # `t[<n>]!==<var>` in the chain.
    i = slot_check_match.start()
    leftmost = i
    while True:
        prev_link = re.search(rf"t\[\d+\]!=={JS_ID}\|\|$", text[:leftmost])
        if prev_link is None:
            break
        if prev_link.end() != leftmost:
            break
        leftmost = prev_link.start()
        if leftmost < 0:
            break

    # The chain's left edge is at `leftmost`. The character immediately before
    # it should be either `;` or end of `return `. We insert the rename
    # context declaration just after the previous statement boundary.
    if leftmost > 0:
        # Find the statement boundary preceding the chain: nearest `;` to the
        # left, OR the start of a `return ` if any.
        boundary = leftmost
        # Move boundary back over an optional `return ` keyword.
        m_ret = re.search(r"return\s+$", text[:boundary])
        if m_ret is not None:
            boundary = m_ret.start()
        # Insert the codexRenameContext declaration at *boundary*.
        text = text[:boundary] + rename_context_decl + text[boundary:]
    else:
        text = rename_context_decl + text

    # Replace the three references to data_var with codexRenameContext,
    # bounded to the window so we never touch another row.
    insert_pos = text.find(rename_context_decl)
    if insert_pos == -1:
        raise RuntimeError("Lost codexRenameContext insertion point")
    after_pos = insert_pos + len(rename_context_decl)
    window_end_now = after_pos + search_window  # text grew; extend by same window
    if window_end_now > len(text):
        window_end_now = len(text)

    def replace_once(pattern: re.Pattern[str], replacement: str) -> None:
        nonlocal text, window_end_now
        m = pattern.search(text, after_pos, window_end_now)
        if m is None:
            raise RuntimeError(f"Could not apply replacement for pattern {pattern.pattern!r}")
        new_text = text[: m.start()] + replacement + text[m.end() :]
        window_end_now += len(replacement) - (m.end() - m.start())
        text = new_text

    replace_once(slot_check_re, f"t[{slot}]!==codexRenameContext||")
    replace_once(
        re.compile(rf"dataAttributes:{re.escape(data_var)}(?=[,)}}])"),
        "dataAttributes:codexRenameContext",
    )
    replace_once(
        re.compile(rf"t\[{slot}\]={re.escape(data_var)}(?=[,)])"),
        f"t[{slot}]=codexRenameContext",
    )

    return text, True


def patch_rename_webview_assets(extension_dir: Path) -> bool:
    """Add data-vscode-context to webview rows so VS Code's right-click menu
    can identify them as Codex task rows.

    The context menu is rendered by VS Code from the DOM row that was
    right-clicked, so the row itself must carry the task id/title. The helper
    patch covers builds where parent attributes flow through cleanly; the row
    splice below covers builds where the task rows receive a generic
    dataAttributes object that does not include codexThreadId.
    """
    assets_dir = extension_dir / "webview" / "assets"
    if not assets_dir.exists():
        return False
    changed = _patch_sidebar_thread_row(assets_dir)
    changed |= _patch_vscode_post_message_bridge(assets_dir)

    row_asset = _find_asset_by_regex(
        extension_dir,
        r"codex\.localTaskRow\.archiveTask[\s\S]*?dataAttributes:|dataAttributes:[\s\S]*?codex\.cloudTaskRow\.archiveTask",
    )
    if row_asset is not None:
        text = read(row_asset)
        text, local_changed = _patch_task_row_data_attrs(
            text,
            "codex.localTaskRow.archiveTask",
            thread_id_expr="n",
            title_expr_override='(typeof ue=="string"?ue:typeof He=="string"?He:typeof nt=="string"?nt:"")',
            search_window=12000,
        )
        text, cloud_changed = _patch_task_row_data_attrs(
            text,
            "codex.cloudTaskRow.archiveTask",
            thread_id_expr="n.id",
            title_expr_override='(typeof F=="string"?F:typeof je=="string"?je:"")',
            search_back_window=12000,
            search_window=12000,
        )
        if local_changed or cloud_changed:
            write(row_asset, text)
            changed = True
    return changed


def _find_asset_by_literal(extension_dir: Path, literal: str) -> Path | None:
    """Return the first webview asset that contains *literal*, or None."""
    for path in iter_webview_assets(extension_dir) or []:
        if literal in read(path):
            return path
    return None


def _find_asset_by_regex(extension_dir: Path, pattern: str, flags: int = 0) -> Path | None:
    compiled = re.compile(pattern, flags)
    for path in iter_webview_assets(extension_dir) or []:
        if compiled.search(read(path)):
            return path
    return None


def patch_recent_tasks_menu(extension_dir: Path) -> bool:
    changed = False

    # ------------------------------------------------------------------
    # Recent-tasks header asset. The literal `header.recentTasks.seeAll`
    # appears in 60+ locale translation files, so we discriminate by
    # requiring a JSX call to that message id (only the implementation has
    # one) OR the slice expression that builds the 3-item preview list.
    # ------------------------------------------------------------------
    recent_asset = _find_asset_by_regex(
        extension_dir,
        rf"\(0,{JS_ID}\.jsx\)\({JS_ID},\{{id:`header\.recentTasks\.seeAll`",
    )
    if recent_asset is None:
        recent_asset = _find_asset_by_regex(
            extension_dir,
            r"=\(0,[A-Za-z_$][\w$]*\.default\)\(\[\.\.\.e,\.\.\.n\],[A-Za-z_$][\w$]*\)\.slice\(0,Math\.max\(3,e\.length\)\)",
        )
    if recent_asset is None:
        raise RuntimeError(
            "Could not find recent-tasks-menu asset (header.recentTasks.seeAll JSX call or slice anchor)"
        )
    text = read(recent_asset)

    # 1) Remove the .slice(0, Math.max(3, e.length)) cap.
    text, n = re.subn(
        rf"(=\(0,{JS_ID}\.default\)\(\[\.\.\.e,\.\.\.n\],{JS_ID}\))\.slice\(0,Math\.max\(3,e\.length\)\)",
        r"\1",
        text,
        count=1,
    )
    if n:
        changed = True

    # 2) Neutralize the "View all" link. We replace the entire
    #    `let <F>;t[i1]!==n.length||t[i2]!==<U>?(...):...=t[i3];let <P>;`
    #    block with `let <F>=null;t[i1]=n.length,t[i2]=<U>,t[i3]=<F>;let <P>;`.
    #
    # Anchor on the `header.recentTasks.seeAll` literal: from that point,
    # scan backwards for the `let <F>;t[i1]!==n.length` opening and forward
    # for the `let <P>;` close. This avoids fragile regex over the deeply-
    # nested JSX argument.
    see_all_idx = text.find("`header.recentTasks.seeAll`")
    if see_all_idx != -1:
        # Match the OPEN of the View-all block: `let <F>;t[i1]!==n.length||t[i2]!==<U>?(`
        open_re = re.compile(
            rf"let\s+(?P<f>{JS_ID});" rf"t\[(?P<i1>\d+)\]!==n\.length\|\|t\[(?P<i2>\d+)\]!==(?P<u>{JS_ID})\?\("
        )
        m_open = None
        for m in open_re.finditer(text, max(0, see_all_idx - 2000), see_all_idx):
            m_open = m  # take the LAST match before seeAll
        # Match the CLOSE: `,t[i1]=n.length,t[i2]=<U>,t[i3]=<F>):<F>=t[i3];let <P>;`
        if m_open is not None:
            close_re = re.compile(
                rf",t\[{m_open.group('i1')}\]=n\.length,"
                rf"t\[{m_open.group('i2')}\]={re.escape(m_open.group('u'))},"
                rf"t\[(?P<i3>\d+)\]={re.escape(m_open.group('f'))}\):"
                rf"{re.escape(m_open.group('f'))}=t\[(?P=i3)\];"
                rf"let\s+(?P<p>{JS_ID});"
            )
            m_close = close_re.search(text, see_all_idx, see_all_idx + 2000)
            if m_close is not None:
                replacement = (
                    f"let {m_open.group('f')}=null;"
                    f"t[{m_open.group('i1')}]=n.length,"
                    f"t[{m_open.group('i2')}]={m_open.group('u')},"
                    f"t[{m_close.group('i3')}]={m_open.group('f')};"
                    f"let {m_close.group('p')};"
                )
                text = text[: m_open.start()] + replacement + text[m_close.end() :]
                changed = True

    # 3) Section / outer height tweaks. Class names are stable literals.
    old_section_height = "className:`vertical-scroll-fade-mask flex max-h-[60vh] flex-col gap-0 overflow-y-auto pb-1`"
    new_section_height = "className:`vertical-scroll-fade-mask flex max-h-[calc(var(--radix-popper-available-height)_-_120px)] flex-col gap-0 overflow-y-auto pb-1`"
    if old_section_height in text:
        text = text.replace(old_section_height, new_section_height, 1)
        changed = True

    old_outer_height = "className:`flex max-h-[300px] w-[calc(var(--radix-popper-available-width)_-_var(--padding-panel))] flex-col gap-1`"
    new_outer_height = "className:`flex max-h-[calc(var(--radix-popper-available-height)_-_var(--padding-panel))] w-[calc(var(--radix-popper-available-width)_-_var(--padding-panel))] flex-col gap-1`"
    if old_outer_height in text:
        text = text.replace(old_outer_height, new_outer_height, 1)
        changed = True

    if changed:
        write(recent_asset, text)

    # ------------------------------------------------------------------
    # Profile dropdown — usually in a different asset (history-*.js).
    # Stable anchor: a JSX call constructing the keyboardShortcuts message.
    # The plain literal `codex.profileDropdown.keyboardShortcuts` also
    # appears in every locale translation file, so we discriminate by
    # requiring the surrounding JSX call.
    # ------------------------------------------------------------------
    dropdown_asset = _find_asset_by_regex(
        extension_dir,
        rf"\(0,{JS_ID}\.jsx\)\({JS_ID},\{{id:`codex\.profileDropdown\.keyboardShortcuts`",
    )
    if dropdown_asset is None:
        log(
            "Note: profile dropdown asset (codex.profileDropdown.keyboardShortcuts) not found; skipping Search-Chats menu injection"
        )
    else:
        dt = read(dropdown_asset)
        if "codex.profileDropdown.searchChats" not in dt:
            dt2, dropdown_changed = _inject_search_chats_menu_item(dt)
            if dropdown_changed:
                write(dropdown_asset, dt2)
                changed = True
        else:
            # Already has search-chats — try to normalize the click delay.
            normalized = re.sub(
                rf"(onClick:\(\)=>\{{[^}}]*?\(0,{JS_ID}\.jsx\)\({JS_ID},\{{id:`codex\.profileDropdown\.searchChats`)",
                lambda m: m.group(0),  # passthrough; below handles delay
                dt,
            )
            normalized, dn = re.subn(
                r"(window\.setTimeout\(\(\)=>window\.dispatchEvent\(new CustomEvent\(`open-recent-tasks-menu`\)\)),\s*\d+\)",
                r"\g<1>,250)",
                normalized,
            )
            if dn:
                write(dropdown_asset, normalized)
                changed = True

    return changed


def _inject_search_chats_menu_item(text: str) -> tuple[str, bool]:
    """Inject a Search-Chats item into the profile dropdown children array.

    Re-uses components and aliases discovered from the existing
    `keyboardShortcuts` menu entry so we don't have to know any
    minified identifiers up front.
    """
    # The most reliable anchor is the existing keyboardShortcuts menu item:
    # it's right next to where we want to inject, and the surrounding code
    # tells us the JSX alias, the wrapper/item components, an icon, AND the
    # dropdown's close-state setter (captured from its onClick handler).
    #
    # Pattern: `(<setter>(!1), ae.dispatchMessage(\`open-keyboard-shortcuts\`,...)`
    # — every menu item shares the same close-setter, so we capture it here.
    close_setter_probe = re.search(
        rf"\(\)=>\{{(?P<setter>{JS_ID})\(!1\),{JS_ID}\.dispatchMessage\(`open-keyboard-shortcuts`",
        text,
    )
    if close_setter_probe is None:
        return text, False
    close_setter = close_setter_probe.group("setter")

    # Probe the keyboardShortcuts JSX call to discover the jsx alias, the
    # wrapper component, the menu-item component, and an icon.
    probe = re.search(
        rf"\(0,(?P<jsx>{JS_ID})\.jsx\)\((?P<wrap>{JS_ID}),\{{extension:!0,"
        rf"children:\(0,(?P=jsx)\.jsx\)\((?P<item>{JS_ID}),\{{LeftIcon:(?P<icon>{JS_ID}),"
        rf"onClick:{JS_ID},children:{JS_ID}\}}\)\}}\)",
        text,
    )
    if probe is None:
        probe = re.search(
            rf"\(0,(?P<jsx>{JS_ID})\.jsx\)\((?P<wrap>{JS_ID}),\{{"
            rf"children:\(0,(?P=jsx)\.jsx\)\((?P<item>{JS_ID}),\{{LeftIcon:(?P<icon>{JS_ID}),"
            rf"onClick:{JS_ID},children:{JS_ID}\}}\)\}}\)",
            text,
        )
    if probe is None:
        return text, False
    jsx_ns = probe.group("jsx")
    wrap = probe.group("wrap")
    item = probe.group("item")
    icon = probe.group("icon")

    # Locate the intl-message component by finding a sibling profile-dropdown
    # message id.
    msg_probe = re.search(
        rf"\(0,{re.escape(jsx_ns)}\.jsx\)\((?P<msg>{JS_ID}),\{{id:`codex\.profileDropdown\.",
        text,
    )
    if msg_probe is None:
        return text, False
    msg = msg_probe.group("msg")

    # Locate the children array of the profile dropdown — the one within
    # ~4KB of the keyboardShortcuts message.
    children_re = re.compile(
        rf"children:\[(?P<c1>{JS_ID}),(?P<c2>{JS_ID}),(?P<c3>{JS_ID}),(?P<c4>{JS_ID}),"
        rf"(?P<c5>{JS_ID}),(?P<c6>{JS_ID}),(?P<c7>{JS_ID}),(?P<c8>{JS_ID})\]"
    )
    target = None
    for m in children_re.finditer(text):
        start = max(0, m.start() - 4000)
        if "codex.profileDropdown.keyboardShortcuts" in text[start : m.end()]:
            target = m
            break
    if target is None:
        return text, False

    children = [target.group(f"c{i}") for i in range(1, 9)]
    search_chat_jsx = (
        f"(0,{jsx_ns}.jsx)({wrap},{{extension:!0,children:(0,{jsx_ns}.jsx)({item},"
        f"{{LeftIcon:{icon},onClick:()=>{{{close_setter}(!1),window.setTimeout(()=>"
        f"window.dispatchEvent(new CustomEvent(`open-recent-tasks-menu`)),250)}},"
        f"children:(0,{jsx_ns}.jsx)({msg},{{id:`codex.profileDropdown.searchChats`,"
        f"defaultMessage:`Search Chats`,description:`Menu item to search recent Codex chats`}})}})}})"
    )
    new_children = ",".join(children[:4] + [search_chat_jsx] + children[4:])
    text = text[: target.start()] + f"children:[{new_children}]" + text[target.end() :]
    return text, True


WORKSPACE_GROUP_HELPER_JS = r"""function codexPatchWorkspacePath(e){return typeof e=="string"&&e.trim()?e.trim():null}
function codexPatchWorkspaceLabel(e){let t=codexPatchWorkspacePath(e);if(!t)return`Other`;t=t.replace(/^\\\\\?\\/,"").replace(/\\/g,`/`).replace(/\/+$/,"");let n=t.split(`/`).filter(Boolean).pop();return n||t}
function codexPatchFirstPath(e){if(!e)return null;if(typeof e=="string")return codexPatchWorkspacePath(e);if(Array.isArray(e))for(let t of e){let n=codexPatchFirstPath(t);if(n)return n}if(typeof e=="object")return codexPatchWorkspacePath(e.path)||codexPatchWorkspacePath(e.uri)||codexPatchWorkspacePath(e.fsPath)||codexPatchWorkspacePath(e.root)||codexPatchWorkspacePath(e.cwd)||null;return null}
function codexPatchItemWorkspace(e){switch(e.kind){case`local`:return codexPatchWorkspacePath(e.conversation.cwd)||codexPatchWorkspacePath(e.conversation.workspaceRoot)||codexPatchFirstPath(e.conversation.workspaceRoots)||codexPatchWorkspacePath(e.conversation.workspacePath)||codexPatchWorkspacePath(e.conversation.rootPath)||codexPatchWorkspacePath(e.conversation.workingDirectory)||codexPatchWorkspacePath(e.conversation.directory)||codexPatchWorkspacePath(e.conversation.path)||codexPatchWorkspacePath(e.conversation.git?.root)||codexPatchWorkspacePath(e.conversation.metadata?.cwd)||codexPatchFirstPath(e.conversation.metadata?.workspaceRoots)||`Other`;case`remote`:return e.task.task_status_display?.environment_label||e.task.environment_label||e.task.project?.name||e.task.environment?.label||e.task.environment?.name||`Cloud`;case`pending-worktree`:return codexPatchWorkspacePath(e.pendingWorktree.workspacePath)||codexPatchWorkspacePath(e.pendingWorktree.cwd)||codexPatchWorkspacePath(e.pendingWorktree.path)||codexPatchFirstPath(e.pendingWorktree.workspaceRoots)||`Pending worktrees`;default:return`Other`}}
function codexPatchItemTitle(e){switch(e.kind){case`local`:return e.conversation.name||e.conversation.title||e.conversation.label||``;case`remote`:return e.task.title||e.task.name||e.task.task_title||e.task.task_status_display?.title||``;case`pending-worktree`:return e.pendingWorktree.title||e.pendingWorktree.name||``;default:return``}}
function codexPatchIsPinned(e){let t=codexPatchItemTitle(e).trim();return t.startsWith(`📌 `)||t.startsWith(`⭐ 📌 `)}
function codexPatchTaskGroups(e){let t=new Map;for(let n of e){let r=codexPatchItemWorkspace(n),i=codexPatchWorkspaceLabel(r),a=t.get(r);a==null&&(a={key:r,label:i,items:[]},t.set(r,a)),a.items.push(n)}return Array.from(t.values()).map(e=>(e.items.sort((e,t)=>Number(codexPatchIsPinned(t))-Number(codexPatchIsPinned(e))),e)).sort((e,t)=>e.label.localeCompare(t.label)||e.key.localeCompare(t.key))}
var codexPatchCollapsedWorkspaces=new Set;
function codexPatchToggleWorkspace(e,t){let n=!codexPatchCollapsedWorkspaces.has(e.key);n?codexPatchCollapsedWorkspaces.add(e.key):codexPatchCollapsedWorkspaces.delete(e.key);let r=t.currentTarget.parentElement?.children??[],i=!1;for(let a of r){if(a===t.currentTarget){i=!0;continue}if(!i)continue;if(a.getAttribute?.(`data-codex-workspace-header`)===`true`)break;if(a.getAttribute?.(`data-codex-workspace-item`)===e.key)a.style.display=n?`none`:``}let a=t.currentTarget.querySelector?.(`[data-codex-workspace-caret]`);a&&(a.textContent=n?`+`:`-`)}
function codexPatchGroupHeader(e){let t=codexPatchCollapsedWorkspaces.has(e.key);return(0,Q.jsxs)(`div`,{className:`cursor-interaction select-none px-[var(--padding-row-x)] pt-2 pb-1 text-xs font-medium uppercase tracking-wide text-token-input-placeholder-foreground hover:opacity-80`,style:{overflowX:`hidden`},"data-codex-workspace-header":`true`,role:`button`,tabIndex:0,onClick:t=>codexPatchToggleWorkspace(e,t),onKeyDown:t=>{(t.key===`Enter`||t.key===` `)&&(t.preventDefault(),codexPatchToggleWorkspace(e,t))},children:[(0,Q.jsx)(`span`,{"data-codex-workspace-caret":`true`,className:`mr-1 inline-block w-3`,children:t?`+`:`-`}),(0,Q.jsx)(`span`,{children:e.label})]},`${e.key}:header`)}
function codexPatchRenderGroupItems(e,t){let n=codexPatchCollapsedWorkspaces.has(e.key);return[codexPatchGroupHeader(e),...e.items.map(r=>(0,Q.jsx)(`div`,{"data-codex-workspace-item":e.key,style:{display:n?`none`:void 0,overflowX:`hidden`},children:t(r)},`${e.key}:${r.key}`))]}
function codexPatchGroupInlineTasks(e,t){return codexPatchTaskGroups(e).flatMap(e=>codexPatchRenderGroupItems(e,t))}
function codexPatchGroupTasks(e,t,n){return codexPatchTaskGroups(e).map(e=>(0,Q.jsxs)(Q.Fragment,{children:codexPatchRenderGroupItems(e,e=>(0,Q.jsx)(wu,{item:e,isActive:e.kind===`local`&&t===e.conversation.id,onClose:n},e.key))},e.key))}
"""


def _resolve_jsx_alias(text: str) -> str:
    """Discover the local JSX namespace alias used in a webview asset.

    Looks for `var <ns>=<importedJsx>();` near the top. Falls back to "Q".
    """
    # Look for the canonical pattern: `var <ns2>=<jsx_runtime>(),<jsx_ns>=<jsx_runtime2>();`
    m = re.search(rf"var\s+{JS_ID}=[A-Za-z_$][\w$]*\(\),(?P<jsx>{JS_ID})=[A-Za-z_$][\w$]*\(\);", text)
    if m:
        return m.group("jsx")
    m = re.search(rf"var\s+(?P<jsx>{JS_ID})=[A-Za-z_$][\w$]*\(\);", text)
    if m:
        return m.group("jsx")
    return "Q"


def patch_workspace_groups(extension_dir: Path) -> bool:
    # Stable anchor: the `.map(...)` call producing recent-task rows OR, once
    # the patcher has run, the `codexPatchGroupTasks(...)` substitution. We
    # accept either so the patch is idempotent.
    row_map_re = rf"{JS_ID}\.map\(e=>\(0,{JS_ID}\.jsx\)\({JS_ID},\{{item:e,isActive:e\.kind===`local`&&{JS_ID}===e\.conversation\.id,onClose:{JS_ID}\}},e\.key\)\)"
    recent_asset = _find_asset_by_regex(extension_dir, row_map_re)
    if recent_asset is None:
        recent_asset = _find_asset_by_regex(
            extension_dir,
            rf"codexPatchGroupTasks\({JS_ID},{JS_ID},{JS_ID}\)",
        )
    if recent_asset is None:
        raise RuntimeError(
            "Could not find workspace-grouping asset (recent-tasks row .map or codexPatchGroupTasks anchor)"
        )
    text = read(recent_asset)
    changed = False

    # Discover the row-component identifier from the original `.map` call.
    # The helper template hard-codes a name that changes between builds
    # (`wu` in the old build, `Ke` here), so we resolve it dynamically.
    m_probe = re.compile(
        rf"{JS_ID}\.map\(e=>\(0,(?P<jsx>{JS_ID})\.jsx\)\((?P<rowComp>{JS_ID}),"
        rf"\{{item:e,isActive:e\.kind===`local`&&{JS_ID}===e\.conversation\.id,"
        rf"onClose:{JS_ID}\}},e\.key\)\)"
    ).search(text)
    if m_probe is not None:
        row_component = m_probe.group("rowComp")
    else:
        # Patcher already ran. Recover the row component from the existing
        # helper body so a re-run doesn't change behavior.
        m_existing = re.search(
            rf"function codexPatchGroupTasks\([^)]+\)\{{[^}}]*?\(0,(?P<jsx>{JS_ID})\.jsx\)\((?P<rowComp>{JS_ID}),\{{item:",
            text,
            re.DOTALL,
        )
        if m_existing is None:
            raise RuntimeError("Could not resolve row-component identifier for workspace groups")
        row_component = m_existing.group("rowComp")

    # The helper template hard-codes `Q.jsx`/`Q.jsxs` and the row component
    # `wu`. Both vary per build, so substitute the discovered identifiers.
    jsx_alias = _resolve_jsx_alias(text)
    helper_src = WORKSPACE_GROUP_HELPER_JS
    if jsx_alias != "Q":
        helper_src = (
            helper_src.replace("Q.jsx", f"{jsx_alias}.jsx")
            .replace("Q.jsxs", f"{jsx_alias}.jsxs")
            .replace("Q.Fragment", f"{jsx_alias}.Fragment")
        )
    if row_component != "wu":
        helper_src = helper_src.replace("(wu,{item:e,", f"({row_component},{{item:e,")

    # Inject (or re-inject) the helper just after the file's `var <ns>=...();`
    # decl block, where the jsx alias becomes valid.
    existing_start = text.find("function codexPatchWorkspacePath(")
    if existing_start != -1:
        # Find end of existing helper: last `codexPatchGroupTasks(` close brace.
        # The helper ends with the closing `};` of codexPatchGroupTasks. Look
        # for the next non-helper code (any function that isn't codexPatch*).
        m_end = re.search(
            rf"function\s+(?!codexPatch){JS_ID}\(",
            text[existing_start:],
        )
        if m_end is None:
            raise RuntimeError("Could not bracket existing workspace grouping helper for replacement")
        existing_end = existing_start + m_end.start()
        if text[existing_start:existing_end] != helper_src:
            text = text[:existing_start] + helper_src + text[existing_end:]
            changed = True
    else:
        # Find injection point: right after the last top-level `var ... ();`
        # declaration block. As a robust fallback, inject before the FIRST
        # non-IIFE `function <name>(` that follows the imports / var block.
        # The safest place is after the var decl that introduces the jsx alias.
        m_decl = re.search(rf"var\s+(?:{JS_ID}=[A-Za-z_$][\w$]*\(\),)*{JS_ID}=[A-Za-z_$][\w$]*\(\);", text)
        if m_decl is None:
            # Fallback: inject after final import statement.
            last_import_end = 0
            i = 0
            while i < len(text) and text[i : i + 7] == "import{" or text[i : i + 7] == "import ":
                semi = text.find(";", i)
                if semi == -1:
                    break
                last_import_end = semi + 1
                i = semi + 1
                if not text[i : i + 6].startswith("import"):
                    break
            insert_at = last_import_end
        else:
            insert_at = m_decl.end()
        text = text[:insert_at] + helper_src + text[insert_at:]
        changed = True

    # Replace the recent-tasks .map with codexPatchGroupTasks(list, active, onClose).
    row_re = re.compile(
        rf"(?P<list>{JS_ID})\.map\(e=>\(0,(?P<jsx>{JS_ID})\.jsx\)\({JS_ID},"
        rf"\{{item:e,isActive:e\.kind===`local`&&(?P<active>{JS_ID})===e\.conversation\.id,"
        rf"onClose:(?P<onClose>{JS_ID})\}},e\.key\)\)"
    )
    m_row = row_re.search(text)
    if m_row and "codexPatchGroupTasks(" not in text[m_row.start() : m_row.end()]:
        replacement = f"codexPatchGroupTasks({m_row.group('list')},{m_row.group('active')},{m_row.group('onClose')})"
        text = text[: m_row.start()] + replacement + text[m_row.end() :]
        changed = True

    # Replace the inline-map: `<out>=<list>.map(e),t[8]=<v>,`
    inline_re = re.compile(rf"(?P<out>{JS_ID})=(?P<list>{JS_ID})\.map\(e\),t\[8\]=(?P<v0>{JS_ID}),")
    m_inline = inline_re.search(text)
    if m_inline:
        replacement = (
            f"{m_inline.group('out')}=codexPatchGroupInlineTasks({m_inline.group('list')},e),"
            f"t[8]={m_inline.group('v0')},"
        )
        text = text[: m_inline.start()] + replacement + text[m_inline.end() :]
        changed = True

    old_inline_group_class = (
        "className:`group/inline -mx-[var(--padding-row-x)] flex flex-col gap-px rounded-xl pb-1 transition-colors`"
    )
    new_inline_group_class = "className:`group/inline -mx-[var(--padding-row-x)] max-w-full overflow-x-hidden flex flex-col gap-px rounded-xl pb-1 transition-colors`"
    if old_inline_group_class in text:
        text = text.replace(old_inline_group_class, new_inline_group_class, 1)
        changed = True

    if changed:
        write(recent_asset, text)
    return changed


def patch_pin_composer(extension_dir: Path) -> bool:
    changed = False

    # The inline-tasks wrapper lives in the same asset that owns the recent-
    # tasks menu / workspace grouping. Stable anchor: the tasksQuery+mergedTasks
    # JSX call.
    inline_re_str = (
        rf"\(0,(?P<jsx>{JS_ID})\.jsx\)\((?P<tasksComp>{JS_ID}),"
        rf"\{{tasksQuery:(?P<tq>{JS_ID}),mergedTasks:(?P<mt>{JS_ID})\}}\)"
    )
    inline_asset = _find_asset_by_regex(extension_dir, inline_re_str)
    if inline_asset is None:
        raise RuntimeError("Could not find inline-tasks JSX (tasksQuery/mergedTasks)")
    text = read(inline_asset)

    # The wrapper just before that JSX call looks like:
    #   <S>=<c>&&(0,<jsx>.jsx)(`div`,{...children:(0,<jsx>.jsx)(<tasksComp>,{tasksQuery:<tq>,mergedTasks:<mt>})})
    # We replace the whole `<S>=<c>&&...` expression so we don't have to know
    # whatever the existing className/style happens to be (some prior patcher
    # runs may have already mutated it).
    wrapper_re = re.compile(
        rf"(?P<sVar>{JS_ID})=(?P<cVar>{JS_ID})&&\(0,(?P<jsx>{JS_ID})\.jsx\)\(`div`,\{{"
        rf"(?:[^{{}}]|\{{[^{{}}]*\}})*?"
        rf"children:\(0,(?P=jsx)\.jsx\)\((?P<tasksComp>{JS_ID}),"
        rf"\{{tasksQuery:(?P<tq>{JS_ID}),mergedTasks:(?P<mt>{JS_ID})\}}\)\}}\)"
    )
    m_wrap = wrapper_re.search(text)
    if m_wrap is None:
        raise RuntimeError("Could not find inline-tasks wrapper around tasksQuery JSX call")
    new_wrapper = (
        f"{m_wrap.group('sVar')}={m_wrap.group('cVar')}&&(0,{m_wrap.group('jsx')}.jsx)(`div`,"
        f"{{className:`overscroll-contain pr-1`,style:"
        f'{{maxHeight:"calc(100vh - 320px)",overflowY:"auto",overflowX:"hidden",scrollbarGutter:"stable"}},'
        f"onWheel:e=>{{e.currentTarget.scrollTop+=e.deltaY,e.stopPropagation()}},"
        f"children:(0,{m_wrap.group('jsx')}.jsx)({m_wrap.group('tasksComp')},"
        f"{{tasksQuery:{m_wrap.group('tq')},mergedTasks:{m_wrap.group('mt')}}})}})"
    )
    if m_wrap.group(0) != new_wrapper:
        text = text[: m_wrap.start()] + new_wrapper + text[m_wrap.end() :]
        write(inline_asset, text)
        changed = True

    # Sticky composer footer. Stable anchors: NewThreadPanelPage identifier +
    # thread-footer-overlap class + homePage.mainContent literal. Multiple
    # assets carry only one of these (e.g. app-main has thread-footer-overlap
    # too), so we require all three.
    new_thread_page = None
    for path in iter_webview_assets(extension_dir) or []:
        text_probe = read(path)
        if all(
            token in text_probe for token in ("NewThreadPanelPage", "thread-footer-overlap", "homePage.mainContent")
        ):
            new_thread_page = path
            break
    if new_thread_page is None:
        raise RuntimeError(
            "Could not find new-thread-panel asset (NewThreadPanelPage + thread-footer-overlap + homePage.mainContent)"
        )
    text = read(new_thread_page)
    new_footer_class = "sticky bottom-0 z-10 -mt-[var(--thread-footer-overlap)] flex flex-col gap-2 pb-2"
    normalized = re.sub(
        r"(?:sticky bottom-0 )+z-10 -mt-\[var\(--thread-footer-overlap\)\] flex flex-col gap-2 pb-2",
        new_footer_class,
        text,
        count=1,
    )
    if normalized != text:
        text = normalized
        write(new_thread_page, text)
        changed = True

    return changed


WEBVIEW_CACHE_BUST_MARKERS = (
    "__codexTaskContextBridgeV5",
    "__codexPostMessage",
    "__codexTaskContextResponderV1",
    "dataAttributes:codexRenameContext",
    "codex.profileDropdown.searchChats",
    "codexPatchCollapsedWorkspaces",
    "codexPatchGroupInlineTasks",
    "sticky bottom-0 z-10 -mt-[var(--thread-footer-overlap)]",
    'overflowX:"hidden"',
)


def cache_bust_patched_webview_assets(extension_dir: Path) -> bool:
    """Rename patched webview chunks so VS Code's webview SW cannot serve stale JS."""
    webview_dir = extension_dir / "webview"
    assets_dir = webview_dir / "assets"
    if not assets_dir.exists():
        return False

    changed = False
    targets: list[Path] = []
    for path in assets_dir.glob("*.js"):
        if "-codexpatch" in path.stem:
            continue
        text = read(path)
        if any(marker in text for marker in WEBVIEW_CACHE_BUST_MARKERS):
            targets.append(path)

    for old_path in targets:
        if not old_path.exists():
            continue
        new_name = f"{old_path.stem}-codexpatch{old_path.suffix}"
        new_path = old_path.with_name(new_name)
        if new_path.exists():
            new_path.unlink()
        old_name = old_path.name
        old_path.rename(new_path)
        for ref_path in webview_dir.rglob("*"):
            if not ref_path.is_file() or ref_path.suffix.lower() not in {".html", ".js", ".css"}:
                continue
            text = read(ref_path)
            if old_name not in text:
                continue
            write(ref_path, text.replace(old_name, new_name))
        log(f"Cache-busted webview asset: {old_name} -> {new_name}")
        changed = True
    return changed


def zip_dir(src: Path, dest: Path) -> None:
    if dest.exists():
        dest.unlink()
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in src.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(src).as_posix())


def parse_patches(raw: str) -> set[str]:
    if raw.strip().lower() == "all":
        return set(DEFAULT_PATCHES)
    patches = {part.strip() for part in raw.split(",") if part.strip()}
    unknown = patches - set(DEFAULT_PATCHES)
    if unknown:
        raise ValueError(f"Unknown patch name(s): {', '.join(sorted(unknown))}")
    return patches


def verify_extension_dir(extension_dir: Path, patches: set[str]) -> None:
    log("Checking patch markers")
    package_json = extension_dir / "package.json"
    extension_js = extension_dir / "out" / "extension.js"
    if not package_json.exists():
        raise RuntimeError(f"Missing package.json at {package_json}")
    if not extension_js.exists():
        raise RuntimeError(f"Missing extension.js at {extension_js}")

    package = json.loads(read(package_json))
    extension_text = read(extension_js)
    webview_text = "\n".join(read(path) for path in iter_webview_assets(extension_dir) or [])
    checks: list[tuple[str, bool]] = []
    webview_when = "(webviewId == 'chatgpt.sidebarView' || webviewId == 'chatgpt.sidebarSecondaryView') && codexTask == true"

    if PATCH_RENAME in patches:
        commands = package.get("contributes", {}).get("commands", [])
        checks += [
            ("rename command contribution", any(command.get("command") == COMMAND_ID for command in commands)),
            ("pin command contribution", any(command.get("command") == PIN_COMMAND_ID for command in commands)),
            ("unpin command contribution", any(command.get("command") == UNPIN_COMMAND_ID for command in commands)),
            ("star command contribution", any(command.get("command") == STAR_COMMAND_ID for command in commands)),
            ("unstar command contribution", any(command.get("command") == UNSTAR_COMMAND_ID for command in commands)),
            (
                "rename webview context menu",
                any(
                    item.get("command") == COMMAND_ID and item.get("when") == webview_when
                    for item in package.get("contributes", {}).get("menus", {}).get("webview/context", [])
                ),
            ),
            ("rename command registration", 'registerCommand("chatgpt.renameTask"' in extension_text),
            ("pin command registration", 'registerCommand("chatgpt.pinTask"' in extension_text),
            ("unpin command registration", 'registerCommand("chatgpt.unpinTask"' in extension_text),
            ("star command registration", 'registerCommand("chatgpt.starTask"' in extension_text),
            ("unstar command registration", 'registerCommand("chatgpt.unstarTask"' in extension_text),
            ("task context message handler", 'case"codex-task-context"' in extension_text),
            ("task context response handler", 'case"codex-task-context-response"' in extension_text),
            ("last task context fallback", "codexRememberTaskContext" in extension_text),
            ("command task context request", "codexWithTaskContext" in extension_text),
            ("live thread/name/set route", '"thread/name/set"' in extension_text),
            ("task row context data", "data-vscode-context" in webview_text and "codexTask:!0" in webview_text),
            ("task row direct context", "dataAttributes:codexRenameContext" in webview_text),
            ("task row pin/star state", "codexPinned" in webview_text and "codexStarred" in webview_text),
            ("task row context bridge", "__codexTaskContextBridgeV5" in webview_text),
            ("webview postMessage bridge", "__codexPostMessage" in webview_text),
            ("webview task context responder", "__codexTaskContextResponderV1" in webview_text),
        ]
    if PATCH_RECENT_MENU in patches:
        checks.append(("Search Chats menu item", "codex.profileDropdown.searchChats" in webview_text))
    if PATCH_WORKSPACE_GROUPS in patches:
        checks.append(("workspace grouping", "codexPatchCollapsedWorkspaces" in webview_text))
        checks.append(("pinned task sorting", "codexPatchIsPinned" in webview_text))
        checks.append(
            (
                "inline workspace grouping hook",
                bool(re.search(rf"codexPatchGroupInlineTasks\({JS_ID},e\)", webview_text)),
            )
        )
        checks.append(
            (
                "search workspace grouping hook",
                bool(re.search(rf"codexPatchGroupTasks\({JS_ID},{JS_ID},{JS_ID}\)", webview_text)),
            )
        )
    if PATCH_PIN_COMPOSER in patches:
        checks.append(
            (
                "pinned composer / no horizontal overflow",
                "sticky bottom-0" in webview_text and 'overflowX:"hidden"' in webview_text,
            )
        )

    missing = [label for label, ok in checks if not ok]
    if missing:
        raise RuntimeError("Verification failed: " + ", ".join(missing))
    log(f"Patch marker verification passed ({len(checks)} check(s))")

    node = shutil.which("node")
    if node:
        log(f"Running JS syntax check: {extension_js}")
        subprocess.check_call([node, "--check", str(extension_js)])
    else:
        log("Warning: node was not found; skipped JS syntax check.")


def patch_extension_dir(extension_dir: Path, patches: set[str]) -> bool:
    changed = False
    for patch in DEFAULT_PATCHES:
        if patch not in patches:
            continue
        log(f"Applying patch: {patch}")
        try:
            patch_changed = False
            if patch == PATCH_RENAME:
                patch_changed |= patch_package_json(extension_dir / "package.json")
                patch_changed |= patch_extension_js(extension_dir / "out" / "extension.js")
                patch_changed |= patch_rename_webview_assets(extension_dir)
            elif patch == PATCH_RECENT_MENU:
                patch_changed |= patch_recent_tasks_menu(extension_dir)
            elif patch == PATCH_WORKSPACE_GROUPS:
                patch_changed |= patch_workspace_groups(extension_dir)
            elif patch == PATCH_PIN_COMPOSER:
                patch_changed |= patch_pin_composer(extension_dir)
            changed |= patch_changed
            status = "updated" if patch_changed else "no file changes; verification will confirm"
            log(f"Patch complete: {patch} (status={status})")
        except Exception as exc:
            log(f"Patch failed: {patch}: {exc}")
            raise RuntimeError(f"{patch} patch failed: {exc}") from exc
    changed |= cache_bust_patched_webview_assets(extension_dir)
    return changed


def _try_install_vsix(vsix_path: Path) -> bool:
    """Install a patched VSIX via the `code` CLI. Returns True on success.

    Falls back gracefully if `code` is not on PATH — the user still has the
    .vsix on disk and can install it manually.
    """
    code_cli = shutil.which("code") or shutil.which("code.cmd") or shutil.which("code-insiders")
    if code_cli is None:
        log("`code` CLI not found on PATH; skipping auto-install.")
        log(f"Install manually: Extensions -> ... -> Install from VSIX -> {vsix_path}")
        return False
    log(f"Installing patched VSIX via {code_cli}")
    try:
        subprocess.check_call([code_cli, "--install-extension", str(vsix_path), "--force"])
    except subprocess.CalledProcessError as exc:
        log(f"Auto-install failed (exit {exc.returncode}). Install manually from: {vsix_path}")
        return False
    log("Patched extension installed.")
    return True


def main() -> int:
    global LOG_PATH
    parser = argparse.ArgumentParser(description="Patch the OpenAI Codex VS Code extension.")
    parser.add_argument(
        "target",
        nargs="?",
        default=DEFAULT_MARKETPLACE_ITEM,
        help="Marketplace URL/item, extracted VSIX root, extension directory, or .vsix file. Default: openai.chatgpt.",
    )
    parser.add_argument("--out", default="", help="Output VSIX path when target is a VSIX")
    parser.add_argument("--version", default="", help="Marketplace extension version to download. Default: latest.")
    parser.add_argument("--download-dir", default=".", help="Directory for downloaded Marketplace VSIX files.")
    parser.add_argument("--log", default="codex-vsix-patch.log", help="Patch log path. Default: codex-vsix-patch.log.")
    parser.add_argument("--skip-dependency-check", action="store_true", help="Skip scanning local Python imports.")
    parser.add_argument("--no-verify", action="store_true", help="Skip marker and JS syntax verification.")
    parser.add_argument(
        "--vsix-only",
        action="store_true",
        help="Skip the auto-install step. Just write the patched .vsix to disk; you install it yourself.",
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="Deprecated; auto-install is now the default. Pass --vsix-only to skip it.",
    )
    parser.add_argument(
        "--patches",
        default="all",
        help="Comma-separated patch names to apply: rename,recent-menu,workspace-groups,pin-composer. Default: all.",
    )
    parser.add_argument("-V", "--patcher-version", action="version", version=f"vscodexfix {__version__}")
    args = parser.parse_args()
    patches = parse_patches(args.patches)
    LOG_PATH = Path(args.log).expanduser().resolve()
    LOG_PATH.write_text("", encoding="utf-8")
    log(f"Starting Codex VSIX patcher v{__version__}")
    log(f"Target argument: {args.target}")
    log(f"Patches: {', '.join(patch for patch in DEFAULT_PATCHES if patch in patches)}")
    log(f"Log file: {LOG_PATH}")
    if args.skip_dependency_check:
        log("Python dependency check skipped")
    else:
        log("Checking Python dependencies for patcher")
        check_python_dependencies(Path(__file__).resolve())

    raw_target = args.target
    raw_path = Path(raw_target).expanduser()
    if raw_path.exists() or raw_path.suffix.lower() == ".vsix":
        target = raw_path.resolve()
        if not target.exists():
            raise RuntimeError(f"VSIX file not found: {target}")
        log(f"Using local target: {target}")
    else:
        marketplace_item = marketplace_item_from_target(raw_target)
        if marketplace_item is None:
            raise RuntimeError(f"Target does not exist and is not a Marketplace item or URL: {raw_target}")
        log(f"Resolving Marketplace item: {marketplace_item}")
        target = download_marketplace_vsix(
            marketplace_item,
            Path(args.download_dir).expanduser().resolve(),
            args.version or None,
        ).resolve()

    if target.suffix.lower() == ".vsix":
        out = Path(args.out).resolve() if args.out else target.with_name(target.stem + ".rename-patched.vsix")
        log(f"Output VSIX: {out}")
        with tempfile.TemporaryDirectory(prefix="codex-vsix-rename-") as temp:
            root = Path(temp) / "vsix"
            log(f"Extracting VSIX: {target}")
            with zipfile.ZipFile(target) as zf:
                zf.extractall(root)
            extension_dir = root / "extension"
            log(f"Patching extracted extension: {extension_dir}")
            changed = patch_extension_dir(extension_dir, patches)
            if not args.no_verify:
                log("Verifying patched extension")
                verify_extension_dir(extension_dir, patches)
            else:
                log("Verification skipped by --no-verify")
            log("Writing patched VSIX")
            zip_dir(root, out)
        log(f"Patched VSIX written: {out}")
        log(f"Overall status: {'updated files' if changed else 'already patched'}")
        if not args.vsix_only:
            if _try_install_vsix(out):
                log("Reload the VS Code window (Developer: Reload Window) to pick up the new bundle.")
        else:
            log("--vsix-only set; skipping auto-install. Install via Extensions -> ... -> Install from VSIX.")
        log("Patch run complete")
        return 0

    extension_dir = target / "extension" if (target / "extension" / "package.json").exists() else target
    log(f"Patching extension directory: {extension_dir}")
    changed = patch_extension_dir(extension_dir, patches)
    if not args.no_verify:
        log("Verifying patched extension")
        verify_extension_dir(extension_dir, patches)
    else:
        log("Verification skipped by --no-verify")
    log(f"Patched extension directory: {extension_dir}")
    log(f"Overall status: {'updated files' if changed else 'already patched'}")
    log("Patch run complete")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        log(f"Patch run failed: {exc}")
        raise
