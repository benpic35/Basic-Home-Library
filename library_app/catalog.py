"""Reading and writing the master "Home Library Catalog" workbook.

Every operation opens the workbook, changes it and closes it again rather than
holding it open, so the file is never left locked while the user is thinking.
Columns are matched by header *name* (whitespace- and case-insensitively), so
the app keeps working if columns are reordered in Excel, and unknown columns
are simply left alone.
"""

import datetime
import os
import re
import shutil

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import MASTER_BASENAME
from .model import BookRecord
from .schema import (
    BOOLEAN_SCALARS,
    RETIRED_HEADERS,
    ROLES,
    SCALAR_FIELDS,
    SCALAR_HEADER_BY_KEY,
    TRUE_VALUE,
    default_column_order,
    display_header,
    lccn_url,
    norm_header,
    parse_role_column,
)

SHEET_NAME = "Books"
HEADER_ROW = 1
FIRST_DATA_ROW = 2

_SCALAR_KEY_BY_NORM = {norm_header(h): k for k, h, _l in SCALAR_FIELDS}
_RETIRED_NORM = {norm_header(h) for h in RETIRED_HEADERS}


class CatalogError(Exception):
    """A problem the user needs to know about, phrased for the status bar."""


class ColumnMap(object):
    """Where each field lives in the sheet, derived from the header row."""

    def __init__(self, headers):
        # headers: list of raw header strings, index 0 == column A
        self.headers = list(headers)
        self.scalars = {}      # field key -> 1-based column index
        self.roles = {key: [] for key, _b, _l, _n in ROLES}
        self.subjects = {}     # raw header -> 1-based column index

        for zero_based, raw in enumerate(headers):
            col = zero_based + 1
            if raw is None or not str(raw).strip():
                continue
            if norm_header(raw) in _RETIRED_NORM:
                # A column the app has dropped: leave it and its data alone,
                # and do not let it turn up as a subject checkbox.
                continue
            key = _SCALAR_KEY_BY_NORM.get(norm_header(raw))
            if key is not None:
                self.scalars.setdefault(key, col)
                continue
            slot = None
            for role_key, base, _label, _initial in ROLES:
                slot = parse_role_column(raw, base)
                if slot is not None:
                    self.roles[role_key].append((slot, col))
                    break
            if slot is not None:
                continue
            self.subjects[str(raw)] = col

        for role_key in self.roles:
            self.roles[role_key].sort()
            self.roles[role_key] = [col for _slot, col in self.roles[role_key]]

    def role_capacity(self, role_key):
        return len(self.roles.get(role_key, []))

    def subject_column(self, header):
        """Look up a subject column tolerantly (newlines/case do not matter)."""
        target = norm_header(header)
        for raw, col in self.subjects.items():
            if norm_header(raw) == target:
                return col
        return None

    def width(self):
        return len(self.headers)

    def managed_columns(self):
        """Every column the app owns and is therefore allowed to overwrite.

        Anything else in the sheet -- a retired column, or a column the user
        added by hand in Excel -- is left exactly as it is when a row is
        rewritten, so editing an entry never destroys data the app does not
        understand.
        """
        columns = set(self.scalars.values())
        for cols in self.roles.values():
            columns.update(cols)
        columns.update(self.subjects.values())
        return columns


