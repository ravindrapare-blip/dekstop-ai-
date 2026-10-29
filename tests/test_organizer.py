import errno
import json
import os
import stat
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from automation.choices import ChoiceStore
from automation.files import FileFinder, PRIMARY_DIRS, SECONDARY_DIRS
from backend.actions import ActionRouter, build_router
from backend.config import ROOT
from backend.llm import OllamaClient
from backend.main import create_app
from backend.pipeline import Pipeline
from backend.prompt import build_messages
from backend.tool_defs import build_registry
from backend.toolcall import ToolCall
from browserctl import BrowserService
from fake_browser import FakeWorker
from fakes import make_world
from organizer import ORGANIZER_TOOLS, OrganizerService
from organizer import mover
from organizer.categories import categorize, is_hidden_or_system, parse_only
from organizer.movelog import MoveLog
from organizer.planner import build_plan, fold, unique_name
from organizer.service import PlanStore, preview_text
from organizer.undo import undo
from safety import AuditLog, GuardrailManager, Outcome, Policy

POLICY = Policy.load(ROOT / "safety" / "policy.yaml")
NOW = datetime(2026, 10, 29, 14, 0, 0)
STAMP = "2026-10-29 14-00-00"


def make_file(path: Path, text: str = "x", age_days: float = 2) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    when = (NOW - timedelta(days=age_days)).timestamp()
    os.utime(path, (when, when))
    return path


def tree(root: Path) -> dict[str, str]:
    """Everything under root (names and contents), to prove a step changed nothing."""
    out = {}
    for p in sorted(root.rglob("*")):
        out[str(p.relative_to(root))] = "<dir>" if p.is_dir() else (p.read_text() if p.is_file() and not p.is_symlink() else "<other>")
    return out


class Org:
    """A real temporary home folder, the organizer, and the real safety layer in front of it."""

    def __init__(self, tmp_path, monkeypatch, **options):
        self.home = (tmp_path / "home").resolve()
        for name in ("Desktop", "Downloads", "Documents"):
            (self.home / name).mkdir(parents=True)
        monkeypatch.setenv("HOME", str(self.home))             # so "~/Library" and friends in the policy mean this home
        self.desktop = self.home / "Desktop"
        self.downloads = self.home / "Downloads"
        self.now = NOW
        self.log_path = tmp_path / "logs" / "moves.jsonl"
        self.movelog = MoveLog(self.log_path)
        finder = FileFinder(PRIMARY_DIRS, SECONDARY_DIRS, home=self.home)
        self.plans = PlanStore()
        self.service = OrganizerService(self.movelog, files=finder, home=self.home, clock=lambda: self.now,
                                        plans=self.plans, **options)
        self.audit = tmp_path / "audit.jsonl"
        self.guard = GuardrailManager(POLICY, AuditLog(self.audit))
        self.pipeline = Pipeline(self.guard, self.service)

    def run(self, **args):
        return self.pipeline.run("organize_desktop", args, True)

    def approve(self, out, acknowledge=False):
        decision = self.guard.confirm(out.decision.confirmation_id, True, acknowledge_risk=acknowledge)
        return self.pipeline.after_confirmation(decision)

    def move_events(self):
        return [e for e in self.movelog.read() if e.get("event") == "move"]


@pytest.fixture
def o(tmp_path, monkeypatch):
    return Org(tmp_path, monkeypatch)


def messy_desktop(o: Org):
    d = o.desktop
    for name in ("report.pdf", "notes.txt", "photo.jpg", "movie.mp4", "song.mp3", "budget.xlsx", "app.zip", "resume.docx", "script.py"):
        make_file(d / name, name)
    return d


# ---- which folder a file belongs in ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,category", [
    ("report.pdf", "Documents"), ("notes.TXT", "Documents"), ("book.epub", "Documents"), ("letter.docx", "Documents"),
    ("budget.xlsx", "Work"), ("slides.pptx", "Work"), ("main.py", "Work"), ("data.csv", "Work"),
    ("photo.JPG", "Images"), ("logo.png", "Images"), ("Screenshot 2026-01-01 at 10.00.00.png", "Images"), ("pic.heic", "Images"),
    ("song.mp3", "Media"), ("clip.mov", "Media"), ("talk.mkv", "Media"),
    ("resume.pdf", "Personal"), ("My Passport scan.jpg", "Personal"), ("TaxReturn2025.pdf", "Personal"), ("invite.ics", "Personal"),
    ("meeting-notes.pdf", "Work"), ("project plan.docx", "Work"),
    ("archive.zip", "Other"), ("installer.dmg", "Other"), ("mystery", "Other"), ("weird.qqqq", "Other"), ("archive.tar.gz", "Other"),
    ("resume.mp3", "Media"),                                    # media keeps its folder whatever it is called
])
def test_files_are_sorted_by_name_and_type(name, category):
    assert categorize(name) == category


@pytest.mark.parametrize("name", [".DS_Store", ".hidden.pdf", ".localized", "Icon\r", "desktop.ini", "Thumbs.db", "~$report.docx",
                                  "draft.docx~", "movie.mp4.crdownload", "file.download", "x.part", "x.TMP", ".name.icloud"])
def test_hidden_system_and_unfinished_files_are_recognised(name):
    assert is_hidden_or_system(name)


@pytest.mark.parametrize("name", ["report.pdf", "my.hidden.pdf", "notes.txt", "part.pdf", "download.pdf"])
def test_ordinary_files_are_not_mistaken_for_hidden_ones(name):
    assert not is_hidden_or_system(name)


@pytest.mark.parametrize("text,categories,extensions", [
    ("PDFs", set(), {"pdf"}), ("images", {"Images"}, set()), ("photos and videos", {"Images"}, {"mp4", "mov", "m4v", "avi", "mkv", "webm", "wmv", "mpg", "mpeg", "3gp"}),
    ("docx", set(), {"docx"}), (".png files", set(), {"png"}), ("work", {"Work"}, set()), ("only the spreadsheets", set(), {"xls", "xlsx", "csv", "numbers", "ods"}),
])
def test_only_understands_kinds_of_files(text, categories, extensions):
    f = parse_only(text)
    assert set(f.categories) == categories and set(f.extensions) == extensions


