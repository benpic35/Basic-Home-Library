"""Online metadata lookup.

Search order, as specified for the Basic-Home-Library:
  1. Library of Congress catalog (catalog.loc.gov) via its public SRU service,
     which returns full MARC21 records -- the authoritative source for the
     Dewey number, LC classification and LCCN.
  2. Open Library, which needs no API key and carries classifications, an LCCN
     and a cover image.
  3. Google Books, consulted only when something is still missing.  It rate
     limits unkeyed callers per IP address, so it is deliberately last and
     deliberately conditional.

Every source is optional and failures are non-fatal: each returns whatever it
managed to retrieve plus a note for the status log, and the caller merges them
in priority order so an earlier source always wins a contested field.
"""

import json
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .model import BookRecord
from .schema import is_lcc_like, normalize_dds, normalize_lcc, normalize_lccn

USER_AGENT = "BasicHomeLibrary/1.0 (personal library cataloguing)"

LOC_SRU = "http://lx2.loc.gov:210/lcdb"
OPENLIBRARY_API = "https://openlibrary.org/api/books"
OPENLIBRARY_SEARCH = "https://openlibrary.org/search.json"
GOOGLE_BOOKS_API = "https://www.googleapis.com/books/v1/volumes"

MARC_NS = "{http://www.loc.gov/MARC21/slim}"

# MARC language codes seen in this catalog and their display names.  English is
# deliberately absent: the existing catalog leaves Language blank for English
# books and only records the exceptions.
LANGUAGE_NAMES = {
    "spa": "Spanish", "fre": "French", "fra": "French", "ger": "German",
    "deu": "German", "ita": "Italian", "lat": "Latin", "jpn": "Japanese",
    "chi": "Chinese", "zho": "Chinese", "rus": "Russian", "por": "Portuguese",
    "heb": "Hebrew", "ara": "Arabic", "grc": "Ancient Greek", "gre": "Greek",
    "ell": "Greek", "dut": "Dutch", "nld": "Dutch", "swe": "Swedish",
    "nor": "Norwegian", "dan": "Danish", "pol": "Polish", "kor": "Korean",
    "hin": "Hindi", "gle": "Irish Gaelic", "gla": "Scottish Gaelic",
    "wel": "Welsh", "cym": "Welsh", "sgn": "ASL", "ase": "ASL",
    "tur": "Turkish", "vie": "Vietnamese", "tha": "Thai", "swa": "Swahili",
    "cze": "Czech", "ces": "Czech", "hun": "Hungarian", "fin": "Finnish",
    "ukr": "Ukrainian", "yid": "Yiddish", "per": "Persian", "fas": "Persian",
}


class LookupError_(Exception):
    """Raised when an identifier cannot be understood at all."""


# --------------------------------------------------------------------------
# Identifier handling
# --------------------------------------------------------------------------

def clean_identifier(raw):
    """Strip hyphens/spaces from a scanned or typed ISBN/ISSN."""
    return re.sub(r"[^0-9Xx]", "", raw or "").upper()


def identifier_kind(raw):
    """Classify an identifier as 'isbn' or 'issn'.

    ISSNs are 8 digits and conventionally written NNNN-NNNN; ISBNs are 10 or
    13.  An 8-digit bare number is ambiguous, so a hyphen in the 5th position
    is treated as the deciding signal for ISSN.
    """
    digits = clean_identifier(raw)
    if len(digits) in (10, 13):
        return "isbn"
    if len(digits) == 8:
        return "issn"
    raise LookupError_(
        "'%s' is not a valid ISBN (10 or 13 digits) or ISSN (8 digits)." % raw.strip()
    )


def isbn10_to_13(isbn10):
    digits = clean_identifier(isbn10)
    if len(digits) != 10:
        return ""
    core = "978" + digits[:9]
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(core))
    return core + str((10 - total % 10) % 10)


