"""Doing things on the Mac: opening, focusing and quitting apps, and opening and finding files.

Hammerspoon does the app control (its Lua side is in hammerspoon/init.lua); AppleScript and `open` are fallbacks.
This package never decides what is allowed. The safety layer does, and `backend.pipeline` makes sure
nothing here runs unless it said yes.
"""
from automation.factory import build_actions
from automation.service import ActionResult, ActionService

__all__ = ["ActionResult", "ActionService", "build_actions"]
