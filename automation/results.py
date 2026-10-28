"""The shapes shared by everything that does actions (the desktop service and the browser service)."""
from dataclasses import dataclass, field

# done           it worked
# failed         it did not work; the message says why
# not_available  the tool needed (Hammerspoon, Chrome, ...) is not set up; the message says how to fix it
# choose         several things matched; the user must pick one
# needs_login    the page is asking the user to sign in; the assistant stops and waits
STATUSES = ("done", "failed", "not_available", "choose", "needs_login")


@dataclass
class ActionResult:
    status: str
    message: str
    items: list = field(default_factory=list)    # lines for lists, such as search results or the steps taken
    choice: dict | None = None                   # for "choose": {choice_id, prompt, options}
    via: str = ""

    def to_dict(self) -> dict:
        return {"status": self.status, "message": self.message, "items": self.items,
                "choice": self.choice, "via": self.via}


@dataclass
class Prepared:
    kind: str                                    # "ready", "choose" or "failed"
    args: dict | None = None                     # the call's arguments with names replaced by real targets
    result: ActionResult | None = None           # for "choose" and "failed"
