"""
county_configs.py

COUNTY_CONFIGS is the directory scraper.py and main.py read from. Each key
is "<county>|<2-letter state>" (lowercase, produced by config_key()) mapping
to a dict describing how to search and parse that county's property
appraiser site.

--------------------------------------------------------------------------
CONFIG SCHEMA
--------------------------------------------------------------------------
display_name       str  - human-readable name, used in logs/output.
search_type         str  - one of:
                        "html_get"        simple GET request, name goes in
                                          a query string param.
                        "aspnet_postback" classic ASP.NET WebForms search:
                                          GET the page first to harvest
                                          __VIEWSTATE/__EVENTVALIDATION
                                          hidden fields, then POST the form.
                        "selenium"        page requires JS to render
                                          results (SPA / client-side
                                          rendering) - handled by
                                          selenium_scraper.py instead of
                                          requests+BeautifulSoup.
                        "arcgis_query"    county's GIS parcel data is
                                          exposed as a public ArcGIS
                                          FeatureServer/MapServer layer -
                                          queried directly as JSON (no
                                          HTML/browser involved at all).
                                          See the "arcgis" key below.
                                          Prefer this over html_get/
                                          aspnet_postback/selenium
                                          whenever a county has one -
                                          it's far more reliable than
                                          scraping a web UI. Check for it
                                          by looking for an
                                          "*.arcgis.com" or "/arcgis/rest/
                                          services" / "/gis/rest/services"
                                          URL anywhere on the county GIS
                                          site, then hit
                                          "<.../MapServer/N>?f=json" to see
                                          if it lists owner/address
                                          fields.
arcgis                dict - only for search_type "arcgis_query":
                        query_url: the layer's .../query endpoint.
                        owner_field: field name to filter owner name on
                          (e.g. "OWNER1").
                        out_fields: list of field names to request.
                        owner_name_fields: field(s) to join (with " & ")
                          into owner_name_found (e.g. ["OWNER1","OWNER2"]
                          for co-owners).
                        property_address_compose /
                        mailing_address_compose: dicts with optional
                          "street"/"street2"/"city"/"state"/"zip" keys
                          mapping to field names, combined into one
                          address string.
                        parcel_id_field: field name for the parcel ID.
                        max_results: cap on resultRecordCount per query
                          (default 50).
search_page_url     str  - page to GET first (form page). Same as
                          search_url for html_get.
search_url          str  - for html_get: URL template containing "{name}"
                          (already URL-quoted by the scraper). For
                          aspnet_postback: the POST target (usually same
                          as search_page_url). For selenium: the URL to
                          load in the browser.
method               str  - "GET" or "POST" (html_get is GET, aspnet is
                          POST; selenium ignores this).
name_param           str  - form field name / query param that carries the
                          owner name being searched.
extra_static_params  dict - additional fixed form fields/query params to
                          always send (e.g. a "search type = owner"
                          selector, a submit-button field).
hidden_field_selector str - CSS selector used to harvest ALL hidden
                          <input> fields from the GET'd form page (only
                          used for aspnet_postback). Default:
                          'input[type="hidden"]'.
row_selector         str  - CSS selector (relative to the parsed results
                          page) that matches ONE result row per property.
fields                dict - maps our output field names to extraction
                          rules, each either {"selector": "<css, relative
                          to the row>"} or {"index": <int>} for plain
                          "nth <td> in the row". Recognized keys:
                          "owner_name_found", "property_address",
                          "mailing_address", "parcel_id".
no_results_markers   list[str] - case-insensitive substrings that, if
                          present in the response body, mean "no matches"
                          even if row_selector happens to match nothing
                          (or vice versa - checked either way).
selenium_search_box_selector / selenium_submit_selector /
selenium_wait_selector - only used when search_type == "selenium"; CSS
                          selectors for the name input, the submit
                          control, and an element to wait for after
                          submitting (results container).
verified              bool - True once someone has confirmed this config
                          against the live site with a real search. False
                          means "structurally correct pattern, but exact
                          field names/selectors are unconfirmed" - see
                          verification_note.
verification_note     str  - explains what to check / how to verify.

See README.md -> "Adding a New County" for the step-by-step, non-technical
walkthrough, and `python main.py --inspect-county "County ST"` for a tool
that dumps a search page's form fields to help you fill this in.
--------------------------------------------------------------------------
"""