def isbn13_to_10(isbn13):
    digits = clean_identifier(isbn13)
    if len(digits) != 13 or not digits.startswith("978"):
        return ""
    core = digits[3:12]
    total = sum(int(d) * (10 - i) for i, d in enumerate(core))
    check = (11 - total % 11) % 11
    return core + ("X" if check == 10 else str(check))


def format_isbn10(isbn):
    """Hyphenate an ISBN-10 loosely in the style used by the catalog.

    Exact registrant-group boundaries need the full ISBN range table; the
    catalog is not consistent about them either, so the raw digits are kept
    when a confident split is not possible.
    """
    digits = clean_identifier(isbn)
    return digits if len(digits) == 10 else ""


def format_isbn13(isbn):
    digits = clean_identifier(isbn)
    return digits if len(digits) == 13 else ""


def format_issn(raw):
    digits = clean_identifier(raw)
    if len(digits) == 8:
        return digits[:4] + "-" + digits[4:]
    return (raw or "").strip()


# --------------------------------------------------------------------------
# HTTP helper
# --------------------------------------------------------------------------

# Minimum seconds between consecutive requests to the same host.  Google Books
# limits unkeyed callers by IP address and starts returning 429 when asked in
# quick succession, which is exactly what cataloguing several books in a row
# does.  Spacing the requests out keeps us under that ceiling; the wait happens
# on the lookup worker thread, so the window stays responsive.
_HOST_MIN_INTERVAL = {
    "www.googleapis.com": 2.0,
    "openlibrary.org": 0.5,
    "covers.openlibrary.org": 0.5,
}
_DEFAULT_MIN_INTERVAL = 0.0

# With an API key Google's ceiling is far higher, so the gap can be much
# smaller; without one the 2.0s above applies.
GOOGLE_KEYED_MIN_INTERVAL = 0.25

_throttle_lock = threading.Lock()
_last_request_at = {}

# Responses already fetched this session, so re-searching an identifier or
# flipping between tabs never spends a fresh request.
_response_cache = {}
_CACHE_LIMIT = 256


def _throttle(host, override=None):
    """Block until this host may be asked again."""
    minimum = _HOST_MIN_INTERVAL.get(host, _DEFAULT_MIN_INTERVAL) if override is None else override
    if not minimum:
        return
    while True:
        with _throttle_lock:
            now = time.monotonic()
            ready_at = _last_request_at.get(host, 0.0) + minimum
            if now >= ready_at:
                _last_request_at[host] = now
                return
            wait = ready_at - now
        time.sleep(wait)


def _note_request(host):
    with _throttle_lock:
        _last_request_at[host] = time.monotonic()


def clear_cache():
    """Forget cached responses (used when the user re-searches deliberately)."""
    _response_cache.clear()


def _fetch(url, timeout=20, headers=None, binary=False, retries=2, use_cache=True,
           min_interval=None):
    """GET a URL, retrying briefly on rate limits and transient server errors.

    Google Books rate limits unkeyed callers by IP address and returns 429; a
    short backoff usually clears it.  Retrying is pointless for a 404 or a bad
    API key, so only 429 and 5xx are retried.
    """
    if use_cache and url in _response_cache:
        return _response_cache[url]

    host = urllib.parse.urlparse(url).hostname or ""
    request = urllib.request.Request(url)
    request.add_header("User-Agent", USER_AGENT)
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    context = ssl.create_default_context()

    attempt = 0
    while True:
        _throttle(host, min_interval)
        try:
            opener_kwargs = {"timeout": timeout}
            if url.lower().startswith("https"):
                opener_kwargs["context"] = context
            with urllib.request.urlopen(request, **opener_kwargs) as response:
                data = response.read()
            result = data if binary else data.decode("utf-8", "replace")
            if use_cache and len(_response_cache) < _CACHE_LIMIT:
                _response_cache[url] = result
            return result
        except (urllib.error.URLError, urllib.error.HTTPError, socket.timeout,
                ssl.SSLError, OSError) as exc:
            # A refusal still counts as a request as far as the server's
            # rate limiter is concerned, so restart this host's clock.
            _note_request(host)
            if attempt >= retries or not _worth_retrying(exc):
                raise IOError(_describe_http_error(exc))
            time.sleep(_retry_delay(exc, attempt))
            attempt += 1


