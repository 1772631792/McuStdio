"""MCU Live Watch Studio application package."""

__all__ = ["run_application"]


def run_application() -> int:
    """Start the desktop application without importing Qt at package import time."""
    from .ui import run_application as _run_application

    return _run_application()
