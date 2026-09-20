"""Silent desktop entry point for starting and opening UZGERMED."""
from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime"
LOG = RUNTIME / "desktop-launcher.log"


def run() -> int:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    with LOG.open("a", encoding="utf-8") as stream:
        previous_stdout, previous_stderr = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = stream
        try:
            print(f"[{datetime.now().isoformat(timespec='seconds')}] launcher started", flush=True)
            from deploy.open_site import main

            result = main()
            print(f"[{datetime.now().isoformat(timespec='seconds')}] launcher finished: {result}", flush=True)
            return result
        except Exception as exc:
            print(f"[{datetime.now().isoformat(timespec='seconds')}] launcher failed: {type(exc).__name__}: {exc}", flush=True)
            return 1
        finally:
            sys.stdout, sys.stderr = previous_stdout, previous_stderr


if __name__ == "__main__":
    run()