def _worth_retrying(exc):
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code == 429 or 500 <= exc.code < 600
    return isinstance(exc, (socket.timeout, urllib.error.URLError))


def _retry_delay(exc, attempt):
    """How long to wait before retrying.

    A server's own Retry-After wins.  Otherwise a rate limit gets a longer,
    growing wait (3s then 6s) because the quota needs time to refill, while an
    ordinary network hiccup only needs a moment (1.5s then 3s).
    """
    if isinstance(exc, urllib.error.HTTPError):
        header = (exc.headers or {}).get("Retry-After")
        if header:
            try:
                return max(0.5, min(float(header), 15.0))
            except (TypeError, ValueError):
                pass
        if exc.code == 429:
            return 3.0 * (attempt + 1)
    return 1.5 * (attempt + 1)


def _describe_http_error(exc):
    """A short, plain-English reason fit for the one-line status log."""
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 404:
            return "no record for this identifier"
        if exc.code in (401, 403):
            return "access denied (HTTP %d)" % exc.code
        if exc.code == 429:
            return "rate limited - this source caps how often one address may ask"
        if 500 <= exc.code < 600:
            return "the server is having trouble (HTTP %d)" % exc.code
        return "HTTP %d" % exc.code

    # Socket errors carry a paragraph of Windows text; reduce it to the gist.
    reason = getattr(exc, "reason", None) or exc
    code = getattr(reason, "errno", None)
    text = str(reason).lower()
    if isinstance(exc, socket.timeout) or "timed out" in text or code in (10060,):
        return "timed out - the site did not respond"
    if code in (11001, 11004) or "name or service" in text or "getaddrinfo" in text:
        return "could not look up the address - is the internet connection up?"
    if code in (10061,) or "refused" in text:
        return "the connection was refused"
    return "could not reach the site"


def _fetch_json(url, timeout=20, headers=None, min_interval=None):
    text = _fetch(url, timeout=timeout, headers=headers, min_interval=min_interval)
    try:
        return json.loads(text)
    except ValueError:
        raise IOError("the server returned a response that was not valid JSON")


# --------------------------------------------------------------------------
# 1. Library of Congress
# --------------------------------------------------------------------------

def _marc_text(field, code):
    """First subfield value for ``code`` within a MARC datafield element."""
    for sub in field.findall(MARC_NS + "subfield"):
        if sub.get("code") == code:
            return (sub.text or "").strip()
    return ""


def _marc_all(field, code):
    return [
        (sub.text or "").strip()
        for sub in field.findall(MARC_NS + "subfield")
        if sub.get("code") == code and (sub.text or "").strip()
    ]


def _tidy_name(name):
    """Normalise a MARC name heading to the catalog's "Last, First" style."""
    name = re.sub(r"\s+", " ", (name or "").strip())
    name = name.rstrip(",;:/ ")
    # Drop trailing dates: "Twain, Mark, 1835-1910" -> "Twain, Mark"
    name = re.sub(r",\s*\d{4}\s*-\s*(\d{4})?\.?$", "", name)
    name = name.rstrip(",;:/ ")
    # A single trailing period after an initial is meaningful ("Rutsky, R. L.")
    if name.endswith(".") and not re.search(r"\b[A-Z]\.$", name):
        name = name[:-1].rstrip()
    return name


def _tidy_title(title, subtitle):
    title = re.sub(r"\s+", " ", (title or "").strip()).rstrip(" /:;,")
    subtitle = re.sub(r"\s+", " ", (subtitle or "").strip()).rstrip(" /:;,")
    if subtitle:
        return "%s: %s" % (title, subtitle)
    return title


def _year(text):
    m = re.search(r"(1[0-9]{3}|20[0-9]{2})", str(text or ""))
    return m.group(1) if m else ""


