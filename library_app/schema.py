"""Column schema for the master catalog.

These column names and their order define a freshly created master file, and are
also what an existing spreadsheet is matched against when the app opens one.
Several headers contain embedded newlines (e.g. "Edition\nYear"); those are
preserved verbatim when writing, while the UI shows a clean label.
"""

import re

# --- Person roles -----------------------------------------------------------
# Each role owns a block of numbered columns.  ``base`` is the header stem, and
# ``initial`` is how many slots the example catalog ships with.  The app grows a
# block by appending "<base> <n>" columns when a book needs more people.
ROLES = [
    ("authors", "Author", "Authors", 4),
    ("translators", "Translator", "Translators", 2),
    ("editors", "Editor/Compiler", "Editors / Compilers", 2),
    ("illustrators", "Illustrator", "Illustrators", 1),
]

ROLE_BY_KEY = {key: (base, label, initial) for key, base, label, initial in ROLES}

# --- Subject categories -----------------------------------------------------
# Header text on the left (exactly as stored in the workbook, newlines and all),
# display label on the right.
DEFAULT_SUBJECTS = [
    ("Nonfiction", "Nonfiction"),
    ("History", "History"),
    ("Science", "Science"),
    ("Biography", "Biography"),
    ("Autobiography", "Autobiography"),
    ("Memoir", "Memoir"),
    ("Anthology", "Anthology"),
    ("Recipes", "Recipes"),
    ("Travel", "Travel"),
    ("Poetry", "Poetry"),
    ("Graphic\nNovel", "Graphic Novel"),
    ("Coffeetable", "Coffeetable"),
    ("Board", "Board"),
    ("Picture", "Picture"),
    ("Young\nAdult", "Young Adult"),
    ("Fiction", "Fiction"),
    ("Historical\nFiction", "Historical Fiction"),
    ("Mystery", "Mystery"),
    ("Sci-Fi", "Sci-Fi"),
    ("Fantasy", "Fantasy"),
    ("Myth", "Myth"),
    ("Spirituality", "Spirituality"),
    ("Self-Help", "Self-Help"),
    ("Pop-Up", "Pop-Up"),
    ("Puzzles", "Puzzles"),
    ("Crafts", "Crafts"),
    ("Jokes", "Jokes"),
    ("Easter", "Easter"),
    ("Halloween", "Halloween"),
    ("Christmas", "Christmas"),
    ("Other\nHoliday", "Other Holiday"),
]

# --- Scalar (single value) fields ------------------------------------------
# key -> (header text, display label)
SCALAR_FIELDS = [
    ("internal_number", "Internal Library Number", "Internal Library Number"),
    ("title", "Title", "Title"),
    ("series", "Series", "Series"),
    ("isbn10", "ISBN 10", "ISBN 10"),
    ("isbn13", "ISBN 13", "ISBN 13"),
    ("issn", "ISSN", "ISSN"),
    ("edition_year", "Edition\nYear", "Edition Year"),
    ("original_year", "Original\nPub Year", "Original Pub Year"),
    ("dds", "DDS", "DDS (Dewey)"),
    # Two different things that are easy to confuse: LCC is the shelving
    # classification ("PN1994"), LCCN is the catalog record's control number
    # ("2013016108").  They are kept in separate columns.
    ("lcc", "LCC", "LCC (classification)"),
    ("lccn", "LCCN", "LCCN (control number)"),
    ("publisher", "Publisher", "Publisher"),
    ("location", "Location", "Location"),
    ("origin", "Origin", "Origin"),
    ("notes", "Notes", "Notes"),
    ("signed", "Signed \nby Author", "Signed by Author"),
    ("language", "Language", "Language"),
    ("cover_image", "Cover Image", "Cover Image"),
    ("date_added", "Date Added", "Date Added"),
]

SCALAR_HEADER_BY_KEY = {key: header for key, header, _ in SCALAR_FIELDS}
SCALAR_LABEL_BY_KEY = {key: label for key, _, label in SCALAR_FIELDS}

# Fields written as "yes"/"" rather than free text.
BOOLEAN_SCALARS = {"signed"}

# Columns the app no longer manages but that may still exist in an older master
# file.  They are neither read nor written, and -- importantly -- are not
# mistaken for subject categories, so the data already in them is left intact.
# Add a header here when a field is dropped from a later version.
RETIRED_HEADERS = []

# Fields that should never be blanked out by a source that knows less than the
# user does; the UI always wins for these.
TRUE_VALUE = "yes"

# Value used by the existing catalog when a classification is unknown.
UNKNOWN_CLASS = "zzz"