class Catalog(object):
    def __init__(self, config):
        self.config = config
        # Version control: the master is archived once per app session, on the
        # first write, never on every save.
        self._backed_up_this_session = False
        self.last_backup_path = None

    # -- file plumbing -------------------------------------------------------
    @property
    def path(self):
        return self.config.master_path

    def exists(self):
        return os.path.isfile(self.path)

    def _open(self):
        try:
            wb = load_workbook(self.path)
        except (IOError, OSError) as exc:
            raise CatalogError("Could not open the master file:\n%s" % exc)
        ws = wb[SHEET_NAME] if SHEET_NAME in wb.sheetnames else wb[wb.sheetnames[0]]
        return wb, ws

    def _save(self, wb):
        try:
            wb.save(self.path)
        except PermissionError:
            raise CatalogError(
                "Could not save the master file because it is open in another "
                "program.\n\nClose '%s' in Excel and try again." % os.path.basename(self.path)
            )
        except (IOError, OSError) as exc:
            raise CatalogError("Could not save the master file:\n%s" % exc)

    def create_if_missing(self):
        """Create a master workbook in the app's standard column layout."""
        if self.exists():
            return False
        directory = os.path.dirname(os.path.abspath(self.path))
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError as exc:
            raise CatalogError("Could not create the catalog folder:\n%s" % exc)

        wb = Workbook()
        ws = wb.active
        ws.title = SHEET_NAME
        headers = default_column_order()
        # Any subjects the user added before the file existed belong here too.
        known = {norm_header(h) for h in headers}
        for subject in self.config.subjects:
            if norm_header(subject) not in known:
                headers.insert(headers.index("Language"), subject)
                known.add(norm_header(subject))
        for index, header in enumerate(headers, start=1):
            _write_header_cell(ws, index, header)
        _finish_sheet(ws, len(headers))
        self._save(wb)
        return True

    # -- version control -----------------------------------------------------
    def backup_once_per_session(self):
        """Copy the master into "old versions" the first time we write today.

        The archived name carries the date and time so successive sessions never
        collide.  Returns the backup path, or None if one was already made.
        """
        if self._backed_up_this_session or not self.exists():
            return None
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H-%M-%S")
        target_dir = self.config.versions_dir
        try:
            os.makedirs(target_dir, exist_ok=True)
        except OSError as exc:
            raise CatalogError("Could not create the 'old versions' folder:\n%s" % exc)
        base = os.path.splitext(os.path.basename(self.path))[0] or MASTER_BASENAME
        target = os.path.join(target_dir, "%s %s.xlsx" % (base, stamp))
        try:
            shutil.copy2(self.path, target)
        except (IOError, OSError) as exc:
            raise CatalogError("Could not archive the previous version:\n%s" % exc)
        self._backed_up_this_session = True
        self.last_backup_path = target
        return target

    # -- schema --------------------------------------------------------------
    def _read_headers(self, ws):
        width = max(ws.max_column, 1)
        return [ws.cell(row=HEADER_ROW, column=c).value for c in range(1, width + 1)]

    def column_map(self):
        wb, ws = self._open()
        try:
            return ColumnMap(self._read_headers(ws))
        finally:
            wb.close()

    def _ensure_capacity(self, ws, cmap, record):
        """Add any columns this record needs, appending them at the far right.

        New columns are appended rather than inserted so that existing data,
        formatting and hyperlinks are never shifted.
        """
        added = []
        next_col = cmap.width() + 1

        def append(header):
            nonlocal next_col
            _write_header_cell(ws, next_col, header)
            cmap.headers.append(header)
            col = next_col
            next_col += 1
            added.append(header)
            return col

        for key, _header, _label in SCALAR_FIELDS:
            if key not in cmap.scalars and (record.get(key) or key == "date_added"):
                cmap.scalars[key] = append(SCALAR_HEADER_BY_KEY[key])

        for role_key, base, _label, _initial in ROLES:
            needed = len(record.people.get(role_key, []))
            have = cmap.role_capacity(role_key)
            for slot in range(have + 1, needed + 1):
                # Slot 1 of a bare single-slot role keeps the unnumbered header.
                header = base if slot == 1 else "%s %d" % (base, slot)
                cmap.roles[role_key].append(append(header))

        for subject in record.subjects:
            if cmap.subject_column(subject) is None:
                cmap.subjects[subject] = append(subject)

        if added:
            _finish_sheet(ws, cmap.width())
        return added

    # -- writing -------------------------------------------------------------
    def append(self, record):
        """Append one book as a new row. Returns (row_index, backup_path, added_columns)."""
        self.create_if_missing()
        backup = self.backup_once_per_session()
        wb, ws = self._open()
        try:
            cmap = ColumnMap(self._read_headers(ws))
            added = self._ensure_capacity(ws, cmap, record)
            row = self._first_free_row(ws, cmap)
            self._write_record(ws, cmap, row, record)
            _finish_sheet(ws, cmap.width())
            self._save(wb)
        finally:
            wb.close()
        record.row_index = row
        return row, backup, added

    def update(self, record):
        """Rewrite an existing row in place."""
        if not record.row_index:
            raise CatalogError("This entry has no row to update.")
        backup = self.backup_once_per_session()
        wb, ws = self._open()
        try:
            cmap = ColumnMap(self._read_headers(ws))
            added = self._ensure_capacity(ws, cmap, record)
            self._write_record(ws, cmap, record.row_index, record, clear_first=True)
            _finish_sheet(ws, cmap.width())
            self._save(wb)
        finally:
            wb.close()
        return record.row_index, backup, added

    def delete(self, row_indices):
        """Delete whole rows, closing the gap behind them.

        Rows are removed from the bottom up so the indices stay valid as the
        sheet shrinks.  Returns ``(count_deleted, backup_path)``.
        """
        rows = sorted({int(r) for r in row_indices if r}, reverse=True)
        if not rows:
            return 0, None
        if not self.exists():
            raise CatalogError("There is no master file to delete from.")

        backup = self.backup_once_per_session()
        wb, ws = self._open()
        try:
            cmap = ColumnMap(self._read_headers(ws))
            for row in rows:
                if row < FIRST_DATA_ROW:
                    raise CatalogError("Row %d is the header and cannot be deleted." % row)
                ws.delete_rows(row, 1)
            _reindex_hyperlinks(ws)
            _finish_sheet(ws, cmap.width())
            self._save(wb)
        finally:
            wb.close()
        return len(rows), backup

    def column_usage(self, header):
        """How many data rows have something in the named column."""
        if not self.exists():
            return 0
        wb, ws = self._open()
        try:
            cmap = ColumnMap(self._read_headers(ws))
            col = _find_column(cmap, header)
            if col is None:
                return 0
            return sum(
                1 for row in range(FIRST_DATA_ROW, ws.max_row + 1)
                if _text(ws.cell(row=row, column=col).value)
            )
        finally:
            wb.close()

    def delete_column(self, header):
        """Remove an empty column, closing the gap. Returns True if one went.

        Only ever called for a column with no data in it -- the Settings tab
        checks usage first and refuses to drop a category still in use.
        """
        if not self.exists():
            return False
        backup = self.backup_once_per_session()
        wb, ws = self._open()
        try:
            cmap = ColumnMap(self._read_headers(ws))
            col = _find_column(cmap, header)
            if col is None:
                return False
            used = sum(
                1 for row in range(FIRST_DATA_ROW, ws.max_row + 1)
                if _text(ws.cell(row=row, column=col).value)
            )
            if used:
                raise CatalogError(
                    "'%s' still has data in %d row%s, so the column was left alone."
                    % (display_header(header), used, "" if used == 1 else "s")
                )
            ws.delete_cols(col, 1)
            _reindex_hyperlinks(ws)
            _finish_sheet(ws, cmap.width() - 1)
            self._save(wb)
        finally:
            wb.close()
        return True

    def _first_free_row(self, ws, cmap):
        """Lowest row with no content, so stray formatting does not push rows down."""
        row = ws.max_row
        while row >= FIRST_DATA_ROW:
            if any(
                _text(ws.cell(row=row, column=col).value)
                for col in range(1, cmap.width() + 1)
            ):
                return row + 1
            row -= 1
        return FIRST_DATA_ROW

    def _write_record(self, ws, cmap, row, record, clear_first=False):
        if clear_first:
            for col in sorted(cmap.managed_columns()):
                cell = ws.cell(row=row, column=col)
                cell.value = None
                cell.hyperlink = None

        for key, col in cmap.scalars.items():
            value = record.get(key)
            if key in BOOLEAN_SCALARS:
                value = TRUE_VALUE if value else ""
            ws.cell(row=row, column=col).value = value or None

        for role_key, columns in cmap.roles.items():
            names = record.people.get(role_key, [])
            for index, col in enumerate(columns):
                ws.cell(row=row, column=col).value = names[index] if index < len(names) else None

        for header, col in cmap.subjects.items():
            marked = any(norm_header(s) == norm_header(header) for s in record.subjects)
            ws.cell(row=row, column=col).value = TRUE_VALUE if marked else None

        # The catalog links its classification cells to the LC record, the way
        # the existing file does.  The link is built from the LCCN; the internal
        # library number is a shelving number and stays plain text.
        url = lccn_url(record.get("lccn"))
        if url:
            for key in ("lcc", "lccn"):
                col = cmap.scalars.get(key)
                if col and _text(ws.cell(row=row, column=col).value):
                    _link(ws.cell(row=row, column=col), url)

        cover = record.get("cover_image")
        col = cmap.scalars.get("cover_image")
        if cover and col:
            cell = ws.cell(row=row, column=col)
            cell.value = os.path.basename(cover)
            _link(cell, _relative_cover_link(cover, self.config))

    # -- reading -------------------------------------------------------------
    def load_all(self):
        """Read every row into BookRecords. Returns (records, column_map)."""
        if not self.exists():
            return [], ColumnMap(default_column_order())
        wb, ws = self._open()
        try:
            cmap = ColumnMap(self._read_headers(ws))
            records = []
            for row in range(FIRST_DATA_ROW, ws.max_row + 1):
                values = [ws.cell(row=row, column=c).value for c in range(1, cmap.width() + 1)]
                if not any(_text(v) for v in values):
                    continue
                record = self._record_from_values(values, cmap)
                record.row_index = row
                records.append(record)
            return records, cmap
        finally:
            wb.close()

    def _record_from_values(self, values, cmap):
        record = BookRecord()

        def at(col):
            return _text(values[col - 1]) if 0 < col <= len(values) else ""

        for key, col in cmap.scalars.items():
            record.set(key, at(col))
        for role_key, columns in cmap.roles.items():
            record.set_people(role_key, [at(c) for c in columns])
        for header, col in cmap.subjects.items():
            if at(col).strip().lower() in ("yes", "y", "true", "x", "1"):
                record.add_subject(header)
        return record

    def distinct_values(self, records, cmap):
        """Values already in use, for seeding dropdowns."""
        locations, origins = set(), set()
        for record in records:
            if record.get("location"):
                locations.add(record.get("location"))
            if record.get("origin"):
                origins.add(record.get("origin"))
        return locations, origins, list(cmap.subjects.keys())


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------

