"""Stage B instrumented pilot on a controlled reference agent adapter.

Synthetic only. No real purchase, no live money, no third-party network call,
and no live coverage. The run mode is ``PILOT``; ``LIVE`` is not a reachable
state in this package.
"""

MODE = "PILOT"
ALLOWED_MODES = ("DEMO", "PILOT")

__all__ = ["MODE", "ALLOWED_MODES"]
