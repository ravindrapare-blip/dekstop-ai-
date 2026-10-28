"""Turns an authorized tool call into a real action on the Mac.

Order of work, which keeps the safety layer in charge:
    prepare()  read-only: find the real app or file the words refer to (ask the user if unsure)
    -- the safety layer then judges the real target, not the words --
    execute()  only called for a call the safety layer authorized

Nothing here calls the safety layer or decides what is allowed.
"""
import logging
import os
import time

from automation.apps import AppCatalog
from automation.choices import ChoiceStore, Chosen
from automation.controller import AppController
from automation.files import FileFinder, Lookup
from automation.results import ActionResult, Prepared

log = logging.getLogger("assistant.actions")

RESOLVED_TOOLS = ("open_app", "focus_app", "close_app", "open_file", "open_folder")
APP_TOOLS = ("open_app", "focus_app", "close_app")


def _listing(prefix: str, names: list[str], total: int) -> str:
    shown = ", ".join(names[:3])
    more = f" (+{total - 3} more)" if total > 3 else ""
    return f"{prefix} {shown}{more}"


class ActionService:
    def __init__(self, apps: AppCatalog, files: FileFinder, controller: AppController, *,
                 clock=time.monotonic, choices: ChoiceStore | None = None):
        self.apps = apps
        self.files = files
        self.controller = controller
        self.choices = choices if choices is not None else ChoiceStore(clock=clock)    # not `or`: an empty store is falsy

    # -- step 1: find what the words refer to (read-only) -----------------------------------------------------

    def prepare(self, tool: str, args: dict, requires_confirmation: bool = False,
                dry_run: bool | None = None) -> Prepared:
        if tool in APP_TOOLS:
            return self._prepare_app(tool, str(args.get("app_name", "")), requires_confirmation, dry_run)
        if tool in ("open_file", "open_folder"):
            return self._prepare_path(tool, str(args.get("path_or_name", "")), requires_confirmation, dry_run)
        return Prepared("ready", dict(args))

    def _prepare_app(self, tool: str, name: str, confirm: bool, dry_run: bool | None) -> Prepared:
        found = self.apps.find(name)
        if found.kind == "none":
            return Prepared("failed", result=ActionResult("failed", f"I couldn't find an app called “{name}”."))
        if found.kind == "one":
            return Prepared("ready", {"app_name": found.items[0].name})
        options = {str(i): app.name for i, app in enumerate(found.items, 1)}
        shown = [{"id": str(i), "label": app.name, "detail": ""} for i, app in enumerate(found.items, 1)]
        return self._ask(tool, "app_name", options, shown, confirm, dry_run, f"Which app did you mean by “{name}”?")

    def _prepare_path(self, tool: str, text: str, confirm: bool, dry_run: bool | None) -> Prepared:
        kind = "file" if tool == "open_file" else "folder"
        lookup = self.files.locate(text, kind)
        if lookup.kind in ("exact", "one"):
            return Prepared("ready", {"path_or_name": lookup.matches[0].path})
        if lookup.kind == "none":
            return Prepared("failed", result=ActionResult("failed", self._not_found(text, kind, lookup)))
        options = {str(i): m.path for i, m in enumerate(lookup.matches, 1)}
        shown = [{"id": str(i), "label": m.name,
                  "detail": self.files.display(os.path.dirname(m.path))}
                 for i, m in enumerate(lookup.matches, 1)]
        noun = "files" if kind == "file" else "folders"
        return self._ask(tool, "path_or_name", options, shown, confirm, dry_run,
                         f"I found {len(shown)} {noun} that could match “{text}”. Which one?")

    def _not_found(self, text: str, kind: str, lookup: Lookup) -> str:
        if lookup.reason == "missing_path":
            return f"There is nothing at {text.strip()}."
        if lookup.reason == "not_folder":
            return f"{text.strip()} is a file, not a folder. Ask me to open the file instead."
        names = list(dict.fromkeys(lookup.searched))
        where = (", ".join(names[:3]) + " and a few other folders") if len(names) > 3 else (_join_names(names) or "your usual folders")
        early = " I stopped early to keep things fast." if lookup.truncated else ""
        return f"I couldn't find a {kind} matching “{text}” in {where}.{early}"

    def _ask(self, tool: str, arg_name: str, targets: dict, shown: list, confirm: bool,
             dry_run: bool | None, prompt: str) -> Prepared:
        return self.choices.ask(tool, {i: {arg_name: v} for i, v in targets.items()}, shown, confirm, dry_run, prompt)

    def choose(self, choice_id: str, option_id: str | None) -> Chosen | ActionResult:
        """Resolve the user's pick. Passing no option cancels. The pick is still judged by the safety layer."""
        return self.choices.resolve(choice_id, option_id)

    def status(self) -> dict:
        """For diagnostics: what the assistant can see and reach."""
        hs = self.controller.hammerspoon
        return {
            "hammerspoon": bool(hs and hs.available()),
            "apps_found": len(self.apps.apps()),
            "searched_first": [str(p) for p in self.files.primary],
            "searched_if_needed": [str(p) for p in self.files.secondary],
        }

    # -- step 2: do it (only for calls the safety layer authorized) --------------------------------------------

    def execute(self, tool: str, args: dict) -> ActionResult | None:
        """None means there is nothing to run (a plain reply)."""
        if tool == "reply":
            return None
        try:
            handler = getattr(self, f"_do_{tool}", None)
            if handler is None:
                return ActionResult(
                    "not_available",
                    "That isn't something I can do yet, so nothing was done.",
                )
            return handler(args)
        except Exception:
            log.exception("action %s failed unexpectedly", tool)
            return ActionResult("failed", "Something went wrong while doing that. Nothing was changed.")

    def _do_open_app(self, args: dict) -> ActionResult:
        return self._app_action(args, self.controller.open_app)

    def _do_focus_app(self, args: dict) -> ActionResult:
        return self._app_action(args, self.controller.focus_app)

    def _do_close_app(self, args: dict) -> ActionResult:
        return self._app_action(args, self.controller.close_app)

    def _app_action(self, args: dict, act) -> ActionResult:
        name = str(args.get("app_name", ""))
        app = self.apps.exact(name)
        if app is None:
            return ActionResult("failed", f"I couldn't find an app called “{name}” any more.")
        outcome = act(app)
        return ActionResult("done" if outcome.ok else "failed", outcome.message, via=outcome.via)

    def _do_open_file(self, args: dict) -> ActionResult:
        return self._open_path(args, folder=False)

    def _do_open_folder(self, args: dict) -> ActionResult:
        return self._open_path(args, folder=True)

    def _open_path(self, args: dict, folder: bool) -> ActionResult:
        path = str(args.get("path_or_name", ""))
        if not path.startswith("/"):
            # Execution only ever takes real paths; names are resolved (and judged) before this point.
            return ActionResult("failed", "I need an exact location to open that.")
        if not os.path.exists(path):
            return ActionResult("failed", f"There is nothing at {self.files.display(path)} any more.")
        if folder and not os.path.isdir(path):
            return ActionResult("failed", f"{os.path.basename(path)} is a file, not a folder.")
        name = os.path.basename(path) or path
        if not self.controller.open_path(path):
            return ActionResult("failed", f"I couldn't open {name}.", via="open")
        what = f"the {name} folder" if folder or os.path.isdir(path) else name
        return ActionResult("done", f"Opened {what}.", via="open")

    def _do_search_files(self, args: dict) -> ActionResult:
        query = str(args.get("query", ""))
        lookup = self.files.search(query)
        if not lookup.matches:
            return ActionResult("done", self._not_found(query, "file", lookup))
        names = [m.name for m in lookup.matches]
        message = _listing(f"Found {len(names)}:", names, len(names))
        if lookup.truncated:
            message += ". I stopped early to keep things fast"
        items = [f"{m.name}  ({self.files.display(os.path.dirname(m.path))})" for m in lookup.matches]
        return ActionResult("done", message, items=items)

    def _do_get_recent_files(self, args: dict) -> ActionResult:
        lookup = self.files.recent()
        if not lookup.matches:
            return ActionResult("done", "I didn't find any files in " + (_join_names(lookup.searched) or "your usual folders") + ".")
        names = [m.name for m in lookup.matches]
        where = _join_names(lookup.searched)
        message = _listing(f"Most recently changed in {where}:", names, len(names))
        items = [f"{m.name}  ({self.files.display(os.path.dirname(m.path))})" for m in lookup.matches]
        return ActionResult("done", message, items=items)


def _join_names(names: list[str]) -> str:
    unique = list(dict.fromkeys(names))
    if len(unique) <= 1:
        return "".join(unique)
    return ", ".join(unique[:-1]) + " and " + unique[-1]
