"""Private supervised worker; use daily-run so the lock/deadline is enforced."""

import json
import sys
from pathlib import Path
from .daily import run_worker

if __name__ == "__main__":
    report = run_worker(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
    raise SystemExit(1 if report["execution_status"] == "FAILED" else 0)
