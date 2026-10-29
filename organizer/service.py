"""Safe desktop organization, in the same two steps as the other services:

    prepare()  read-only: work out the plan (the preview) for the folder, or ask a question if something is unclear
    -- the safety layer then judges the real folder, and the user sees the preview and approves it --
    execute()  only called for a plan the safety layer authorized; every file is re-checked as it moves

Nothing here moves a file by itself. The one way to skip the question is a *trusted rule* that the user wrote in
config.yaml (the model cannot create one).
"""
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from automation.fuzzy import normalize, pick, rank, strip_words
from automation.results import ActionResult, Prepared
from organizer import mover
from organizer.categories import CATEGORIES, parse_only
from organizer.movelog import MoveLog
from organizer.planner import Destination, Plan, build_plan, fold, group_rank, order_groups

log = logging.getLogger("assistant.organizer")

TOOL = "organize_desktop"
TRUSTED_TOOL = "organize_desktop_trusted"       # never offered to the model: only prepare() can produce it, from a rule in config.yaml
ORGANIZER_TOOLS = (TOOL, TRUSTED_TOOL)
PLAN_TTL_SECONDS = 600
MAX_PENDING_PLANS = 5
DEFAULT_TRUSTED_MAX = 25
FOLDER_FILLER = {"the", "my", "folder", "directory", "please", "files", "in", "on", "of"}
PREVIEW_MAX_CHARS = 1100


class _Stop(Exception):
    def __init__(self, message: str) -> None:
        self.message = message


@dataclass(frozen=True)
class TrustedRule:
    name: str
    folder: Path
    categories: frozenset | None
    max_files: int


class PlanStore:
    """Plans waiting for approval. A plan can be used once, and only for a while."""

    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._plans: dict[str, tuple[float, Plan]] = {}
        self._lock = threading.Lock()

    def put(self, plan: Plan) -> None:
        with self._lock:
            self._purge()
            while len(self._plans) >= MAX_PENDING_PLANS:
                self._plans.pop(next(iter(self._plans)))
            self._plans[plan.id] = (self._clock() + PLAN_TTL_SECONDS, plan)

    def take(self, plan_id) -> Plan | None:
        with self._lock:
            self._purge()
            entry = self._plans.pop(plan_id, None) if isinstance(plan_id, str) else None
        return entry[1] if entry else None

    def _purge(self) -> None:
        now = self._clock()
        for key in [k for k, (expires, _) in self._plans.items() if expires <= now]:
            del self._plans[key]

    def __len__(self) -> int:
        with self._lock:
            self._purge()
            return len(self._plans)


def tilde(path: Path, home: Path) -> str:
    text = str(path)
    return "~" + text[len(str(home)):] if mover.is_within(path, home) and path != home else text


def _names(moves, limit: int = 3) -> str:
    shown = ", ".join(m.name for m in moves[:limit])
    return shown + (f" +{len(moves) - limit} more" if len(moves) > limit else "")


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}" + ("" if n == 1 else "s")


def preview_text(plan: Plan, home: Path) -> str:
    """What would happen, in plain words. Short enough for a confirmation card."""
    head = f"In {tilde(plan.folder, home)} I would move {_plural(len(plan.moves), 'file')}"
    if plan.only:
        head += f" ({plan.only} only)"
    lines = [head + ":"]
    groups = order_groups(plan)
    for (group, dest_dir), moves in groups[:7]:
        new = " (new folder)" if dest_dir in plan.new_dirs else ""
        where = f" → {tilde(dest_dir, home)}" if plan.into_path else ""
        lines.append(f"• {group}{new}{where}, {len(moves)}: {_names(moves)}")
    if len(groups) > 7:
        lines.append(f"• and {len(groups) - 7} more folders")
    clashes = [m for m in plan.moves if m.renamed]
    if clashes:
        example = f"{clashes[0].name} → {clashes[0].dest_name}"
        lines.append(f"Name already taken ({len(clashes)}), so a timestamp is added: {example}" + (" and more" if len(clashes) > 1 else ""))
    if plan.skipped:
        lines.append("Left alone: " + ", ".join(f"{len(names)} {reason}" for reason, names in list(plan.skipped.items())[:6]))
    if plan.truncated:
        lines.append("That is only the first part; ask again afterwards for the rest.")
    text = "\n".join(lines)
    return text if len(text) <= PREVIEW_MAX_CHARS else text[: PREVIEW_MAX_CHARS - 1] + "…"


