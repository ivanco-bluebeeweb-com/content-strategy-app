"""Content Strategy app — decides what content to create, why, for which
site/audience, and hands off a structured brief downstream to article
writing and the future Image/Media app.

Boundaries (see notes "System architecture — Content Strategy app MVP"):
- does NOT publish to WordPress (wp-site-connector's job)
- does NOT generate/edit images (future Image/Media app's job)
- does NOT own technical SEO crawling (SEO Audit Engine's job)
"""
from __future__ import annotations

from imperal_sdk import Extension, ChatExtension

ext = Extension(
    "content-strategy-app",
    version="0.1.0",
    display_name="Content Strategy",
    description=(
        "Plans what to write next for your sites. Discovers content opportunities "
        "from Google Search Console data, clusters them into topics, generates "
        "structured article briefs (with image requirements for downstream "
        "generation), and tracks each idea through an editorial queue from idea "
        "to published."
    ),
    icon="icon.svg",
    actions_explicit=True,
)

chat = ChatExtension(
    ext,
    tool_name="content-strategy-app",
    description=(
        "Content strategy assistant — discovers article opportunities from "
        "search data, builds article briefs, and tracks editorial queue status "
        "per site."
    ),
    system_prompt=(
        "You help the user decide what content to create next for their sites. "
        "You surface opportunities from search data, turn the best ones into "
        "structured briefs, and track their status through an editorial queue. "
        "You do not write full articles, generate images, or publish to "
        "WordPress yourself — you hand off clean briefs for those next steps."
    ),
)

# Side-effect imports: register decorators
from . import handlers_chat     # noqa: F401,E402
from . import handlers_panel    # noqa: F401,E402
