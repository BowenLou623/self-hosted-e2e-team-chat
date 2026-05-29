"""Structured stdout events consumed by the macOS Launcher."""

import json
import sys
import time
from typing import Any


EVENT_PREFIX = "IMT_EVENT "


def emit_launcher_event(event_type: str, **payload: Any) -> None:
    """Emit one line of structured process state for the Launcher.

    The normal Python client still writes human-readable logs. The Launcher can
    detect these prefixed lines without depending on log wording.
    """
    event = {
        "type": event_type,
        "timestamp": time.time(),
        **payload,
    }
    try:
        print(f"{EVENT_PREFIX}{json.dumps(event, ensure_ascii=False, sort_keys=True)}", flush=True)
    except Exception:
        print(
            f"{EVENT_PREFIX}{json.dumps({'type': event_type, 'timestamp': time.time()}, sort_keys=True)}",
            file=sys.stdout,
            flush=True,
        )