from __future__ import annotations

# Full state-name -> 2-letter abbreviation, so the input CSV can say either
# "FL" or "Florida" and still match.
US_STATE_ABBR = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR",
    "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT",
    "DELAWARE": "DE", "DISTRICT OF COLUMBIA": "DC", "FLORIDA": "FL",
    "GEORGIA": "GA", "HAWAII": "HI", "IDAHO": "ID", "ILLINOIS": "IL",
    "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS", "KENTUCKY": "KY",
    "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD",
    "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN",
    "MISSISSIPPI": "MS", "MISSOURI": "MO", "MONTANA": "MT",
    "NEBRASKA": "NE", "NEVADA": "NV", "NEW HAMPSHIRE": "NH",
    "NEW JERSEY": "NJ", "NEW MEXICO": "NM", "NEW YORK": "NY",
    "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH",
    "OKLAHOMA": "OK", "OREGON": "OR", "PENNSYLVANIA": "PA",
    "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC", "SOUTH DAKOTA": "SD",
    "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT", "VERMONT": "VT",
    "VIRGINIA": "VA", "WASHINGTON": "WA", "WEST VIRGINIA": "WV",
    "WISCONSIN": "WI", "WYOMING": "WY",
}
_VALID_ABBR = set(US_STATE_ABBR.values())


def normalize_state(state: str) -> str:
    """'Florida' / 'FL' / ' fl ' -> 'FL'. Returns '' if unrecognized."""
    if not state:
        return ""
    s = state.strip().upper()
    if s in _VALID_ABBR:
        return s
    return US_STATE_ABBR.get(s, "")


def normalize_county(county: str) -> str:
    """'Duval County' / 'duval' / ' Duval ' -> 'duval'."""
    if not county:
        return ""
    c = county.strip().lower()
    for suffix in (" county", " parish", " borough"):
        if c.endswith(suffix):
            c = c[: -len(suffix)]
    return c.strip()


def config_key(county: str, state: str) -> str:
    # COUNTY_CONFIGS keys are lowercase (e.g. "duval|fl"); normalize_state()
    # returns an uppercase abbreviation ("FL") for display/comparison
    # purposes elsewhere, so it must be lowercased again here.
    return f"{normalize_county(county)}|{normalize_state(state).lower()}"