ROLE_KEYWORDS = [
    ("illustrators", ("illustrator", "illus", "artist", "photographer")),
    ("translators", ("translator", "translated")),
    ("editors", ("editor", "compiler", "edited", "compiled")),
]


def _role_from_relator(relator):
    text = (relator or "").lower()
    for key, words in ROLE_KEYWORDS:
        if any(word in text for word in words):
            return key
    return "authors"


def _roles_from_statement(statement):
    """Infer roles from a 245$c statement of responsibility.

    Older MARC records often omit the $e relator, but the statement itself says
    "edited by" / "illustrated by" / "translated by".
    """
    text = (statement or "").lower()
    roles = set()
    if re.search(r"\b(edited|compiled|selected)\b", text):
        roles.add("editors")
    if re.search(r"\b(translated|translation)\b", text):
        roles.add("translators")
    if re.search(r"\b(illustrat|pictures by|drawings by|photographs by)", text):
        roles.add("illustrators")
    return roles


def parse_marcxml(record_el):
    """Turn one MARC21 slim <record> element into a BookRecord."""
    import xml.etree.ElementTree as ET  # noqa: F401  (documents the element type)

    book = BookRecord()
    src = "Library of Congress"
    statement = ""
    isbns, series, langs = [], [], []
    added = []  # (role, name) from 700/710 fields

    for field in record_el.findall(MARC_NS + "controlfield"):
        if field.get("tag") == "008":
            text = field.text or ""
            if len(text) >= 38:
                langs.append(text[35:38].strip().lower())
        elif field.get("tag") == "001" and not book.get("internal_number"):
            # 001 is the LC control number for records from this catalog.
            pass

    for field in record_el.findall(MARC_NS + "datafield"):
        tag = field.get("tag")
        if tag == "010":
            lccn = normalize_lccn(_marc_text(field, "a"))
            if lccn:
                book.set("lccn", lccn, src)
        elif tag == "020":
            isbns.extend(clean_identifier(v) for v in _marc_all(field, "a"))
        elif tag == "022":
            book.fill("issn", format_issn(_marc_text(field, "a")), src)
        elif tag == "041":
            langs.extend(v.strip().lower() for v in _marc_all(field, "a"))
        elif tag == "050" and not book.get("lcc"):
            # Skip shelving strings such as "CPB Box no. 1797 vol. 3".
            raw = _marc_text(field, "a")
            if is_lcc_like(raw):
                book.set("lcc", normalize_lcc(raw), src)
        elif tag == "082" and not book.get("dds"):
            book.set("dds", normalize_dds(_marc_text(field, "a")), src)
        elif tag == "245":
            book.set("title", _tidy_title(_marc_text(field, "a"), _marc_text(field, "b")), src)
            statement = _marc_text(field, "c")
        elif tag == "100":
            book.people["authors"].append(_tidy_name(_marc_text(field, "a")))
        elif tag == "110":
            book.people["authors"].append(_tidy_name(_marc_text(field, "a")))
        elif tag in ("700", "710"):
            name = _tidy_name(_marc_text(field, "a"))
            if name:
                added.append((_role_from_relator(_marc_text(field, "e")), name))
        elif tag in ("260", "264"):
            book.fill("publisher", _tidy_name(_marc_text(field, "b")), src)
            book.fill("edition_year", _year(_marc_text(field, "c")), src)
        elif tag in ("490", "830"):
            name = _marc_text(field, "a").rstrip(" ;,/")
            number = _marc_text(field, "v")
            if name:
                series.append("%s #%s" % (name, number.lstrip("v. ")) if number else name)

    # Added entries with no explicit relator inherit the role implied by the
    # statement of responsibility, when that points at exactly one role.
    implied = _roles_from_statement(statement)
    default_role = implied.pop() if len(implied) == 1 else "authors"
    for role, name in added:
        target = default_role if role == "authors" else role
        if name not in book.people[target]:
            book.people[target].append(name)

    # A record whose only names are editors puts them under 100 by mistake
    # surprisingly often; if the statement says "edited by" and we found no
    # added editors, move the main entry across.
    if "editors" in _roles_from_statement(statement) and not book.people["editors"]:
        if book.people["authors"] and default_role == "editors":
            book.people["editors"] = book.people["authors"]
            book.people["authors"] = []

    for role in list(book.people):
        book.people[role] = [n for n in book.people[role] if n]
        if book.people[role]:
            book.sources.setdefault(role, src)

    for isbn in isbns:
        if len(isbn) == 13:
            book.fill("isbn13", format_isbn13(isbn), src)
        elif len(isbn) == 10:
            book.fill("isbn10", format_isbn10(isbn), src)
    if series:
        book.fill("series", series[0], src)
    for code in langs:
        name = LANGUAGE_NAMES.get(code)
        if name:
            book.fill("language", name, src)
            break
    return book


