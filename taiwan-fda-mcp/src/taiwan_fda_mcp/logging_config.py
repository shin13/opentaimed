# path: src/taiwan_fda_mcp/logging_config.py
# brief: Configure JSON structured logging once at app startup.

import logging
import sys

from pythonjsonlogger import jsonlogger

_state: dict[str, bool] = {"configured": False}


def configure_logging(level: str = "INFO") -> None:
    """Install a single JSON formatter on the root logger.

    Logs go to stderr — stdout is reserved for the MCP stdio protocol.
    Idempotent — safe to call multiple times.
    """
    if _state["configured"]:
        return

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        jsonlogger.JsonFormatter(
            "%(asctime)s %(name)s %(levelname)s %(message)s",
            json_ensure_ascii=False,
        )
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    _state["configured"] = True