COUNTY_CONFIGS = {
    # ----------------------------------------------------------------
    # Duval County, FL - Duval County Property Appraiser (DCPAO)
    #
    # Public search tool: https://paopropertysearch.coj.net/Basic/Search.aspx
    #
    # This site (and Railway/most cloud IPs generally) can't be reached
    # directly from the environments used to build/verify this config -
    # every attempt got a connection timeout, not just an HTTP block.
    # Real field names below were instead recovered from the Wayback
    # Machine's archived copies of Search.aspx and Results.aspx (recent
    # snapshots, June 2026), which confirmed two things the ORIGINAL
    # placeholder guess got wrong:
    #
    #  1. name_param/submit field names were just wrong (see below for
    #     the real ones, read straight off the archived <input> tags).
    #  2. Clicking "Search" is NOT an in-place AJAX/JS update - it's a
    #     classic ASP.NET "cross-page postback": the onclick handler
    #     (WebForm_DoPostBackWithOptions(..., "Results.aspx", ...))
    #     just points the form's action at a DIFFERENT page
    #     (Results.aspx) before the browser's native form submission
    #     runs. That's a plain HTML form POST under the hood - no JS
    #     execution is needed to reproduce it, just POSTing to
    #     Results.aspx (not Search.aspx) with the harvested hidden
    #     fields. This is why search_url below differs from
    #     search_page_url, unlike every other aspnet_postback county.
    #
    # !! ROW-PARSING STILL UNVERIFIED !!
    # The Wayback snapshots only ever showed the empty "No Results
    # Found" state (archive.org can't submit search forms with real
    # names), so the exact results-table markup is still a best guess:
    # row_selector targets the <div class="gv"> results container seen
    # in the archived HTML (empty when there are no results, so a real
    # <table> presumably renders there for a match), and "property_address"
    # uses from_index (join every cell from index 2 onward) rather than
    # a specific column count/order, since the "Sort Results By" dropdown
    # confirmed the address is split across several columns (StreetNumber/
    # StreetPrefix/StreetName/StreetSuffix/StreetUnit/City/ZipCode) whose
    # on-screen left-to-right order isn't confirmed - joining everything
    # after RE#/Owner Name at least avoids guessing that order wrong in a
    # way that silently drops data. parse_results() logs a snippet of the
    # raw response if this ever parses 0 rows without hitting the
    # no-results marker, so a single live run's logs are enough to fix
    # this if it's wrong - see that function in scraper.py.
    # ----------------------------------------------------------------
    "duval|fl": {
        "display_name": "Duval County, FL",
        "search_type": "aspnet_postback",
        "search_page_url": "https://paopropertysearch.coj.net/Basic/Search.aspx",
        "search_url": "https://paopropertysearch.coj.net/Basic/Results.aspx",
        "method": "POST",
        "name_param": "ctl00$cphBody$tbName",
        "extra_static_params": {
            "ctl00$cphBody$bSearch": "Search",
        },
        "hidden_field_selector": 'input[type="hidden"]',
        "row_selector": "div.gv table tr:has(td)",
        "fields": {
            "parcel_id": {"index": 0},
            "owner_name_found": {"index": 1},
            "property_address": {"from_index": 2},
        },
        "no_results_markers": ["no results found"],
        "verified": False,
        "verification_note": (
            "Form field names and the Search->Results.aspx cross-page-"
            "postback mechanism were confirmed via Wayback Machine "
            "snapshots (no live access from any build environment tried "
            "so far). Row parsing (row_selector/fields) is still a best "
            "guess - no archived snapshot had real result rows to check "
            "against. If a live run logs a 'row_selector/fields may be "
            "wrong' warning, paste the logged HTML snippet back in to "
            "get this fixed, or run "
            "`python main.py --inspect-county \"Duval FL\" "
            "--inspect-sample-name \"Smith John\"` from a machine that "
            "can reach the site."
        ),
    },
    # ----------------------------------------------------------------
    # Pinellas County, FL - Pinellas County Property Appraiser (PCPAO)
    #
    # PCPAO's own web UI ("Advanced Search" at pcpao.gov) turned out to be
    # a JS-heavy report-builder that DOWNLOADS a file rather than
    # rendering results inline - not a good scraping target. Instead,
    # this queries Pinellas County's public ArcGIS parcel layer directly
    # as JSON. No authentication required.
    #
    # !! LIVE-VERIFIED !!
    # A real query for surname "SMITH" against this endpoint returned
    # actual owner/address/parcel records (owner names, situs addresses,
    # mailing addresses, and parcel IDs all populated). See
    # verification_note for the one real caveat.
    # ----------------------------------------------------------------
    "pinellas|fl": {
        "display_name": "Pinellas County, FL",
        "search_type": "arcgis_query",
        "arcgis": {
            "query_url": "https://egis.pinellas.gov/gis/rest/services/PublicWebGIS/Parcels/MapServer/1/query",
            "owner_field": "OWNER1",
            "out_fields": [
                "OWNER1", "OWNER2", "SITE_ADDRESS", "SITE_CITY", "SITE_STATE", "SITE_ZIP",
                "OWNADD_1", "OWNADD_2", "OWNCITY", "OWNSTATE", "OWNZIP", "PARCELID", "STRAP", "LEGAL",
            ],
            "owner_name_fields": ["OWNER1", "OWNER2"],
            "property_address_compose": {
                "street": "SITE_ADDRESS", "city": "SITE_CITY", "state": "SITE_STATE", "zip": "SITE_ZIP",
            },
            "mailing_address_compose": {
                "street": "OWNADD_1", "street2": "OWNADD_2",
                "city": "OWNCITY", "state": "OWNSTATE", "zip": "OWNZIP",
            },
            "parcel_id_field": "PARCELID",
            # Layer field confirmed live (GET .../MapServer/1?f=json): "LEGAL"
            # (255-char string). Used to auto-resolve MULTIPLE MATCHES when
            # the input CSV supplies a property_description - see
            # matching.py's legal-description cross-check.
            "legal_description_field": "LEGAL",
            "max_results": 50,
        },
        "verified": True,
        "verification_note": (
            "Live-tested successfully. Search strategy (see "
            "scraper._build_owner_where()) queries for OWNER1 LIKE "
            "'LAST, FIRST%' using BOTH possible readings of a two-token "
            "input name, since real lead lists mix 'First Last' and "
            "'Last First' ordering and OWNER1 is always stored 'LAST, "
            "FIRST[ MI]' - then fuzzy-matches whatever candidates come "
            "back against the full input name. A single-token input (or "
            "a trust/business name like 'ABC HOLDINGS LLC') falls back to "
            "a plain substring search, so entity-owned properties may "
            "still need a wider query if they come back NOT FOUND."
        ),
    },
    # ----------------------------------------------------------------
    # Hillsborough County, FL - Hillsborough County Property Appraiser (HCPA)
    #
    # HCPA's own ArcGIS Server (gis.hcpafl.org) exposes a public parcel
    # layer used by its own web map. No mailing address or legal
    # description field is exposed on this "NonConfidential" layer (likely
    # withheld for homestead-exempt owners) - property address only.
    #
    # !! LIVE-VERIFIED !!
    # A real query for surname "SMITH" returned actual owner/address/
    # parcel records. Owner1 format is "LAST FIRST[ MIDDLE/SUFFIX]" with
    # NO comma (unlike Pinellas) - confirmed live, which is exactly why
    # _build_owner_where() tries both the comma and the plain-space
    # convention rather than assuming one.
    # ----------------------------------------------------------------
    "hillsborough|fl": {
        "display_name": "Hillsborough County, FL",
        "search_type": "arcgis_query",
        "arcgis": {
            "query_url": "https://gis.hcpafl.org/arcgis/rest/services/Webmaps/HillsboroughFL_WebParcels/MapServer/0/query",
            "owner_field": "Owner1",
            "out_fields": ["Owner1", "Owner2", "FullAddress", "SiteCity", "SiteZip", "folio", "strap"],
            "owner_name_fields": ["Owner1", "Owner2"],
            "property_address_compose": {"street": "FullAddress", "zip": "SiteZip"},
            "mailing_address_compose": {},
            "parcel_id_field": "folio",
            "max_results": 50,
        },
        "verified": True,
        "verification_note": (
            "Live-tested successfully. This layer (WebParcels_NonConfidential) "
            "has no mailing-address or legal-description fields, so those "
            "output columns will always be blank for this county - if that "
            "data is needed, look for HCPA's confidential/full parcel layer "
            "(likely requires different access) and add the fields here."
        ),
    },
    # ----------------------------------------------------------------
    # Lee County, FL - Lee County Property Appraiser
    #
    # Lee County publishes its parcel layer (with owner + mailing +
    # legal-description fields) as a hosted ArcGIS Online FeatureServer
    # (services2.arcgis.com), found via the "Lee County Parcels" item on
    # Lee County GIS's ArcGIS Hub site.
    #
    # !! LIVE-VERIFIED !!
    # A real query for surname "SMITH" returned actual owner/address/
    # mailing/legal-description records. O_NAME format is "LAST FIRST[
    # MIDDLE/SUFFIX]" with no comma, same as Hillsborough.
    # ----------------------------------------------------------------
    "lee|fl": {
        "display_name": "Lee County, FL",
        "search_type": "arcgis_query",
        "arcgis": {
            "query_url": "https://services2.arcgis.com/LvWGAAhHwbCJ2GMP/arcgis/rest/services/Lee_County_Parcels/FeatureServer/0/query",
            "owner_field": "O_NAME",
            "out_fields": [
                "O_NAME", "O_OTHERS", "SITEADDR", "SITECITY", "SITEZIP",
                "O_ADDR1", "O_ADDR2", "O_CITY", "O_STATE", "O_ZIP", "STRAP", "LEGAL",
            ],
            "owner_name_fields": ["O_NAME", "O_OTHERS"],
            "property_address_compose": {"street": "SITEADDR", "city": "SITECITY", "zip": "SITEZIP"},
            "mailing_address_compose": {
                "street": "O_ADDR1", "street2": "O_ADDR2",
                "city": "O_CITY", "state": "O_STATE", "zip": "O_ZIP",
            },
            "parcel_id_field": "STRAP",
            "legal_description_field": "LEGAL",
            "max_results": 50,
        },
        "verified": True,
        "verification_note": "Live-tested successfully.",
    },
    # ----------------------------------------------------------------
    # Palm Beach County, FL - Palm Beach County Property Appraiser (PBCPAO)
    #
    # Hosted ArcGIS Online FeatureServer (services1.arcgis.com) - "Parcels
    # downloaded from PBC GIS and Owners tabular information" per its own
    # service description, which also warns this schema may be replaced
    # by a newer one from PBC's open data site at some point; if this
    # county starts returning odd results, re-check the field names here
    # against the live service first.
    #
    # !! LIVE-VERIFIED !!
    # A real query for surname "SMITH" returned actual owner/address/
    # mailing/legal-description records. OWNER_NAME1 format is "LAST
    # FIRST[ MIDDLE/SUFFIX]" with no comma. Legal description is split
    # across three fixed-width columns (LEGAL1/LEGAL2/LEGAL3), concatenated
    # by _compose_legal_description() in scraper.py.
    # ----------------------------------------------------------------
    "palm beach|fl": {
        "display_name": "Palm Beach County, FL",
        "search_type": "arcgis_query",
        "arcgis": {
            "query_url": "https://services1.arcgis.com/RTiKiFNGzgAobBzy/arcgis/rest/services/Parcels/FeatureServer/0/query",
            "owner_field": "OWNER_NAME1",
            "out_fields": [
                "OWNER_NAME1", "OWNER_NAME2", "SITE_ADDR_STR", "MUNICIPALITY",
                "PADDR1", "PADDR2", "CITYNAME", "STATE", "ZIP1", "ZIP2",
                "PARCEL_NUMBER", "LEGAL1", "LEGAL2", "LEGAL3",
            ],
            "owner_name_fields": ["OWNER_NAME1", "OWNER_NAME2"],
            "property_address_compose": {"street": "SITE_ADDR_STR", "city": "MUNICIPALITY"},
            "mailing_address_compose": {
                "street": "PADDR1", "street2": "PADDR2",
                "city": "CITYNAME", "state": "STATE", "zip": "ZIP1",
            },
            "parcel_id_field": "PARCEL_NUMBER",
            "legal_description_field": ["LEGAL1", "LEGAL2", "LEGAL3"],
            "max_results": 50,
        },
        "verified": True,
        "verification_note": (
            "Live-tested successfully. Mailing zip is truncated to the "
            "5-digit ZIP1 (ZIP2, the +4 suffix, isn't concatenated on)."
        ),
    },
}

