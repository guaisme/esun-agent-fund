from __future__ import annotations

import hashlib
import json
import logging
import random
import time
from pathlib import Path
from typing import Any

import requests

from .providers.base import ProviderError
from .time_safety import now_iso

LOG = logging.getLogger(__name__)


class HttpClient:
    """Sequential, bounded retries; immutable response archives contain no secrets."""

    def __init__(
        self,
        raw_dir: Path,
        timeout: float = 20,
        max_retries: int = 3,
        interval: float = 2,
        initial_backoff: float = 2,
        max_backoff: float = 15,
        refresh: bool = False,
    ) -> None:
        if timeout <= 0 or max_retries < 0 or interval < 0:
            raise ValueError("Invalid HTTP policy")
        self.raw_dir = Path(raw_dir)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.timeout, self.max_retries, self.interval = timeout, max_retries, interval
        self.initial_backoff, self.max_backoff = initial_backoff, max_backoff
        self.refresh, self.last_request = refresh, 0.0
        self.session = requests.Session()
        self.cache: dict[str, tuple[dict, str, str]] = {}

    def get(
        self, url: str, params: dict[str, Any], headers: dict[str, str] | None = None
    ) -> tuple[dict, str, str]:
        # Secrets only travel in headers; never serialize headers or exception text.
        if any(k.lower() in {"token", "key", "api_key"} for k in params):
            raise ValueError("Credentials must be in headers")
        key = hashlib.sha256(json.dumps([url, params], sort_keys=True).encode()).hexdigest()
        if key in self.cache:
            return self.cache[key]
        pointer = self.raw_dir / f"{key}.latest.json"
        if pointer.exists() and not self.refresh:
            envelope = json.loads(pointer.read_text(encoding="utf-8"))
            self.cache[key] = (envelope["payload"], envelope["fetched_at"], envelope["archive"])
            return self.cache[key]
        for attempt in range(self.max_retries + 1):
            time.sleep(max(0, self.interval - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                response = self.session.get(
                    url, params=params, headers=headers, timeout=self.timeout
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ProviderError("Expected JSON object")
                # FinMind can report a rate-limit through an HTTP-200 JSON body.
                status = payload.get("status")
                if isinstance(status, int) and status != 200:
                    error = requests.HTTPError()
                    error.response = requests.Response()
                    error.response.status_code = status
                    raise error
                fetched = now_iso()
                archive = self.raw_dir / f"{key}.{time.time_ns()}.json"
                envelope = dict(
                    url=url,
                    params=params,
                    payload=payload,
                    fetched_at=fetched,
                    archive=str(archive),
                )
                archive.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
                temp = pointer.with_suffix(".tmp")
                temp.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
                temp.replace(pointer)
                self.cache[key] = payload, fetched, str(archive)
                return self.cache[key]
            except (requests.RequestException, ValueError) as error:
                code = getattr(getattr(error, "response", None), "status_code", None)
                retryable = code in {429, 500, 502, 503, 504} or isinstance(
                    error, (requests.Timeout, requests.ConnectionError)
                )
                LOG.warning(
                    "provider_request_failed type=%s code=%s attempt=%s",
                    type(error).__name__,
                    code,
                    attempt + 1,
                )
                if not retryable or attempt == self.max_retries:
                    raise ProviderError(
                        f"HTTP request failed: {type(error).__name__}, status={code}"
                    ) from None
                cap = min(self.max_backoff, self.initial_backoff * 2**attempt)
                time.sleep(random.uniform(cap / 2, cap))
        raise AssertionError("Unreachable")
