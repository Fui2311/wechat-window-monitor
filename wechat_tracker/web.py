"""Stable console entry points; implementation lives in console/."""
from .console.handler import WebControlHandler
from .console.server import WebControlServer, run_web
from .console.discovery import _list_services
from .console.presentation import _delivery_info, _sanitize_service
from .console.queries import _captured_messages, _list_databases, _safe_db_connect

__all__ = ["WebControlHandler", "WebControlServer", "run_web"]
