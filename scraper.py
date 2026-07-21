"""
scraper.py

requests + BeautifulSoup search/parse engine shared by every county whose
site doesn't need JavaScript. Handles:
  - session reuse (keep-alive) per county
  - User-Agent rotation per request
  - 2-4s randomized rate limiting between requests
  - retry with 30s backoff (up to 3 attempts) on HTTP 429 / 503
  - two search styles: plain GET (html_get) and ASP.NET WebForms
    postback (aspnet_postback)
  - generic result-row parsing driven entirely by county_configs.py

If a county config has search_type == "selenium", search() delegates to
selenium_scraper.py instead.
"""

from __future__ import annotations

import random
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup

from logger import get_logger
from name_matcher import SUFFIXES

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 "
    "Firefox/126.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

RETRY_STATUS_CODES = {429, 503}
MAX_RETRIES = 3
RETRY_WAIT_SECONDS = 30
REQUEST_TIMEOUT = 20


class ScraperError(Exception):
    """Raised for anything that should make main.py record status=ERROR."""


@dataclass
class PropertyMatch:
    owner_name_found: str = ""
    property_address: str = ""
    mailing_address: str = ""
    parcel_id: str = ""
    source_url: str = ""
    legal_description: str = ""


@dataclass
class RateLimiter:
    """Per-county random 2-4s (or --delay-driven) gap between requests."""
    min_delay: float
    max_delay: float
    _last_request_ts: float = field(default=0.0, init=False)

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last_request_ts
        target = random.uniform(self.min_delay, self.max_delay)
        remaining = target - elapsed
        if remaining > 0:
            time.sleep(remaining)
        self._last_request_ts = time.monotonic()


def make_delay_bounds(delay: float) -> tuple[float, float]:
    """CLI --delay is a center point; jitter +/-33% but never below 1s,
    matching the "2-4 second random delay" spirit when delay=3 (default).
    """
    low = max(1.0, delay * 0.67)
    high = delay * 1.33
    return (low, high)


def new_session() -> requests.Session:
    """A fresh keep-alive session. Call once per county."""
    session = requests.Session()
    session.headers.update(
        {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Connection": "keep-alive",
        }
    )
    return session


def _random_ua_headers() -> Dict[str, str]:
    return {"User-Agent": random.choice(USER_AGENTS)}


