# path: src/taiwan_fda_mcp/logging_config.py
# brief: Configure JSON structured logging once at app startup.

import logging
import sys

from pythonjsonlogger.json import JsonFormatter

_CONFIGURED = False


def configure_logging(level: str = "INFO") -> None:
    """Install a single JSON formatter on the root logger.

    Logs go to stderr — stdout is reserved for the MCP stdio protocol.
    Idempotent — safe to call multiple times.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        JsonFormatter(
            "%(asctime)s %(name)s %(levelname)s %(message)s",
            json_ensure_ascii=False,
        )
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    _CONFIGURED = True