def default_column_order():
    """Header names, in order, for a brand-new master workbook.

    Title first through Language last, with the app's extra columns grouped
    sensibly around them.
    """
    cols = ["Internal Library Number", "Title"]
    for _key, base, _label, initial in ROLES:
        cols.extend(role_column_names(base, initial))
    cols += [
        "Series",
        "ISBN 10",
        "ISBN 13",
        "ISSN",
        "Edition\nYear",
        "Original\nPub Year",
        "DDS",
        "LCC",
        "LCCN",
        "Publisher",
        "Location",
        "Origin",
        "Notes",
        "Signed \nby Author",
    ]
    cols += [header for header, _label in DEFAULT_SUBJECTS]
    cols += ["Language", "Cover Image", "Date Added"]
    return cols


def role_column_names(base, count):
    """Column headers for a role block.

    The example catalog uses a bare "Illustrator" for a single-slot role and
    numbered headers ("Author 1"..) for multi-slot roles.  Slots beyond the
    first are always numbered, and slot 1 of a formerly-bare role keeps its
    bare name so existing files stay readable.
    """
    if count <= 1:
        return [base]
    return ["%s %d" % (base, n) for n in range(1, count + 1)]


_WS_RE = re.compile(r"\s+")


def norm_header(text):
    """Normalise a header for matching: case-fold and collapse whitespace.

    Lets "Edition\nYear", "Edition Year" and "edition  year" all resolve to one
    column, which matters because several headers carry embedded newlines.
    """
    if text is None:
        return ""
    return _WS_RE.sub(" ", str(text)).strip().lower()


def display_header(text):
    """Human-readable form of a stored header (newlines become spaces)."""
    return _WS_RE.sub(" ", str(text or "")).strip()


_ROLE_COL_RE = re.compile(r"^(?P<base>.+?)(?:\s+(?P<num>\d+))?$")


def parse_role_column(header, base):
    """Return the 1-based slot number if ``header`` belongs to role ``base``.

    "Author" -> 1, "Author 3" -> 3, "Authorship" -> None.
    """
    m = _ROLE_COL_RE.match(display_header(header))
    if not m:
        return None
    if norm_header(m.group("base")) != norm_header(base):
        return None
    num = m.group("num")
    return int(num) if num else 1


# --- Classification normalisers --------------------------------------------

# Letters, then the class number, then an optional decimal *digit* part.  A
# following ".M66" is a Cutter number, not part of the class, so matching stops
# before it -- the catalog stores "PN6737", not "PN6737.M66".
_LCC_RE = re.compile(r"^\s*([A-Za-z]{1,3})\s*(\d+)(\.\d+)?")


def is_lcc_like(value):
    """True if ``value`` looks like a real LC class number.

    MARC 050 sometimes holds shelving strings such as "CPB Box no. 1797 vol. 3";
    those should not be mistaken for a classification.
    """
    return bool(_LCC_RE.match(str(value or "")))


def normalize_lcc(value):
    """Reduce an LC call number to the catalog's class-only, 4-digit style.

    The existing catalog stores "AG0005", "SF0433", "PN1994" -- letters followed
    by a zero-padded 4-digit number, with any Cutter number dropped.  Values
    that do not look like a class number are returned trimmed but untouched.
    """
    if not value:
        return ""
    m = _LCC_RE.match(str(value))
    if not m:
        return str(value).strip()
    letters, number, decimal = m.group(1).upper(), m.group(2), m.group(3) or ""
    return "%s%s%s" % (letters, number.zfill(4), decimal)


def normalize_dds(value):
    """Reduce a MARC 082 Dewey number to the catalog's abridged form.

    MARC marks the recommended abridgement points with "/", e.g. "791.43/75"
    (stored as "791.43") and "636.7/088/7" (stored as "636.7").  The mark can
    also fall before the decimal point -- "812/.54" is Dewey 812.54, not 812 --
    so segments are accumulated until the number has a decimal part.
    """
    if not value:
        return ""
    segments = [s for s in str(value).split("/")]
    text = ""
    for segment in segments:
        text += segment
        if "." in text:
            break
    return text.strip().rstrip(".").strip()


_LCCN_RE = re.compile(r"[^0-9A-Za-z]")


def normalize_lccn(value):
    """Strip the padding spaces and punctuation LC puts around an LCCN."""
    if not value:
        return ""
    return _LCCN_RE.sub("", str(value)).strip()


def lccn_url(lccn):
    """Permalink for an LCCN, matching the links already in the catalog."""
    clean = normalize_lccn(lccn)
    return "https://lccn.loc.gov/%s" % clean if clean else ""