def _loc_search(query, timeout=20, maximum=1):
    """Run one SRU query. Returns (list of BookRecord, error message or "")."""
    import xml.etree.ElementTree as ET

    url = "%s?%s" % (LOC_SRU, urllib.parse.urlencode({
        "version": "1.1",
        "operation": "searchRetrieve",
        "query": query,
        "maximumRecords": str(maximum),
        "recordSchema": "marcxml",
    }))
    try:
        text = _fetch(url, timeout=timeout)
    except IOError as exc:
        return [], str(exc)
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return [], "unreadable response"

    diagnostic = root.find(".//{http://www.loc.gov/zing/srw/diagnostic/}message")
    if diagnostic is not None and (diagnostic.text or "").strip():
        return [], diagnostic.text.strip()

    books = [parse_marcxml(el) for el in root.findall(".//" + MARC_NS + "record")]
    return [b for b in books if not b.is_empty()], ""


def lookup_loc(identifier, kind, timeout=20):
    """Query the Library of Congress catalog by identifier."""
    index = "bath.isbn" if kind == "isbn" else "bath.issn"
    books, error = _loc_search(
        "%s=%s" % (index, clean_identifier(identifier)), timeout=timeout
    )
    if error:
        return None, "Library of Congress: %s" % error
    if not books:
        return None, "Library of Congress: no matching record"
    return books[0], "Library of Congress: found '%s'" % (books[0].get("title") or "record")


def _quote(text):
    """Quote a phrase for a CQL query, dropping characters CQL treats specially."""
    return '"%s"' % re.sub(r'["\\=<>()/]', " ", str(text or "")).strip()


def lookup_loc_by_title(title, author, timeout=20):
    """Second-pass LC search by title and author, for books whose ISBN LC lacks.

    LC catalogs the original edition, so a later reprint's ISBN often misses
    even though the work is there.  Matching on title *and* author keeps this
    reasonably precise, but it is still a fuzzy match on a different edition, so
    the caller only takes the classification fields from the result and tells
    the user to check them.
    """
    if not title or not author:
        return None, ""
    # The author column holds "Last, First"; the surname alone matches best.
    surname = str(author).split(",")[0].strip()
    query = "bath.title=%s and bath.author=%s" % (_quote(title), _quote(surname))
    books, error = _loc_search(query, timeout=timeout)
    if error:
        return None, "Library of Congress (by title): %s" % error
    if not books:
        return None, "Library of Congress (by title): no match for '%s'" % title
    book = books[0]
    if not (book.get("dds") or book.get("lcc")):
        return None, "Library of Congress (by title): match had no classification"
    return book, (
        "Library of Congress (by title): matched '%s' - DDS/LCC taken from a "
        "different edition, please verify" % (book.get("title") or "record")
    )


# --------------------------------------------------------------------------
# 2. Open Library
# --------------------------------------------------------------------------

