from __future__ import annotations

import re
from typing import Any


class RegexRedactor:
    def __init__(self) -> None:
        self.patterns: tuple[tuple[re.Pattern[str], str], ...] = (
            (re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE), "[EMAIL]"),
            (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),
            (re.compile(r"\b(?:\d[ -]*?){13,19}\b"), "[CARD]"),
            (
                re.compile(r"(?<!\w)(?:\+?1[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)\d{3}[\s.-]?\d{4}(?!\w)"),
                "[PHONE]",
            ),
        )

    def redact_text(self, value: str | None) -> str | None:
        if value is None:
            return None
        redacted = value
        for pattern, replacement in self.patterns:
            redacted = pattern.sub(replacement, redacted)
        return redacted

    def redact_json(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.redact_text(value)
        if isinstance(value, list):
            return [self.redact_json(item) for item in value]
        if isinstance(value, dict):
            return {key: self.redact_json(item) for key, item in value.items()}
        return value