@pytest.mark.parametrize("text", ["", "   ", "banana", "pdfs and wombats", "the"])
def test_only_refuses_what_it_does_not_understand(text):
    with pytest.raises(ValueError, match="I don't know what kind of files"):
        parse_only(text)


# ---- preview logic -----------------------------------------------------------------------------------------

def test_organize_my_desktop_shows_a_preview_and_changes_nothing(o):
    messy_desktop(o)
    before = tree(o.home)
    out = o.run()
    assert out.decision.outcome is Outcome.CONFIRM and out.result is None
    message = out.decision.message
    assert message.startswith("In ~/Desktop I would move 9 files:")
    assert "• Work (new folder), 2: budget.xlsx, script.py" in message
    assert "• Documents (new folder), 2: notes.txt, report.pdf" in message
    assert "• Media (new folder), 2: movie.mp4, song.mp3" in message
    assert "• Images (new folder), 1: photo.jpg" in message and "• Personal (new folder), 1: resume.docx" in message
    assert "• Other (new folder), 1: app.zip" in message
    assert message.endswith("Nothing moves until you confirm.")
    assert message.index("Work") < message.index("Documents") < message.index("Media") < message.index("Images") < message.index("Personal") < message.index("Other")
    assert tree(o.home) == before                                  # no folder created, no file moved
    assert o.move_events() == [] and not o.log_path.exists()


def test_the_preview_is_also_what_a_dry_run_describes(o):
    messy_desktop(o)
    before = tree(o.home)
    out = o.pipeline.run("organize_desktop", {}, True, dry_run=True)
    assert out.decision.outcome is Outcome.DRY_RUN and out.decision.message.startswith("Dry run. In ~/Desktop I would move 9 files:")
    assert out.decision.message.endswith("Nothing was moved.") and out.result is None
    assert tree(o.home) == before


def test_the_global_dry_run_setting_is_respected(o):
    messy_desktop(o)
    o.guard.dry_run = True
    out = o.run()
    assert out.decision.outcome is Outcome.DRY_RUN
    assert [p.name for p in o.desktop.iterdir()].count("Documents") == 0


def test_existing_category_folders_are_reused_not_recreated(o):
    make_file(o.desktop / "Documents" / "old.pdf", "old")
    make_file(o.desktop / "new.pdf", "new")
    make_file(o.desktop / "pic.png", "p")
    message = o.run().decision.message
    assert "• Documents, 1: new.pdf" in message and "• Images (new folder), 1: pic.png" in message


def test_a_folder_in_the_way_of_a_category_is_respected_case_insensitively(o):
    make_file(o.desktop / "documents" / "old.pdf", "old")
    make_file(o.desktop / "new.pdf", "new")
    out = o.run()
    o.approve(out)
    assert (o.desktop / "documents" / "new.pdf").read_text() == "new" and not (o.desktop / "Documents").exists()


def test_a_file_named_like_a_category_blocks_only_that_category(o):
    make_file(o.desktop / "Documents", "I am a file, not a folder", age_days=0)      # just changed, so it stays where it is
    make_file(o.desktop / "a.pdf", "doc")
    make_file(o.desktop / "b.png", "img")
    out = o.run()
    assert "something that isn't a folder is already named “Documents”" in out.decision.message
    o.approve(out)
    assert (o.desktop / "a.pdf").exists() and (o.desktop / "Images" / "b.png").exists()
    assert (o.desktop / "Documents").read_text() == "I am a file, not a folder"


def test_downloads_can_be_sorted_too(o):
    make_file(o.downloads / "installer.dmg", "d")
    make_file(o.downloads / "tax 2025.pdf", "t")
    out = o.run(folder="downloads")
    assert out.decision.message.startswith("In ~/Downloads I would move 2 files") and "• Personal (new folder), 1: tax 2025.pdf" in out.decision.message
    out2 = o.run(folder="my Downloads folder")
    assert out2.decision.args["folder"] == str(o.downloads)


def test_sort_only_some_kinds_of_files(o):
    messy_desktop(o)
    out = o.run(only="images")
    assert out.decision.message.startswith("In ~/Desktop I would move 1 file (images only):") and "Left alone: 8 other kinds of files" in out.decision.message
    assert o.run(only="PDFs").decision.message.count("•") == 1
    assert "no spreadsheets to move" in o.run(only="spreadsheets", folder="Downloads").result.message


def test_unknown_kinds_and_folders_are_explained(o):
    messy_desktop(o)
    assert "don't know what kind of files “wombats”" in o.run(only="wombats").result.message
    for bad in ("Documents", "Library", "/etc", "~/.ssh", "../..", "Pictures"):
        out = o.run(folder=bad)
        assert out.decision is None and "I only organize these folders: Desktop, Downloads" in out.result.message, bad


def test_an_already_tidy_folder_has_nothing_to_do(o):
    make_file(o.desktop / "Documents" / "kept.pdf")
    (o.desktop / "Some Folder").mkdir()
    make_file(o.desktop / ".hidden")
    out = o.run()
    assert out.decision is None and out.result.status == "failed"
    assert "already tidy" in out.result.message and "2 folders" in out.result.message and "1 hidden or system files" in out.result.message


def test_a_big_folder_is_previewed_in_parts(tmp_path, monkeypatch):
    o = Org(tmp_path, monkeypatch, max_files=5)
    for i in range(8):
        make_file(o.desktop / f"f{i}.pdf")
    out = o.run()
    assert out.decision.args["count"] == 5 and "only the first part" in out.decision.message
    o.approve(out)
    assert len(list((o.desktop / "Documents").iterdir())) == 5
    again = o.run()                                                  # the rest comes next time
    assert again.decision.args["count"] == 3


def test_more_than_a_hundred_files_need_the_risk_acknowledged(tmp_path, monkeypatch):
    o = Org(tmp_path, monkeypatch, max_files=500)
    for i in range(101):
        make_file(o.desktop / f"f{i}.txt")
    out = o.run()
    assert out.decision.outcome is Outcome.CONFIRM and out.decision.requires_acknowledgment and "more than 100 files" in out.decision.reason
    plain = o.guard.confirm(out.decision.confirmation_id, True)
    assert plain.outcome is Outcome.BLOCK and not (o.desktop / "Documents").exists()