def _request_with_retry(
    session: requests.Session,
    method: str,
    url: str,
    *,
    params: Optional[dict] = None,
    data: Optional[dict] = None,
) -> requests.Response:
    logger = get_logger()
    attempt = 0
    while True:
        attempt += 1
        try:
            resp = session.request(
                method,
                url,
                params=params,
                data=data,
                headers=_random_ua_headers(),
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            if attempt >= MAX_RETRIES:
                raise ScraperError(f"Network error after {attempt} attempts: {exc}") from exc
            logger.warning(
                "Network error on attempt %d/%d for %s: %s - retrying in %ds",
                attempt, MAX_RETRIES, url, exc, RETRY_WAIT_SECONDS,
            )
            time.sleep(RETRY_WAIT_SECONDS)
            continue

        if resp.status_code in RETRY_STATUS_CODES:
            if attempt >= MAX_RETRIES:
                raise ScraperError(
                    f"HTTP {resp.status_code} from {url} after {attempt} attempts"
                )
            logger.warning(
                "HTTP %d from %s (attempt %d/%d) - waiting %ds before retry",
                resp.status_code, url, attempt, MAX_RETRIES, RETRY_WAIT_SECONDS,
            )
            time.sleep(RETRY_WAIT_SECONDS)
            continue

        if resp.status_code >= 400:
            raise ScraperError(f"HTTP {resp.status_code} from {url}")

        return resp


def _harvest_hidden_fields(html: str, selector: str) -> Dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    fields: Dict[str, str] = {}
    for tag in soup.select(selector):
        name = tag.get("name")
        if name:
            fields[name] = tag.get("value", "")
    return fields


def _extract_field(row, rule: dict) -> str:
    if "selector" in rule:
        el = row.select_one(rule["selector"])
        return el.get_text(strip=True) if el else ""
    cells = row.find_all(["td", "th"])
    if "index" in rule:
        idx = rule["index"]
        if idx < len(cells):
            return cells[idx].get_text(strip=True)
        return ""
    if "from_index" in rule:
        # Joins every remaining cell from this index to the end of the
        # row - for a county whose results table splits an address across
        # several columns (street number/name/suffix/unit/city/zip) in an
        # unconfirmed order/count, this at least captures all of it in a
        # readable (if not perfectly ordered) string rather than requiring
        # exact column-index knowledge we don't have.
        idx = rule["from_index"]
        parts = [c.get_text(strip=True) for c in cells[idx:]]
        return " ".join(p for p in parts if p)
    return ""


def parse_results(html: str, config: dict, source_url: str) -> List[PropertyMatch]:
    """Turn a results page (already-fetched HTML) into PropertyMatch rows,
    using the county's row_selector / fields config. Shared by GET,
    postback, and Selenium (Selenium hands us rendered page_source).
    """
    lower_html = html.lower()
    for marker in config.get("no_results_markers", []):
        if marker.lower() in lower_html:
            return []

    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select(config["row_selector"])
    matches: List[PropertyMatch] = []
    for row in rows:
        fields_cfg = config["fields"]
        match = PropertyMatch(
            owner_name_found=_extract_field(row, fields_cfg.get("owner_name_found", {})),
            property_address=_extract_field(row, fields_cfg.get("property_address", {})),
            mailing_address=_extract_field(row, fields_cfg.get("mailing_address", {})),
            parcel_id=_extract_field(row, fields_cfg.get("parcel_id", {})),
            source_url=source_url,
        )
        # Skip fully-empty rows (header rows sometimes match loose selectors).
        if any([match.owner_name_found, match.property_address, match.parcel_id]):
            matches.append(match)

    if not matches and config.get("verified") is False:
        # For an unverified county, 0 parsed rows with no "no results"
        # marker matched is ambiguous: either a genuine empty result, or
        # row_selector/fields guessed wrong against the real page. Log a
        # snippet so a live run's logs are enough to fix the config,
        # without needing another round-trip to re-fetch and inspect.
        get_logger().warning(
            "%s: 0 rows parsed and no no_results_markers matched - "
            "row_selector/fields may be wrong (county marked unverified). "
            "First 1500 chars of response:\n%s",
            config.get("display_name", "county"), html[:1500],
        )
    return matches


def _search_html_get(config: dict, owner_name: str, session: requests.Session) -> tuple[str, str]:
    url = config["search_url"].format(name=urllib.parse.quote(owner_name))
    resp = _request_with_retry(session, "GET", url)
    return resp.text, url


def _build_owner_where(owner_fields: List[str], owner_name: str) -> str:
    """WHERE clause for an ArcGIS owner-name search.

    County parcel layers store owner names as "LAST" followed by the
    given name(s) - but the separator convention differs per county
    (Pinellas: "POULOS, ALEXANDER" with a comma; Hillsborough: "SMITH
    DAVID A TRUSTEE", just a space - verified live for both). We also
    can't tell from a two-word *input* name alone whether it's "First
    Last" or already "Last First" - real lead lists use both conventions.
    Guessing either of these wrong the naive way (a single substring
    anchor on one token) causes two distinct failures found live:
    anchoring on a common first name instead of the actual surname pulls
    in unrelated owners who share that first name, and even anchoring on
    the *correct* surname as a bare substring can still blow up on
    unrelated names that merely contain it (e.g. "POULOS" substring-
    matches "PETROPOULOS", "GIANOPOULOS", every other Greek "-poulos"
    surname - in a Greek-heavy area like Tarpon Springs that's enough
    noise to push the real "POULOS, ALEXANDER" record past the API's row
    cap entirely).

    Fix: OR together anchored "SURNAME<sep>GIVEN%" prefix patterns for
    both possible token orderings AND both separator conventions (a
    space, or a comma followed by anything - covering "LAST,FIRST" and
    "LAST, FIRST" alike). A prefix match anchored on "SURNAME " or
    "SURNAME," never matches a different surname that merely contains
    those letters, unlike a bare substring search, and requiring the
    given name too (not just the surname) keeps the candidate set small
    even for a common surname. Falls back to a plain substring search for
    single-token input (business/trust names, or a name with no clear
    split).

    `owner_fields` is every field the search should try (e.g. OWNER1 AND
    OWNER2), not just the primary one - a second owner is routinely only
    present in the second field (e.g. "OWNER1: TURNEY, NELSON T JR /
    OWNER2: TURNEY, LYNDA"), and searching only the first field means a
    lead for that second owner never comes back as a candidate at all -
    found live, and worse than a mismatch since there's no ambiguous
    candidate for the classifier to even weigh; it's silently NOT FOUND.

    A 3+-token input (a middle name, e.g. "DOOLEY DAWN PATRICIA") needs
    its own handling: county records commonly only keep a middle
    *initial* or drop it entirely ("DOOLEY, DAWN P"), so anchoring on the
    first-and-last tokens as a surname/given pair - "DOOLEY"/"PATRICIA" -
    misses it just as completely as the OWNER2 case (found live: 0
    candidates, not a mismatch). _owner_hypotheses() below adds the
    "surname is the first token, given name is the very next token"
    reading alongside the original "surname is the last token, given
    name is the first token" one, so the true given name (the token
    adjacent to whichever token is actually the surname) gets tried
    regardless of trailing middle names.

    A trailing status/suffix word (e.g. a lead list marking someone
    "HERRIER MICHAEL PHILIP DECEASED") needs stripping before any of the
    above, not just at scoring time - name_matcher.match_names() already
    strips these before comparing, but if scraper.py doesn't also strip
    them before picking anchor tokens, a genuinely "First Last SUFFIX"
    name (3 real tokens) gets misread as a plain 3-token name and the
    surname/given hypotheses land on the wrong tokens entirely.
    """
    tokens = [t for t in owner_name.strip().upper().split() if t and t not in SUFFIXES]

    field_patterns = []
    for field in owner_fields:
        if len(tokens) < 2:
            token = (tokens[0] if tokens else owner_name.strip().upper()).replace("'", "''")
            field_patterns.append(f"UPPER({field}) LIKE '%{token}%'")
            continue

        for surname, given in _owner_hypotheses(tokens):
            surname = surname.replace("'", "''")
            given = given.replace("'", "''")
            field_patterns.append(f"UPPER({field}) LIKE '{surname} {given}%'")
            field_patterns.append(f"UPPER({field}) LIKE '{surname},%{given}%'")

    return "(" + " OR ".join(field_patterns) + ")"


def _owner_hypotheses(tokens: List[str]) -> List[tuple[str, str]]:
    """(surname, given) candidate pairs for an input name of 2+ tokens.

    Tries every ADJACENT pair of tokens, in both orders, as a candidate
    (surname, given) anchor - not just the first/last positions. This
    covers the usual "First [Middle] Last" / "Last First [Middle]"
    orderings (which only need the boundary tokens), but also a case
    found live: a lead list that concatenates two co-owners' full names
    together with no separator at all, e.g. input "ESCOBAR STEVEN MUNOZ
    DIAZ VALENTINA" for two owners actually stored as "MUNOZ ESCOBAR
    STEVEN" (note: even the surname/given order within that first name
    doesn't match the input's) and "DIAZ VALENTINA" - the real,
    findable 2-token surname/given pair for the second owner sits in
    the *middle* of the token list, not at either end, so only checking
    the boundary tokens would silently miss it (0 candidates, not a
    mismatch) exactly like the plain OWNER2 case did.
    """
    pairs = set()
    for i in range(len(tokens) - 1):
        pairs.add((tokens[i], tokens[i + 1]))
        pairs.add((tokens[i + 1], tokens[i]))
    return list(pairs)


def _join_fields(attributes: dict, fields: List[str], sep: str) -> str:
    parts = [str(attributes.get(f) or "").strip() for f in fields]
    return sep.join(p for p in parts if p)


def _compose_owner_name(attributes: dict, fields: List[str]) -> str:
    return _join_fields(attributes, fields, " & ")


def _compose_legal_description(attributes: dict, fields) -> str:
    """`fields` is a single field name or a list - some counties split the
    legal description across several fixed-width columns (e.g. Palm
    Beach's LEGAL1/LEGAL2/LEGAL3), which just get concatenated in order.
    """
    if not fields:
        return ""
    if isinstance(fields, str):
        fields = [fields]
    return _join_fields(attributes, fields, " ")


def _compose_address(attributes: dict, compose: dict) -> str:
    street = " ".join(
        p for p in (
            str(attributes.get(compose.get("street")) or "").strip() if compose.get("street") else "",
            str(attributes.get(compose.get("street2")) or "").strip() if compose.get("street2") else "",
        ) if p
    )
    city = str(attributes.get(compose.get("city")) or "").strip() if compose.get("city") else ""
    state = str(attributes.get(compose.get("state")) or "").strip() if compose.get("state") else ""
    zip_code = str(attributes.get(compose.get("zip")) or "").strip() if compose.get("zip") else ""
    city_state_zip = ", ".join(p for p in (city, " ".join(p for p in (state, zip_code) if p)) if p)
    return ", ".join(p for p in (street, city_state_zip) if p)


def _search_arcgis_query(config: dict, owner_name: str, session: requests.Session) -> List[PropertyMatch]:
    """Query a public ArcGIS FeatureServer/MapServer layer directly (JSON,
    no HTML parsing, no browser needed) - used by counties whose GIS
    parcel data is exposed this way (e.g. Pinellas County, FL). Bypasses
    parse_results()/row_selector entirely since there's no HTML involved.
    See _build_owner_where() for how the WHERE clause is built.
    """
    logger = get_logger()
    arc = config["arcgis"]
    owner_field = arc["owner_field"]
    where = _build_owner_where(arc.get("owner_name_fields", [owner_field]), owner_name)

    params = {
        "where": where,
        "outFields": ",".join(arc["out_fields"]),
        "f": "json",
        "resultRecordCount": arc.get("max_results", 50),
        "returnGeometry": "false",
    }
    url = arc["query_url"]
    logger.debug("ArcGIS query: %s where=%r", url, where)
    # POST, not GET: a long concatenated name (several co-owners run
    # together with no separator) can push _owner_hypotheses() past a
    # dozen candidate pairs, and the resulting WHERE clause easily
    # exceeds URL length limits as a GET query string - found live, this
    # failed outright with an HTTP 404 rather than just missing a match.
    # ArcGIS REST endpoints accept the same params as a POST body, which
    # has no such length limit.
    resp = _request_with_retry(session, "POST", url, data=params)

    try:
        data = resp.json()
    except ValueError as exc:
        raise ScraperError(f"ArcGIS response was not valid JSON from {url}: {exc}") from exc

    if "error" in data:
        raise ScraperError(f"ArcGIS query error from {url}: {data['error']}")

    source_url = f"{url}?{urllib.parse.urlencode({'where': where, 'f': 'json'})}"
    matches: List[PropertyMatch] = []
    for feature in data.get("features", []):
        attrs = feature.get("attributes", {})
        matches.append(
            PropertyMatch(
                owner_name_found=_compose_owner_name(attrs, arc.get("owner_name_fields", [owner_field])),
                property_address=_compose_address(attrs, arc.get("property_address_compose", {})),
                mailing_address=_compose_address(attrs, arc.get("mailing_address_compose", {})),
                parcel_id=str(attrs.get(arc.get("parcel_id_field")) or ""),
                source_url=source_url,
                legal_description=_compose_legal_description(attrs, arc.get("legal_description_field")),
            )
        )
    return matches


def _search_aspnet_postback(config: dict, owner_name: str, session: requests.Session) -> tuple[str, str]:
    logger = get_logger()
    form_url = config["search_page_url"]
    logger.debug("GET form page: %s", form_url)
    get_resp = _request_with_retry(session, "GET", form_url)

    hidden_selector = config.get("hidden_field_selector", 'input[type="hidden"]')
    payload = _harvest_hidden_fields(get_resp.text, hidden_selector)
    payload.update(config.get("extra_static_params", {}))
    payload[config["name_param"]] = owner_name

    post_url = config["search_url"]
    logger.debug("POST search: %s (fields=%s)", post_url, list(payload.keys()))
    post_resp = _request_with_retry(session, "POST", post_url, data=payload)
    return post_resp.text, post_url


def search(
    config: dict,
    owner_name: str,
    session: requests.Session,
    rate_limiter: RateLimiter,
) -> List[PropertyMatch]:
    """Dispatch to the right search strategy and return parsed matches.
    Raises ScraperError on unrecoverable failures.
    """
    logger = get_logger()
    search_type = config.get("search_type")

    rate_limiter.wait()

    # Strip status/suffix words (DECEASED, TRUSTEE, ESTATE, JR, ...) before
    # searching at all, not just when scoring the result - a county search
    # box or query filter can fail to find a real record if a lead list's
    # extra word is included verbatim. arcgis_query's _build_owner_where()
    # already does this internally, but html_get/aspnet_postback/selenium
    # just submit owner_name as typed into the county's own search
    # form/API, so it needs stripping here too - found live: Duval's own
    # search returned nothing for "Williams Patricia Deceased" but found
    # "Williams Patricia" immediately once "Deceased" was dropped.
    tokens = [t for t in owner_name.strip().split() if t.upper() not in SUFFIXES]
    if tokens:
        owner_name = " ".join(tokens)

    if not config.get("verified", True):
        logger.warning(
            "%s config is UNVERIFIED (%s) - results may be wrong if "
            "field names/selectors don't match the live site.",
            config.get("display_name", "county"),
            config.get("verification_note", "no note"),
        )

    if search_type == "arcgis_query":
        matches = _search_arcgis_query(config, owner_name, session)
    elif search_type == "html_get":
        html, url = _search_html_get(config, owner_name, session)
        matches = parse_results(html, config, url)
    elif search_type == "aspnet_postback":
        html, url = _search_aspnet_postback(config, owner_name, session)
        matches = parse_results(html, config, url)
    elif search_type == "selenium":
        # Local import: keep selenium (and its driver dependency) optional
        # for users who never hit a JS-rendered county.
        from selenium_scraper import search_selenium

        html, url = search_selenium(config, owner_name)
        matches = parse_results(html, config, url)
    else:
        raise ScraperError(f"Unknown search_type: {search_type!r}")

    logger.info(
        "%s search for %r -> %d match(es)",
        config.get("display_name", "county"), owner_name, len(matches),
    )
    return matches


def inspect_search_page(config: dict) -> str:
    """Diagnostic helper for `main.py --inspect-county`: GET the search
    page and return a human-readable dump of every form field found, so
    you can confirm/fix name_param, extra_static_params, hidden_field
    handling, etc. without opening browser DevTools.
    """
    session = new_session()
    url = config.get("search_page_url") or config["search_url"]
    resp = _request_with_retry(session, "GET", url)
    soup = BeautifulSoup(resp.text, "html.parser")

    lines = [f"GET {url} -> HTTP {resp.status_code}", ""]
    for form in soup.find_all("form"):
        action = form.get("action", "(same page)")
        method = form.get("method", "GET").upper()
        lines.append(f"<form action={action!r} method={method}>")
        for inp in form.find_all(["input", "select", "textarea"]):
            tag = inp.name
            itype = inp.get("type", "text" if tag == "input" else tag)
            name = inp.get("name")
            iid = inp.get("id")
            value = inp.get("value", "")
            value_preview = (value[:40] + "...") if len(value) > 40 else value
            lines.append(
                f"  <{tag} type={itype!r} name={name!r} id={iid!r} value={value_preview!r}>"
            )
        lines.append("")

    if not soup.find_all("form"):
        lines.append(
            "No <form> tags found - this page likely renders/searches via "
            "JavaScript. Consider search_type: 'selenium' for this county."
        )

    return "\n".join(lines)
