"""LangGraph checkpointer selection.

Prefers durable local SQLite; falls back to the in-memory saver when the optional
package is unavailable, since our own SQLite persistence already keeps runs,
exports and audit events across restarts.
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from sye.observability.logging import get_logger

logger = get_logger("sye.checkpointer")


async def build_checkpointer(stack: AsyncExitStack, path: str | Path) -> tuple[Any, str]:
    """Return ``(checkpointer, kind)``; the exit stack owns the connection."""
    try:
        import aiosqlite  # noqa: F401
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    except Exception as exc:  # pragma: no cover - depends on optional extras
        logger.debug("SQLite checkpointer unavailable (%s); using in-memory saver", exc)
    else:
        try:
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            saver = await stack.enter_async_context(AsyncSqliteSaver.from_conn_string(str(target)))
            return saver, "sqlite"
        except Exception as exc:  # pragma: no cover
            logger.warning("falling back to in-memory checkpointer: %s", exc)

    from langgraph.checkpoint.memory import InMemorySaver

    return InMemorySaver(), "memory"