def test_the_message_stays_short_whatever_the_folder_holds(o):
    for i in range(60):
        make_file(o.desktop / f"a very long file name number {i} for testing the preview length.pdf")
    assert len(o.run().decision.message) < 1300


# ---- move PDFs into the project folder -------------------------------------------------------------------------

def test_move_pdfs_into_a_new_project_folder(o):
    make_file(o.desktop / "a.pdf", "a"); make_file(o.desktop / "b.pdf", "b"); make_file(o.desktop / "c.png", "c")
    out = o.run(only="PDFs", into="project folder")
    assert out.decision.outcome is Outcome.CONFIRM
    assert "(pdfs only)" in out.decision.message and "• Project (new folder) → ~/Desktop/Project, 2: a.pdf, b.pdf" in out.decision.message
    o.approve(out)
    assert sorted(p.name for p in (o.desktop / "Project").iterdir()) == ["a.pdf", "b.pdf"] and (o.desktop / "c.png").exists()


def test_move_pdfs_into_an_existing_folder_elsewhere(o):
    (o.home / "Documents" / "Project Apollo").mkdir()
    make_file(o.desktop / "a.pdf")
    out = o.run(only="pdfs", into="project apollo")
    assert out.decision.args["into_path"] == str(o.home / "Documents" / "Project Apollo")
    assert "(new folder)" not in out.decision.message
    o.approve(out)
    assert (o.home / "Documents" / "Project Apollo" / "a.pdf").exists()


def test_a_folder_next_to_the_files_is_found_first(o):
    (o.desktop / "Project").mkdir()
    (o.home / "Documents" / "Project").mkdir()
    make_file(o.desktop / "a.pdf")
    assert o.run(only="pdfs", into="project").decision.args["into_path"] == str(o.desktop / "Project")


def test_two_matching_folders_are_not_guessed_between(o):
    (o.home / "Documents" / "Project").mkdir()
    (o.home / "Downloads" / "Project").mkdir()
    make_file(o.desktop / "a.pdf")
    out = o.run(only="pdfs", into="project")
    assert out.decision is None and "more than one folder like “Project”" in out.result.message and "~/Documents/Project" in out.result.message
    assert o.run(only="pdfs", into="~/Documents/Project").decision.args["into_path"] == str(o.home / "Documents" / "Project")


def test_into_needs_to_know_which_files(o):
    make_file(o.desktop / "a.pdf")
    assert "which files to move into it" in o.run(into="project").result.message


@pytest.mark.parametrize("into", ["~/Library", "~/.ssh", "/etc", "/", "~", "~/Desktop", "~/Nowhere"])
def test_unsafe_destinations_are_refused(o, into):
    (o.home / "Library").mkdir(); (o.home / ".ssh").mkdir()
    make_file(o.desktop / "a.pdf")
    out = o.run(only="pdfs", into=into)
    assert out.decision is None and out.result.status == "failed", into
    assert (o.desktop / "a.pdf").exists()


