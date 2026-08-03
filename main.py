"""DIAGNOSTIC MINIMAL BUILD — everything inline in one file, no side-effect
imports across modules, to isolate whether the multi-file split itself is
what the deploy validator chokes on.
"""
from __future__ import annotations

from pydantic import BaseModel, Field
from imperal_sdk import Extension, ChatExtension, ActionResult, ui

ext = Extension(
    "content-strategy-app",
    version="0.1.0",
    display_name="Content Strategy",
    description=(
        "Plans what to write next for your sites. Discovers content opportunities "
        "from Google Search Console data, clusters them into topics, generates "
        "structured article briefs, and tracks each idea through an editorial "
        "queue from idea to published."
    ),
    icon="icon.svg",
    actions_explicit=True,
    capabilities=["content_strategy:read", "content_strategy:write"],
)

chat = ChatExtension(
    ext,
    tool_name="content-strategy-app",
    description="Content strategy assistant — diagnostic minimal build.",
    system_prompt="Diagnostic minimal build.",
)


class PingParams(BaseModel):
    message: str = Field("", description="Optional message to echo back")


class PingResult(BaseModel):
    echo: str = ""


@chat.function(
    "ping",
    description="Diagnostic health-check tool — echoes back a message.",
    action_type="read",
    data_model=PingResult,
)
async def ping(ctx, params: PingParams) -> ActionResult:
    """Echo back a message — used to verify the extension loads correctly."""
    return ActionResult.success(
        PingResult(echo=params.message or "pong"),
        summary="pong",
    )


@ext.health_check
async def health_check(ctx) -> bool:
    """Diagnostic health check — always healthy, no external deps."""
    return True


@ext.panel(
    "queue",
    slot="left",
    title="Editorial Queue",
    icon="📋",
)
async def queue_panel(ctx, **kwargs) -> object:
    return ui.Empty(message="Diagnostic minimal build — no data yet.", icon="📋")
