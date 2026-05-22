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

__version__ = "0.1.0"

COMMAND_ID = "chatgpt.renameTask"
PIN_COMMAND_ID = "chatgpt.pinTask"
STAR_COMMAND_ID = "chatgpt.starTask"
DEFAULT_MARKETPLACE_ITEM = "openai.chatgpt"
MARKETPLACE_QUERY_URL = "https://marketplace.visualstudio.com/_apis/public/gallery/extensionquery?api-version=7.2-preview.1"
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
            "Missing Python package(s): "
            f"{packages}\nInstall them with: python -m pip install {packages}"
        )
    log(f"Python dependency check passed ({len(py_files)} file(s) scanned)")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


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
        (STAR_COMMAND_ID, "Star Task", "$(star-full)"),
    ):
        if not any(command.get("command") == command_id for command in commands):
            commands.append({"command": command_id, "title": title, "category": "Codex", "icon": icon})
            changed = True

    menus = contributes.setdefault("menus", {})
    webview_context_menu = menus.setdefault("webview/context", [])
    webview_when = "(webviewId == 'chatgpt.sidebarView' || webviewId == 'chatgpt.sidebarSecondaryView') && webviewSection == 'codex-task'"
    for command_id, group in ((COMMAND_ID, "navigation@1"), (PIN_COMMAND_ID, "navigation@2"), (STAR_COMMAND_ID, "navigation@3")):
        webview_item = next((item for item in webview_context_menu if item.get("command") == command_id), None)
        if webview_item is None:
            webview_context_menu.insert(0, {"command": command_id, "group": group, "when": webview_when})
            changed = True
        else:
            if webview_item.get("when") != webview_when:
                webview_item["when"] = webview_when
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
function codexRenameThreadId(t){if(t&&typeof t.id=="string")return t.id;if(t&&typeof t.codexThreadId=="string")return t.codexThreadId;if(t&&typeof t["data-codex-thread-id"]=="string")return t["data-codex-thread-id"];if(t&&t.resource){let e=typeof t.resource.toString=="function"?t.resource.toString():String(t.resource),r=/\/local\/([^/?#]+)/.exec(e);if(r)return decodeURIComponent(r[1])}return null}
function codexRenameThreadTitle(t){return typeof t?.label=="string"?t.label:typeof t?.codexThreadTitle=="string"?t.codexThreadTitle:typeof t?.["data-codex-thread-title"]=="string"?t["data-codex-thread-title"]:""}
async function codexSetTaskTitle(t,e,r,n,o){let i=codexRenameThreadId(t);if(!i){e.window.showErrorMessage("Right-click a Codex task row first.");return!1}if(typeof r!="function")throw new Error("Codex app-server rename route is unavailable");await r(i,n),o&&e.window.showInformationMessage(o);return!0}
async function codexRenameTask(t,e,r){let n=codexRenameThreadId(t);if(!n){e.window.showErrorMessage("Right-click a Codex task row to rename it.");return!1}let o=codexRenameThreadTitle(t),i=await e.window.showInputBox({title:"Rename Codex Task",prompt:"Enter a new task name",value:o,ignoreFocusOut:!0,validateInput:s=>s.trim().length===0?"Task name cannot be empty":void 0});if(i==null)return!1;let a=i.replace(/\s+/g," ").trim();return!a||a===o?!1:codexSetTaskTitle(t,e,r,a,"Codex task renamed.")}
async function codexStarTask(t,e,r){let n=codexRenameThreadTitle(t).trim();return n.startsWith("⭐ ")?!1:codexSetTaskTitle(t,e,r,`⭐ ${n}`,"Codex task starred.")}
async function codexPinTask(t,e,r){let n=codexRenameThreadTitle(t).trim(),o=n.startsWith("⭐ "),i=o?n.slice(2).trimStart():n,a;i.startsWith("📌 ")?a=(o?"⭐ ":"")+i.slice(2).trimStart():a=(o?"⭐ 📌 ":"📌 ")+i;return a===n?!1:codexSetTaskTitle(t,e,r,a,"Codex task pin toggled.")}
"""

OLD_RENAME_HELPER_START = 'var codexRenameFs=require("fs"),codexRenameCp=require("child_process"),codexRenameOs=require("os"),codexRenamePath=require("path");'


def patch_extension_js(extension_js: Path) -> bool:
    text = read(extension_js)
    changed = False
    anchor = 'var Nee="codex.chatSessionProvider",QFe="codex.chatSessionObserver",f0="Codex Agent",Dee=80;'

    if OLD_RENAME_HELPER_START in text:
        start = text.index(OLD_RENAME_HELPER_START)
        end = text.find(anchor, start)
        if end == -1:
            raise RuntimeError("Could not find chat session provider anchor after old rename helper")
        text = text[:start] + RENAME_HELPER_JS + text[end:]
        changed = True
    elif "function codexStarTask(" not in text and "function codexRenameThreadId(" in text:
        start = text.index("function codexRenameThreadId(")
        end = text.find(anchor, start)
        if end == -1:
            raise RuntimeError("Could not find chat session provider anchor after rename helper")
        text = text[:start] + RENAME_HELPER_JS + text[end:]
        changed = True
    elif "function codexRenameTask(" not in text:
        if anchor not in text:
            raise RuntimeError("Could not find chat session provider anchor in extension.js")
        text = text.replace(anchor, RENAME_HELPER_JS + anchor, 1)
        changed = True

    old_method = "async trackTabIfNeeded(e){let r=e.input;if(!(r instanceof ll.TabInputCustom))return;"
    old_rename_method = "async renameChatSessionItem(e){let r=await codexRenameTask(e,ll);return r&&(this.pendingConversations.delete(codexRenameThreadId(e)),this.onDidChangeChatSessionItemsEmitter.fire()),r}"
    current_rename_method = "async renameChatSessionItem(e){let r=await codexRenameTask(e,ll,(n,o)=>this.conversationLoader.requestThreadNameSet(n,o));return r&&(this.pendingConversations.delete(codexRenameThreadId(e)),this.onDidChangeChatSessionItemsEmitter.fire()),r}"
    task_action_methods = current_rename_method + "async pinChatSessionItem(e){let r=await codexPinTask(e,ll,(n,o)=>this.conversationLoader.requestThreadNameSet(n,o));return r&&(this.pendingConversations.delete(codexRenameThreadId(e)),this.onDidChangeChatSessionItemsEmitter.fire()),r}async starChatSessionItem(e){let r=await codexStarTask(e,ll,(n,o)=>this.conversationLoader.requestThreadNameSet(n,o));return r&&(this.pendingConversations.delete(codexRenameThreadId(e)),this.onDidChangeChatSessionItemsEmitter.fire()),r}"
    new_method = f"{task_action_methods}async trackTabIfNeeded(e){{let r=e.input;if(!(r instanceof ll.TabInputCustom))return;"
    if "async starChatSessionItem(e)" not in text and current_rename_method in text:
        text = text.replace(current_rename_method, task_action_methods, 1)
        changed = True
    elif old_rename_method in text:
        text = text.replace(old_rename_method, task_action_methods, 1)
        changed = True
    elif "async renameChatSessionItem(e)" not in text:
        if old_method not in text:
            raise RuntimeError("Could not find R_ class method anchor in extension.js")
        text = text.replace(old_method, new_method, 1)
        changed = True

    old_thread_list_method = 'requestThreadList(e){let r=String(this.nextRequestId++),n=new Promise((o,i)=>{this.requestToCallback.set(r,s=>{if(s.error){i(new Error(s.error.message));return}if(s.result==null){i(new Error("No result in response"));return}o(s.result)})});return this.codexAppServer.sendRequest(Nee,r,"thread/list",{limit:50,cursor:null,sortKey:"created_at",modelProviders:e?[ib]:null,archived:!1,sourceKinds:sm}),n}};function Lee'
    new_thread_list_method = 'requestThreadList(e){let r=String(this.nextRequestId++),n=new Promise((o,i)=>{this.requestToCallback.set(r,s=>{if(s.error){i(new Error(s.error.message));return}if(s.result==null){i(new Error("No result in response"));return}o(s.result)})});return this.codexAppServer.sendRequest(Nee,r,"thread/list",{limit:50,cursor:null,sortKey:"created_at",modelProviders:e?[ib]:null,archived:!1,sourceKinds:sm}),n}requestThreadNameSet(e,r){let n=String(this.nextRequestId++),o=new Promise((i,s)=>{this.requestToCallback.set(n,a=>{if(a.error){s(new Error(a.error.message));return}i(a.result)})});return this.codexAppServer.sendRequest(Nee,n,"thread/name/set",{threadId:e,name:r}),o}};function Lee'
    if "requestThreadNameSet(e,r)" not in text:
        if old_thread_list_method not in text:
            raise RuntimeError("Could not find h0 requestThreadList anchor in extension.js")
        text = text.replace(old_thread_list_method, new_thread_list_method, 1)
        changed = True

    old_register = "e.push(at.commands.registerCommand(o6e,async()=>{await Qo(),tt.triggerNewChatViaWebview()})),zr(Ht.COMMENT_CODELENS_ENABLED,!0)"
    old_rename_register_picker_reload = 'e.push(at.commands.registerCommand("chatgpt.renameTask",async J=>{try{let xe=!codexRenameThreadId(J);if(xe){let Ye=await g?.provideChatSessionItems({isCancellationRequested:!1,onCancellationRequested:()=>({dispose(){}})}),pt=(Ye??[]).map(zt=>({label:String(zt.label||"Untitled task"),description:String(zt.id||""),item:zt})),ir=await at.window.showQuickPick(pt,{placeHolder:"Select a Codex task to rename",matchOnDescription:!0});if(!ir)return;J=ir.item}let nr=await g?.renameChatSessionItem(J);nr&&xe&&at.commands.executeCommand("workbench.action.webview.reloadWebviewAction").then(()=>{},()=>{})}catch(xe){at.window.showErrorMessage(`Failed to rename Codex task: ${xe instanceof Error?xe.message:String(xe)}`)}}))'
    old_rename_register_picker = 'e.push(at.commands.registerCommand("chatgpt.renameTask",async J=>{try{if(!codexRenameThreadId(J)){let Ye=await g?.provideChatSessionItems({isCancellationRequested:!1,onCancellationRequested:()=>({dispose(){}})}),pt=(Ye??[]).map(zt=>({label:String(zt.label||"Untitled task"),description:String(zt.id||""),item:zt})),ir=await at.window.showQuickPick(pt,{placeHolder:"Select a Codex task to rename",matchOnDescription:!0});if(!ir)return;J=ir.item}await g?.renameChatSessionItem(J)}catch(xe){at.window.showErrorMessage(`Failed to rename Codex task: ${xe instanceof Error?xe.message:String(xe)}`)}}))'
    new_rename_register = 'e.push(at.commands.registerCommand("chatgpt.renameTask",async J=>{try{await g?.renameChatSessionItem(J)}catch(xe){at.window.showErrorMessage(`Failed to rename Codex task: ${xe instanceof Error?xe.message:String(xe)}`)}}))'
    new_pin_register = 'e.push(at.commands.registerCommand("chatgpt.pinTask",async J=>{try{await g?.pinChatSessionItem(J)}catch(xe){at.window.showErrorMessage(`Failed to pin Codex task: ${xe instanceof Error?xe.message:String(xe)}`)}}))'
    new_star_register = 'e.push(at.commands.registerCommand("chatgpt.starTask",async J=>{try{await g?.starChatSessionItem(J)}catch(xe){at.window.showErrorMessage(`Failed to star Codex task: ${xe instanceof Error?xe.message:String(xe)}`)}}))'
    task_action_registers = f"{new_rename_register},{new_pin_register},{new_star_register}"
    new_register = f"e.push(at.commands.registerCommand(o6e,async()=>{{await Qo(),tt.triggerNewChatViaWebview()}})),{task_action_registers},zr(Ht.COMMENT_CODELENS_ENABLED,!0)"
    if 'registerCommand("chatgpt.pinTask"' not in text and new_rename_register in text:
        text = text.replace(new_rename_register, task_action_registers, 1)
        changed = True
    elif old_rename_register_picker_reload in text:
        text = text.replace(old_rename_register_picker_reload, task_action_registers, 1)
        changed = True
    elif old_rename_register_picker in text:
        text = text.replace(old_rename_register_picker, task_action_registers, 1)
        changed = True
    elif 'registerCommand("chatgpt.renameTask"' not in text:
        if old_register not in text:
            raise RuntimeError("Could not find activation command-registration anchor in extension.js")
        text = text.replace(old_register, new_register, 1)
        changed = True

    if changed:
        write(extension_js, text)
    return changed


def iter_webview_assets(extension_dir: Path):
    assets_dir = extension_dir / "webview" / "assets"
    if not assets_dir.exists():
        return
    yield from assets_dir.glob("*.js")


def find_asset(extension_dir: Path, token: str) -> Path:
    for path in iter_webview_assets(extension_dir) or []:
        if token in read(path):
            return path
    raise RuntimeError(f"Could not find webview asset containing {token!r}")


def find_asset_all(extension_dir: Path, tokens: tuple[str, ...]) -> Path:
    for path in iter_webview_assets(extension_dir) or []:
        text = read(path)
        if all(token in text for token in tokens):
            return path
    raise RuntimeError(f"Could not find webview asset containing all tokens: {tokens!r}")


def patch_rename_webview_assets(extension_dir: Path) -> bool:
    assets_dir = extension_dir / "webview" / "assets"
    if not assets_dir.exists():
        return False

    old = "sidebarThreadRow:({active:e,hostId:t,id:n,kind:r,pinned:i,title:a})=>({[U.sidebarThreadActive]:String(e),[U.sidebarThreadHostId]:t??``,[U.sidebarThreadId]:n,[U.sidebarThreadKind]:r,[U.sidebarThreadPinned]:String(i),[U.sidebarThreadRow]:``,[U.sidebarThreadTitle]:a})"
    new = 'sidebarThreadRow:({active:e,hostId:t,id:n,kind:r,pinned:i,title:a})=>({[U.sidebarThreadActive]:String(e),[U.sidebarThreadHostId]:t??``,[U.sidebarThreadId]:n,[U.sidebarThreadKind]:r,[U.sidebarThreadPinned]:String(i),[U.sidebarThreadRow]:``,[U.sidebarThreadTitle]:a,"data-vscode-context":JSON.stringify({webviewSection:`codex-task`,codexThreadId:n,codexThreadTitle:a,preventDefaultContextMenuItems:!0})})'

    changed = False
    saw_patched_helper = False
    for path in assets_dir.glob("*.js"):
        text = read(path)
        if new in text:
            saw_patched_helper = True
            continue
        if old in text:
            write(path, text.replace(old, new, 1))
            changed = True

    if not changed and not saw_patched_helper:
        raise RuntimeError("Could not find sidebarThreadRow data-attributes helper in webview assets")

    local_thread = find_asset_all(
        extension_dir,
        ("function Dl(e){", "function pl(e){", "function Ql(e){", "codex.localTaskRow.archiveTask"),
    )
    text = read(local_thread)

    local_context = 'let Qe=We,codexRenameContext={...R,"data-vscode-context":JSON.stringify({webviewSection:`codex-task`,codexThreadId:n,codexThreadTitle:typeof w=="string"?w:typeof ge=="string"?ge:"",preventDefaultContextMenuItems:!0})},$e;'
    if "codexThreadId:n,codexThreadTitle" not in text:
        local_anchor = "let Qe=We,$e;"
        if local_anchor not in text:
            raise RuntimeError("Could not find local task row rename context anchor")
        text = text.replace(local_anchor, local_context, 1)
        text = text.replace("t[63]!==R||", "t[63]!==codexRenameContext||", 1)
        text = text.replace(
            "dataAttributes:R,archiveAriaLabel", "dataAttributes:codexRenameContext,archiveAriaLabel", 1
        )
        text = text.replace("t[63]=R,", "t[63]=codexRenameContext,", 1)
        changed = True

    cloud_context = 'let codexRenameContext={...w,"data-vscode-context":JSON.stringify({webviewSection:`codex-task`,codexThreadId:P,codexThreadTitle:typeof I=="string"?I:"",preventDefaultContextMenuItems:!0})};let be;'
    if "codexThreadId:P,codexThreadTitle" not in text:
        cloud_anchor = "let be;t[50]!==w||"
        if cloud_anchor not in text:
            raise RuntimeError("Could not find cloud task row rename context anchor")
        text = text.replace(cloud_anchor, cloud_context + "t[50]!==codexRenameContext||", 1)
        text = text.replace(
            "renderActions:d,dataAttributes:w})", "renderActions:d,dataAttributes:codexRenameContext})", 1
        )
        text = text.replace("t[50]=w,", "t[50]=codexRenameContext,", 1)
        changed = True

    if changed:
        write(local_thread, text)
    return changed


def patch_recent_tasks_menu(extension_dir: Path) -> bool:
    changed = False
    local_thread = find_asset_all(
        extension_dir,
        ("function Ql(e){", "function xu(e){", "codex.profileDropdown.keyboardShortcuts"),
    )
    text = read(local_thread)

    old_limit = "c=(0,Zl.default)([...e,...n],tu).slice(0,Math.max(3,e.length))"
    new_limit = "c=(0,Zl.default)([...e,...n],tu)"
    if old_limit in text:
        text = text.replace(old_limit, new_limit, 1)
        changed = True

    old_view_all = "let f;t[17]!==n.length||t[18]!==u?(f=u&&(0,Q.jsx)(`div`,{className:`flex w-full cursor-interaction items-center gap-0 rounded-md px-[var(--padding-row-x)] py-1 text-sm opacity-40 hover:opacity-80`,onClick:$l,children:(0,Q.jsx)(Y,{id:`header.recentTasks.seeAll`,defaultMessage:`View all ({total})`,description:`See all recent tasks link with total count`,values:{total:n.length}})}),t[17]=n.length,t[18]=u,t[19]=f):f=t[19];let p;"
    new_view_all = "let f=null;t[17]=n.length,t[18]=u,t[19]=f;let p;"
    if old_view_all in text:
        text = text.replace(old_view_all, new_view_all, 1)
        changed = True

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

    old_dropdown_children = "children:[Te,De,ke,Me,Ne,Ie,Le,Re]"
    new_dropdown_children = "children:[Te,De,ke,Me,(0,Q.jsx)(Da,{extension:!0,children:(0,Q.jsx)(Eo,{LeftIcon:So,onClick:()=>{o(!1),window.setTimeout(()=>window.dispatchEvent(new CustomEvent(`open-recent-tasks-menu`)),250)},children:(0,Q.jsx)(Y,{id:`codex.profileDropdown.searchChats`,defaultMessage:`Search Chats`,description:`Menu item to search recent Codex chats`})})}),Ne,Ie,Le,Re]"
    if "codex.profileDropdown.searchChats" not in text:
        if old_dropdown_children not in text:
            raise RuntimeError("Could not find profile dropdown children anchor")
        text = text.replace(old_dropdown_children, new_dropdown_children, 1)
        changed = True
    else:
        old_search_click = "onClick:()=>{o(!1),window.dispatchEvent(new CustomEvent(`open-recent-tasks-menu`))},children:(0,Q.jsx)(Y,{id:`codex.profileDropdown.searchChats`"
        new_search_click = "onClick:()=>{o(!1),window.setTimeout(()=>window.dispatchEvent(new CustomEvent(`open-recent-tasks-menu`)),0)},children:(0,Q.jsx)(Y,{id:`codex.profileDropdown.searchChats`"
        if old_search_click in text:
            text = text.replace(old_search_click, new_search_click, 1)
            changed = True
        old_search_click_zero = "onClick:()=>{o(!1),window.setTimeout(()=>window.dispatchEvent(new CustomEvent(`open-recent-tasks-menu`)),0)},children:(0,Q.jsx)(Y,{id:`codex.profileDropdown.searchChats`"
        new_search_click_delay = "onClick:()=>{o(!1),window.setTimeout(()=>window.dispatchEvent(new CustomEvent(`open-recent-tasks-menu`)),250)},children:(0,Q.jsx)(Y,{id:`codex.profileDropdown.searchChats`"
        if old_search_click_zero in text:
            text = text.replace(old_search_click_zero, new_search_click_delay, 1)
            changed = True
        old_search_click_short_delay = "onClick:()=>{o(!1),window.setTimeout(()=>window.dispatchEvent(new CustomEvent(`open-recent-tasks-menu`)),120)},children:(0,Q.jsx)(Y,{id:`codex.profileDropdown.searchChats`"
        if old_search_click_short_delay in text:
            text = text.replace(old_search_click_short_delay, new_search_click_delay, 1)
            changed = True

    if changed:
        write(local_thread, text)

    apps_asset = find_asset(extension_dir, "var Wx=new Map")
    apps_text = read(apps_asset)
    old_manage_tasks = "[`manageTasks`,()=>{I.dispatchHostMessage({type:`navigate-to-route`,path:`/automations`,state:{automationMode:`create`}})}]"
    new_manage_tasks = "[`manageTasks`,()=>{window.setTimeout(()=>window.dispatchEvent(new CustomEvent(`open-recent-tasks-menu`)),250)}]"
    if old_manage_tasks in apps_text:
        apps_text = apps_text.replace(old_manage_tasks, new_manage_tasks, 1)
        write(apps_asset, apps_text)
        changed = True
    else:
        old_manage_tasks_immediate = (
            "[`manageTasks`,()=>{window.dispatchEvent(new CustomEvent(`open-recent-tasks-menu`))}]"
        )
        if old_manage_tasks_immediate in apps_text:
            apps_text = apps_text.replace(old_manage_tasks_immediate, new_manage_tasks, 1)
            write(apps_asset, apps_text)
            changed = True
        else:
            old_manage_tasks_zero = "[`manageTasks`,()=>{window.setTimeout(()=>window.dispatchEvent(new CustomEvent(`open-recent-tasks-menu`)),0)}]"
            if old_manage_tasks_zero in apps_text:
                apps_text = apps_text.replace(old_manage_tasks_zero, new_manage_tasks, 1)
                write(apps_asset, apps_text)
                changed = True
            else:
                old_manage_tasks_short_delay = "[`manageTasks`,()=>{window.setTimeout(()=>window.dispatchEvent(new CustomEvent(`open-recent-tasks-menu`)),120)}]"
                if old_manage_tasks_short_delay in apps_text:
                    apps_text = apps_text.replace(old_manage_tasks_short_delay, new_manage_tasks, 1)
                    write(apps_asset, apps_text)
                    changed = True

    try:
        shortcuts_asset = find_asset_all(
            extension_dir,
            ('codex.command.manageTasks":{id:`codex.command.manageTasks`', "defaultMessage:`Manage automations`"),
        )
    except RuntimeError:
        shortcuts_asset = None
    if shortcuts_asset is not None:
        shortcuts_text = read(shortcuts_asset)
        old_manage_message = 'codex.command.manageTasks":{id:`codex.command.manageTasks`,defaultMessage:`Manage automations`,description:`Command menu item to manage automations`}'
        new_manage_message = 'codex.command.manageTasks":{id:`codex.command.manageTasks`,defaultMessage:`Search Chats`,description:`Command menu item to search recent Codex chats`}'
        if old_manage_message in shortcuts_text:
            shortcuts_text = shortcuts_text.replace(old_manage_message, new_manage_message, 1)
            write(shortcuts_asset, shortcuts_text)
            changed = True

    try:
        descriptions_asset = find_asset_all(
            extension_dir,
            (
                'codex.commandDescription.manageTasks":{id:`codex.commandDescription.manageTasks`',
                "Create or manage automations from the current page",
            ),
        )
    except RuntimeError:
        descriptions_asset = None
    if descriptions_asset is not None:
        shortcuts_text = read(descriptions_asset)
        old_manage_description = 'codex.commandDescription.manageTasks":{id:`codex.commandDescription.manageTasks`,defaultMessage:`Create or manage automations from the current page`,description:`Description for the Manage automations command`}'
        new_manage_description = 'codex.commandDescription.manageTasks":{id:`codex.commandDescription.manageTasks`,defaultMessage:`Search recent Codex chats`,description:`Description for the Search Chats command`}'
        if old_manage_description in shortcuts_text:
            shortcuts_text = shortcuts_text.replace(old_manage_description, new_manage_description, 1)
            write(descriptions_asset, shortcuts_text)
            changed = True

    for locale_asset in iter_webview_assets(extension_dir) or []:
        locale_text = read(locale_asset)
        updated = re.sub(r'("codex\.command\.manageTasks":)`[^`]*`', r"\1`Search Chats`", locale_text)
        if updated != locale_text:
            write(locale_asset, updated)
            changed = True

    return changed


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


def patch_workspace_groups(extension_dir: Path) -> bool:
    local_thread = find_asset_all(
        extension_dir,
        ("function xu(e){", "codex.recentTasksMenu.errorCloud.inline", "function Su(e){"),
    )
    text = read(local_thread)
    changed = False

    helper_start = text.find("function codexPatchWorkspacePath(")
    if helper_start != -1:
        helper_end = text.find("function xu(e){", helper_start)
        if helper_end == -1:
            raise RuntimeError("Could not find end of workspace grouping helper")
        current_helper = text[helper_start:helper_end]
        if current_helper != WORKSPACE_GROUP_HELPER_JS:
            text = text[:helper_start] + WORKSPACE_GROUP_HELPER_JS + text[helper_end:]
            changed = True
    else:
        anchor = "function xu(e){"
        if anchor not in text:
            raise RuntimeError("Could not find recent tasks menu anchor")
        text = text.replace(anchor, WORKSPACE_GROUP_HELPER_JS + anchor, 1)
        changed = True

    old_recent_map = "D.map(e=>(0,Q.jsx)(wu,{item:e,isActive:e.kind===`local`&&f===e.conversation.id,onClose:i},e.key))"
    new_recent_map = "codexPatchGroupTasks(D,f,i)"
    if old_recent_map in text:
        text = text.replace(old_recent_map, new_recent_map, 1)
        changed = True

    old_inline_map = "d=l.map(e),t[8]=o,"
    new_inline_map = "d=codexPatchGroupInlineTasks(l,e),t[8]=o,"
    if old_inline_map in text:
        text = text.replace(old_inline_map, new_inline_map, 1)
        changed = True

    old_inline_group_class = "className:`group/inline -mx-[var(--padding-row-x)] flex flex-col gap-px rounded-xl pb-1 transition-colors`"
    new_inline_group_class = "className:`group/inline -mx-[var(--padding-row-x)] max-w-full overflow-x-hidden flex flex-col gap-px rounded-xl pb-1 transition-colors`"
    if old_inline_group_class in text:
        text = text.replace(old_inline_group_class, new_inline_group_class, 1)
        changed = True

    if changed:
        write(local_thread, text)
    return changed


def patch_pin_composer(extension_dir: Path) -> bool:
    changed = False

    local_thread = find_asset_all(
        extension_dir,
        ("function Ql(e){", "(0,Q.jsx)(Ql,{tasksQuery:f,mergedTasks:m})", "function ju(e)"),
    )
    text = read(local_thread)
    new_inline_tasks = 'S=c&&(0,Q.jsx)(`div`,{className:`overscroll-contain pr-1`,style:{maxHeight:"calc(100vh - 320px)",overflowY:"auto",overflowX:"hidden",scrollbarGutter:"stable"},onWheel:e=>{e.currentTarget.scrollTop+=e.deltaY,e.stopPropagation()},children:(0,Q.jsx)(Ql,{tasksQuery:f,mergedTasks:m})})'
    old_inline_task_variants = [
        "S=c&&(0,Q.jsx)(`div`,{children:(0,Q.jsx)(Ql,{tasksQuery:f,mergedTasks:m})})",
        "S=c&&(0,Q.jsx)(`div`,{className:`max-h-[calc(100vh-220px)] overflow-y-auto overscroll-contain pr-1`,children:(0,Q.jsx)(Ql,{tasksQuery:f,mergedTasks:m})})",
        'S=c&&(0,Q.jsx)(`div`,{className:`overscroll-contain pr-1`,style:{maxHeight:"calc(100vh - 320px)",overflowY:"auto",scrollbarGutter:"stable"},onWheel:e=>{e.currentTarget.scrollTop+=e.deltaY,e.stopPropagation()},children:(0,Q.jsx)(Ql,{tasksQuery:f,mergedTasks:m})})',
    ]
    for old_inline_tasks in old_inline_task_variants:
        if old_inline_tasks in text:
            text = text.replace(old_inline_tasks, new_inline_tasks, 1)
            write(local_thread, text)
            changed = True
            break
    if new_inline_tasks not in text and "onWheel:e=>{e.currentTarget.scrollTop+=e.deltaY" not in text:
        raise RuntimeError("Could not find inline task list container anchor")
    if changed:
        write(local_thread, text)

    new_thread_page = find_asset_all(
        extension_dir,
        ("NewThreadPanelPage", "thread-footer-overlap", "homePage.mainContent"),
    )
    text = read(new_thread_page)
    old_footer_class = "z-10 -mt-[var(--thread-footer-overlap)] flex flex-col gap-2 pb-2"
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
    elif old_footer_class in text and new_footer_class not in text:
        text = text.replace(old_footer_class, new_footer_class, 1)
        write(new_thread_page, text)
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

    if PATCH_RENAME in patches:
        commands = package.get("contributes", {}).get("commands", [])
        checks += [
            ("rename command contribution", any(command.get("command") == COMMAND_ID for command in commands)),
            ("pin command contribution", any(command.get("command") == PIN_COMMAND_ID for command in commands)),
            ("star command contribution", any(command.get("command") == STAR_COMMAND_ID for command in commands)),
            ("rename command registration", 'registerCommand("chatgpt.renameTask"' in extension_text),
            ("pin command registration", 'registerCommand("chatgpt.pinTask"' in extension_text),
            ("star command registration", 'registerCommand("chatgpt.starTask"' in extension_text),
            ("live thread/name/set route", '"thread/name/set"' in extension_text),
            ("task row context data", "data-vscode-context" in webview_text and "codexThreadId:" in webview_text),
        ]
    if PATCH_RECENT_MENU in patches:
        checks.append(("Search Chats menu item", "codex.profileDropdown.searchChats" in webview_text))
    if PATCH_WORKSPACE_GROUPS in patches:
        checks.append(("workspace grouping", "codexPatchCollapsedWorkspaces" in webview_text))
        checks.append(("pinned task sorting", "codexPatchIsPinned" in webview_text))
        checks.append(("inline workspace grouping hook", "codexPatchGroupInlineTasks(l,e)" in webview_text))
        checks.append(("search workspace grouping hook", "codexPatchGroupTasks(D,f,i)" in webview_text))
    if PATCH_PIN_COMPOSER in patches:
        checks.append(("pinned composer / no horizontal overflow", "sticky bottom-0" in webview_text and 'overflowX:"hidden"' in webview_text))

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
    return changed


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
    parser.add_argument("--install", action="store_true", help="Install the patched VSIX with code --install-extension")
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
        if args.install:
            log("Installing patched VSIX with VS Code")
            subprocess.check_call(["code", "--install-extension", str(out), "--force"])
            log("Install complete")
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