def test_a_shortcut_as_a_destination_is_refused(o, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (o.desktop / "Link").symlink_to(outside)
    make_file(o.desktop / "a.pdf")
    out = o.run(only="pdfs", into="~/Desktop/Link")
    assert out.decision is None and not list(outside.iterdir())


def test_a_destination_name_cannot_climb_out_of_the_folder(o):
    make_file(o.desktop / "a.pdf")
    out = o.run(only="pdfs", into="../../etc/evil")
    assert out.decision.args["into_path"].startswith(str(o.desktop)) and "/.." not in out.decision.args["into_path"]


# ---- duplicate names -----------------------------------------------------------------------------------------

def test_a_name_clash_gets_a_timestamp_and_is_shown_in_the_preview(o):
    make_file(o.desktop / "Documents" / "report.pdf", "the old one")
    make_file(o.desktop / "report.pdf", "the new one")
    out = o.run()
    assert f"Name already taken (1), so a timestamp is added: report.pdf → report {STAMP}.pdf" in out.decision.message
    o.approve(out)
    assert (o.desktop / "Documents" / "report.pdf").read_text() == "the old one"                  # never overwritten
    assert (o.desktop / "Documents" / f"report {STAMP}.pdf").read_text() == "the new one"
    assert not (o.desktop / "report.pdf").exists()


def test_clashes_ignore_case_and_unicode_form(o):
    made = make_file(o.desktop / "Documents" / "REPORT.PDF", "old")
    make_file(o.desktop / "report.pdf", "new")
    plan = build_plan(o.desktop, NOW)
    assert plan.moves[0].dest_name == f"report {STAMP}.pdf"
    composed, decomposed = "café.txt", "café.txt"
    assert fold(composed) == fold(decomposed) and unique_name(composed, {fold(decomposed)}, STAMP) == f"café {STAMP}.txt"
    assert made.exists()


def test_two_files_that_would_land_on_the_same_name_are_both_kept(o):
    make_file(o.desktop / "Report.pdf", "upper"); make_file(o.desktop / "report.pdf", "lower")
    if len({p.name for p in o.desktop.iterdir()}) < 2:
        pytest.skip("this disk ignores case, so the two names can't both exist")
    plan = build_plan(o.desktop, NOW)
    assert sorted(m.dest_name for m in plan.moves) == sorted(["Report.pdf", f"report {STAMP}.pdf"])


def test_even_a_taken_timestamp_name_is_not_reused(o):
    make_file(o.desktop / "Documents" / "r.pdf"); make_file(o.desktop / "Documents" / f"r {STAMP}.pdf")
    make_file(o.desktop / "r.pdf", "mine")
    out = o.run()
    o.approve(out)
    assert (o.desktop / "Documents" / f"r {STAMP} 2.pdf").read_text() == "mine"


def test_names_without_extensions_and_dotfile_like_names_are_handled(o):
    assert unique_name("README", {"readme"}, STAMP) == f"README {STAMP}"
    assert unique_name("a.tar.gz", {"a.tar.gz"}, STAMP) == f"a.tar {STAMP}.gz"
    assert unique_name("free.pdf", set(), STAMP) == "free.pdf"


def test_very_long_names_stay_within_the_limit(o):
    name = "x" * 240 + ".pdf"
    renamed = unique_name(name, {fold(name)}, STAMP)
    assert len(renamed.encode()) <= 255 and renamed.endswith(f" {STAMP}.pdf")


def test_a_name_that_appears_after_the_preview_is_still_not_overwritten(o):
    make_file(o.desktop / "report.pdf", "mine")
    out = o.run()
    make_file(o.desktop / "Documents" / "report.pdf", "appeared meanwhile")
    o.approve(out)
    assert (o.desktop / "Documents" / "report.pdf").read_text() == "appeared meanwhile"
    [moved] = [p for p in (o.desktop / "Documents").iterdir() if p.name != "report.pdf"]
    assert moved.read_text() == "mine" and STAMP in moved.name


def test_safe_move_never_replaces_anything(tmp_path, monkeypatch):
    a, b = make_file(tmp_path / "a", "A"), make_file(tmp_path / "b", "B")
    with pytest.raises(FileExistsError):
        mover.safe_move(a, b)
    assert a.read_text() == "A" and b.read_text() == "B"
    mover.safe_move(a, tmp_path / "c")
    assert not a.exists() and (tmp_path / "c").read_text() == "A"


def test_on_a_disk_without_hard_links_it_still_refuses_to_overwrite(tmp_path, monkeypatch):
    def no_links(*args, **kwargs):
        raise OSError(errno.EPERM, "not permitted")

    monkeypatch.setattr(os, "link", no_links)
    a, b = make_file(tmp_path / "a", "A"), make_file(tmp_path / "b", "B")
    with pytest.raises(FileExistsError):
        mover.safe_move(a, b)
    mover.safe_move(a, tmp_path / "c")
    assert (tmp_path / "c").read_text() == "A" and b.read_text() == "B"


def test_moving_to_another_disk_is_refused_not_copied(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "link", lambda *a, **k: (_ for _ in ()).throw(OSError(errno.EXDEV, "cross-device")))
    a = make_file(tmp_path / "a", "A")
    with pytest.raises(OSError, match="different disk"):
        mover.safe_move(a, tmp_path / "c")
    assert a.exists() and not (tmp_path / "c").exists()


# ---- hidden and system things are left alone -----------------------------------------------------------------

def test_hidden_system_and_unfinished_files_and_folders_are_not_touched(o):
    d = o.desktop
    make_file(d / "report.pdf", "move me")
    make_file(d / ".DS_Store"); make_file(d / ".secret.pdf"); make_file(d / "~$lock.docx"); make_file(d / "movie.mp4.crdownload")
    make_file(d / "Thumbs.db")
    (d / "Some Folder").mkdir(); make_file(d / "Some Folder" / "inner.pdf", "inner")
    (d / ".hiddenfolder").mkdir(); make_file(d / ".hiddenfolder" / "x.pdf", "hidden inner")
    (d / "My.app").mkdir()
    (d / "link.pdf").symlink_to(d / "report.pdf")
    os.mkfifo(d / "pipe.txt")
    make_file(d / "fresh.pdf", "just downloaded", age_days=0.00001)
    before = tree(d)
    out = o.run()
    message = out.decision.message
    assert "I would move 1 file" in message
    for reason in ("hidden or system files", "folders", "shortcuts and links", "special files", "files changed in the last minute"):
        assert reason in message, reason
    o.approve(out)
    after = tree(d)
    assert after.pop("Documents/report.pdf") == "move me" and after.pop("Documents") == "<dir>"
    before.pop("report.pdf")
    assert after == before                                              # everything else is exactly as it was


def test_hidden_flags_and_locked_files_are_skipped(o, monkeypatch):
    make_file(o.desktop / "hidden-by-flag.pdf"); make_file(o.desktop / "locked.pdf"); make_file(o.desktop / "ok.pdf")
    real = os.DirEntry.stat

    class Flagged:
        def __init__(self, info, flags): self._info, self.st_flags = info, flags
        def __getattr__(self, name): return getattr(self._info, name)

    import organizer.planner as planner
    original = planner._listing

    def listing(path):
        entries = original(path)

        class Entry:
            def __init__(self, e): self._e, self.name = e, e.name
            def stat(self, follow_symlinks=True):
                info = self._e.stat(follow_symlinks=follow_symlinks)
                flags = {"hidden-by-flag.pdf": planner.HIDDEN_FLAG or 0x8000, "locked.pdf": planner.LOCKED_FLAG or 0x2}.get(self.name, 0)
                return Flagged(info, flags)
            def is_symlink(self): return self._e.is_symlink()
            def is_dir(self, follow_symlinks=True): return self._e.is_dir(follow_symlinks=follow_symlinks)

        return [Entry(e) for e in entries]

    monkeypatch.setattr(planner, "_listing", listing)
    monkeypatch.setattr(planner, "HIDDEN_FLAG", 0x8000)
    monkeypatch.setattr(planner, "LOCKED_FLAG", 0x2)
    plan = build_plan(o.desktop, NOW)
    assert [m.name for m in plan.moves] == ["ok.pdf"] and set(plan.skipped) == {"hidden or system files", "locked files"}
    assert real


def test_it_never_goes_into_folders(o):
    make_file(o.desktop / "Projects" / "deep" / "a.pdf", "deep")
    make_file(o.desktop / "top.pdf", "top")
    o.approve(o.run())
    assert (o.desktop / "Projects" / "deep" / "a.pdf").read_text() == "deep" and (o.desktop / "Documents" / "top.pdf").exists()


@pytest.mark.parametrize("make", ["hidden", "library", "outside", "home", "missing"])
def test_only_visible_folders_inside_home_can_be_organized(tmp_path, monkeypatch, make):
    o = Org(tmp_path, monkeypatch)
    (o.home / ".config").mkdir(); (o.home / "Library").mkdir(); (o.home / ".config" / "Desktop").mkdir()
    outside = tmp_path / "outside"; outside.mkdir()
    folder = {"hidden": "~/.config", "library": "~/Library", "outside": str(outside), "home": "~", "missing": "~/Nope"}[make]
    service = OrganizerService(o.movelog, home=o.home, folders=[folder], clock=lambda: NOW)
    assert service.folders() == []
    out = Pipeline(o.guard, service).run("organize_desktop", {}, True)
    assert out.decision is None and "None of the folders I'm allowed to organize exist" in out.result.message


def test_a_folder_that_became_a_shortcut_before_approval_is_not_touched(o, tmp_path):
    make_file(o.desktop / "a.pdf", "mine")
    out = o.run()
    elsewhere = tmp_path / "elsewhere"
    o.desktop.rename(tmp_path / "moved-away")
    elsewhere.mkdir()
    o.desktop.symlink_to(elsewhere)
    result = o.approve(out)
    assert result.status == "failed" and not list(elsewhere.iterdir()) and (tmp_path / "moved-away" / "a.pdf").exists()


def test_a_folder_swapped_for_a_shortcut_to_look_alike_files_is_not_trusted(o, tmp_path):
    """Even if the look-alike folder holds the very same files (same inode, size and time), nothing moves through it."""
    make_file(o.desktop / "a.pdf", "mine")
    out = o.run()
    lookalike = tmp_path / "lookalike"
    lookalike.mkdir()
    os.link(o.desktop / "a.pdf", lookalike / "a.pdf")
    o.desktop.rename(tmp_path / "real-desktop")
    o.desktop.symlink_to(lookalike)
    result = o.approve(out)
    assert result.status == "failed" and "the folder changed" in result.message
    assert [p.name for p in lookalike.iterdir()] == ["a.pdf"] and (tmp_path / "real-desktop" / "a.pdf").exists()


@pytest.mark.parametrize("change", ["time", "size", "identity"])
def test_each_kind_of_change_since_the_preview_keeps_a_file_in_place(o, change):
    path = make_file(o.desktop / "a.pdf", "twelve chars")
    original = path.stat()
    out = o.run()
    if change == "time":
        os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns + 5_000_000_000))
    elif change == "size":
        path.write_text("a different length now")
        os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
    else:                                                       # replaced by another file of the same size and time
        replacement = o.desktop.parent / "replacement"          # made while the original still exists, so its identity differs
        replacement.write_text("twelve chars")
        os.utime(replacement, ns=(original.st_atime_ns, original.st_mtime_ns))
        os.replace(replacement, path)
        assert path.stat().st_ino != original.st_ino
    result = o.approve(out)
    assert result.status == "failed" and (o.desktop / "a.pdf").exists() and not (o.desktop / "Documents" / "a.pdf").exists()
    assert any("changed since the preview" in why for why in [result.message])


