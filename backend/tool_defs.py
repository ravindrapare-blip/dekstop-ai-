"""The tool list the model may choose from. Add a tool here to make it selectable."""
from backend.tools import Category, Param, ToolRegistry, ToolSpec

REPLY = "reply"


def build_registry() -> ToolRegistry:
    r = ToolRegistry()
    add = r.register

    # app tools
    add(ToolSpec("open_app", Category.APP, "Open an application",
                 (Param("app_name", description="e.g. Google Chrome"),)))
    add(ToolSpec("focus_app", Category.APP, "Bring a running app to the front",
                 (Param("app_name"),)))
    add(ToolSpec("close_app", Category.APP, "Quit an application",
                 (Param("app_name"),), requires_confirmation=True))

    # file tools (no delete, rename or move tools exist)
    add(ToolSpec("open_file", Category.FILE, "Open a file by path or name",
                 (Param("path_or_name"),)))
    add(ToolSpec("open_folder", Category.FILE, "Open a folder in Finder",
                 (Param("path_or_name", description="e.g. Downloads"),)))
    add(ToolSpec("search_files", Category.FILE, "Search common folders for files",
                 (Param("query"),)))
    add(ToolSpec("get_recent_files", Category.FILE, "List recently used files"))
    add(ToolSpec("organize_desktop", Category.FILE, "Plan how to sort Desktop files into folders",
                 requires_confirmation=True))

    # browser tools
    add(ToolSpec("open_url", Category.BROWSER, "Open a web address in Chrome",
                 (Param("url"),)))
    add(ToolSpec("search_chrome", Category.BROWSER, "Search the web in Chrome",
                 (Param("query"),)))
    add(ToolSpec("new_tab", Category.BROWSER, "Open a new Chrome tab"))
    add(ToolSpec("switch_tab", Category.BROWSER, "Switch to a Chrome tab by number or title",
                 (Param("tab", description="tab number or part of its title"),)))
    add(ToolSpec("close_tab", Category.BROWSER, "Close a Chrome tab",
                 (Param("index", "int", required=False),), requires_confirmation=True))

    # reminder tools
    add(ToolSpec("create_reminder", Category.REMINDER, "Create a reminder",
                 (Param("text"), Param("due_datetime", description="ISO 8601 local time"))))
    add(ToolSpec("list_reminders", Category.REMINDER, "List reminders"))
    add(ToolSpec("delete_reminder", Category.REMINDER, "Delete a reminder by id",
                 (Param("id", "int"),), requires_confirmation=True))

    # note tools
    add(ToolSpec("save_note", Category.NOTE, "Save a note",
                 (Param("title"), Param("content"))))
    add(ToolSpec("list_notes", Category.NOTE, "List saved notes"))

    # system tools
    add(ToolSpec(REPLY, Category.SYSTEM,
                 "Answer in text, ask a question, or decline. Use when no other tool fits",
                 (Param("text"),)))
    add(ToolSpec("get_time", Category.SYSTEM, "Get the current date and time"))
    add(ToolSpec("get_battery", Category.SYSTEM, "Get the battery level"))
    add(ToolSpec("set_volume", Category.SYSTEM, "Set the output volume from 0 to 100",
                 (Param("level", "int"),), requires_confirmation=True))
    return r
