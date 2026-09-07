"""Redact credential material from captured harness artifacts.

A harness session transcript records tool results verbatim, and an agent that
dumps its environment lands every credential it holds in one. Redaction is by
shape: the credentials a factory carries have recognizable forms — GitHub
tokens, Atlassian API tokens, JWTs, Basic auth headers, PEM private keys —
matched whether they sit raw in the text or JSON-escaped inside a string.
Matching by pattern rather than by known value means the scrubber holds no
secrets of its own, and catches a credential Tina was never told about.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SecretPattern:
    label: str
    regex: re.Pattern[str]


#: Most specific first; a redacted marker contains none of these shapes, so an
#: earlier match is never re-matched by a later pattern. The private-key body
#: is DOTALL so it matches across both real newlines and the escaped `\n` it
#: becomes inside a JSON string.
PATTERNS: tuple[SecretPattern, ...] = (
    SecretPattern(
        "private-key",
        re.compile(
            r"-----BEGIN[A-Z ]*PRIVATE KEY-----.*?-----END[A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
    SecretPattern("github-fine-grained-pat", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    SecretPattern("github-token", re.compile(r"gh[oprsu]_[A-Za-z0-9]{20,}")),
    SecretPattern("atlassian-api-token", re.compile(r"ATATT[A-Za-z0-9_=+/-]{20,}")),
    SecretPattern(
        "jwt",
        re.compile(r"eyJ[A-Za-z0-9_-]{6,}\.eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}"),
    ),
    SecretPattern("basic-auth-header", re.compile(r"(?i:basic)\s+[A-Za-z0-9+/=]{20,}")),
)

_BASIC_CREDENTIAL = re.compile(r"[A-Za-z0-9+/=]{20,}$")


def scrub(text: str) -> str:
    """Return `text` with credential material replaced by `<REDACTED:label>`."""
    for pattern in PATTERNS:
        if pattern.label == "basic-auth-header":
            # Keep the "basic " keyword, redact only the credential after it.
            text = pattern.regex.sub(
                lambda m: _BASIC_CREDENTIAL.sub("<REDACTED:basic-auth-header>", m.group(0)),
                text,
            )
            continue
        text = pattern.regex.sub(f"<REDACTED:{pattern.label}>", text)
    return text