def test_a_destination_that_became_a_shortcut_to_another_folder_is_not_used(o):
    other = o.home / "Documents"
    make_file(o.desktop / "a.pdf", "mine")
    out = o.run()
    (o.desktop / "Documents").symlink_to(other)                # appeared after the preview, pointing somewhere else in the home folder
    result = o.approve(out)
    assert result.status == "failed" and "shortcut" in result.message
    assert (o.desktop / "a.pdf").exists() and list(other.iterdir()) == []


def test_the_safety_layer_judges_the_real_folder_too():
    guard = GuardrailManager(POLICY)
    for folder in ("/System/Library", "/etc", "/usr/local", "/home/user/.ssh", "/tmp/.hidden/x"):
        d = guard.evaluate(ToolCall("organize_desktop", {"folder": folder, "plan_id": "0123456789ab", "count": 3}, True))
        assert d.risk.label in ("high", "critical"), folder
    assert guard.evaluate(ToolCall("organize_desktop", {"folder": "Desktop"}, True)).outcome is Outcome.BLOCK       # not a full path
    for args in ({"count": -1}, {"count": True}, {"count": "5"}, {"plan_id": "../../x"}, {"plan_id": 5}, {"into_path": "relative/dir"}):
        assert guard.evaluate(ToolCall("organize_desktop", args, True)).outcome is Outcome.BLOCK, args


# ---- the confirmation flow ------------------------------------------------------------------------------------

def test_nothing_moves_until_the_user_confirms_and_then_exactly_the_preview_happens(o):
    messy_desktop(o)
    out = o.run()
    assert out.decision.outcome is Outcome.CONFIRM and out.decision.confirmation_id and not out.decision.requires_acknowledgment
    assert out.decision.risk.label == "medium" and out.decision.args["count"] == 9
    assert (o.desktop / "report.pdf").exists() and not (o.desktop / "Documents").exists()
    result = o.approve(out)
    assert result.status == "done" and result.message.startswith("Moved 9 files in your Desktop folder into Work, Documents, Media, Images, Personal, Other.")
    assert "scripts/organize_undo.py" in result.message
    assert sorted(p.name for p in o.desktop.iterdir()) == ["Documents", "Images", "Media", "Other", "Personal", "Work"]
    assert (o.desktop / "Work" / "budget.xlsx").read_text() == "budget.xlsx" and (o.desktop / "Personal" / "resume.docx").exists()
    assert result.items[0] == "Work (2): budget.xlsx, script.py"


def test_declining_moves_nothing_and_the_old_preview_is_gone(o):
    messy_desktop(o)
    before = tree(o.home)
    out = o.run()
    denied = o.guard.confirm(out.decision.confirmation_id, False)
    assert o.pipeline.after_confirmation(denied) is None and tree(o.home) == before and o.move_events() == []
    assert len(o.plans) == 1                                      # it waits to expire; it can't be run without an approval
    assert o.pipeline.after_confirmation(o.guard.confirm(out.decision.confirmation_id, True)) is None


def test_an_approval_works_once(o):
    make_file(o.desktop / "a.pdf")
    out = o.run()
    assert o.approve(out).status == "done"
    assert o.pipeline.after_confirmation(o.guard.confirm(out.decision.confirmation_id, True)) is None
    assert o.service.execute("organize_desktop", out.decision.args).message.startswith("That preview has expired")


def test_an_expired_preview_moves_nothing(tmp_path, monkeypatch):
    o = Org(tmp_path, monkeypatch)
    clock = [0.0]
    o.service.plans = o.plans = PlanStore(clock=lambda: clock[0])
    make_file(o.desktop / "a.pdf")
    out = o.run()
    clock[0] = 10_000
    result = o.approve(out)
    assert result.status == "failed" and "expired" in result.message and (o.desktop / "a.pdf").exists()


