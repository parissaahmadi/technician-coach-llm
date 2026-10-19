"""Errors. Each carries a short machine-readable ``code``."""

from __future__ import annotations


class CoachError(Exception):
    code = "error"


class NotFound(CoachError):
    code = "not_found"


class Forbidden(CoachError):
    code = "forbidden"


class Invalid(CoachError):
    code = "invalid"


class Conflict(CoachError):
    code = "conflict"


class LLMError(CoachError):
    code = "llm_error"


class LLMUnavailable(LLMError):
    """Network failure, timeout, 429 or 5xx after all retries."""

    code = "llm_unavailable"


class LLMBadRequest(LLMError):
    """4xx other than 429: wrong key, wrong model, malformed request. Not retried, not hidden."""

    code = "llm_bad_request"


class LLMBadResponse(LLMError):
    """The service answered, but not with something usable."""

    code = "llm_bad_response"