def _openlibrary_edition(digits, book, src, timeout):
    """Top up a record from the edition endpoint.

    The "jscmd=data" view resolves author names and cover URLs but frequently
    omits the classifications, which the raw edition record does carry.  This
    runs only when something is actually missing, so the common case is still
    a single request.
    """
    try:
        data = _fetch_json("https://openlibrary.org/isbn/%s.json" % digits, timeout=timeout)
    except IOError:
        return  # a bonus lookup; its failure is not worth reporting
    for field, key, normaliser in (
        ("dds", "dewey_decimal_class", normalize_dds),
        ("lcc", "lc_classifications", normalize_lcc),
        ("lccn", "lccn", normalize_lccn),
    ):
        values = data.get(key) or []
        if isinstance(values, str):
            values = [values]
        if values and not book.get(field):
            if field == "lcc" and not is_lcc_like(values[0]):
                continue
            book.set(field, normaliser(values[0]), src)
    book.fill("series", (data.get("series") or [""])[0] if data.get("series") else "", src)


def lookup_openlibrary(identifier, kind, timeout=20):
    digits = clean_identifier(identifier)
    key = ("ISBN:" if kind == "isbn" else "ISSN:") + digits
    url = "%s?%s" % (OPENLIBRARY_API, urllib.parse.urlencode({
        "bibkeys": key, "format": "json", "jscmd": "data",
    }))
    try:
        payload = _fetch_json(url, timeout=timeout)
    except IOError as exc:
        return None, "Open Library: %s" % exc
    data = payload.get(key)
    if not data:
        return None, "Open Library: no matching record"

    book = BookRecord()
    src = "Open Library"
    book.set("title", _tidy_title(data.get("title"), data.get("subtitle")), src)
    book.set_people("authors", [_tidy_name(_flip_name(a.get("name"))) for a in data.get("authors") or []], src)
    book.set("publisher", ", ".join(p.get("name", "") for p in data.get("publishers") or []), src)
    book.set("edition_year", _year(data.get("publish_date")), src)
    for ident_key, field in (("isbn_13", "isbn13"), ("isbn_10", "isbn10")):
        values = (data.get("identifiers") or {}).get(ident_key) or []
        if values:
            book.set(field, format_isbn13(values[0]) if field == "isbn13" else format_isbn10(values[0]), src)
    for classification, field, normaliser in (
        ("dewey_decimal_class", "dds", normalize_dds),
        ("lc_classifications", "lcc", normalize_lcc),
    ):
        values = (data.get("classifications") or {}).get(classification) or []
        if values:
            book.set(field, normaliser(values[0]), src)
    lccns = (data.get("identifiers") or {}).get("lccn") or []
    if lccns:
        book.set("lccn", normalize_lccn(lccns[0]), src)
    cover = data.get("cover") or {}
    book.cover_url = cover.get("large") or cover.get("medium") or cover.get("small") or ""
    if not book.cover_url and kind == "isbn":
        book.cover_url = "https://covers.openlibrary.org/b/isbn/%s-L.jpg?default=false" % digits

    # Open Library is now the main fallback, so it is worth a second request to
    # recover a classification the summary view left out.
    if kind == "isbn" and not (book.get("dds") and book.get("lcc")):
        _openlibrary_edition(digits, book, src, timeout)

    if book.is_empty():
        return None, "Open Library: record was empty"
    return book, "Open Library: found '%s'" % (book.get("title") or "record")