def test_the_approved_call_must_match_the_preview(o):
    make_file(o.desktop / "a.pdf")
    out = o.run()
    swapped = dict(out.decision.args, folder=str(o.downloads))
    assert "doesn't match" in o.service.execute("organize_desktop", swapped).message
    assert (o.desktop / "a.pdf").exists()
    assert "expired" in o.service.execute("organize_desktop", {"folder": str(o.desktop), "plan_id": "0123456789ab"}).message
    assert "expired" in o.service.execute("organize_desktop", {}).message


def test_a_file_that_changed_after_the_preview_stays_put(o):
    make_file(o.desktop / "a.pdf", "short"); make_file(o.desktop / "b.pdf", "stable"); make_file(o.desktop / "c.pdf", "gone")
    out = o.run()
    (o.desktop / "a.pdf").write_text("this is now much longer than before")        # being edited
    (o.desktop / "c.pdf").unlink()
    result = o.approve(out)
    assert result.status == "done" and "Moved 1 file" in result.message and "2 stayed where they were" in result.message
    assert (o.desktop / "a.pdf").read_text().startswith("this is now") and (o.desktop / "Documents" / "b.pdf").exists()
    assert any("it changed since the preview" in i for i in result.items) and any("no longer there" in i for i in result.items)


def test_a_folder_that_could_not_be_filled_reports_failure(o):
    make_file(o.desktop / "a.pdf")
    out = o.run()
    os.chmod(o.desktop, 0o500)
    try:
        if os.access(o.desktop, os.W_OK):
            pytest.skip("this user can write anywhere (root)")
        result = o.approve(out)
    finally:
        os.chmod(o.desktop, 0o700)
    assert result.status == "failed" and "couldn't move anything" in result.message and (o.desktop / "a.pdf").exists()


def test_a_missing_move_log_means_nothing_moves(tmp_path, monkeypatch):
    o = Org(tmp_path, monkeypatch)
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, so a log can't be made inside it")
    o.service.movelog = MoveLog(blocker / "moves.jsonl")
    make_file(o.desktop / "a.pdf")
    result = o.approve(o.run())
    assert result.status == "failed" and "move log" in result.message and (o.desktop / "a.pdf").exists()
    assert not (o.desktop / "Documents").exists()


def test_every_move_is_logged_before_and_after(o):
    make_file(o.desktop / "Documents" / "r.pdf", "old"); make_file(o.desktop / "r.pdf", "new"); make_file(o.desktop / "p.png")
    out = o.run()
    o.approve(out)
    events = o.movelog.read()
    batch = out.decision.args["plan_id"]
    assert {e["batch"] for e in events} == {batch}
    assert [e["path"] for e in events if e["event"] == "mkdir"] == [str(o.desktop / "Images")]
    moves = [e for e in events if e["event"] == "move"]
    assert [(e["status"], Path(e["src"]).name) for e in moves] == [("attempt", "p.png"), ("moved", "p.png"), ("attempt", "r.pdf"), ("moved", "r.pdf")]
    assert moves[3]["dst"] == str(o.desktop / "Documents" / f"r {STAMP}.pdf")
    assert all(e["at"] for e in events)
    assert events.index(next(e for e in events if e["event"] == "mkdir")) < events.index(moves[0])      # the folder is logged first


def test_failed_and_skipped_moves_are_logged_as_such(o, monkeypatch):
    make_file(o.desktop / "a.pdf"); make_file(o.desktop / "b.pdf")
    out = o.run()
    real = mover.safe_move
    monkeypatch.setattr(mover, "safe_move", lambda s, d: (_ for _ in ()).throw(PermissionError(errno.EACCES, "Permission denied")) if s.name == "a.pdf" else real(s, d))
    result = o.approve(out)
    statuses = [(Path(e["src"]).name, e["status"]) for e in o.move_events()]
    assert statuses == [("a.pdf", "attempt"), ("a.pdf", "failed"), ("b.pdf", "attempt"), ("b.pdf", "moved")]
    assert result.status == "done" and "1 stayed where they were" in result.message and (o.desktop / "a.pdf").exists()


def test_the_log_is_trimmed_not_unbounded(tmp_path):
    log = MoveLog(tmp_path / "m.jsonl", max_bytes=500)
    for i in range(60):
        log.write({"event": "move", "n": i, "pad": "x" * 40})
    assert (tmp_path / "m.jsonl.1").exists() and (tmp_path / "m.jsonl").stat().st_size < 3000
    assert log.read()[-1]["n"] == 59


def test_the_audit_log_records_the_decision_and_the_result_but_not_file_contents(o):
    make_file(o.desktop / "secret plans.pdf", "TOP SECRET CONTENTS")
    out = o.run()
    o.approve(out)
    audit = o.audit.read_text()
    events = [json.loads(line) for line in audit.splitlines()]
    assert [e["stage"] for e in events] == ["evaluate", "confirm", "execute"] and events[0]["outcome"] == "confirm"
    assert events[2]["status"] == "done" and out.decision.args["plan_id"] in audit and "TOP SECRET" not in audit


# ---- trusted personal rules ----------------------------------------------------------------------------------

def rule(**kw):
    return {"name": "downloads-pictures", "folder": "~/Downloads", "categories": ["Images", "Media"], "max_files": 3, **kw}


def test_without_a_rule_the_user_is_always_asked(o):
    make_file(o.downloads / "a.png")
    assert o.run(folder="Downloads").decision.outcome is Outcome.CONFIRM and o.move_events() == []


def test_a_trusted_rule_the_user_wrote_allows_the_move_without_asking(tmp_path, monkeypatch):
    o = Org(tmp_path, monkeypatch, trusted_rules=[rule()])
    make_file(o.downloads / "a.png", "img"); make_file(o.downloads / "b.mp3", "song")
    out = o.run(folder="Downloads")
    assert out.decision.outcome is Outcome.ALLOW and out.decision.tool == "organize_desktop_trusted" and out.decision.args["rule"] == "downloads-pictures"
    assert out.result.status == "done" and out.result.items[0] == "Trusted rule “downloads-pictures”: moved without asking."
    assert (o.downloads / "Images" / "a.png").read_text() == "img" and (o.downloads / "Media" / "b.mp3").exists()
    assert [e["status"] for e in o.move_events()] == ["attempt", "moved"] * 2                                   # and it is logged
    assert [json.loads(l)["stage"] for l in o.audit.read_text().splitlines()] == ["evaluate", "execute"]


