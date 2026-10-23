"""Reusable Tk widgets.

``BookForm`` is deliberately generic: the same widget backs the lookup tab, the
manual-entry tab and the edit pane of the search tab, so a field only ever has
to be defined once.
"""

import os
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from . import covers
from .model import BookRecord
from .schema import (
    BOOLEAN_SCALARS,
    ROLES,
    SCALAR_FIELDS,
    SCALAR_LABEL_BY_KEY,
    display_header,
    norm_header,
)

PAD = 6


class ScrollableFrame(ttk.Frame):
    """A frame that scrolls vertically, with a working Windows mouse wheel."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self.canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.inner = ttk.Frame(self.canvas)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        # Bind the wheel while the pointer is over this canvas only, so nested
        # scrollable areas do not fight each other.
        self.canvas.bind("<Enter>", lambda _e: self.canvas.bind_all("<MouseWheel>", self._on_wheel))
        self.canvas.bind("<Leave>", lambda _e: self.canvas.unbind_all("<MouseWheel>"))

    def _on_inner_configure(self, _event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self._window, width=event.width)

    def _on_wheel(self, event):
        if self.canvas.yview() == (0.0, 1.0):
            return  # nothing to scroll
        self.canvas.yview_scroll(int(-event.delta / 120), "units")


class PersonList(ttk.Frame):
    """A growable list of name entries for one role (authors, illustrators...).

    Always shows at least ``minimum`` rows so the form does not look empty, and
    the user can add as many more as a book needs.
    """

    def __init__(self, parent, label, minimum=1, width=34):
        super().__init__(parent)
        self.minimum = minimum
        self.width = width
        self.rows = []

        header = ttk.Frame(self)
        header.pack(fill="x")
        ttk.Label(header, text=label, style="Field.TLabel").pack(side="left")
        ttk.Button(header, text="+", width=3, command=self.add_row).pack(side="right")

        self.body = ttk.Frame(self)
        self.body.pack(fill="x", pady=(2, 0))
        for _ in range(minimum):
            self.add_row()

    def add_row(self, value=""):
        row = ttk.Frame(self.body)
        row.pack(fill="x", pady=1)
        var = tk.StringVar(value=value)
        entry = ttk.Entry(row, textvariable=var, width=self.width)
        entry.pack(side="left", fill="x", expand=True)
        remove = ttk.Button(row, text="X", width=3)
        remove.pack(side="left", padx=(3, 0))
        item = {"frame": row, "var": var, "entry": entry}
        remove.configure(command=lambda: self._remove(item))
        self.rows.append(item)
        return entry

    def _remove(self, item):
        if len(self.rows) <= self.minimum:
            item["var"].set("")
            return
        item["frame"].destroy()
        self.rows.remove(item)

    def get(self):
        return [r["var"].get().strip() for r in self.rows if r["var"].get().strip()]

    def set(self, names):
        names = [n for n in (names or []) if n]
        for item in list(self.rows):
            item["frame"].destroy()
        self.rows = []
        for name in names:
            self.add_row(name)
        while len(self.rows) < self.minimum:
            self.add_row()

    def clear(self):
        self.set([])


class SubjectGrid(ttk.Frame):
    """Multi-select subject categories, with the option to add new ones.

    A book can carry any number of these -- "Nonfiction" and "Science" together,
    for instance -- and each becomes its own "yes" column in the spreadsheet.
    """

    COLUMNS = 4

    def __init__(self, parent, config, on_add=None):
        super().__init__(parent)
        self.config_obj = config
        self.on_add = on_add
        self.vars = {}  # raw header -> BooleanVar

        self.grid_body = ttk.Frame(self)
        self.grid_body.pack(fill="both", expand=True)

        footer = ttk.Frame(self)
        footer.pack(fill="x", pady=(PAD, 0))
        ttk.Button(footer, text="Add category...", command=self._add_category).pack(side="left")
        ttk.Button(footer, text="Clear all", command=self.clear).pack(side="left", padx=(PAD, 0))

        self.rebuild()

    def rebuild(self):
        """Lay the categories out in their configured order, column by column.

        The list reads top-to-bottom down the first column, then continues at
        the top of the next -- newspaper style -- rather than wrapping along
        each row, so the order of the categories stays easy to follow.
        """
        for child in self.grid_body.winfo_children():
            child.destroy()
        for column in range(self.COLUMNS):
            self.grid_body.columnconfigure(column, weight=1, uniform="subject")

        headers = list(self.config_obj.subjects)
        # Forget variables for categories that no longer exist.  Without this a
        # category ticked before being removed in Settings would still be
        # harvested into the record -- and would put its column straight back.
        known = {norm_header(h) for h in headers}
        self.vars = {h: v for h, v in self.vars.items() if norm_header(h) in known}

        # Ceiling division: the last column is the one allowed to be short.
        rows = max(1, -(-len(headers) // self.COLUMNS))
        for index, header in enumerate(headers):
            var = self.vars.setdefault(header, tk.BooleanVar(value=False))
            ttk.Checkbutton(
                self.grid_body, text=display_header(header), variable=var
            ).grid(row=index % rows, column=index // rows, sticky="w", padx=2, pady=1)

    def _add_category(self):
        name = simpledialog.askstring(
            "Add subject category",
            "Name of the new subject category:",
            parent=self.winfo_toplevel(),
        )
        if not name or not name.strip():
            return
        name = name.strip()
        if not self.config_obj.add_subject(name):
            messagebox.showinfo(
                "Already there", "'%s' is already a subject category." % name,
                parent=self.winfo_toplevel(),
            )
            return
        self.rebuild()
        self.vars[name].set(True)
        if self.on_add:
            self.on_add(name)

    def get(self):
        return {header for header, var in self.vars.items() if var.get()}

    def set(self, subjects):
        wanted = {norm_header(s) for s in (subjects or [])}
        # A record may carry a subject the dropdown list has not seen yet.
        for subject in subjects or []:
            if not any(norm_header(h) == norm_header(subject) for h in self.config_obj.subjects):
                self.config_obj.add_subject(subject)
                self.rebuild()
        for header, var in self.vars.items():
            var.set(norm_header(header) in wanted)

    def clear(self):
        for var in self.vars.values():
            var.set(False)


class OptionListEditor(ttk.LabelFrame):
    """Add, remove and reorder one list of dropdown options.

    Edits are held here until the Settings tab applies them, so nothing changes
    under the rest of the app mid-edit.  ``usage_lookup`` reports how many
    catalog entries use a value: an option still in use cannot be removed,
    because the app rebuilds these lists from the master file and a value still
    present there would simply come back.
    """

    def __init__(self, parent, title, prompt, usage_lookup=None,
                 allow_reorder=False, on_change=None, height=9, noun="option"):
        super().__init__(parent, text=title, padding=PAD)
        self.prompt = prompt
        self.usage_lookup = usage_lookup
        self.on_change = on_change
        self.noun = noun

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(
            body, height=height, exportselection=False, activestyle="none"
        )
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scroll.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.listbox.bind("<Double-Button-1>", lambda _e: self.rename_selected())

        buttons = ttk.Frame(self)
        buttons.pack(fill="x", pady=(PAD, 0))
        ttk.Button(buttons, text="Add...", command=self.add).pack(side="left")
        ttk.Button(buttons, text="Rename...", command=self.rename_selected).pack(
            side="left", padx=(4, 0))
        ttk.Button(buttons, text="Remove", command=self.remove_selected).pack(
            side="left", padx=(4, 0))
        if allow_reorder:
            ttk.Button(buttons, text="Up", width=4,
                       command=lambda: self._move(-1)).pack(side="right")
            ttk.Button(buttons, text="Down", width=6,
                       command=lambda: self._move(1)).pack(side="right", padx=(0, 4))

    # -- contents ------------------------------------------------------------
    def set_items(self, items):
        self.listbox.delete(0, "end")
        for item in items:
            self.listbox.insert("end", display_header(item))

    def get_items(self):
        return list(self.listbox.get(0, "end"))

    def _changed(self):
        if self.on_change:
            self.on_change()

    def _selected_index(self):
        selection = self.listbox.curselection()
        return selection[0] if selection else None

    # -- actions -------------------------------------------------------------
    def add(self):
        name = simpledialog.askstring(
            "Add %s" % self.noun, self.prompt, parent=self.winfo_toplevel()
        )
        if not name or not name.strip():
            return
        name = name.strip()
        existing = [i.lower() for i in self.get_items()]
        if name.lower() in existing:
            messagebox.showinfo(
                "Already there", "'%s' is already in the list." % name,
                parent=self.winfo_toplevel(),
            )
            return
        self.listbox.insert("end", name)
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set("end")
        self.listbox.see("end")
        self._changed()

    def rename_selected(self):
        index = self._selected_index()
        if index is None:
            return
        current = self.listbox.get(index)
        used = self.usage_lookup(current) if self.usage_lookup else 0
        if used:
            messagebox.showinfo(
                "In use",
                "'%s' is used by %d entr%s in the catalog.\n\n"
                "Renaming it here would only change the list of choices, not "
                "those entries, so they would no longer match. Change the "
                "entries on the Find and Edit tab first."
                % (current, used, "y" if used == 1 else "ies"),
                parent=self.winfo_toplevel(),
            )
            return
        name = simpledialog.askstring(
            "Rename %s" % self.noun, self.prompt,
            initialvalue=current, parent=self.winfo_toplevel(),
        )
        if not name or not name.strip() or name.strip() == current:
            return
        name = name.strip()
        others = [i.lower() for n, i in enumerate(self.get_items()) if n != index]
        if name.lower() in others:
            messagebox.showinfo(
                "Already there", "'%s' is already in the list." % name,
                parent=self.winfo_toplevel(),
            )
            return
        self.listbox.delete(index)
        self.listbox.insert(index, name)
        self.listbox.selection_set(index)
        self._changed()

    def remove_selected(self):
        index = self._selected_index()
        if index is None:
            messagebox.showinfo(
                "Nothing selected", "Choose a %s in the list first." % self.noun,
                parent=self.winfo_toplevel(),
            )
            return
        name = self.listbox.get(index)
        used = self.usage_lookup(name) if self.usage_lookup else 0
        if used:
            messagebox.showwarning(
                "Still in use",
                "'%s' is used by %d entr%s in the catalog, so it cannot be "
                "removed.\n\nChange those entries on the Find and Edit tab "
                "first, then remove it here."
                % (name, used, "y" if used == 1 else "ies"),
                parent=self.winfo_toplevel(),
            )
            return
        if not messagebox.askyesno(
            "Remove %s" % self.noun,
            "Remove '%s' from the list of choices?" % name,
            parent=self.winfo_toplevel(), default="no",
        ):
            return
        self.listbox.delete(index)
        self._changed()

    def _move(self, delta):
        index = self._selected_index()
        if index is None:
            return
        target = index + delta
        if not 0 <= target < self.listbox.size():
            return
        value = self.listbox.get(index)
        self.listbox.delete(index)
        self.listbox.insert(target, value)
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(target)
        self.listbox.see(target)
        self._changed()


class CoverPanel(ttk.LabelFrame):
    """Preview and management of the book's cover image."""

    def __init__(self, parent, config, get_record):
        super().__init__(parent, text="Cover image", padding=PAD)
        self.config_obj = config
        self.get_record = get_record
        self.path = ""            # saved file on disk
        self.pending_url = ""     # found online, not yet fetched
        self.pending_data = None  # fetched during lookup, written on save
        self.pending_ext = ""
        self._photo = None

        self.preview = ttk.Label(self, anchor="center", justify="center")
        self.preview.pack(fill="both", expand=True)
        self.caption = ttk.Label(self, text="No cover", style="Hint.TLabel", wraplength=230,
                                 anchor="center", justify="center")
        self.caption.pack(fill="x", pady=(4, PAD))

        buttons = ttk.Frame(self)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Choose file...", command=self.choose_file).pack(fill="x")
        self.download_button = ttk.Button(
            buttons, text="Download found cover", command=self.download_now, state="disabled"
        )
        self.download_button.pack(fill="x", pady=(3, 0))
        ttk.Button(buttons, text="Remove", command=self.clear).pack(fill="x", pady=(3, 0))

    # -- state ---------------------------------------------------------------
    def set_saved(self, path):
        self.path = path or ""
        self.pending_url = ""
        self.pending_data = None
        self.download_button.configure(state="disabled")
        self._render()

    def set_pending_url(self, url):
        self.pending_url = url or ""
        self.download_button.configure(state="normal" if url else "disabled")
        self._render()

    def set_downloaded(self, data, extension, url=""):
        """Show a cover fetched during lookup, before it is written to disk."""
        self.pending_data = data
        self.pending_ext = extension or ".jpg"
        self.pending_url = url or self.pending_url
        self.path = ""
        self.download_button.configure(state="disabled")
        self._render()

    def clear(self):
        self.path = ""
        self.pending_url = ""
        self.pending_data = None
        self.pending_ext = ""
        self.download_button.configure(state="disabled")
        self._render()

    def _render(self):
        if self.pending_data is not None:
            image = covers.thumbnail_from_bytes(self.pending_data)
            self._photo = image
            if image is not None:
                self.preview.configure(image=image, text="")
            else:
                self.preview.configure(image="", text="\n(cover found)\n")
            self.caption.configure(
                text="Downloaded automatically.\nSaved when you add the book."
            )
            return

        image = covers.load_thumbnail(covers.resolve(self.path, self.config_obj))
        self._photo = image
        if image is not None:
            self.preview.configure(image=image, text="")
            self.caption.configure(text=os.path.basename(self.path))
        else:
            self.preview.configure(image="", text="\n(no image)\n")
            if self.path:
                self.caption.configure(text="Linked: %s\n(file not found)" % os.path.basename(self.path))
            elif self.pending_url:
                self.caption.configure(text="A cover was found online.\nUse the button below to fetch it.")
            else:
                self.caption.configure(text="No cover")

    # -- actions -------------------------------------------------------------
    def choose_file(self):
        path = filedialog.askopenfilename(
            title="Choose a cover image",
            parent=self.winfo_toplevel(),
            filetypes=[("Images", "*.jpg *.jpeg *.png *.gif"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            saved = covers.save_from_file(self.get_record(), path, self.config_obj)
        except (IOError, OSError) as exc:
            messagebox.showerror("Cover image", "Could not copy that image:\n%s" % exc,
                                 parent=self.winfo_toplevel())
            return
        self.set_saved(saved)

    def download_now(self):
        if not self.pending_url:
            return
        try:
            saved = covers.save_from_url(self.get_record(), self.pending_url, self.config_obj)
        except (IOError, OSError) as exc:
            messagebox.showerror("Cover image", "Could not download the cover:\n%s" % exc,
                                 parent=self.winfo_toplevel())
            return
        self.set_saved(saved)

    def commit(self):
        """Write the cover to disk. Returns the saved path or ""."""
        if self.path:
            return self.path
        try:
            if self.pending_data is not None:
                self.path = covers.save_from_bytes(
                    self.get_record(), self.pending_data, self.pending_ext, self.config_obj
                )
                self.pending_data = None
            elif self.pending_url:
                self.path = covers.save_from_url(
                    self.get_record(), self.pending_url, self.config_obj
                )
            self.pending_url = ""
        except (IOError, OSError):
            return ""  # a missing cover must never block filing the book
        return self.path


class BookForm(ttk.Frame):
    """The full editable view of one book."""

    # Text fields laid out in the left-hand "Book details" block.
    DETAIL_FIELDS = [
        ("title", 52), ("series", 52), ("isbn10", 24), ("isbn13", 24), ("issn", 24),
        ("edition_year", 12), ("original_year", 12), ("publisher", 40),
        ("dds", 16), ("lcc", 16), ("lccn", 24), ("internal_number", 24),
        ("language", 24),
    ]

    def __init__(self, parent, config, on_dirty=None, show_clear=True, on_clear=None):
        super().__init__(parent, padding=PAD)
        self.config_obj = config
        self.on_dirty = on_dirty
        self.on_clear = on_clear
        self.show_clear = show_clear
        # Signature of the data as last loaded or cleared; anything different
        # on screen counts as an unsaved change.
        self._clean_signature = None
        self.vars = {}
        self.person_lists = {}
        self._current_row_index = None
        # Tracks the LCC value the internal number was last derived from, so
        # typing in LCC keeps updating it until the user edits it by hand.
        self._internal_follows_lcc = ""
        # Values of the record last loaded, so fields with no widget on this
        # form survive an edit instead of being blanked out.
        self._loaded_scalars = {}

        scroller = ScrollableFrame(self)
        scroller.pack(fill="both", expand=True)
        body = scroller.inner
        # Three columns: details grow, people and the cover stay their natural
        # width.  Subjects sit beside the shelf block on the second row, which
        # keeps the whole form visible without scrolling on a 1180px window.
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=0)
        body.columnconfigure(2, weight=0)

        self._row0 = 0
        self._build_details(body)
        self._build_people(body)
        self._build_cover(body)
        self._build_shelf(body)
        self._build_subjects(body)

        # Set up last, so building the fields does not trigger it.
        self.vars["lcc"].trace_add("write", self._follow_lcc)
        self.mark_clean()

    def clear_with_confirm(self):
        """Empty the whole form, asking first if there is anything to lose."""
        if not self.is_blank():
            if not messagebox.askyesno(
                "Clear all fields",
                "Clear everything on this form?\n\nAnything not yet added to the "
                "catalog will be lost.",
                parent=self.winfo_toplevel(), default="no",
            ):
                return
        self.clear()
        if self.on_clear:
            self.on_clear()
        self.focus_title()

    # -- construction --------------------------------------------------------
    def _build_details(self, parent):
        frame = ttk.LabelFrame(parent, text="Book details", padding=PAD)
        frame.grid(row=self._row0, column=0, sticky="nsew", padx=(0, PAD), pady=(0, PAD))
        frame.columnconfigure(1, weight=1)
        for row, (key, width) in enumerate(self.DETAIL_FIELDS):
            ttk.Label(frame, text=SCALAR_LABEL_BY_KEY[key] + ":", style="Field.TLabel").grid(
                row=row, column=0, sticky="w", pady=1, padx=(0, PAD)
            )
            var = tk.StringVar()
            var.trace_add("write", self._mark_dirty)
            self.vars[key] = var
            entry = ttk.Entry(frame, textvariable=var, width=width)
            entry.grid(row=row, column=1, sticky="ew", pady=1)
            if key == "title":
                self.title_entry = entry
            elif key == "internal_number":
                self.internal_number_entry = entry
        ttk.Label(
            frame,
            text="LCC is the shelving classification (PN1994); LCCN is the catalog "
                 "record's number (2013016108). Internal Library Number follows the "
                 "LCC and can be overwritten.",
            style="Hint.TLabel", wraplength=380, justify="left",
        ).grid(row=len(self.DETAIL_FIELDS), column=0, columnspan=2, sticky="w", pady=(PAD, 0))

    def _build_people(self, parent):
        frame = ttk.LabelFrame(parent, text="People", padding=PAD)
        frame.grid(row=self._row0, column=1, sticky="nsew", padx=(0, PAD), pady=(0, PAD))
        for index, (key, _base, label, initial) in enumerate(ROLES):
            widget = PersonList(frame, label, minimum=min(initial, 2))
            widget.pack(fill="x", pady=(0, PAD) if index < len(ROLES) - 1 else 0)
            self.person_lists[key] = widget
        ttk.Label(
            frame, text="Names are stored as \"Last, First\". Use + to add more.",
            style="Hint.TLabel", wraplength=300, justify="left",
        ).pack(anchor="w", pady=(PAD, 0))

    def _build_shelf(self, parent):
        frame = ttk.LabelFrame(parent, text="Shelf and provenance", padding=PAD)
        frame.grid(row=self._row0 + 1, column=0, sticky="nsew", padx=(0, PAD), pady=(0, PAD))
        frame.columnconfigure(1, weight=1)
        self.shelf_frame = frame

        ttk.Label(frame, text="Location:", style="Field.TLabel").grid(row=0, column=0, sticky="w", padx=(0, PAD))
        self.vars["location"] = tk.StringVar()
        self.vars["location"].trace_add("write", self._mark_dirty)
        self.location_box = ttk.Combobox(
            frame, textvariable=self.vars["location"], state="readonly",
            values=list(self.config_obj.locations), width=24,
        )
        self.location_box.grid(row=0, column=1, sticky="ew", pady=1)
        ttk.Button(frame, text="Add...", width=8, command=self._add_location).grid(
            row=0, column=2, padx=(4, 0)
        )

        ttk.Label(frame, text="Origin:", style="Field.TLabel").grid(row=1, column=0, sticky="w", padx=(0, PAD))
        self.vars["origin"] = tk.StringVar()
        self.vars["origin"].trace_add("write", self._mark_dirty)
        self.origin_box = ttk.Combobox(
            frame, textvariable=self.vars["origin"], state="readonly",
            values=list(self.config_obj.origins), width=24,
        )
        self.origin_box.grid(row=1, column=1, sticky="ew", pady=1)
        ttk.Button(frame, text="Add...", width=8, command=self._add_origin).grid(
            row=1, column=2, padx=(4, 0)
        )

        flags = ttk.Frame(frame)
        flags.grid(row=2, column=0, columnspan=3, sticky="w", pady=(PAD, 0))
        for key in ("signed",):
            var = tk.BooleanVar()
            var.trace_add("write", self._mark_dirty)
            self.vars[key] = var
            ttk.Checkbutton(flags, text=SCALAR_LABEL_BY_KEY[key], variable=var).pack(side="left", padx=(0, PAD * 2))

        ttk.Label(frame, text="Notes:", style="Field.TLabel").grid(row=3, column=0, sticky="nw", pady=(PAD, 0))
        self.notes = tk.Text(frame, height=4, width=40, wrap="word")
        self.notes.grid(row=3, column=1, columnspan=2, sticky="ew", pady=(PAD, 0))
        self.notes.bind("<<Modified>>", self._on_notes_modified)

    def _build_cover(self, parent):
        self.cover = CoverPanel(parent, self.config_obj, self.harvest)
        self.cover.grid(row=self._row0, column=2, sticky="nsew", pady=(0, PAD))

    def _build_subjects(self, parent):
        frame = ttk.LabelFrame(
            parent, text="Subject categories  (tick every one that applies)", padding=PAD
        )
        frame.grid(row=self._row0 + 1, column=1, columnspan=2, sticky="nsew", pady=(0, PAD))
        self.subjects = SubjectGrid(frame, self.config_obj, on_add=lambda _n: self._mark_dirty())
        self.subjects.pack(fill="both", expand=True)

    # -- events --------------------------------------------------------------
    def _mark_dirty(self, *_args):
        if self.on_dirty:
            self.on_dirty()

    def _follow_lcc(self, *_args):
        """Mirror the LC classification into the internal library number.

        Only while the internal number is empty or still holds the value it was
        given from a previous LCC -- once the user types their own shelving
        number, editing LCC leaves it alone.
        """
        lcc = self.vars["lcc"].get().strip()
        current = self.vars["internal_number"].get().strip()
        if not current or current == self._internal_follows_lcc:
            self._internal_follows_lcc = lcc
            self.vars["internal_number"].set(lcc)

    def _on_notes_modified(self, _event):
        if self.notes.edit_modified():
            self.notes.edit_modified(False)
            self._mark_dirty()

    def _add_location(self):
        name = simpledialog.askstring(
            "Add location", "Name of the new location:", parent=self.winfo_toplevel()
        )
        if not name or not name.strip():
            return
        name = name.strip()
        if not self.config_obj.add_location(name):
            messagebox.showinfo("Already there", "'%s' is already a location." % name,
                                parent=self.winfo_toplevel())
        self.refresh_options()
        self.vars["location"].set(name)

    def _add_origin(self):
        name = simpledialog.askstring(
            "Add origin",
            "Where did this book come from?\n"
            "(for example: Secondhand sale, or Birthday gift 2017)",
            parent=self.winfo_toplevel(),
        )
        if not name or not name.strip():
            return
        name = name.strip()
        if not self.config_obj.add_origin(name):
            messagebox.showinfo("Already there", "'%s' is already an origin." % name,
                                parent=self.winfo_toplevel())
        self.refresh_options()
        self.vars["origin"].set(name)

    # -- options -------------------------------------------------------------
    def refresh_options(self, origins=None):
        self.location_box.configure(values=list(self.config_obj.locations))
        self.origin_box.configure(values=list(self.config_obj.origins))
        self.subjects.rebuild()

    # -- data ----------------------------------------------------------------
    def load(self, record, treat_as_saved=True):
        """Put a record on the form.

        ``treat_as_saved`` says whether what is being loaded already exists in
        the catalog.  It does for a row opened on the Find and Edit tab, so the
        form starts clean.  It does not for a record just fetched from an online
        search: that is unsaved work from the moment it appears, and closing the
        app with it on screen has to prompt.
        """
        record = record or BookRecord()
        self._current_row_index = record.row_index
        self._loaded_scalars = dict(record.scalars)
        # An internal number that already differs from the LCC was set
        # deliberately, so leave it alone when LCC is edited later.
        internal, lcc = record.get("internal_number"), record.get("lcc")
        self._internal_follows_lcc = lcc if (not internal or internal == lcc) else "\x00"
        for key, var in self.vars.items():
            value = record.get(key)
            if key in BOOLEAN_SCALARS:
                var.set(bool(value))
            else:
                var.set(value)
        self.notes.delete("1.0", "end")
        self.notes.insert("1.0", record.get("notes"))
        self.notes.edit_modified(False)
        for key, widget in self.person_lists.items():
            widget.set(record.people.get(key, []))
        self.subjects.set(record.subjects)

        # A readonly combobox cannot show a value that is not in its list, so
        # anything the master file uses has to be adopted into the dropdown.
        location, origin = record.get("location"), record.get("origin")
        if location:
            self.config_obj.add_location(location)
        if origin:
            self.config_obj.add_origin(origin)
        if location or origin:
            self.refresh_options()
            if location:
                self.vars["location"].set(location)
            if origin:
                self.vars["origin"].set(origin)

        saved = covers.resolve(record.get("cover_image"), self.config_obj)
        self.cover.set_saved(saved or record.get("cover_image"))
        if not record.get("cover_image"):
            self.cover.set_pending_url(record.cover_url)

        if treat_as_saved:
            self.mark_clean()
        else:
            self.mark_unsaved()

    def harvest(self):
        """Build a BookRecord from what is currently on screen."""
        record = BookRecord()
        record.row_index = self._current_row_index
        for key, var in self.vars.items():
            record.set(key, var.get())
        record.set("notes", self.notes.get("1.0", "end").strip())
        for key, widget in self.person_lists.items():
            record.set_people(key, widget.get())
        for header in self.subjects.get():
            record.add_subject(header)
        record.set("cover_image", self.cover.path)
        record.cover_url = self.cover.pending_url
        # Fields the form does not show -- Date Added, for one -- would come
        # back empty and blank the cell when the row is rewritten. Carry over
        # whatever the loaded record held for them.
        handled = set(self.vars) | {"notes", "cover_image"}
        for key, _header, _label in SCALAR_FIELDS:
            if key not in handled and not record.get(key):
                record.set(key, self._loaded_scalars.get(key, ""))
        return record

    def clear(self):
        self._current_row_index = None
        self._internal_follows_lcc = ""
        self._loaded_scalars = {}
        for key, var in self.vars.items():
            var.set(False if key in BOOLEAN_SCALARS else "")
        self.notes.delete("1.0", "end")
        self.notes.edit_modified(False)
        for widget in self.person_lists.values():
            widget.clear()
        self.subjects.clear()
        self.cover.clear()
        self.mark_clean()

    def focus_title(self):
        self.title_entry.focus_set()

    def focus_internal_number(self):
        self.internal_number_entry.focus_set()
        self.internal_number_entry.select_range(0, "end")

    # -- unsaved-change tracking --------------------------------------------
    def mark_clean(self):
        """Record the current contents as the saved state."""
        self._clean_signature = self._signature()

    def mark_unsaved(self):
        """Treat whatever is on the form as work that has not been saved.

        The baseline becomes an empty form, so any content at all counts as a
        change -- used after an online search fills the form with a book that is
        not in the catalog yet.
        """
        self._clean_signature = BookRecord().signature()

    def _signature(self):
        record = self.harvest()
        # The cover counts as changed only once it is actually attached.
        record.cover_url = "1" if (self.cover.path or self.cover.pending_data is not None) else ""
        return record.signature()

    def is_dirty(self):
        """True if the form holds edits that have not been written to the catalog."""
        return self._signature() != self._clean_signature

    def is_blank(self):
        """True if there is nothing on the form worth keeping."""
        record = self.harvest()
        return (
            record.is_empty()
            and not self.cover.path
            and self.cover.pending_data is None
        )

    def validate(self):
        """Return a complaint string if the entry is not fit to file, else ""."""
        record = self.harvest()
        if not record.get("title"):
            return "A title is required before the book can be added."
        if not record.get("location"):
            return "Please choose a Location for the book."
        return ""

