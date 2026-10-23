"""The Basic-Home-Library main window."""

import datetime
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, ttk

from . import APP_NAME, __version__
from .catalog import Catalog, CatalogError, search as search_records
from .config import Config, shorten_path
from .covers import HAVE_PILLOW, load_thumbnail, resolve as resolve_cover
from . import duplicates
from .model import BookRecord
from .schema import (
    ROLES,
    SCALAR_FIELDS,
    SCALAR_LABEL_BY_KEY,
    display_header,
    norm_header,
)
from .sources import LookupError_, download_cover, lookup
from .widgets import PAD, BookForm, OptionListEditor

# Fields offered in the search-tab dropdown: (key, label)
SEARCH_FIELDS = (
    [("any", "Any field")]
    + [(key, SCALAR_LABEL_BY_KEY[key]) for key, _h, _l in SCALAR_FIELDS]
    + [(key, label) for key, _b, label, _n in ROLES]
    + [("subjects", "Subject categories")]
)


class BasicHomeLibrary(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("%s  v%s" % (APP_NAME, __version__))
        self.geometry("1240x880")
        self.minsize(980, 680)

        self.config_obj = Config()
        self.catalog = Catalog(self.config_obj)
        self.results_queue = queue.Queue()
        self._records = []
        self._column_map = None
        self._search_hits = []

        self._configure_styles()
        self._build_menu()

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=PAD, pady=(PAD, 0))
        self._build_lookup_tab()
        self._build_manual_tab()
        self._build_search_tab()
        self._build_settings_tab()
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        self.status = tk.StringVar(value="Ready.")
        status_bar = ttk.Frame(self)
        status_bar.pack(fill="x", side="bottom")
        ttk.Separator(status_bar, orient="horizontal").pack(fill="x")
        ttk.Label(status_bar, textvariable=self.status, anchor="w", padding=(PAD, 3)).pack(
            side="left", fill="x", expand=True
        )
        self.master_label = ttk.Label(status_bar, anchor="e", padding=(PAD, 3), style="Hint.TLabel")
        self.master_label.pack(side="right")

        self._refresh_master_label()
        self.after(200, self._startup)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # Chrome
    # ------------------------------------------------------------------
    def _configure_styles(self):
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Field.TLabel", font=("Segoe UI", 9))
        style.configure("Hint.TLabel", foreground="#606060", font=("Segoe UI", 8))
        style.configure("Heading.TLabel", font=("Segoe UI", 11, "bold"))
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"))

    def _build_menu(self):
        menubar = tk.Menu(self)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Choose master catalog file...", command=self._choose_master)
        file_menu.add_command(label="Open master catalog in Excel", command=self._open_master)
        file_menu.add_command(label="Open cover images folder", command=self._open_covers)
        file_menu.add_command(label="Open 'old versions' folder", command=self._open_versions)
        file_menu.add_separator()
        file_menu.add_command(label="Settings", command=self._show_settings_tab)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_close)
        menubar.add_cascade(label="File", menu=file_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="About", command=self._about)
        menubar.add_cascade(label="Help", menu=help_menu)
        self.configure(menu=menubar)

    def _refresh_master_label(self):
        state = "" if self.catalog.exists() else "  (will be created)"
        self.master_label.configure(
            text="Master: %s%s" % (self.config_obj.master_path_display, state))

    def _startup(self):
        try:
            created = self.catalog.create_if_missing()
        except CatalogError as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)
            return
        if created:
            self.set_status(
                "Created a new master catalog: %s" % self.config_obj.master_path_display)
        self.refresh_from_master(announce=not created)
        self._refresh_master_label()
        if not HAVE_PILLOW:
            self.set_status(
                "Ready. (Pillow is not installed, so JPEG cover previews are unavailable.)"
            )

    # ------------------------------------------------------------------
    # Tab 1 - lookup by identifier
    # ------------------------------------------------------------------
    def _build_lookup_tab(self):
        tab = ttk.Frame(self.notebook, padding=PAD)
        self.notebook.add(tab, text="  1. Look Up by ISBN / ISSN  ")

        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=(0, PAD))
        ttk.Label(bar, text="ISBN or ISSN:", style="Heading.TLabel").pack(side="left")
        self.identifier_var = tk.StringVar()
        entry = ttk.Entry(bar, textvariable=self.identifier_var, width=28, font=("Segoe UI", 11))
        entry.pack(side="left", padx=PAD)
        entry.bind("<Return>", lambda _e: self._start_lookup())
        self.identifier_entry = entry
        self.search_button = ttk.Button(
            bar, text="Search online", style="Accent.TButton", command=self._start_lookup
        )
        self.search_button.pack(side="left")
        # Packed only while a search is running, so an idle bar does not sit
        # there looking like a stalled download.
        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=140)

        ttk.Label(
            tab,
            text="Checks the Library of Congress catalog first, then Open Library, then "
                 "Google Books. Review and correct anything below before adding the book.",
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(0, 4))

        # The search log gets its own scrollbar: several sources each report a
        # line, and the notes at the end matter as much as the first line.
        log_frame = ttk.Frame(tab)
        log_frame.pack(fill="x", pady=(0, PAD))
        self.lookup_log = tk.Text(
            log_frame, height=5, wrap="word", state="disabled",
            background="#f7f7f7", relief="flat", font=("Consolas", 8),
        )
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.lookup_log.yview)
        self.lookup_log.configure(yscrollcommand=log_scroll.set)
        log_scroll.pack(side="right", fill="y")
        self.lookup_log.pack(side="left", fill="both", expand=True)

        self.lookup_form = BookForm(tab, self.config_obj, on_clear=self._clear_lookup_extras)
        self.lookup_form.pack(fill="both", expand=True)

        footer = ttk.Frame(tab)
        footer.pack(fill="x", pady=(PAD, 0))
        ttk.Button(
            footer, text="Clear all fields", command=self.lookup_form.clear_with_confirm
        ).pack(side="left")
        ttk.Button(
            footer, text="Confirm and add to catalog", style="Accent.TButton",
            command=lambda: self._commit(self.lookup_form, after=self._clear_lookup),
        ).pack(side="right")

    def _clear_lookup_extras(self):
        """The parts of tab 1 that live outside the form itself."""
        self.identifier_var.set("")
        self._log_lookup("", replace=True)

    def _clear_lookup(self):
        self.lookup_form.clear()
        self._clear_lookup_extras()
        self.identifier_entry.focus_set()

    def _log_lookup(self, text, replace=False):
        self.lookup_log.configure(state="normal")
        if replace:
            self.lookup_log.delete("1.0", "end")
        if text:
            self.lookup_log.insert("end", text + "\n")
            self.lookup_log.see("end")
        self.lookup_log.configure(state="disabled")

    def _start_lookup(self):
        identifier = self.identifier_var.get().strip()
        if not identifier:
            self.set_status("Enter an ISBN or ISSN first.")
            return

        # Catch a book that is already catalogued before spending a search on it.
        already = duplicates.find_by_identifier(self._records, identifier)
        if already:
            rows = [(record, ["same ISBN/ISSN"]) for record in already]
            if not self._match_dialog(
                title="Already in the catalog",
                heading="You have already catalogued this ISBN/ISSN.",
                explanation=(
                    "%s already in the catalog with this identifier. You can still "
                    "search -- for a second copy, or a different edition -- or stop "
                    "here." % ("There is 1 entry" if len(already) == 1
                               else "There are %d entries" % len(already))
                ),
                rows=rows,
                proceed_text="Search anyway",
                cancel_text="Cancel search",
            ):
                self.set_status("Search cancelled - that ISBN/ISSN is already catalogued.")
                return

        self.search_button.configure(state="disabled")
        self.progress.pack(side="left", padx=PAD)
        self.progress.start(12)
        self._log_lookup("Searching for %s ..." % identifier, replace=True)
        self.set_status("Searching online...")

        def worker():
            try:
                record, notes = lookup(identifier, self.config_obj, progress=self._post_progress)
                # Fetch the cover here, on this thread, so it is on screen the
                # moment the results are. It is held in memory and only written
                # to the covers folder once the entry is actually added.
                if record.cover_url:
                    self._post_progress("Downloading the cover image...")
                    try:
                        data, extension = download_cover(
                            record.cover_url, timeout=self.config_obj.request_timeout
                        )
                        record.cover_data = (data, extension)
                        notes.append("Cover image: downloaded (%.0f KB)" % (len(data) / 1024.0))
                    except IOError as exc:
                        record.cover_data = None
                        notes.append("Cover image: %s" % exc)
                else:
                    record.cover_data = None
                    notes.append("Cover image: none found")
                self.results_queue.put(("ok", record, notes))
            except LookupError_ as exc:
                self.results_queue.put(("bad-identifier", str(exc), []))
            except Exception:  # a source misbehaving must not kill the app
                self.results_queue.put(("error", traceback.format_exc(), []))

        threading.Thread(target=worker, daemon=True).start()
        self.after(120, self._poll_lookup)

    def _post_progress(self, message):
        self.results_queue.put(("progress", message, []))

    def _poll_lookup(self):
        try:
            kind, payload, notes = self.results_queue.get_nowait()
        except queue.Empty:
            self.after(120, self._poll_lookup)
            return

        if kind == "progress":
            self.set_status(payload)
            self.after(120, self._poll_lookup)
            return

        self.progress.stop()
        self.progress.pack_forget()
        self.search_button.configure(state="normal")

        if kind == "bad-identifier":
            self._log_lookup(payload)
            self.set_status(payload)
            return
        if kind == "error":
            self._log_lookup("The search failed unexpectedly. See details below.")
            self._log_lookup(payload)
            self.set_status("The online search failed.")
            return

        record = payload
        for note in notes:
            self._log_lookup("  " + note)
        if record.is_empty():
            self.set_status("Nothing found. Enter the details on the Manual Entry tab.")
            self._log_lookup("No metadata found for this identifier.")
            return

        record.set("date_added", datetime.date.today().isoformat())
        # A search result is not in the catalog, so the form now holds unsaved
        # work: closing the app with it on screen must prompt.
        self.lookup_form.load(record, treat_as_saved=False)
        downloaded = getattr(record, "cover_data", None)
        if downloaded:
            data, extension = downloaded
            self.lookup_form.cover.set_downloaded(data, extension, record.cover_url)
        self.notebook.select(0)
        self.set_status(
            "Found: %s  -  check the details, then confirm." % (record.get("title") or "a record")
        )

    # ------------------------------------------------------------------
    # Tab 2 - manual entry
    # ------------------------------------------------------------------
    def _build_manual_tab(self):
        tab = ttk.Frame(self.notebook, padding=PAD)
        self.notebook.add(tab, text="  2. Manual Entry  ")

        ttk.Label(
            tab, text="Enter a book by hand when it has no ISBN, or when the online "
                      "search cannot find it.", style="Hint.TLabel",
        ).pack(anchor="w", pady=(0, PAD))

        self.manual_form = BookForm(tab, self.config_obj)
        self.manual_form.pack(fill="both", expand=True)

        footer = ttk.Frame(tab)
        footer.pack(fill="x", pady=(PAD, 0))
        ttk.Button(
            footer, text="Clear all fields", command=self.manual_form.clear_with_confirm
        ).pack(side="left")
        ttk.Button(
            footer, text="Add to catalog", style="Accent.TButton",
            command=lambda: self._commit(self.manual_form, after=self.manual_form.clear),
        ).pack(side="right")

    # ------------------------------------------------------------------
    # Tab 3 - search and edit
    # ------------------------------------------------------------------
    def _build_search_tab(self):
        tab = ttk.Frame(self.notebook, padding=PAD)
        self.notebook.add(tab, text="  3. Find and Edit  ")

        bar = ttk.Frame(tab)
        bar.pack(fill="x")
        ttk.Label(bar, text="Search", style="Heading.TLabel").pack(side="left", padx=(0, PAD))
        ttk.Label(bar, text="in:").pack(side="left")
        self.search_field_var = tk.StringVar(value=SEARCH_FIELDS[0][1])
        ttk.Combobox(
            bar, textvariable=self.search_field_var, state="readonly", width=22,
            values=[label for _key, label in SEARCH_FIELDS],
        ).pack(side="left", padx=(4, PAD))
        self.query_var = tk.StringVar()
        query_entry = ttk.Entry(bar, textvariable=self.query_var, width=34, font=("Segoe UI", 10))
        query_entry.pack(side="left")
        query_entry.bind("<Return>", lambda _e: self._run_search())
        self.regex_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Full regular expression", variable=self.regex_var).pack(
            side="left", padx=PAD
        )
        ttk.Button(bar, text="Search", style="Accent.TButton", command=self._run_search).pack(side="left")
        ttk.Button(bar, text="Show all", command=self._show_all).pack(side="left", padx=(4, 0))
        ttk.Button(bar, text="Reload from file", command=lambda: self.refresh_from_master()).pack(
            side="left", padx=(PAD, 0)
        )

        ttk.Label(
            tab,
            text="Wildcards:  test*  starts with \"test\"   |   *test*  contains \"test\"   |   "
                 "te?t  matches one character.  Plain text with no wildcard matches anywhere in "
                 "the field. Tick \"Full regular expression\" to use regex syntax as typed.",
            style="Hint.TLabel", wraplength=1100, justify="left",
        ).pack(anchor="w", pady=(4, PAD))

        panes = ttk.PanedWindow(tab, orient="vertical")
        panes.pack(fill="both", expand=True)

        upper = ttk.Frame(panes)
        panes.add(upper, weight=1)
        columns = ("title", "author", "year", "location", "lcc", "row")
        self.results = ttk.Treeview(upper, columns=columns, show="headings", height=8)
        for column, heading, width in (
            ("title", "Title", 420), ("author", "Author / Editor", 220),
            ("year", "Year", 60), ("location", "Location", 120),
            ("lcc", "LCC", 90), ("row", "Row", 55),
        ):
            self.results.heading(column, text=heading)
            self.results.column(column, width=width, anchor="w")
        scroll = ttk.Scrollbar(upper, orient="vertical", command=self.results.yview)
        self.results.configure(yscrollcommand=scroll.set)
        self.results.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.results.bind("<<TreeviewSelect>>", self._on_result_selected)

        lower = ttk.Frame(panes)
        panes.add(lower, weight=3)
        self.selection_label = ttk.Label(
            lower, text="Select a result above to view and edit it.", style="Heading.TLabel"
        )
        self.selection_label.pack(anchor="w", pady=(PAD, 4))
        # No clear button here: this pane edits an existing row, and "Revert"
        # is the action that belongs next to it.
        self.edit_form = BookForm(lower, self.config_obj, show_clear=False)
        self.edit_form.pack(fill="both", expand=True)

        footer = ttk.Frame(lower)
        footer.pack(fill="x", pady=(PAD, 0))
        self.save_button = ttk.Button(
            footer, text="Save changes to catalog", style="Accent.TButton",
            command=self._save_edit, state="disabled",
        )
        self.save_button.pack(side="right")
        ttk.Button(footer, text="Revert", command=self._revert_edit).pack(side="right", padx=(0, PAD))
        self.delete_button = ttk.Button(
            footer, text="Delete entry...", command=self._delete_selected, state="disabled"
        )
        self.delete_button.pack(side="left")

    def _selected_search_key(self):
        label = self.search_field_var.get()
        for key, text in SEARCH_FIELDS:
            if text == label:
                return key
        return "any"

    def _run_search(self):
        query = self.query_var.get().strip()
        if not query:
            self._show_all()
            return
        try:
            hits = search_records(
                self._records, self._column_map, query,
                field_key=self._selected_search_key(), use_regex=self.regex_var.get(),
            )
        except CatalogError as exc:
            messagebox.showerror("Search", str(exc), parent=self)
            return
        self._populate_results(hits)
        if not hits:
            self.set_status("No books matched '%s'." % query)
        elif len(hits) == 1:
            self.set_status("1 match.")
            self.results.selection_set(self.results.get_children()[0])
        else:
            self.set_status("%d matches - choose one from the list." % len(hits))

    def _show_all(self):
        self._populate_results(self._records)
        self.set_status("Showing all %d books in the catalog." % len(self._records))

    def _refresh_results(self):
        """Rebuild the results list, keeping whatever search is in force."""
        if self.query_var.get().strip():
            self._run_search()
        else:
            self._show_all()

    def _populate_results(self, hits):
        self.results.delete(*self.results.get_children())
        self._search_hits = list(hits)
        for index, record in enumerate(self._search_hits):
            people = record.people.get("authors") or record.people.get("editors") or []
            self.results.insert(
                "", "end", iid=str(index),
                values=(
                    record.get("title"), people[0] if people else "",
                    record.get("edition_year"), record.get("location"),
                    record.get("lcc"), record.row_index or "",
                ),
            )
        self._clear_edit_pane()

    def _clear_edit_pane(self):
        self.edit_form.clear()
        self.save_button.configure(state="disabled")
        self.delete_button.configure(state="disabled")
        self.selection_label.configure(text="Select a result above to view and edit it.")

    def _on_result_selected(self, _event=None):
        selection = self.results.selection()
        if not selection:
            return
        index = int(selection[0])
        if not 0 <= index < len(self._search_hits):
            return
        record = self._search_hits[index]
        self.edit_form.load(record)
        self.save_button.configure(state="normal")
        self.delete_button.configure(state="normal")
        self.selection_label.configure(
            text="Editing:  %s   (spreadsheet row %s)" % (record.summary(), record.row_index)
        )

    def _revert_edit(self):
        self._on_result_selected()

    def _save_edit(self):
        problem = self.edit_form.validate()
        if problem:
            messagebox.showwarning("Check the entry", problem, parent=self)
            return
        record = self.edit_form.harvest()
        # Exclude the row being edited, or every entry would clash with itself.
        if not self._duplicate_check_passes(
            self.edit_form, record, exclude_row=record.row_index, verb="save"
        ):
            return
        record.set("cover_image", self.edit_form.cover.commit())
        try:
            row, backup, added = self.catalog.update(record)
        except CatalogError as exc:
            messagebox.showerror("Could not save", str(exc), parent=self)
            return
        self.refresh_from_master(announce=False)
        self._refresh_results()
        # Announced last: rebuilding the results list writes its own status.
        self._announce_write("Updated row %d" % row, backup, added)

    # ------------------------------------------------------------------
    # Tab 4 - settings
    # ------------------------------------------------------------------
    def _build_settings_tab(self):
        tab = ttk.Frame(self.notebook, padding=PAD)
        self.notebook.add(tab, text="  4. Settings  ")
        self._settings_dirty = False

        ttk.Label(
            tab,
            text="Change the choices offered on the entry forms, and where the app "
                 "keeps its files. Nothing here takes effect until you press "
                 "\"Update and refresh all tabs\".",
            style="Hint.TLabel", wraplength=1150, justify="left",
        ).pack(anchor="w", pady=(0, PAD))

        body = ttk.Frame(tab)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1, uniform="settings")
        body.columnconfigure(1, weight=1, uniform="settings")
        body.columnconfigure(2, weight=1, uniform="settings")
        body.rowconfigure(0, weight=1)

        def mark_dirty():
            self._settings_dirty = True
            self.settings_status.configure(
                text="Unapplied changes - press Update to put them into effect.")

        left = ttk.Frame(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, PAD))
        left.rowconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)

        self.location_editor = OptionListEditor(
            left, "Locations", "Name of the location:",
            usage_lookup=lambda name: self._usage_count("location", name),
            on_change=mark_dirty, noun="location", height=9,
        )
        self.location_editor.grid(row=0, column=0, sticky="nsew", pady=(0, PAD))

        self.origin_editor = OptionListEditor(
            left, "Origins",
            "Where did the book come from?\n"
            "(for example: Secondhand sale, or Birthday gift 2017)",
            usage_lookup=lambda name: self._usage_count("origin", name),
            on_change=mark_dirty, noun="origin", height=9,
        )
        self.origin_editor.grid(row=1, column=0, sticky="nsew")

        middle = ttk.Frame(body)
        middle.grid(row=0, column=1, sticky="nsew", padx=(0, PAD))
        middle.rowconfigure(0, weight=1)
        middle.columnconfigure(0, weight=1)
        self.subject_editor = OptionListEditor(
            middle, "Subject categories", "Name of the subject category:",
            usage_lookup=lambda name: self._usage_count("subject", name),
            allow_reorder=True, on_change=mark_dirty, noun="category", height=20,
        )
        self.subject_editor.grid(row=0, column=0, sticky="nsew")
        ttk.Label(
            middle,
            text="Up and Down set the order the checkboxes appear in, reading down "
                 "each column. Removing a category also removes its (empty) column "
                 "from the spreadsheet.",
            style="Hint.TLabel", wraplength=330, justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        right = ttk.Frame(body)
        right.grid(row=0, column=2, sticky="nsew")
        right.columnconfigure(0, weight=1)

        files = ttk.LabelFrame(right, text="Files", padding=PAD)
        files.grid(row=0, column=0, sticky="ew", pady=(0, PAD))
        files.columnconfigure(0, weight=1)
        ttk.Label(files, text="Master catalog file:", style="Field.TLabel").grid(
            row=0, column=0, sticky="w")
        self.settings_master = tk.StringVar()
        ttk.Entry(files, textvariable=self.settings_master).grid(
            row=1, column=0, sticky="ew", pady=(2, 0))
        self.settings_master.trace_add("write", lambda *_a: mark_dirty())
        row = ttk.Frame(files)
        row.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        ttk.Button(row, text="Browse...", command=self._browse_master).pack(side="left")
        ttk.Button(row, text="Open catalog folder",
                   command=self._open_master_folder).pack(side="left", padx=(4, 0))
        ttk.Label(
            files,
            text="Just a file name keeps the catalog in the app's own folder, "
                 "which is usually what you want. Give a full path to keep it "
                 "somewhere else, such as a synced folder. Cover images and "
                 "'old versions' always live beside it.",
            style="Hint.TLabel", wraplength=330, justify="left",
        ).grid(row=3, column=0, sticky="w", pady=(4, 0))

        online = ttk.LabelFrame(right, text="Online searches", padding=PAD)
        online.grid(row=1, column=0, sticky="ew", pady=(0, PAD))
        online.columnconfigure(0, weight=1)
        ttk.Label(online, text="Google Books API key:", style="Field.TLabel").grid(
            row=0, column=0, sticky="w")
        key_row = ttk.Frame(online)
        key_row.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        self.settings_key = tk.StringVar()
        self.settings_key.trace_add("write", lambda *_a: mark_dirty())
        key_entry = ttk.Entry(key_row, textvariable=self.settings_key, show="*")
        key_entry.pack(side="left", fill="x", expand=True)
        show_key = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            key_row, text="Show", variable=show_key,
            command=lambda: key_entry.configure(show="" if show_key.get() else "*"),
        ).pack(side="left", padx=(4, 0))
        ttk.Label(
            online,
            text="Optional. The app works without one, but Google limits how often "
                 "a single address may ask, so searches are spaced out to stay "
                 "under it. A free key from the Google Cloud console removes that "
                 "wait. It is kept in your own settings file.",
            style="Hint.TLabel", wraplength=330, justify="left",
        ).grid(row=2, column=0, sticky="w", pady=(4, 0))

        ttk.Label(online, text="Network timeout (seconds):", style="Field.TLabel").grid(
            row=3, column=0, sticky="w", pady=(PAD, 0))
        self.settings_timeout = tk.StringVar()
        self.settings_timeout.trace_add("write", lambda *_a: mark_dirty())
        ttk.Entry(online, textvariable=self.settings_timeout, width=8).grid(
            row=4, column=0, sticky="w", pady=(2, 0))

        footer = ttk.Frame(tab)
        footer.pack(fill="x", pady=(PAD, 0))
        self.settings_status = ttk.Label(footer, text="", style="Hint.TLabel")
        self.settings_status.pack(side="left")
        ttk.Button(
            footer, text="Update and refresh all tabs", style="Accent.TButton",
            command=self._apply_settings,
        ).pack(side="right")
        ttk.Button(footer, text="Discard changes", command=self._reload_settings).pack(
            side="right", padx=(0, PAD))

        self._reload_settings()

    def _usage_count(self, kind, name):
        """How many catalog entries use a location, origin or subject."""
        target = norm_header(name)
        if kind == "subject":
            return sum(
                1 for record in self._records
                if any(norm_header(s) == target for s in record.subjects)
            )
        return sum(
            1 for record in self._records
            if norm_header(record.get(kind)) == target
        )

    def _reload_settings(self):
        """Fill the Settings tab from the configuration as it stands."""
        self.location_editor.set_items(self.config_obj.locations)
        self.origin_editor.set_items(self.config_obj.origins)
        self.subject_editor.set_items(self.config_obj.subjects)
        self.settings_master.set(self.config_obj.master_path_display)
        self.settings_key.set(self.config_obj.google_books_key)
        self.settings_timeout.set(str(self.config_obj.request_timeout))
        self._settings_dirty = False
        self.settings_status.configure(text="")

    def _on_tab_changed(self, _event=None):
        """Re-read the Settings tab when it is opened with no pending edits.

        An option added from a form's own Add... button would otherwise not show
        up here until the app was restarted.
        """
        if self.notebook.index("current") == 3 and not self._settings_dirty:
            self._reload_settings()

    def _browse_master(self):
        path = filedialog.asksaveasfilename(
            title="Choose or create the master catalog",
            parent=self, defaultextension=".xlsx",
            initialfile=os.path.basename(self.config_obj.master_path),
            initialdir=self.config_obj.master_dir,
            filetypes=[("Excel workbook", "*.xlsx")],
            confirmoverwrite=False,
        )
        if path:
            # Shown by name alone when it landed in the app's own folder.
            self.settings_master.set(shorten_path(path))

    def _open_master_folder(self):
        self._open_path(self.config_obj.master_dir, "catalog folder")

    def _apply_settings(self):
        """Commit every change on the tab, then rebuild the other three."""
        subjects = self.subject_editor.get_items()
        dropped = [
            header for header in self.config_obj.subjects
            if not any(norm_header(header) == norm_header(s) for s in subjects)
        ]

        # A subject lives in the spreadsheet as a column, so removing it from the
        # list has to remove the column too -- otherwise the app would find the
        # header again on the next read and put the category straight back.
        removed_columns, failed = [], []
        for header in dropped:
            try:
                if self.catalog.delete_column(header):
                    removed_columns.append(display_header(header))
            except CatalogError as exc:
                failed.append(str(exc))
        if failed:
            messagebox.showwarning("Some categories were kept", "\n\n".join(failed), parent=self)
            for header in dropped:
                if display_header(header) not in removed_columns:
                    subjects.append(header)

        self.config_obj.set_locations(self.location_editor.get_items())
        self.config_obj.set_origins(self.origin_editor.get_items())
        self.config_obj.set_subjects(subjects)
        self.config_obj.google_books_key = self.settings_key.get().strip()
        try:
            self.config_obj.request_timeout = max(5, int(self.settings_timeout.get().strip()))
        except ValueError:
            self.config_obj.request_timeout = 20
        self.config_obj.save()

        # A bare file name is taken as "in the app's folder"; a full path is
        # taken as given.
        moved = self.config_obj.set_master_path_from_display(self.settings_master.get())
        if moved:
            self.config_obj.save()
            self.catalog = Catalog(self.config_obj)
            self._startup()

        # Rebuild every form so the new choices appear everywhere at once.
        for form in (self.lookup_form, self.manual_form, self.edit_form):
            form.refresh_options()
        self.refresh_from_master(announce=False)
        self._refresh_results()
        self._reload_settings()

        parts = ["Settings updated"]
        if removed_columns:
            parts.append("removed column%s: %s" % (
                "" if len(removed_columns) == 1 else "s", ", ".join(removed_columns)))
        if moved:
            parts.append("now using %s" % self.config_obj.master_path_display)
        self.set_status(".  ".join(parts) + ".")
        self.settings_status.configure(text="Applied. All tabs refreshed.")

    # ------------------------------------------------------------------
    # Deleting an entry
    # ------------------------------------------------------------------
    def _selected_records(self):
        chosen = []
        for item in self.results.selection():
            try:
                index = int(item)
            except ValueError:
                continue
            if 0 <= index < len(self._search_hits):
                chosen.append(self._search_hits[index])
        return [r for r in chosen if r.row_index]

    def _confirm_delete(self, records):
        """Confirm a deletion. Returns (go_ahead, also_delete_cover_files)."""
        if len(records) == 1:
            what = "this entry"
            listing = "    %s\n    (spreadsheet row %s)" % (
                records[0].summary(), records[0].row_index)
        else:
            what = "these %d entries" % len(records)
            listing = "\n".join(
                "    %s  (row %s)" % (r.summary(), r.row_index) for r in records[:12])
            if len(records) > 12:
                listing += "\n    ... and %d more" % (len(records) - 12)

        on_disk = [
            path for path in (
                resolve_cover(r.get("cover_image"), self.config_obj) for r in records
            ) if path
        ]

        dialog = tk.Toplevel(self)
        dialog.title("Delete from catalog")
        dialog.transient(self)
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=PAD * 2)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Permanently delete %s?" % what,
                  style="Heading.TLabel").pack(anchor="w")
        ttk.Label(frame, text=listing, justify="left", wraplength=560).pack(
            anchor="w", pady=(PAD, PAD))
        ttk.Label(
            frame,
            text="The row is removed from the spreadsheet completely and the rows "
                 "below move up, leaving no gap. This cannot be undone from inside "
                 "the app -- but a copy of the catalog as it is now will be saved "
                 "in the 'old versions' folder first.",
            justify="left", wraplength=560, style="Hint.TLabel",
        ).pack(anchor="w")

        delete_covers = tk.BooleanVar(value=False)
        if on_disk:
            ttk.Checkbutton(
                frame,
                text="Also delete the cover image file%s from 'Book Covers'"
                     % ("" if len(on_disk) == 1 else "s"),
                variable=delete_covers,
            ).pack(anchor="w", pady=(PAD, 0))

        choice = {"go": False}

        def go():
            choice["go"] = True
            dialog.destroy()

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(PAD * 2, 0))
        # Cancel is the default: the destructive action should take a deliberate click.
        ttk.Button(buttons, text="Cancel", style="Accent.TButton",
                   command=dialog.destroy).pack(side="right")
        ttk.Button(buttons, text="Delete", command=go).pack(side="right", padx=(0, PAD))

        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.bind("<Escape>", lambda _e: dialog.destroy())
        dialog.grab_set()
        dialog.wait_window()
        return choice["go"], (delete_covers.get() and bool(on_disk))

    def _delete_selected(self):
        records = self._selected_records()
        if not records:
            self.set_status("Select an entry in the list first.")
            return

        go, remove_covers = self._confirm_delete(records)
        if not go:
            self.set_status("Deletion cancelled - nothing was changed.")
            return

        cover_paths = []
        if remove_covers:
            cover_paths = [
                path for path in (
                    resolve_cover(r.get("cover_image"), self.config_obj) for r in records
                ) if path
            ]

        try:
            count, backup = self.catalog.delete([r.row_index for r in records])
        except CatalogError as exc:
            messagebox.showerror("Could not delete", str(exc), parent=self)
            return

        removed_covers = 0
        for path in cover_paths:
            try:
                os.remove(path)
                removed_covers += 1
            except OSError:
                pass  # the row is already gone; a stuck image is not worth failing over

        # Row numbers below the deletion have all shifted, so reload before the
        # results list is rebuilt from them.
        self.refresh_from_master(announce=False)
        self._refresh_results()

        headline = "Deleted %d %s" % (count, "entry" if count == 1 else "entries")
        if removed_covers:
            headline += " and %d cover image%s" % (
                removed_covers, "" if removed_covers == 1 else "s")
        # Announced last: rebuilding the results list writes its own status.
        self._announce_write(headline, backup, [])

    # ------------------------------------------------------------------
    # Duplicate checking
    # ------------------------------------------------------------------
    def _match_dialog(self, title, heading, explanation, rows, proceed_text,
                      cancel_text, default_proceed=False):
        """Show matching entries and ask whether to go ahead.

        ``rows`` is a list of ``(record, reasons)``. Returns True to proceed.
        """
        dialog = tk.Toplevel(self)
        dialog.title(title)
        dialog.transient(self)
        dialog.geometry("1020x380")
        dialog.minsize(760, 300)
        frame = ttk.Frame(dialog, padding=PAD * 2)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text=heading, style="Heading.TLabel").pack(anchor="w")
        ttk.Label(frame, text=explanation, justify="left", wraplength=840).pack(
            anchor="w", pady=(4, PAD)
        )

        holder = ttk.Frame(frame)
        holder.pack(fill="both", expand=True)
        columns = ("internal", "title", "author", "year", "location", "row", "why")
        tree = ttk.Treeview(holder, columns=columns, show="headings", height=8)
        for column, label, width in (
            ("internal", "Internal No.", 100), ("title", "Title", 230),
            ("author", "Author / Editor", 140), ("year", "Year", 45),
            ("location", "Location", 85), ("row", "Row", 40),
            ("why", "Why it matched", 330),
        ):
            tree.heading(column, text=label)
            # Only the reason column may grow; the rest keep their width so the
            # reasons stay readable when the dialog is resized.
            tree.column(column, width=width, minwidth=40, anchor="w",
                        stretch=(column == "why"))
        scroll = ttk.Scrollbar(holder, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        for record, reasons in rows:
            people = record.people.get("authors") or record.people.get("editors") or []
            tree.insert("", "end", values=(
                record.get("internal_number"), record.get("title"),
                people[0] if people else "", record.get("edition_year"),
                record.get("location"), record.row_index or "",
                ", ".join(reasons),
            ))

        choice = {"go": False}

        def proceed():
            choice["go"] = True
            dialog.destroy()

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(PAD * 2, 0))
        if default_proceed:
            ttk.Button(buttons, text=proceed_text, style="Accent.TButton",
                       command=proceed).pack(side="right")
            ttk.Button(buttons, text=cancel_text, command=dialog.destroy).pack(
                side="right", padx=(0, PAD))
        else:
            ttk.Button(buttons, text=cancel_text, style="Accent.TButton",
                       command=dialog.destroy).pack(side="right")
            ttk.Button(buttons, text=proceed_text, command=proceed).pack(
                side="right", padx=(0, PAD))

        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)  # closing means cancel
        dialog.bind("<Escape>", lambda _e: dialog.destroy())
        dialog.grab_set()
        dialog.wait_window()
        return choice["go"]

    def _blocked_by_internal_number(self, form, record, exclude_row=None):
        """Refuse an entry whose internal number is already in use.

        Unlike the other checks this one is not a warning: the internal library
        number identifies a book on the shelf, so a clash has to be resolved
        before the entry can be filed.
        """
        clash = duplicates.internal_number_conflict(
            self._records, record, exclude_row=exclude_row
        )
        if clash is None:
            return False

        number = record.get("internal_number")
        suggestion = duplicates.suggest_internal_number(
            self._records, number, exclude_row=exclude_row
        )

        dialog = tk.Toplevel(self)
        dialog.title("Internal Library Number already used")
        dialog.transient(self)
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=PAD * 2)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="That internal library number is already taken.",
                  style="Heading.TLabel").pack(anchor="w")
        ttk.Label(
            frame,
            text="'%s' belongs to:\n\n    %s\n    (spreadsheet row %s)\n\n"
                 "Every book needs its own internal library number, so please give "
                 "this one a number of its own before adding it." % (
                     number, clash.summary(), clash.row_index),
            justify="left", wraplength=460,
        ).pack(anchor="w", pady=(PAD, PAD))

        def use_suggestion():
            form.vars["internal_number"].set(suggestion)
            dialog.destroy()

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(PAD, 0))
        ttk.Button(buttons, text="Let me edit it", command=dialog.destroy).pack(side="right")
        if suggestion and suggestion != number:
            ttk.Button(buttons, text="Use '%s'" % suggestion, style="Accent.TButton",
                       command=use_suggestion).pack(side="right", padx=(0, PAD))

        dialog.grab_set()
        dialog.wait_window()

        # Whatever they chose, the entry is not filed on this click; if they took
        # the suggestion the number is now in the form, ready to add.
        form.focus_internal_number()
        return True

    def _duplicate_check_passes(self, form, record, exclude_row=None, verb="add"):
        """Run both checks. Returns True if the entry may be written."""
        if self._blocked_by_internal_number(form, record, exclude_row):
            self.set_status(
                "Not added: the internal library number is already used by another book."
            )
            return False
        matches = duplicates.find_matches(self._records, record, exclude_row=exclude_row)
        if not matches:
            return True
        go = self._match_dialog(
            title="Possible duplicate",
            heading="This book looks like one already in the catalog.",
            explanation=(
                "%s something with this entry. Sharing a Dewey or LC number is "
                "normal for books on the same subject, so this may well be fine "
                "-- have a look and decide." % (
                    "1 existing entry shares" if len(matches) == 1
                    else "%d existing entries share" % len(matches))
            ),
            rows=matches,
            proceed_text="Add it anyway" if verb == "add" else "Save it anyway",
            cancel_text="Cancel",
        )
        if not go:
            self.set_status("Cancelled - nothing was written to the catalog.")
        return go

    # ------------------------------------------------------------------
    # Shared actions
    # ------------------------------------------------------------------
    def _commit(self, form, after=None):
        """Validate, save the cover, append the row, and report what happened."""
        problem = form.validate()
        if problem:
            messagebox.showwarning("Check the entry", problem, parent=self)
            return
        record = form.harvest()
        if not self._duplicate_check_passes(form, record, verb="add"):
            return
        if not record.get("date_added"):
            record.set("date_added", datetime.date.today().isoformat())
        record.set("cover_image", form.cover.commit())
        try:
            row, backup, added = self.catalog.append(record)
        except CatalogError as exc:
            messagebox.showerror("Could not save", str(exc), parent=self)
            return
        self._announce_write(
            "Added '%s' as row %d" % (record.get("title"), row), backup, added
        )
        self.refresh_from_master(announce=False)
        if after:
            after()

    def _announce_write(self, headline, backup, added):
        parts = [headline]
        if backup:
            parts.append("previous version archived to 'old versions\\%s'" % os.path.basename(backup))
        if added:
            parts.append("new columns added: %s" % ", ".join(display_header(a) for a in added))
        self.set_status(".  ".join(parts) + ".")

    def refresh_from_master(self, announce=True):
        """Re-read the master file and fold its values into the dropdowns."""
        try:
            records, cmap = self.catalog.load_all()
        except CatalogError as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)
            return
        self._records, self._column_map = records, cmap
        locations, origins, subjects = self.catalog.distinct_values(records, cmap)
        self.config_obj.merge_locations(locations)
        self.config_obj.merge_origins(origins)
        self.config_obj.merge_subjects(subjects)
        for form in (self.lookup_form, self.manual_form, self.edit_form):
            form.refresh_options()
        self._populate_results(records)
        self._refresh_master_label()
        if announce:
            self.set_status("Loaded %d books from %s" % (len(records), os.path.basename(self.config_obj.master_path)))

    def set_status(self, text):
        self.status.set(text)

    # ------------------------------------------------------------------
    # Menu actions
    # ------------------------------------------------------------------
    def _choose_master(self):
        path = filedialog.asksaveasfilename(
            title="Choose or create the master catalog",
            parent=self, defaultextension=".xlsx",
            initialfile=os.path.basename(self.config_obj.master_path),
            initialdir=self.config_obj.master_dir,
            filetypes=[("Excel workbook", "*.xlsx")],
            confirmoverwrite=False,
        )
        if not path:
            return
        self.config_obj.set_master_path_from_display(path)
        self.config_obj.save()
        self.catalog = Catalog(self.config_obj)
        self._startup()

    def _open_path(self, path, description):
        if not os.path.exists(path):
            messagebox.showinfo(APP_NAME, "There is no %s yet." % description, parent=self)
            return
        try:
            os.startfile(path)  # noqa: S606 - Windows shell open, by design
        except OSError as exc:
            messagebox.showerror(APP_NAME, "Could not open %s:\n%s" % (description, exc), parent=self)

    def _open_master(self):
        self._open_path(self.config_obj.master_path, "master catalog")

    def _open_covers(self):
        self._open_path(self.config_obj.covers_dir, "cover images folder")

    def _open_versions(self):
        self._open_path(self.config_obj.versions_dir, "'old versions' folder")

    def _show_settings_tab(self):
        """The Settings tab replaced the old dialog; the menu item opens it."""
        self.notebook.select(3)

    def _about(self):
        messagebox.showinfo(
            "About %s" % APP_NAME,
            "%s version %s\n\n"
            "Catalogues books into '%s'.\n\n"
            "Metadata sources, in order:\n"
            "  1. Library of Congress catalog (catalog.loc.gov)\n"
            "  2. Open Library (openlibrary.org)\n"
            "  3. Google Books, only if a gap remains\n\n"
            "The master file is archived into 'old versions' once per session,\n"
            "before the first entry of that session is written."
            % (APP_NAME, __version__, os.path.basename(self.config_obj.master_path)),
            parent=self,
        )

    def _unsaved_work(self):
        """Which tabs hold edits that have not reached the catalog."""
        pending = []
        for index, form, name in (
            (0, self.lookup_form, "Look Up by ISBN / ISSN"),
            (1, self.manual_form, "Manual Entry"),
            (2, self.edit_form, "Find and Edit"),
        ):
            # The edit pane only counts once a row is actually loaded into it.
            if form is self.edit_form and not form._current_row_index:
                continue
            if form.is_dirty() and not form.is_blank():
                pending.append((index, form, name))
        # Settings edits are staged until Update, so they can be lost too.
        if getattr(self, "_settings_dirty", False):
            pending.append((3, None, "Settings"))
        return pending

    def _confirm_discard(self, pending):
        """Ask whether to close and lose the work, or stay and finish it.

        Returns True to close.  A purpose-built dialog rather than a Yes/No box,
        because "Yes" is a poor label for "throw away what I just typed".
        """
        first_tab = pending[0][0]
        self.notebook.select(first_tab)
        names = ", ".join(name for _i, _f, name in pending)

        dialog = tk.Toplevel(self)
        dialog.title("Unsaved entry")
        dialog.transient(self)
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=PAD * 2)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="This entry has not been added to the catalog.",
                  style="Heading.TLabel").pack(anchor="w")
        ttk.Label(
            frame,
            text="Unsaved work on: %s\n\nClosing now will discard it." % names,
            justify="left", wraplength=420,
        ).pack(anchor="w", pady=(PAD, PAD * 2))

        choice = {"close": False}

        def close_anyway():
            choice["close"] = True
            dialog.destroy()

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        ttk.Button(
            buttons, text="Stay open and finish", style="Accent.TButton",
            command=dialog.destroy,
        ).pack(side="right")
        ttk.Button(
            buttons, text="Close without saving", command=close_anyway
        ).pack(side="right", padx=(0, PAD))

        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)  # the X means "stay"
        dialog.bind("<Escape>", lambda _e: dialog.destroy())
        dialog.grab_set()
        dialog.wait_window()
        return choice["close"]

    def _on_close(self):
        pending = self._unsaved_work()
        if pending and not self._confirm_discard(pending):
            return
        self.config_obj.save()
        self.destroy()


def main():
    try:
        app = BasicHomeLibrary()
    except Exception:
        # Without a console there is nowhere for a traceback to go, so show it.
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(APP_NAME, "The app could not start:\n\n%s" % traceback.format_exc())
        return 1
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