def build_matcher(query, use_regex=False):
    """Compile a search query into a predicate over a single cell's text.

    Two modes:
      * Wildcard (default) -- "test*" means "starts with test", "*test*" means
        "contains test", "?" matches one character.  A query with no wildcard at
        all is a plain case-insensitive "contains" search.
      * Regex -- the query is used as a regular expression as typed.
    Both are case-insensitive.
    """
    query = (query or "").strip()
    if not query:
        return None
    if use_regex:
        try:
            pattern = re.compile(query, re.IGNORECASE)
        except re.error as exc:
            raise CatalogError("That is not a valid regular expression:\n%s" % exc)
        return pattern.search

    if "*" not in query and "?" not in query:
        needle = query.lower()
        return lambda text: needle in text.lower()

    translated = "".join(
        ".*" if ch == "*" else "." if ch == "?" else re.escape(ch)
        for ch in query
    )
    pattern = re.compile("^" + translated + "$", re.IGNORECASE | re.DOTALL)
    return pattern.match


def search(records, cmap, query, field_key=None, use_regex=False):
    """Return the records whose ``field_key`` (or any field) matches ``query``.

    ``field_key`` is a scalar key, a role key, "subjects", or None for any.
    """
    matcher = build_matcher(query, use_regex)
    if matcher is None:
        return list(records)
    hits = []
    for record in records:
        for text in _searchable_texts(record, cmap, field_key):
            if text and matcher(text):
                hits.append(record)
                break
    return hits


