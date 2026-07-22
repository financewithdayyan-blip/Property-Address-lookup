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
# we remove all occurrences as whole tokens. Public (not _-prefixed) since
# scraper.py also uses this to strip suffix tokens before deciding which
# tokens are the surname/given-name anchor - see _build_owner_where().
SUFFIXES = {
    "JR", "SR", "II", "III", "IV", "V", "ESQ", "ESQUIRE",
    "TRUSTEE", "TRUST", "TTEE", "EST", "ESTATE", "DECEASED", "DECD",
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

    tokens = [t for t in name.split(" ") if t and t not in SUFFIXES]
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

    The *input* name is routinely several owners concatenated with no
    separator at all too (e.g. "PEREZ DIANA J PEREZ-NUNEZ DIANA" for two
    owners, or "KNAPIK JOHN RICHARD RIZZO MICHAEL RIZZO ERIC RIZZO RENEE" for
    four) - found live, repeatedly - and a real found_name is only ever one
    of those owners, so comparing it against the whole input string dilutes
    the score by however many unrelated tokens the other owners contribute,
    even when found_name is an exact match for a piece of the input. To
    catch that, found_name's full token set (every co-owner combined, not
    just whichever segment scored best above) is also compared against the
    input's full token set with token_set_ratio, which - unlike
    token_sort_ratio - tolerates tokens present on only one side. This finds
    a match regardless of where in the input string it sits (front, back, or
    the middle - an earlier live case, "ESCOBAR STEVEN MUNOZ DIAZ
    VALENTINA", only matched via the middle pair "DIAZ VALENTINA") without
    needing to know in advance which, and - critically - it still penalizes
    a candidate whose *other* co-owner doesn't correspond to anything in the
    input: comparing only a narrow same-length slice of the input against
    one segment at a time (an earlier version of this fix) couldn't tell a
    real match from a coincidental one, since discarding the unmatched
    sibling co-owner entirely let a bare two-token overlap with a
    *different*, unrelated person score just as high as the real match -
    found live, three unrelated candidates all scored 100.0 for this reason,
    tied with the real one and indistinguishable without the legal
    description. Comparing full token sets against each other means an
    unaccounted-for co-owner shows up as an unmatched token and actually
    costs the score, rather than being silently ignored.
    """
    norm_input = normalize_name(input_name)
    if not norm_input:
        return MatchResult(0.0, "NO_MATCH", norm_input, normalize_name(found_name))

    segments = [c.strip() for c in found_name.split("&")] if found_name else [found_name]
    best: MatchResult | None = None
    for segment in segments:
        norm_found = normalize_name(segment)
        if not norm_found:
            continue
        score = fuzz.token_sort_ratio(norm_input, norm_found)
        if best is None or score > best.score:
            best = MatchResult(score, classify(score), norm_input, norm_found)

    norm_found_combined = normalize_name(found_name.replace("&", " ")) if found_name else ""
    if norm_found_combined:
        combined_score = fuzz.token_set_ratio(norm_input, norm_found_combined)
        if best is None or combined_score > best.score:
            best = MatchResult(combined_score, classify(combined_score), norm_input, norm_found_combined)

    if best is None:
        return MatchResult(0.0, "NO_MATCH", norm_input, normalize_name(found_name))
    return best