def lookup_google_books(identifier, kind, timeout=20, api_key=""):
    digits = clean_identifier(identifier)
    term = "isbn:%s" % digits if kind == "isbn" else "issn:%s" % digits
    params = {"q": term, "maxResults": "1"}
    if api_key:
        params["key"] = api_key
    url = "%s?%s" % (GOOGLE_BOOKS_API, urllib.parse.urlencode(params))
    try:
        payload = _fetch_json(
            url, timeout=timeout,
            min_interval=GOOGLE_KEYED_MIN_INTERVAL if api_key else None,
        )
    except IOError as exc:
        hint = ""
        text = str(exc)
        if "rate limited" in text and not api_key:
            hint = " (adding a Google Books API key in Settings raises this limit)"
        elif api_key and ("access denied" in text or "HTTP 400" in text):
            # Google rejects a malformed or unauthorised key with 400, not 401.
            hint = " - check the API key in Settings; it may be invalid, restricted, " \
                   "or the Books API may not be enabled for it"
        return None, "Google Books: %s%s" % (exc, hint)
    items = payload.get("items") or []
    if not items:
        return None, "Google Books: no matching record"
    info = items[0].get("volumeInfo") or {}

    book = BookRecord()
    src = "Google Books"
    book.set("title", _tidy_title(info.get("title"), info.get("subtitle")), src)
    book.set_people("authors", [_tidy_name(_flip_name(a)) for a in info.get("authors") or []], src)
    book.set("publisher", (info.get("publisher") or "").strip(), src)
    book.set("edition_year", _year(info.get("publishedDate")), src)
    for ident in info.get("industryIdentifiers") or []:
        if ident.get("type") == "ISBN_13":
            book.fill("isbn13", format_isbn13(ident.get("identifier")), src)
        elif ident.get("type") == "ISBN_10":
            book.fill("isbn10", format_isbn10(ident.get("identifier")), src)
    language = (info.get("language") or "").strip().lower()
    if language:
        book.fill("language", LANGUAGE_NAMES.get(_iso639_1_to_2(language), ""), src)
    links = info.get("imageLinks") or {}
    book.cover_url = (
        links.get("extraLarge") or links.get("large") or links.get("medium")
        or links.get("thumbnail") or ""
    ).replace("http://", "https://")
    if book.is_empty():
        return None, "Google Books: record was empty"
    return book, "Google Books: found '%s'" % (book.get("title") or "record")


_ISO1_TO_ISO2 = {
    "es": "spa", "fr": "fre", "de": "ger", "it": "ita", "la": "lat",
    "ja": "jpn", "zh": "chi", "ru": "rus", "pt": "por", "he": "heb",
    "ar": "ara", "el": "gre", "nl": "dut", "sv": "swe", "no": "nor",
    "da": "dan", "pl": "pol", "ko": "kor", "hi": "hin", "ga": "gle",
    "cy": "wel", "tr": "tur", "vi": "vie", "th": "tha", "sw": "swa",
    "cs": "cze", "hu": "hun", "fi": "fin", "uk": "ukr", "yi": "yid",
    "fa": "per", "en": "eng",
}


def _iso639_1_to_2(code):
    return _ISO1_TO_ISO2.get(code[:2], code[:3])


def _flip_name(name):
    """Convert "Mark Twain" to "Twain, Mark" to match the catalog's style.

    Names that already contain a comma are left alone.
    """
    name = re.sub(r"\s+", " ", (name or "").strip())
    if not name or "," in name:
        return name
    parts = name.split(" ")
    if len(parts) < 2:
        return name
    # Keep suffixes with the given names: "Martin Luther King Jr."
    suffixes = {"jr.", "jr", "sr.", "sr", "ii", "iii", "iv", "phd", "ph.d."}
    if parts[-1].lower() in suffixes and len(parts) > 2:
        last, rest = parts[-2], parts[:-2] + [parts[-1]]
    else:
        last, rest = parts[-1], parts[:-1]
    return "%s, %s" % (last, " ".join(rest))


# --------------------------------------------------------------------------
# Merge
# --------------------------------------------------------------------------

def merge_records(records):
    """Combine per-source records; earlier sources win contested fields."""
    merged = BookRecord()
    for book in records:
        if book is None:
            continue
        for key, value in book.scalars.items():
            if value:
                source = book.sources.get(key)
                merged.fill(key, value, source)
        for role, names in book.people.items():
            if names:
                merged.fill_people(role, names, book.sources.get(role))
        if not merged.cover_url and book.cover_url:
            merged.cover_url = book.cover_url
    return merged


