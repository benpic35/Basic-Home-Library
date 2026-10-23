"""Persistent user settings: file locations, dropdown options, API keys."""

import json
import os
import sys

from . import APP_NAME, MASTER_BASENAME
from .schema import DEFAULT_SUBJECTS

DEFAULT_LOCATIONS = ["Library", "Office", "Living Room"]

# No key ships with the app.  Google Books works without one, just at a lower
# request rate; anyone who wants the higher rate adds their own free key on the
# Settings tab, and it is stored in their own settings file.
DEFAULT_GOOGLE_BOOKS_KEY = ""


def app_dir():
    """Directory the app lives in - where the master file goes by default.

    When frozen by PyInstaller ``sys.executable`` is the .exe, otherwise fall
    back to the project folder holding this package.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def config_dir():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "BasicHomeLibrary")


def shorten_path(path):
    """Drop the app-folder prefix from a path, leaving anything else intact.

    A catalog kept beside the app then shows as a bare file name, which is the
    same on everyone's machine and keeps a home directory out of screenshots.
    """
    if not path:
        return ""
    full = os.path.abspath(path)
    base = os.path.abspath(app_dir())
    try:
        relative = os.path.relpath(full, base)
    except ValueError:          # different drive: no relative form exists
        return full
    return full if relative.startswith("..") else relative


class Config(object):
    def __init__(self):
        self.path = os.path.join(config_dir(), "config.json")
        self.master_path = os.path.join(app_dir(), MASTER_BASENAME + ".xlsx")
        self.locations = list(DEFAULT_LOCATIONS)
        self.origins = []
        self.subjects = [header for header, _label in DEFAULT_SUBJECTS]
        # Google Books answers without a key but caps how often a given IP
        # address may ask; adding a key on the Settings tab raises that ceiling.
        self.google_books_key = DEFAULT_GOOGLE_BOOKS_KEY
        self.request_timeout = 20
        # False while master_path is just "next to the app".  Only an explicit
        # choice is written to the config file, so moving the app folder keeps
        # the catalog travelling with it instead of pointing at the old path.
        self.master_path_is_explicit = False
        self.load()

    # -- derived paths -------------------------------------------------------
    @property
    def master_dir(self):
        return os.path.dirname(os.path.abspath(self.master_path))

    @property
    def covers_dir(self):
        return os.path.join(self.master_dir, "Book Covers")

    @property
    def versions_dir(self):
        return os.path.join(self.master_dir, "old versions")

    # -- how the master path is shown and typed ------------------------------
    @property
    def master_path_display(self):
        """The master path as the user should see it.

        A catalog sitting beside the app is shown by name alone, which is
        shorter, is the same on everyone's machine, and keeps a home directory
        out of screenshots.  A catalog kept elsewhere is shown in full, because
        then the location is the useful part.
        """
        return shorten_path(self.master_path)

    def set_master_path_from_display(self, text):
        """Accept what the user typed, relative to the app folder if need be.

        Returns True if this actually points somewhere new.
        """
        text = (text or "").strip().strip('"')
        if not text:
            return False
        resolved = os.path.abspath(
            text if os.path.isabs(text) else os.path.join(app_dir(), text)
        )
        if not os.path.splitext(resolved)[1]:
            resolved += ".xlsx"
        if resolved == os.path.abspath(self.master_path):
            return False
        self.master_path = resolved
        # Only a path outside the app folder is worth remembering; one beside
        # the app should keep following the app if the folder is moved.
        self.master_path_is_explicit = (
            os.path.dirname(resolved) != os.path.abspath(app_dir())
        )
        return True

    # -- persistence ---------------------------------------------------------
    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (IOError, OSError, ValueError):
            return
        saved_master = data.get("master_path")
        if saved_master:
            self.master_path = saved_master
            self.master_path_is_explicit = True
        self.locations = data.get("locations") or self.locations
        self.origins = data.get("origins") or self.origins
        self.subjects = data.get("subjects") or self.subjects
        # A saved value wins, including a deliberately blank one; only a config
        # file predating this setting falls through to the built-in key.
        self.google_books_key = data.get("google_books_key", self.google_books_key)
        self.request_timeout = int(data.get("request_timeout", self.request_timeout))

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            payload = {
                "locations": self.locations,
                "origins": self.origins,
                "subjects": self.subjects,
                "google_books_key": self.google_books_key,
                "request_timeout": self.request_timeout,
            }
            if self.master_path_is_explicit:
                payload["master_path"] = self.master_path
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
        except (IOError, OSError) as exc:
            sys.stderr.write("%s: could not save settings: %s\n" % (APP_NAME, exc))

    # -- option lists --------------------------------------------------------
    def add_location(self, name):
        name = (name or "").strip()
        if not name:
            return False
        lowered = [loc.lower() for loc in self.locations]
        if name.lower() in lowered:
            return False
        self.locations.append(name)
        self.save()
        return True

    def merge_locations(self, found):
        """Fold locations discovered in the master file into the dropdown."""
        changed = False
        lowered = {loc.lower() for loc in self.locations}
        for name in sorted(found):
            name = (name or "").strip()
            if name and name.lower() not in lowered:
                self.locations.append(name)
                lowered.add(name.lower())
                changed = True
        if changed:
            self.save()
        return changed

    def add_origin(self, name):
        """Add an origin to the dropdown. Mirrors add_location exactly."""
        name = (name or "").strip()
        if not name:
            return False
        lowered = [item.lower() for item in self.origins]
        if name.lower() in lowered:
            return False
        self.origins.append(name)
        self.origins.sort(key=str.lower)
        self.save()
        return True

    def merge_origins(self, found):
        """Fold origins discovered in the master file into the dropdown."""
        changed = False
        lowered = {item.lower() for item in self.origins}
        for name in sorted(found):
            name = (name or "").strip()
            if name and name.lower() not in lowered:
                self.origins.append(name)
                lowered.add(name.lower())
                changed = True
        if changed:
            self.origins.sort(key=str.lower)
            self.save()
        return changed

    # -- bulk replacement, used by the Settings tab ---------------------------
    def set_locations(self, items):
        self.locations = [i.strip() for i in items if i and i.strip()]
        self.save()

    def set_origins(self, items):
        self.origins = [i.strip() for i in items if i and i.strip()]
        self.save()

    def set_subjects(self, items):
        """Replace the subject list, order included.

        The order here is the order the checkboxes appear in, which is why the
        Settings tab can move entries up and down.
        """
        self.subjects = [i.strip() for i in items if i and i.strip()]
        self.save()

    def add_subject(self, header):
        header = (header or "").strip()
        if not header:
            return False
        lowered = [s.lower().replace("\n", " ") for s in self.subjects]
        if header.lower() in lowered:
            return False
        self.subjects.append(header)
        self.save()
        return True

    def merge_subjects(self, found):
        changed = False
        lowered = {s.lower().replace("\n", " ") for s in self.subjects}
        for header in found:
            key = header.lower().replace("\n", " ")
            if key not in lowered:
                self.subjects.append(header)
                lowered.add(key)
                changed = True
        if changed:
            self.save()
        return changed
