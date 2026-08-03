"""Deploy-required entry point. The actual Extension/ChatExtension objects
live in app.py — this module only re-exports them and triggers the
side-effect imports that register @chat.function / @ext.panel handlers.
"""
from __future__ import annotations

from .app import ext, chat  # noqa: F401

# Side-effect imports: register decorators
from . import handlers_chat     # noqa: F401,E402
from . import handlers_panel    # noqa: F401,E402