# ----------------------------------------------------------------------
# TEMPLATE - copy this into COUNTY_CONFIGS above when adding a county.
# Not registered under any key, so it has no effect by itself.
# See README.md "Adding a New County" for the full walkthrough.
# ----------------------------------------------------------------------
TEMPLATE_HTML_GET = {
    "display_name": "Example County, ST",
    "search_type": "html_get",
    "search_page_url": "https://example-county-appraiser.gov/search",
    # {name} is replaced with the URL-encoded owner name at request time.
    "search_url": "https://example-county-appraiser.gov/search?ownerName={name}&type=owner",
    "method": "GET",
    "name_param": None,  # not used for html_get; name is baked into search_url
    "extra_static_params": {},
    "row_selector": "table.results-table tr.result-row",
    "fields": {
        "owner_name_found": {"selector": "td.owner"},
        "property_address": {"selector": "td.site-address"},
        "mailing_address": {"selector": "td.mailing-address"},
        "parcel_id": {"selector": "td.parcel-number"},
    },
    "no_results_markers": ["no results", "0 records found"],
    "verified": False,
    "verification_note": "Fill in after inspecting the live site (see README).",
}

TEMPLATE_ASPNET_POSTBACK = {
    "display_name": "Example County, ST",
    "search_type": "aspnet_postback",
    "search_page_url": "https://example-county-appraiser.gov/Search.aspx",
    "search_url": "https://example-county-appraiser.gov/Search.aspx",
    "method": "POST",
    "name_param": "ctl00$MainContent$txtOwnerName",
    "extra_static_params": {"ctl00$MainContent$btnSearch": "Search"},
    "hidden_field_selector": 'input[type="hidden"]',
    "row_selector": "table#MainContent_gvResults tr.resultRow",
    "fields": {
        "parcel_id": {"index": 0},
        "owner_name_found": {"index": 1},
        "property_address": {"index": 2},
        "mailing_address": {"index": 3},
    },
    "no_results_markers": ["no records found"],
    "verified": False,
    "verification_note": "Fill in after inspecting the live site (see README).",
}