class OrganizerService:
    def __init__(self, movelog: MoveLog, *, files=None, home: Path | str | None = None, folders=("~/Desktop", "~/Downloads"),
                 clock=datetime.now, enabled: bool = True, settle_seconds: float = 60.0, max_files: int = 200,
                 trusted_rules=(), plans: PlanStore | None = None) -> None:
        self.movelog = movelog
        self.files = files
        self.home = Path(home or Path.home()).resolve()
        self._folder_config = list(folders)
        self.clock = clock
        self.enabled = enabled
        self.settle_seconds = settle_seconds
        self.max_files = max_files
        self.plans = plans if plans is not None else PlanStore()
        self.rules = self._parse_rules(trusted_rules)

    # ---- folders ---------------------------------------------------------------------------------------------

    def _expand(self, text: str) -> Path:
        text = str(text).strip()
        path = self.home if text == "~" else self.home / text[2:] if text.startswith("~/") else Path(text)
        return path.resolve()

    def _usable(self, path: Path) -> bool:
        """A visible folder inside the home folder (not the home folder itself, not Library)."""
        try:
            relative = path.relative_to(self.home)
        except ValueError:
            return False
        parts = relative.parts
        return bool(parts) and not any(p.startswith(".") for p in parts) and parts[0].lower() != "library" and path.is_dir()

    def folders(self) -> list[Path]:
        out = []
        for value in self._folder_config:
            try:
                path = self._expand(value)
            except OSError:
                continue
            if self._usable(path) and path not in out:
                out.append(path)
        return out

    def _parse_rules(self, raw) -> list[TrustedRule]:
        rules = []
        for item in raw or ():
            try:
                name, folder = str(item["name"]).strip(), self._expand(item["folder"])
                cats = item.get("categories")
                categories = frozenset(str(c).strip().title() for c in cats) if cats else None
                if not name or (categories and not categories <= set(CATEGORIES)):
                    raise ValueError("unknown category")
                limit = int(item.get("max_files", DEFAULT_TRUSTED_MAX))
                if limit < 1:
                    raise ValueError("max_files")
                rules.append(TrustedRule(name, folder, categories, limit))
            except (KeyError, TypeError, ValueError, OSError, AttributeError) as e:
                log.warning("ignoring an invalid trusted rule in config.yaml (%s)", type(e).__name__)
        return rules

    def status(self) -> dict:
        return {"enabled": self.enabled, "folders": [tilde(p, self.home) for p in self.folders()],
                "trusted_rules": [r.name for r in self.rules], "move_log": str(self.movelog.path)}

    def _folder(self, value) -> Path:
        allowed = self.folders()
        if not allowed:
            raise _Stop("None of the folders I'm allowed to organize exist. Check organizer.folders in config.yaml.")
        names = ", ".join(p.name for p in allowed)
        raw = str(value).strip() if isinstance(value, str) else ""
        if not raw:
            return next((p for p in allowed if p.name.lower() == "desktop"), allowed[0])
        if raw.startswith(("/", "~")):
            try:
                path = self._expand(raw)
            except OSError:
                path = None
            if path in allowed:
                return path
        else:
            found = pick(rank(strip_words(raw, FOLDER_FILLER) or normalize(raw), allowed, name_of=lambda p: p.name), 3)
            if found.kind == "one":
                return found.items[0]
        raise _Stop(f"I only organize these folders: {names}. You can add more under organizer.folders in config.yaml.")

    # ---- the destination for "move PDFs into the project folder" -------------------------------------------

    def _check_destination(self, path: Path, folder: Path) -> Path:
        if path.is_symlink() or not self._usable(path) or path == folder:
            raise _Stop(f"I can't move files into {tilde(path, self.home)}. Pick a normal folder inside your home folder.")
        return path

    def _destination(self, value, folder: Path) -> Destination:
        raw = str(value).strip() if isinstance(value, str) else ""
        if raw.startswith(("/", "~")):
            try:
                path = self._expand(raw)
            except OSError:
                raise _Stop("I can't find that folder.") from None
            if not path.is_dir():
                raise _Stop(f"There's no folder at {raw}.")
            return Destination(self._check_destination(path, folder), True, path.name)
        name = " ".join("".join(c if c.isalnum() or c in " -_" else " " for c in strip_words(raw, FOLDER_FILLER) or raw).split())
        if not name:
            raise _Stop("Which folder should they go into?")
        name = name[:60].title() if name.islower() else name[:60]
        for child in sorted(folder.iterdir(), key=lambda c: c.name):
            if child.is_dir() and not child.is_symlink() and fold(child.name) == fold(name):
                return Destination(child, True, child.name)
        if self.files is not None:
            lookup = self.files.locate(name, "folder")
            if lookup.kind in ("exact", "one"):
                path = Path(lookup.matches[0].path).resolve()
                return Destination(self._check_destination(path, folder), True, path.name)
            if lookup.kind == "many":
                where = ", ".join(tilde(Path(m.path), self.home) for m in lookup.matches[:3])
                raise _Stop(f"I found more than one folder like “{name}”: {where}. Tell me which, with its full path.")
        return Destination(folder / name, False, name)

    # ---- step 1: the plan (read-only) ----------------------------------------------------------------------

    def prepare(self, tool: str, args: dict, requires_confirmation: bool = False,
                dry_run: bool | None = None) -> Prepared:
        if not self.enabled:
            return Prepared("failed", result=ActionResult(
                "not_available", "Organizing is turned off. Set organizer.enabled: true in config.yaml."))
        try:
            return self._prepare(args)
        except _Stop as stop:
            return Prepared("failed", result=ActionResult("failed", stop.message))
        except OSError as e:
            log.warning("could not read a folder: %s", e.strerror)
            return Prepared("failed", result=ActionResult("failed", "I couldn't read that folder, so nothing was planned."))

    def _prepare(self, args: dict) -> Prepared:
        folder = self._folder(args.get("folder"))
        only_text = args.get("only")
        only = None
        if isinstance(only_text, str) and only_text.strip():
            try:
                only = parse_only(only_text)
            except ValueError as e:
                raise _Stop(str(e)) from None
        into_text = args.get("into")
        into = None
        if isinstance(into_text, str) and into_text.strip():
            if only is None:
                raise _Stop("Tell me which files to move into it, for example “move PDFs into the project folder”.")
            into = self._destination(into_text, folder)

        plan = build_plan(folder, self.clock(), only=only, into=into, settle_seconds=self.settle_seconds, max_files=self.max_files)
        name = folder.name
        if not plan.moves:
            if only is not None:
                raise _Stop(f"I found no {plan.only} to move in your {name} folder.")
            left = "; ".join(f"{len(n)} {r}" for r, n in plan.skipped.items())
            raise _Stop(f"Your {name} folder is already tidy: there is nothing I can move." + (f" (Left alone: {left}.)" if left else ""))
        self.plans.put(plan)

        ready = {"folder": str(folder), "plan_id": plan.id, "count": len(plan.moves)}
        if plan.only:
            ready["only"] = plan.only
        if into is not None:
            ready["into_path"] = str(into.path)
        preview = preview_text(plan, self.home)
        rule = self._trusted(plan)
        if rule is None:
            return Prepared("ready", ready, preview=preview)
        ready["rule"] = rule.name
        return Prepared("ready", ready, preview=preview + f"\nYour trusted rule “{rule.name}” covers this, so no question is asked.",
                        tool=TRUSTED_TOOL, requires_confirmation=False)

    def _trusted(self, plan: Plan) -> TrustedRule | None:
        """A rule the user wrote in config.yaml that covers this whole plan. Custom destinations always ask."""
        if plan.into_path is not None or plan.truncated:
            return None
        for rule in self.rules:
            if (rule.folder == plan.folder and len(plan.moves) <= rule.max_files
                    and (rule.categories is None or plan.categories() <= rule.categories)):
                return rule
        return None

    # ---- step 2: do it (only for a plan the safety layer authorized) -----------------------------------------

    def execute(self, tool: str, args: dict) -> ActionResult | None:
        if tool not in ORGANIZER_TOOLS:
            return ActionResult("not_available", "That isn't something I can do yet, so nothing was done.")
        if not self.enabled:
            return ActionResult("not_available", "Organizing is turned off. Set organizer.enabled: true in config.yaml.")
        plan = self.plans.take(args.get("plan_id"))
        if plan is None:
            return ActionResult("failed", "That preview has expired, so nothing was moved. Ask me to organize again.")
        if args.get("folder") != str(plan.folder) or args.get("into_path") != (str(plan.into_path) if plan.into_path else None):
            return ActionResult("failed", "That doesn't match the preview, so nothing was moved.")
        try:
            report = mover.execute(plan, self.movelog, self.clock(), self.home)
        except Exception:
            log.exception("organizing failed unexpectedly")
            return ActionResult("failed", "Something went wrong while moving files. Check the move log before trying again.")
        return self._result(plan, report, args.get("rule") if tool == TRUSTED_TOOL else None)

    def _result(self, plan: Plan, report: mover.Report, rule: str | None) -> ActionResult:
        name = plan.folder.name
        if not report.moved:
            why = report.skipped[0][1] if report.skipped else "nothing was left to move"
            return ActionResult("failed", f"I couldn't move anything in your {name} folder ({why}).", via="files")
        found: dict[str, list[str]] = {}
        for move, final in report.moved:
            found.setdefault(move.group, []).append(final)
        groups = {g: found[g] for g in sorted(found, key=group_rank)}
        items = [f"Trusted rule “{rule}”: moved without asking."] if rule else []
        items += [f"{group} ({len(names)}): {', '.join(names[:4])}" + (f" +{len(names) - 4} more" if len(names) > 4 else "")
                  for group, names in groups.items()]
        items += [f"Left in place: {n} ({why})" for n, why in report.skipped[:5]]
        message = f"Moved {_plural(len(report.moved), 'file')} in your {name} folder into {', '.join(groups)}."
        if report.skipped:
            message += f" {len(report.skipped)} stayed where they were."
        message += " To undo, run scripts/organize_undo.py."
        return ActionResult("done", message, items, via="files")