@pytest.mark.parametrize("setup", ["other category", "too many", "other folder", "custom destination", "truncated"])
def test_a_rule_only_covers_what_it_says(tmp_path, monkeypatch, setup):
    o = Org(tmp_path, monkeypatch, trusted_rules=[rule()], max_files=10 if setup != "truncated" else 3)
    args = {"folder": "Downloads"}
    if setup == "other category":
        make_file(o.downloads / "a.png"); make_file(o.downloads / "report.pdf")
    elif setup == "too many":
        for i in range(4): make_file(o.downloads / f"{i}.png")
    elif setup == "other folder":
        make_file(o.desktop / "a.png"); args = {"folder": "Desktop"}
    elif setup == "custom destination":
        make_file(o.downloads / "a.png"); args = {"folder": "Downloads", "only": "images", "into": "holiday"}
    else:
        for i in range(5): make_file(o.downloads / f"{i}.png")
    out = o.run(**args)
    assert out.decision.outcome is Outcome.CONFIRM and out.result is None and o.move_events() == [], setup


def test_a_trusted_rule_does_not_defeat_a_dry_run(tmp_path, monkeypatch):
    o = Org(tmp_path, monkeypatch, trusted_rules=[rule()])
    make_file(o.downloads / "a.png")
    out = o.pipeline.run("organize_desktop", {"folder": "Downloads"}, True, dry_run=True)
    assert out.decision.outcome is Outcome.DRY_RUN and "trusted rule “downloads-pictures”" in out.decision.message and (o.downloads / "a.png").exists()


def test_a_rule_without_categories_covers_everything_in_that_folder(tmp_path, monkeypatch):
    o = Org(tmp_path, monkeypatch, trusted_rules=[{"name": "all", "folder": "~/Downloads"}])
    make_file(o.downloads / "a.png"); make_file(o.downloads / "b.pdf")
    assert o.run(folder="Downloads").decision.outcome is Outcome.ALLOW


@pytest.mark.parametrize("bad", [{"folder": "~/Downloads"}, {"name": "x"}, {"name": "x", "folder": "~/Downloads", "categories": ["Bogus"]},
                                 {"name": "x", "folder": "~/Downloads", "max_files": 0}, {"name": "x", "folder": "~/Downloads", "max_files": "many"},
                                 "not a mapping", None])
def test_a_broken_rule_is_ignored_not_trusted(tmp_path, monkeypatch, bad):
    o = Org(tmp_path, monkeypatch, trusted_rules=[bad])
    assert o.service.rules == []
    make_file(o.downloads / "a.png")
    assert o.run(folder="Downloads").decision.outcome is Outcome.CONFIRM


def test_the_model_cannot_make_up_a_rule(o):
    make_file(o.downloads / "a.png")
    out = o.run(folder="Downloads", rule="everything", plan_id="0123456789ab", count=1, trusted=True)
    assert out.decision.outcome is Outcome.CONFIRM and "rule" not in out.decision.args and o.move_events() == []
    guard = GuardrailManager(POLICY)
    assert guard.evaluate(ToolCall("organize_desktop_trusted", {"folder": str(o.downloads)}, False)).outcome is Outcome.BLOCK      # needs a rule name


def test_a_rule_still_cannot_reach_a_folder_that_is_not_allowed(tmp_path, monkeypatch):
    o = Org(tmp_path, monkeypatch, trusted_rules=[{"name": "docs", "folder": "~/Documents"}])
    make_file(o.home / "Documents" / "a.pdf")
    out = o.run(folder="Documents")
    assert out.decision is None and "I only organize these folders" in out.result.message


# ---- undo -----------------------------------------------------------------------------------------------------

def test_undo_puts_everything_back_and_removes_the_folders_it_made(o):
    messy_desktop(o)
    before = tree(o.desktop)
    o.approve(o.run())
    report = undo(o.movelog)
    assert len(report.restored) == 9 and report.skipped == [] and len(report.removed_dirs) == 6
    assert tree(o.desktop) == before
    assert undo(o.movelog).restored == []                                  # nothing left to undo


def test_undo_only_removes_empty_folders_and_never_overwrites(o):
    make_file(o.desktop / "a.pdf", "a"); make_file(o.desktop / "b.pdf", "b")
    o.approve(o.run())
    make_file(o.desktop / "Documents" / "mine.pdf", "added later")         # the user put something in there
    make_file(o.desktop / "a.pdf", "a new a.pdf made after the move")
    report = undo(o.movelog)
    assert [name for name, _ in report.skipped] == ["a.pdf"] and "already there" in report.skipped[0][1]
    assert (o.desktop / "a.pdf").read_text() == "a new a.pdf made after the move" and (o.desktop / "Documents" / "a.pdf").read_text() == "a"
    assert (o.desktop / "b.pdf").read_text() == "b" and (o.desktop / "Documents" / "mine.pdf").exists() and report.removed_dirs == []


def test_undo_goes_back_one_batch_at_a_time(o):
    make_file(o.desktop / "first.pdf")
    o.approve(o.run())
    make_file(o.desktop / "second.pdf")
    o.approve(o.run())
    report = undo(o.movelog)
    assert [Path(dst).name for _, dst in report.restored] == ["second.pdf"] and (o.desktop / "Documents" / "first.pdf").exists()
    assert [Path(dst).name for _, dst in undo(o.movelog).restored] == ["first.pdf"]


def test_undo_with_nothing_to_undo(tmp_path):
    report = undo(MoveLog(tmp_path / "none.jsonl"))
    assert report.batch == "" and report.restored == []


