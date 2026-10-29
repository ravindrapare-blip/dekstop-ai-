"""Safe desktop organization: a preview first, a question before anything moves, nothing overwritten, every move logged.

This package never decides what is allowed: `backend.pipeline` asks the safety layer, and shows you the preview.
"""
from organizer.service import ORGANIZER_TOOLS, OrganizerService

__all__ = ["ORGANIZER_TOOLS", "OrganizerService"]
