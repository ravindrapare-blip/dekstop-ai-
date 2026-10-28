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

    # browser tools (Chrome, driven by browser/; page content is never given back to the model)
    add(ToolSpec("open_chrome", Category.BROWSER, "Open Chrome (its own window and profile)"))
    add(ToolSpec("open_url", Category.BROWSER, "Open a web address or a well-known site such as Gmail",
                 (Param("url", description="e.g. example.com, or gmail"),)))
    add(ToolSpec("search_chrome", Category.BROWSER, "Search the web in Chrome",
                 (Param("query"),)))
    add(ToolSpec("new_tab", Category.BROWSER, "Open a new Chrome tab, optionally at an address",
                 (Param("url", required=False),)))
    add(ToolSpec("switch_tab", Category.BROWSER, "Switch to a Chrome tab by number or title",
                 (Param("tab", description="tab number or part of its title"),)))
    add(ToolSpec("close_tab", Category.BROWSER, "Close a Chrome tab (the current one if no number)",
                 (Param("index", "int", required=False),), requires_confirmation=True))
    add(ToolSpec("click_text", Category.BROWSER, "Click a button or link on the page by its visible text",
                 (Param("text"),)))
    add(ToolSpec("fill_field", Category.BROWSER, "Type text into a box on the page (never passwords or card numbers)",
                 (Param("label", description="the box's label or placeholder"), Param("value"))))
    add(ToolSpec("scroll_page", Category.BROWSER, "Scroll the page: up, down, top or bottom",
                 (Param("direction"),)))
    add(ToolSpec("wait_for_page_load", Category.BROWSER, "Wait until the page has finished loading"))
    add(ToolSpec("get_page_title", Category.BROWSER, "Read the title of the current page"))
    add(ToolSpec("get_current_url", Category.BROWSER, "Read the address of the current page"))
    add(ToolSpec("play_youtube_video", Category.BROWSER,
                 "Play a video on YouTube: the first result for the query, or the one on the current page",
                 (Param("query", required=False),)))

    # reminders, tasks, notes and memory (one local SQLite file)
    add(ToolSpec("create_reminder", Category.REMINDER, "Create a reminder that pops up at a time",
                 (Param("text"), Param("due_datetime", description="local time, ISO 8601"))))
    add(ToolSpec("list_reminders", Category.REMINDER, "List reminders"))
    add(ToolSpec("delete_reminder", Category.REMINDER, "Delete a reminder",
                 (Param("id", "ref", description="its number, or some of its words"),), requires_confirmation=True))
    add(ToolSpec("create_task", Category.TASK, "Add a to-do task",
                 (Param("title"), Param("details", required=False), Param("due_date", required=False, description="YYYY-MM-DD"))))
    add(ToolSpec("list_tasks", Category.TASK, "List open tasks"))
    add(ToolSpec("complete_task", Category.TASK, "Mark a task as done",
                 (Param("id", "ref", description="its number, or some of its words"),)))
    add(ToolSpec("plan_my_day", Category.TASK, "Plan today from reminders and tasks"))
    add(ToolSpec("save_note", Category.NOTE, "Save a note (added to a note with the same title)",
                 (Param("title"), Param("content"))))
    add(ToolSpec("list_notes", Category.NOTE, "List saved notes"))
    add(ToolSpec("get_note", Category.NOTE, "Read a saved note",
                 (Param("title", description="its title or number"),)))
    add(ToolSpec("set_preference", Category.MEMORY, "Remember a preference or fact about the user",
                 (Param("key", description="a few words, e.g. preferred browser"), Param("value"))))
    add(ToolSpec("get_preference", Category.MEMORY, "Recall a remembered preference",
                 (Param("key"),)))

    # system tools
    add(ToolSpec(REPLY, Category.SYSTEM,
                 "Answer in text, ask a question, or decline. Use when no other tool fits",
                 (Param("text"),)))
    add(ToolSpec("get_time", Category.SYSTEM, "Get the current date and time"))
    add(ToolSpec("get_battery", Category.SYSTEM, "Get the battery level"))
    add(ToolSpec("set_volume", Category.SYSTEM, "Set the output volume from 0 to 100",
                 (Param("level", "int"),), requires_confirmation=True))
    return r
