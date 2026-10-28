"""Controlling Chrome. The Node worker in browser/ drives Chrome; this package finds the right target,
starts the worker when needed and turns its answers into messages. It never decides what is allowed:
`backend.pipeline` asks the safety layer first."""
from browserctl.service import BROWSER_TOOLS, BrowserService

__all__ = ["BROWSER_TOOLS", "BrowserService"]
