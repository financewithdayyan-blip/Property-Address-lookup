"""
name_matcher.py

Normalizes owner names and fuzzy-matches a scraped ("found") name against the
input name from the lead CSV. Used to decide whether a scraped result is
actually the same person/entity, and how confident we are in that.

Thresholds (per spec):
    score >= 85         -> "MATCH"          (status will be FOUND)
    60 <= score < 85     -> "LOW_CONFIDENCE" (status will be LOW CONFIDENCE)
    score < 60           -> "NO_MATCH"       (status will be NOT FOUND)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

# Common name suffixes to strip before comparing. Order doesn't matter since
# we remove all occurrences as whole tokens.
_SUFFIXES = {
    "JR", "SR", "II", "III", "IV", "V", "ESQ", "ESQUIRE",
    "TRUSTEE", "TRUST", "TTEE", "EST", "ESTATE",
}

# Anything that isn't a letter, digit, space, or & (owner records frequently
# use "&" to join co-owners, e.g. "SMITH JOHN & SMITH JANE").
_PUNCT_RE = re.compile(r"[^A-Z0-9& ]")
_WHITESPACE_RE = re.compile(r"\s+")

MATCH_THRESHOLD = 85
LOW_CONFIDENCE_THRESHOLD = 60


@dataclass(frozen=True)
class MatchResult:
    score: float          # 0-100 similarity score
    classification: str   # "MATCH" | "LOW_CONFIDENCE" | "NO_MATCH"
    normalized_input: str
    normalized_found: str


def normalize_name(raw_name: str) -> str:
    """Uppercase, strip punctuation, collapse whitespace, drop suffixes.

    "Smith, John Jr."    -> "SMITH JOHN"
    "O'Brien-Garcia III" -> "O BRIEN GARCIA"  (hyphen/apostrophe become spaces)
    "MARIA GARCIA"       -> "MARIA GARCIA"
    """
    if not raw_name:
        return ""

    name = raw_name.upper().strip()
    name = _PUNCT_RE.sub(" ", name)
    name = _WHITESPACE_RE.sub(" ", name).strip()

    tokens = [t for t in name.split(" ") if t and t not in _SUFFIXES]
    return " ".join(tokens)


def classify(score: float) -> str:
    if score >= MATCH_THRESHOLD:
        return "MATCH"
    if score >= LOW_CONFIDENCE_THRESHOLD:
        return "LOW_CONFIDENCE"
    return "NO_MATCH"


def match_names(input_name: str, found_name: str) -> MatchResult:
    """Fuzzy-compare two owner names and classify the result.

    Uses token_sort_ratio so word order differences ("SMITH JOHN" vs
    "JOHN SMITH", common when a county records "LAST FIRST") don't tank the
    score the way a naive Levenshtein ratio would.

    found_name may be multiple co-owners joined by " & " (as produced when
    a source combines e.g. OWNER1 and OWNER2 - very common for married
    couples/joint ownership). Each co-owner is scored independently and
    the best score wins, so a strong match to owner #1 isn't diluted by an
    unrelated owner #2's name pulling the combined-string ratio down.
    """
    norm_input = normalize_name(input_name)
    if not norm_input:
        return MatchResult(0.0, "NO_MATCH", norm_input, normalize_name(found_name))

    candidates = [c.strip() for c in found_name.split("&")] if found_name else [found_name]
    best: MatchResult | None = None
    for candidate in candidates:
        norm_found = normalize_name(candidate)
        if not norm_found:
            continue
        score = fuzz.token_sort_ratio(norm_input, norm_found)
        if best is None or score > best.score:
            best = MatchResult(score, classify(score), norm_input, norm_found)

    if best is None:
        return MatchResult(0.0, "NO_MATCH", norm_input, normalize_name(found_name))
    return best