def test_the_undo_script_runs(o, tmp_path, monkeypatch, capsys):
    import importlib.util
    make_file(o.desktop / "a.pdf")
    o.approve(o.run())
    spec = importlib.util.spec_from_file_location("organize_undo", ROOT / "scripts" / "organize_undo.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    monkeypatch.setattr(script, "MoveLog", lambda path: o.movelog)
    monkeypatch.setattr("sys.argv", ["organize_undo.py", "--yes"])
    assert script.main() == 0
    assert (o.desktop / "a.pdf").exists() and "Put back 1 file" in capsys.readouterr().out


# ---- switched off, routing, API -----------------------------------------------------------------------------

def test_organizing_can_be_turned_off(tmp_path, monkeypatch):
    o = Org(tmp_path, monkeypatch, enabled=False)
    make_file(o.desktop / "a.pdf")
    out = o.run()
    assert out.result.status == "not_available" and "organizer.enabled" in out.result.message
    assert o.service.execute("organize_desktop", {}).status == "not_available" and (o.desktop / "a.pdf").exists()


def test_the_tool_and_the_prompt(o):
    spec = build_registry().get("organize_desktop")
    assert [p.name for p in spec.params] == ["folder", "only", "into"] and not any(p.required for p in spec.params) and spec.requires_confirmation
    assert "organize_desktop" in ORGANIZER_TOOLS and "organize_desktop_trusted" in ORGANIZER_TOOLS
    system = build_messages(build_registry(), "hi", NOW)[0]["content"]
    assert "sort my downloads into folders" in system and "organize_desktop_trusted" not in system and len(system) < 5200


def router(tmp_path, monkeypatch, **options):
    o = Org(tmp_path, monkeypatch, **options)
    world = make_world(tmp_path / "w")
    choices = ChoiceStore()
    world.service.choices = choices
    return ActionRouter(world.service, BrowserService(FakeWorker(), choices=choices), choices, None, o.service), o


def test_the_router_sends_organize_calls_to_the_organizer(tmp_path, monkeypatch):
    r, o = router(tmp_path, monkeypatch, trusted_rules=[rule()])
    assert r.status()["organizer"]["folders"] == ["~/Desktop", "~/Downloads"] and r.status()["organizer"]["trusted_rules"] == ["downloads-pictures"]
    make_file(o.desktop / "a.pdf")
    assert r.prepare("organize_desktop", {}).kind == "ready"
    assert r.execute("organize_desktop_trusted", {}).message.startswith("That preview has expired")
    bare = ActionRouter(make_world(tmp_path / "w2").service, BrowserService(FakeWorker()), ChoiceStore())
    assert bare.execute("organize_desktop", {}).status == "not_available" and "organizer" not in bare.status()


def test_building_the_assistant_includes_the_organizer(tmp_path):
    r = build_router({"browser": {"enabled": False}, "automation": {"hammerspoon": False},
                      "organizer": {"folders": ["~/Desktop"], "trusted_rules": [{"name": "r", "folder": "~/Desktop"}]}}, tmp_path)
    assert isinstance(r.organizer, OrganizerService) and r.organizer.files is r.desktop.files
    assert r.organizer.movelog.path == tmp_path / "data" / "logs" / "moves.jsonl" and not r.organizer.movelog.path.exists()


def api(tmp_path, monkeypatch, model_output):
    r, o = router(tmp_path, monkeypatch)

    def handler(request):
        if request.url.path == "/api/chat":
            return httpx.Response(200, json={"message": {"role": "assistant", "content": model_output}})
        return httpx.Response(200, json={"version": "0", "models": [{"name": "m"}]})

    llm = OllamaClient("http://127.0.0.1:11434", "m", transport=httpx.MockTransport(handler))
    app = create_app({"ollama": {}, "router": {"max_retries": 0}}, llm, guard=o.guard, actions=r)
    return TestClient(app), o


def call(**args):
    return json.dumps({"tool": "organize_desktop", "args": args, "requires_confirmation": True})


def test_the_api_previews_then_moves_after_confirm(tmp_path, monkeypatch):
    client, o = api(tmp_path, monkeypatch, call())
    make_file(o.desktop / "a.pdf", "doc"); make_file(o.desktop / "b.png", "img")
    with client as c:
        asked = c.post("/route", json={"text": "organize my desktop"}).json()
        assert asked["guard"]["outcome"] == "confirm" and asked["result"] is None
        assert asked["guard"]["message"].startswith("In ~/Desktop I would move 2 files:") and (o.desktop / "a.pdf").exists()
        done = c.post("/confirm", json={"confirmation_id": asked["guard"]["confirmation_id"], "approved": True}).json()
    assert done["result"]["status"] == "done" and (o.desktop / "Documents" / "a.pdf").exists() and (o.desktop / "Images" / "b.png").exists()


def test_the_api_dry_run_changes_nothing(tmp_path, monkeypatch):
    client, o = api(tmp_path, monkeypatch, call())
    make_file(o.desktop / "a.pdf")
    with client as c:
        body = c.post("/route", json={"text": "organize my desktop", "dry_run": True}).json()
    assert body["guard"]["outcome"] == "dry_run" and body["guard"]["message"].startswith("Dry run. In ~/Desktop") and (o.desktop / "a.pdf").exists()


def test_the_api_declining_changes_nothing(tmp_path, monkeypatch):
    client, o = api(tmp_path, monkeypatch, call(folder="Desktop"))
    make_file(o.desktop / "a.pdf")
    with client as c:
        asked = c.post("/route", json={"text": "organize my desktop"}).json()
        no = c.post("/confirm", json={"confirmation_id": asked["guard"]["confirmation_id"], "approved": False}).json()
    assert no["outcome"] == "block" and no["result"] is None and (o.desktop / "a.pdf").exists() and o.move_events() == []


def test_the_preview_is_plain_text_for_a_messy_folder(o):
    """What the confirmation card shows, spelled out once so a change to it is deliberate."""
    make_file(o.desktop / "Documents" / "report.pdf", "old")
    make_file(o.desktop / "report.pdf"); make_file(o.desktop / "photo.jpg"); make_file(o.desktop / "song.mp3")
    (o.desktop / "Stuff").mkdir(); make_file(o.desktop / ".DS_Store")
    plan = build_plan(o.desktop, NOW)
    assert preview_text(plan, o.home) == "\n".join([
        "In ~/Desktop I would move 3 files:",
        "• Documents, 1: report.pdf",
        "• Media (new folder), 1: song.mp3",
        "• Images (new folder), 1: photo.jpg",
        f"Name already taken (1), so a timestamp is added: report.pdf → report {STAMP}.pdf",
        "Left alone: 1 hidden or system files, 2 folders",
    ])
