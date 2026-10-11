"""Source attribution checks against captured payloads, never label-only matches."""

from __future__ import annotations

import re
from urllib.parse import urlparse


def has_source_attribution(answer: str, payloads: list[dict]) -> bool:
    def normalize(value: str) -> str:
        return re.sub(r"\s+", "", value).casefold().replace("臺", "台")

    text = normalize(answer)
    for payload in payloads:
        source = payload.get("source")
        if isinstance(source, str) and len(normalize(source)) >= 4:
            if normalize(source) in text:
                return True
        link = payload.get("link")
        if isinstance(link, str) and urlparse(link).scheme == "https":
            if normalize(link) in text:
                return True
        # Common exchange abbreviations are accepted only when the corresponding
        # authority also occurs in the returned source, not for arbitrary labels.
        for names in (
            ("台灣證券交易所", "TWSE", "證交所"),
            ("證券櫃檯買賣中心", "TPEx", "櫃買中心"),
        ):
            if isinstance(source, str) and any(
                normalize(n) in normalize(source) for n in names
            ):
                if any(normalize(n) in text for n in names):
                    return True
    return False