TEMPLATE_ARCGIS_QUERY = {
    "display_name": "Example County, ST",
    "search_type": "arcgis_query",
    "arcgis": {
        # Find this by looking for "*.arcgis.com" or "/arcgis/rest/services"
        # / "/gis/rest/services" anywhere on the county's GIS site, then
        # browsing to a layer and appending "?f=json" to see its fields.
        "query_url": "https://example-county-gis.gov/arcgis/rest/services/Parcels/MapServer/0/query",
        "owner_field": "OWNER1",
        "out_fields": ["OWNER1", "OWNER2", "SITE_ADDRESS", "MAIL_ADDRESS", "PARCEL_ID"],
        "owner_name_fields": ["OWNER1", "OWNER2"],
        "property_address_compose": {"street": "SITE_ADDRESS"},
        "mailing_address_compose": {"street": "MAIL_ADDRESS"},
        "parcel_id_field": "PARCEL_ID",
        # Optional: a legal-description field (add its name to out_fields
        # too), used to auto-resolve MULTIPLE MATCHES when the input CSV
        # supplies a property_description. Omit if the layer has none.
        "legal_description_field": None,
        "max_results": 50,
    },
    "verified": False,
    "verification_note": "Fill in after inspecting the live site (see README).",
}

TEMPLATE_SELENIUM = {
    "display_name": "Example County, ST",
    "search_type": "selenium",
    "search_url": "https://example-county-appraiser.gov/app/search",
    "selenium_search_box_selector": "input#ownerNameInput",
    "selenium_submit_selector": "button#searchButton",
    "selenium_wait_selector": "div.results-container",
    "row_selector": "div.result-card",
    "fields": {
        "owner_name_found": {"selector": ".owner-name"},
        "property_address": {"selector": ".site-address"},
        "mailing_address": {"selector": ".mailing-address"},
        "parcel_id": {"selector": ".parcel-id"},
    },
    "no_results_markers": ["no results found"],
    "verified": False,
    "verification_note": "Fill in after inspecting the live site (see README).",
}


def get_county_config(county: str, state: str):
    """Look up a county's config, or None if not in the directory."""
    return COUNTY_CONFIGS.get(config_key(county, state))
