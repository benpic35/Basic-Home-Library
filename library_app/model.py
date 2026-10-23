"""The in-memory representation of one catalog entry."""

from .schema import ROLES, SCALAR_FIELDS, BOOLEAN_SCALARS, TRUE_VALUE


class BookRecord(object):
    """One book: scalar fields, person lists, and a set of subjects.

    Values are always plain strings so that what the user sees in the form is
    exactly what lands in the spreadsheet.
    """

    def __init__(self):
        self.scalars = {key: "" for key, _h, _l in SCALAR_FIELDS}
        self.people = {key: [] for key, _b, _l, _n in ROLES}
        self.subjects = set()
        # Where each field came from, for the provenance column in the UI.
        self.sources = {}
        # Populated during lookup; the cover is only downloaded on confirm.
        self.cover_url = ""
        # Set when the record was loaded from the master file.
        self.row_index = None

    # -- scalar access -------------------------------------------------------
    def get(self, key):
        return self.scalars.get(key, "")

    def set(self, key, value, source=None):
        text = "" if value is None else str(value).strip()
        if key in BOOLEAN_SCALARS:
            text = TRUE_VALUE if text.strip().lower() in ("yes", "y", "true", "1", "x") else ""
        self.scalars[key] = text
        if source and text:
            self.sources.setdefault(key, source)

    def fill(self, key, value, source=None):
        """Set ``key`` only if it is currently empty. Used when merging sources."""
        if not self.get(key) and value:
            self.set(key, value, source)
            return True
        return False

    # -- people --------------------------------------------------------------
    def set_people(self, role, names, source=None):
        cleaned = [n.strip() for n in names if n and n.strip()]
        self.people[role] = cleaned
        if source and cleaned:
            self.sources.setdefault(role, source)

    def fill_people(self, role, names, source=None):
        if not self.people.get(role):
            self.set_people(role, names, source)
            return True
        return False

    def all_people(self):
        out = []
        for key, _base, _label, _n in ROLES:
            out.extend(self.people.get(key, []))
        return out

    # -- subjects ------------------------------------------------------------
    def add_subject(self, header):
        if header:
            self.subjects.add(header)

    # -- convenience ---------------------------------------------------------
    def summary(self):
        """Short one-line description, for result lists and confirmations."""
        title = self.get("title") or "(untitled)"
        authors = self.people.get("authors") or self.people.get("editors") or []
        who = authors[0] if authors else ""
        year = self.get("edition_year")
        bits = [title]
        if who:
            bits.append(who)
        if year:
            bits.append(year)
        return "  -  ".join(bits)

    def is_empty(self):
        return not any(self.scalars.values()) and not self.all_people() and not self.subjects

    def signature(self):
        """A hashable snapshot of everything the user can edit.

        Two records with equal signatures hold the same data, which is how the
        app decides whether a form has unsaved changes.  ``date_added`` is
        excluded because the app fills it in rather than the user.
        """
        scalars = tuple(
            sorted((k, v) for k, v in self.scalars.items() if k != "date_added")
        )
        people = tuple(sorted((role, tuple(names)) for role, names in self.people.items()))
        return (scalars, people, tuple(sorted(self.subjects)), self.cover_url)

    def copy(self):
        other = BookRecord()
        other.scalars = dict(self.scalars)
        other.people = {k: list(v) for k, v in self.people.items()}
        other.subjects = set(self.subjects)
        other.sources = dict(self.sources)
        other.cover_url = self.cover_url
        other.row_index = self.row_index
        return other
