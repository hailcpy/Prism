from __future__ import annotations

import logging
import os
import re
from typing import Any, Protocol, runtime_checkable

log = logging.getLogger("ingestion-api.redaction")


@runtime_checkable
class Redactor(Protocol):
    def redact_text(self, value: str | None) -> str | None: ...

    def redact_json(self, value: Any) -> Any: ...


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


class PresidioRedactor:
    """Microsoft Presidio-backed redactor.

    Entities are mapped to the same `[EMAIL]` / `[PHONE]` / `[SSN]` / `[CARD]`
    placeholders the regex redactor emits, so downstream consumers don't care
    which implementation produced the value.
    """

    _ENTITY_REPLACEMENTS: dict[str, str] = {
        "EMAIL_ADDRESS": "[EMAIL]",
        "PHONE_NUMBER": "[PHONE]",
        "US_SSN": "[SSN]",
        "CREDIT_CARD": "[CARD]",
        "IBAN_CODE": "[IBAN]",
        "IP_ADDRESS": "[IP]",
        "US_PASSPORT": "[PASSPORT]",
        "US_DRIVER_LICENSE": "[DRIVER_LICENSE]",
        "PERSON": "[PERSON]",
        "LOCATION": "[LOCATION]",
    }

    def __init__(self, language: str = "en", entities: list[str] | None = None) -> None:
        try:
            from presidio_analyzer import AnalyzerEngine  # ty: ignore[unresolved-import]
            from presidio_anonymizer import AnonymizerEngine  # ty: ignore[unresolved-import]
            from presidio_anonymizer.entities import (  # ty: ignore[unresolved-import]
                OperatorConfig,
            )
        except ImportError as exc:
            raise RuntimeError(
                "PresidioRedactor requires the 'presidio' extras. "
                "Install with: uv pip install 'ingestion-api[presidio]' "
                "(or pip install presidio-analyzer presidio-anonymizer)."
            ) from exc

        self._operator_config_cls = OperatorConfig
        self._analyzer = AnalyzerEngine()
        self._anonymizer = AnonymizerEngine()
        self._language = language
        self._entities = entities or list(self._ENTITY_REPLACEMENTS.keys())
        self._operators = {
            entity: OperatorConfig("replace", {"new_value": placeholder})
            for entity, placeholder in self._ENTITY_REPLACEMENTS.items()
        }

    def redact_text(self, value: str | None) -> str | None:
        if value is None:
            return None
        if not value:
            return value
        results = self._analyzer.analyze(
            text=value, entities=self._entities, language=self._language
        )
        if not results:
            return value
        anonymized = self._anonymizer.anonymize(
            text=value, analyzer_results=results, operators=self._operators
        )
        return anonymized.text

    def redact_json(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.redact_text(value)
        if isinstance(value, list):
            return [self.redact_json(item) for item in value]
        if isinstance(value, dict):
            return {key: self.redact_json(item) for key, item in value.items()}
        return value


def build_redactor(name: str | None = None) -> Redactor:
    """Construct a redactor from `PRISM_REDACTOR` (or the explicit `name`).

    Values: `regex` (default), `presidio`. Unknown values fall back to regex
    with a warning so a misconfigured deploy still redacts something.
    """
    selected = (name or os.getenv("PRISM_REDACTOR", "regex")).strip().lower()
    if selected == "presidio":
        return PresidioRedactor()
    if selected != "regex":
        log.warning("unknown PRISM_REDACTOR=%r; falling back to regex", selected)
    return RegexRedactor()