def _searchable_texts(record, cmap, field_key):
    if field_key in (None, "", "any"):
        texts = [v for v in record.scalars.values() if v]
        texts.extend(record.all_people())
        texts.extend(display_header(s) for s in record.subjects)
        return texts
    if field_key == "subjects":
        return [display_header(s) for s in record.subjects]
    if field_key in record.people:
        return list(record.people[field_key])
    return [record.get(field_key)]


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

_HEADER_FILL = PatternFill("solid", fgColor="DDEBF7")
_HEADER_FONT = Font(bold=True)
_LINK_FONT = Font(color="0563C1", underline="single")


def _text(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, datetime.datetime):
        return value.strftime("%Y-%m-%d")
    return str(value).strip()


def _write_header_cell(ws, column, header):
    cell = ws.cell(row=HEADER_ROW, column=column)
    cell.value = header
    cell.font = _HEADER_FONT
    cell.fill = _HEADER_FILL
    cell.alignment = Alignment(wrap_text=True, vertical="bottom")


def _finish_sheet(ws, width):
    """Keep the header frozen, filterable and readable as columns are added."""
    ws.freeze_panes = "A%d" % FIRST_DATA_ROW
    last = get_column_letter(max(width, 1))
    ws.auto_filter.ref = "A%d:%s%d" % (HEADER_ROW, last, max(ws.max_row, HEADER_ROW))
    ws.row_dimensions[HEADER_ROW].height = 30
    for column in range(1, width + 1):
        letter = get_column_letter(column)
        if ws.column_dimensions[letter].width in (None, 0):
            header = display_header(ws.cell(row=HEADER_ROW, column=column).value)
            ws.column_dimensions[letter].width = min(max(len(header) + 3, 9), 42)


def _find_column(cmap, header):
    """1-based index of a column by header name, matched tolerantly."""
    target = norm_header(header)
    for zero_based, raw in enumerate(cmap.headers):
        if raw is not None and norm_header(raw) == target:
            return zero_based + 1
    return None


def _reindex_hyperlinks(ws):
    """Re-point every hyperlink at the cell that actually holds it.

    openpyxl's ``delete_rows`` moves cell values and styles up, but a Hyperlink
    object keeps the ``ref`` it was given when it was created.  Left alone, the
    links in the rows below a deletion would be written back at their old
    coordinates -- so an entry's LCC or cover link would end up attached to a
    different book.  Rebuilding the sheet's list from the surviving cells fixes
    the references and drops the links belonging to deleted rows.
    """
    links = []
    for row in ws.iter_rows():
        for cell in row:
            link = getattr(cell, "hyperlink", None)
            if link is not None:
                link.ref = cell.coordinate
                links.append(link)
    ws._hyperlinks = links


def _link(cell, url):
    if not url:
        return
    cell.hyperlink = url
    cell.font = _LINK_FONT


def _relative_cover_link(cover_path, config):
    """Link covers relatively so the catalog folder can be moved as a unit."""
    try:
        return os.path.relpath(cover_path, config.master_dir).replace("\\", "/")
    except ValueError:
        return cover_path
