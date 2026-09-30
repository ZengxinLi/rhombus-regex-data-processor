"""LLM-backed natural-language rule resolution and conservative regex validation."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass

from django.conf import settings
from django.core.cache import cache


class PatternError(ValueError):
    pass


OPERATIONS = {"replace", "normalize_whitespace", "lowercase", "uppercase", "trim"}


@dataclass(frozen=True)
class ResolvedRule:
    operation: str
    regex: str | None
    source: str


def validate_regex(pattern: str) -> str:
    """Reject unsupported or high-risk expressions before Spark sees them.

    This intentionally allows a conservative subset. Python compiles the syntax,
    while the checks exclude backreferences and common nested quantifiers that can
    cause catastrophic backtracking in Java/Spark's regex engine.
    """
    if not isinstance(pattern, str) or not pattern.strip():
        raise PatternError("The generated regex is empty.")
    if len(pattern) > 512:
        raise PatternError("The generated regex is longer than the 512-character safety limit.")
    if re.search(r"\\[1-9]", pattern):
        raise PatternError("Backreferences are not allowed in generated regex patterns.")
    if re.search(r"\(\?(?:<=|<!|P<|\()", pattern):
        raise PatternError("Lookbehind and recursive constructs are not supported.")
    # Examples blocked: (a+)+, (.*)*, (?:\\w+){2,}. This is deliberately cautious.
    nested_quantifier = r"\((?:\\.|[^()])*?(?:\*|\+|\{\d+(?:,\d*)?\})(?:\\.|[^()])*?\)\s*(?:\*|\+|\{|\?)"
    if re.search(nested_quantifier, pattern):
        raise PatternError("The generated regex contains nested quantifiers and may be unsafe.")
    try:
        re.compile(pattern)
    except re.error as exc:
        raise PatternError(f"The generated regex is invalid: {exc.msg}.") from exc
    return pattern


class PatternResolver:
    cache_prefix = "llm-rule:"

    def resolve(self, request: str, requested_operation: str = "auto") -> ResolvedRule:
        if requested_operation not in OPERATIONS | {"auto"}:
            raise PatternError("Unsupported transformation operation.")
        prompt = request.strip()
        if not prompt:
            raise PatternError("Describe the pattern or transformation to apply.")

        cache_key = self._cache_key(prompt, requested_operation)
        cached = cache.get(cache_key)
        if cached:
            return self._deserialize(cached)

        if requested_operation != "auto" and requested_operation != "replace":
            resolved = ResolvedRule(operation=requested_operation, regex=None, source="explicit")
        elif settings.OPENAI_API_KEY:
            resolved = self._resolve_with_llm(prompt, requested_operation)
        else:
            resolved = self._resolve_locally(prompt, requested_operation)

        if resolved.operation == "replace":
            if not resolved.regex:
                raise PatternError("The LLM did not return a regex for the replacement request.")
            resolved = ResolvedRule(
                operation="replace", regex=validate_regex(resolved.regex), source=resolved.source
            )
        cache.set(cache_key, asdict(resolved), timeout=60 * 60 * 24)
        return resolved

    def _resolve_with_llm(self, request: str, requested_operation: str) -> ResolvedRule:
        from openai import OpenAI

        instructions = """You convert a data-cleaning instruction into one safe Spark-compatible rule.
Return JSON only, with exactly these keys: operation and regex.
operation must be one of replace, normalize_whitespace, lowercase, uppercase, trim.
Use replace for text matching requests. For non-replace operations set regex to null.
For a replace operation, regex must be at most 512 characters, must not use lookbehind,
backreferences, recursive regex, or nested quantifiers such as (.*)+. Do not include delimiters."""
        user = json.dumps({"instruction": request, "requested_operation": requested_operation})
        try:
            response = OpenAI(api_key=settings.OPENAI_API_KEY).responses.create(
                model=settings.OPENAI_MODEL,
                instructions=instructions,
                input=user,
            )
            payload = json.loads(response.output_text)
        except Exception as exc:  # Provider response is intentionally not surfaced or logged.
            raise PatternError("The LLM could not generate a usable transformation. Please retry.") from exc

        operation = payload.get("operation", "replace")
        if requested_operation != "auto":
            operation = requested_operation
        if operation not in OPERATIONS:
            raise PatternError("The LLM returned an unsupported transformation.")
        return ResolvedRule(operation=operation, regex=payload.get("regex"), source="llm")

    def _resolve_locally(self, request: str, requested_operation: str) -> ResolvedRule:
        text = request.lower()
        if requested_operation == "replace":
            operation = "replace"
        elif requested_operation != "auto":
            operation = requested_operation
        elif any(term in text for term in ("normalise whitespace", "normalize whitespace", "collapse spaces")):
            operation = "normalize_whitespace"
        elif "lowercase" in text or "lower case" in text:
            operation = "lowercase"
        elif "uppercase" in text or "upper case" in text:
            operation = "uppercase"
        elif "trim" in text or "strip whitespace" in text:
            operation = "trim"
        else:
            operation = "replace"

        if operation != "replace":
            return ResolvedRule(operation=operation, regex=None, source="local")
        rules = [
            (("email", "e-mail"), r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
            (("phone", "telephone", "mobile"), r"\b(?:\+?\d[\d .()/-]{7,}\d)\b"),
            (("url", "website", "link"), r"\bhttps?://[^\s<>\"]+"),
            (("ip address", "ip addresses"), r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
            (("number", "numbers", "digit", "digits"), r"\b\d+\b"),
        ]
        for keywords, regex in rules:
            if any(keyword in text for keyword in keywords):
                return ResolvedRule(operation="replace", regex=regex, source="local")
        raise PatternError(
            "No LLM key is configured and the local resolver only recognises email, phone, URL, IP, or number requests. "
            "Set OPENAI_API_KEY or use one of those requests."
        )

    @staticmethod
    def _cache_key(request: str, operation: str) -> str:
        digest = hashlib.sha256(f"{operation}\0{request}".encode("utf-8")).hexdigest()
        return PatternResolver.cache_prefix + digest

    @staticmethod
    def _deserialize(value: dict[str, str | None]) -> ResolvedRule:
        operation = value.get("operation")
        regex = value.get("regex")
        if operation not in OPERATIONS:
            raise PatternError("The cached transformation is invalid.")
        if operation == "replace":
            regex = validate_regex(regex or "")
        return ResolvedRule(operation=operation, regex=regex, source=value.get("source", "cache"))