def lookup(identifier, config, progress=None):
    """Run the full search chain for one identifier.

    ``progress`` is an optional callable taking a status string; it is invoked
    from the calling thread, so callers on the GUI thread must marshal it.
    Returns ``(BookRecord, [notes])``.
    """
    kind = identifier_kind(identifier)
    timeout = config.request_timeout
    notes = []
    results = []

    def step(name, fn):
        if progress:
            progress("Searching %s..." % name)
        book, note = fn()
        notes.append(note)
        if book is not None:
            results.append(book)
        return book

    def still_incomplete():
        so_far = merge_records(results)
        return (
            not so_far.get("title")
            or not so_far.get("dds")
            or not so_far.get("lcc")
            or not so_far.all_people()
            or not so_far.cover_url
        )

    loc_book = step("the Library of Congress", lambda: lookup_loc(identifier, kind, timeout))

    if still_incomplete():
        step("Open Library", lambda: lookup_openlibrary(identifier, kind, timeout))
    else:
        notes.append("Open Library: skipped (the Library of Congress record was complete)")

    # Google Books rate limits unkeyed callers per IP, so it is only asked when
    # the first two sources genuinely left a gap.
    if still_incomplete():
        step("Google Books", lambda: lookup_google_books(
            identifier, kind, timeout, getattr(config, "google_books_key", "")))
    else:
        notes.append("Google Books: skipped (nothing left to fill in)")

    merged = merge_records(results)

    # If LC had no record for this exact ISBN but the web search produced a
    # title and author, ask LC again by name -- that is where the Dewey and LC
    # classifications live, and reprints routinely carry an ISBN LC never saw.
    if not merged.get("dds") or not merged.get("lcc"):
        people = merged.people.get("authors") or merged.people.get("editors") or []
        if merged.get("title") and people:
            if progress:
                progress("Re-checking the Library of Congress by title...")
            by_title, note = lookup_loc_by_title(merged.get("title"), people[0], timeout)
            if note:
                notes.append(note)
            if by_title is not None:
                # Classification only: the matched edition may differ in every
                # other respect, so nothing else is copied across.
                for key in ("dds", "lcc", "lccn"):
                    merged.fill(key, by_title.get(key), "Library of Congress (by title)")

    # Fill in whichever ISBN form is missing so both columns are populated.
    if merged.get("isbn13") and not merged.get("isbn10"):
        merged.set("isbn10", isbn13_to_10(merged.get("isbn13")), "derived")
    if merged.get("isbn10") and not merged.get("isbn13"):
        merged.set("isbn13", isbn10_to_13(merged.get("isbn10")), "derived")

    # Record the identifier the user actually typed if nothing supplied it.
    digits = clean_identifier(identifier)
    if kind == "isbn" and len(digits) == 13:
        merged.fill("isbn13", format_isbn13(digits), "entered")
    elif kind == "isbn" and len(digits) == 10:
        merged.fill("isbn10", format_isbn10(digits), "entered")
    elif kind == "issn":
        merged.fill("issn", format_issn(digits), "entered")

    # The catalog marks an unknown classification with "zzz" rather than a blank.
    for key in ("dds", "lcc"):
        if not merged.get(key):
            merged.set(key, "zzz", "default")

    # The internal library number is the shelving number, so it starts life as
    # the LC classification -- not the LCCN, which is the catalog record's id
    # and lives in its own column.  The user can overwrite it.
    merged.fill("internal_number", merged.get("lcc"), "from LCC")

    if loc_book is None:
        notes.append(
            "Note: no Library of Congress record, so DDS/LCC may need checking by hand."
        )
    if any("rate limited" in note for note in notes):
        notes.append(
            "Note: a source was rate limited. Everything found above is still good; "
            "wait a minute and search again if a field you want is blank."
        )
    return merged, notes


def download_cover(url, timeout=20):
    """Fetch a cover image. Returns ``(bytes, extension)`` or raises IOError."""
    if not url:
        raise IOError("no cover image was found for this book")
    data = _fetch(url, timeout=timeout, binary=True)
    if len(data) < 1024:
        # Open Library serves a 1x1 pixel placeholder when it has no cover.
        raise IOError("the cover image was empty or a placeholder")
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return data, ".png"
    if data[:3] == b"\xff\xd8\xff":
        return data, ".jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return data, ".gif"
    raise IOError("the downloaded file was not a recognised image")
