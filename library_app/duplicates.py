"""Finding books already in the catalog that a new entry would duplicate.

Two kinds of check happen:

* A *hard* conflict on the internal library number.  That number is the
  library's own handle for a book, so two entries must never share one; the
  app refuses the entry and offers a free number instead.
* *Soft* matches on any shared identifier or classification, or on title and
  author together.  These are often legitimate -- a second copy, or two books
  filed in the same Dewey class -- so the user is shown what matched and
  decides.

Placeholders never count as a match.  The catalog writes "zzz" where a
classification is unknown, and treating that as a shared value would make every
unclassified book look like a duplicate of every other.
"""

import re

# Values that mean "not known" rather than an actual identifier.
PLACEHOLDERS = {"", "zzz", "n/a", "na", "none", "unknown", "-"}

# Fields compared one-for-one, in the order they are reported.  The labels are
# kept short because several of them are listed together in one table cell.
MATCH_FIELDS = [
    ("internal_number", "Internal No."),
    ("isbn13", "ISBN 13"),
    ("isbn10", "ISBN 10"),
    ("issn", "ISSN"),
    ("lccn", "LCCN"),
    ("lcc", "LCC"),
    ("dds", "DDS"),
]

_PUNCT = re.compile(r"[^0-9a-z]+")


def _matchable(value):
    """Normalise a value for comparison, or return "" if it means nothing."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if text.lower() in PLACEHOLDERS:
        return ""
    return text.lower()


def _identifier_key(value):
    """Compare identifiers ignoring hyphens and spacing: 0-345-41794-1 == 0345417941."""
    text = _matchable(value)
    return _PUNCT.sub("", text) if text else ""


def _title_key(title):
    text = _matchable(title)
    return _PUNCT.sub(" ", text).strip() if text else ""


def _people_keys(record):
    """Names to compare on, preferring authors but falling back to editors."""
    names = record.people.get("authors") or record.people.get("editors") or []
    return {_PUNCT.sub(" ", _matchable(n)).strip() for n in names if _matchable(n)}


def internal_number_conflict(records, record, exclude_row=None):
    """The existing entry already using this internal number, or None."""
    mine = _matchable(record.get("internal_number"))
    if not mine:
        return None
    for other in records:
        if exclude_row is not None and other.row_index == exclude_row:
            continue
        if _matchable(other.get("internal_number")) == mine:
            return other
    return None


def suggest_internal_number(records, base, exclude_row=None):
    """A free internal number close to ``base``, e.g. PN1994 -> PN1994-2."""
    base = re.sub(r"\s+", " ", str(base or "")).strip()
    if not base:
        return ""
    used = set()
    for other in records:
        if exclude_row is not None and other.row_index == exclude_row:
            continue
        value = _matchable(other.get("internal_number"))
        if value:
            used.add(value)
    if _matchable(base) not in used:
        return base
    # Do not stack suffixes: PN1994-2 -> PN1994-3, not PN1994-2-2.
    stem = re.sub(r"-\d+$", "", base)
    for n in range(2, 1000):
        candidate = "%s-%d" % (stem, n)
        if _matchable(candidate) not in used:
            return candidate
    return base


def find_matches(records, record, exclude_row=None):
    """Entries that share something with ``record``.

    Returns a list of ``(existing_record, [reasons])``, most-matched first, so
    an entry duplicating three fields is presented ahead of a Dewey coincidence.
    """
    mine_fields = {}
    for key, label in MATCH_FIELDS:
        value = (_identifier_key(record.get(key))
                 if key in ("isbn13", "isbn10", "issn", "lccn")
                 else _matchable(record.get(key)))
        if value:
            mine_fields[key] = (value, label)

    my_title = _title_key(record.get("title"))
    my_people = _people_keys(record)

    hits = []
    for other in records:
        if exclude_row is not None and other.row_index == exclude_row:
            continue
        reasons = []
        # Title alone is far too weak; title *and* a shared name is not, and it
        # is the most telling sign of a real duplicate -- so it leads the list,
        # and stays visible if the column has to truncate.
        if my_title and my_people and _title_key(other.get("title")) == my_title:
            if my_people & _people_keys(other):
                reasons.append("same title + author")
        # Then the identifiers, strongest first, in MATCH_FIELDS order.
        for key, label in MATCH_FIELDS:
            if key not in mine_fields:
                continue
            value = mine_fields[key][0]
            theirs = (_identifier_key(other.get(key))
                      if key in ("isbn13", "isbn10", "issn", "lccn")
                      else _matchable(other.get(key)))
            if theirs and theirs == value:
                reasons.append("same %s" % label)
        if reasons:
            hits.append((other, reasons))

    hits.sort(key=lambda pair: (-len(pair[1]), pair[0].row_index or 0))
    return hits


def find_by_identifier(records, identifier):
    """Entries whose ISBN or ISSN matches a raw identifier the user typed."""
    key = _identifier_key(identifier)
    if not key:
        return []
    hits = []
    for other in records:
        for field in ("isbn13", "isbn10", "issn"):
            if _identifier_key(other.get(field)) == key:
                hits.append(other)
                break
    return hits
