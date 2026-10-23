"""Cover images: naming, storage in one shared folder, and preview thumbnails."""

import os
import re
import shutil

try:
    from PIL import Image, ImageTk
    HAVE_PILLOW = True
except ImportError:  # pragma: no cover - Pillow is optional
    Image = ImageTk = None
    HAVE_PILLOW = False

from .sources import download_cover

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_name(text, limit=70):
    """Turn a title into something Windows will accept as a file name."""
    text = _UNSAFE.sub("", str(text or "")).strip(" .")
    text = re.sub(r"\s+", " ", text)
    return text[:limit].strip() or "untitled"


def cover_filename(record, extension=".jpg"):
    """Name a cover after the book's identifier and title, so it is findable."""
    identifier = (
        record.get("internal_number")
        or record.get("isbn13")
        or record.get("isbn10")
        or record.get("issn")
    )
    title = safe_name(record.get("title"))
    stem = "%s - %s" % (safe_name(identifier, 25), title) if identifier else title
    return stem + extension


def ensure_dir(config):
    os.makedirs(config.covers_dir, exist_ok=True)
    return config.covers_dir


def _unique_path(directory, filename):
    """Avoid clobbering an existing cover for a different book."""
    stem, extension = os.path.splitext(filename)
    candidate = os.path.join(directory, filename)
    counter = 2
    while os.path.exists(candidate):
        candidate = os.path.join(directory, "%s (%d)%s" % (stem, counter, extension))
        counter += 1
    return candidate


def save_from_url(record, url, config):
    """Download ``url`` into the covers folder. Returns the saved path."""
    data, extension = download_cover(url, timeout=config.request_timeout)
    directory = ensure_dir(config)
    path = _unique_path(directory, cover_filename(record, extension))
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def save_from_bytes(record, data, extension, config):
    """Write an already-downloaded cover into the covers folder.

    Lookups fetch the image up front so the user can see it, but nothing is
    written to disk until the entry is filed -- abandoning a search must not
    leave a stray image behind.
    """
    directory = ensure_dir(config)
    path = _unique_path(directory, cover_filename(record, extension or ".jpg"))
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def save_from_file(record, source_path, config):
    """Copy a cover the user picked from disk into the covers folder."""
    extension = os.path.splitext(source_path)[1].lower() or ".jpg"
    directory = ensure_dir(config)
    path = _unique_path(directory, cover_filename(record, extension))
    shutil.copy2(source_path, path)
    return path


def resolve(path_or_name, config):
    """Find a cover on disk given either a full path or a bare file name."""
    if not path_or_name:
        return ""
    if os.path.isabs(path_or_name) and os.path.isfile(path_or_name):
        return path_or_name
    for base in (config.covers_dir, config.master_dir):
        candidate = os.path.join(base, path_or_name)
        if os.path.isfile(candidate):
            return candidate
    return ""


def thumbnail_from_bytes(data, max_size=(240, 320)):
    """Preview an in-memory image. Needs Pillow; returns None without it."""
    if not data or not HAVE_PILLOW:
        return None
    try:
        import io

        image = Image.open(io.BytesIO(data))
        image.load()
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGB")
        image.thumbnail(max_size, Image.LANCZOS)
        return ImageTk.PhotoImage(image)
    except Exception:
        return None


def load_thumbnail(path, max_size=(240, 320)):
    """Return a Tk-displayable image scaled to fit ``max_size``, or None.

    Uses Pillow when available (needed for JPEG); otherwise falls back to Tk's
    own PNG/GIF support so the app still previews something without it.
    """
    if not path or not os.path.isfile(path):
        return None
    if HAVE_PILLOW:
        try:
            image = Image.open(path)
            image.load()
            if image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGB")
            image.thumbnail(max_size, Image.LANCZOS)
            return ImageTk.PhotoImage(image)
        except Exception:
            return None
    try:
        import tkinter as tk

        photo = tk.PhotoImage(file=path)
        factor = max(
            1,
            -(-photo.width() // max_size[0]),
            -(-photo.height() // max_size[1]),
        )
        return photo.subsample(factor, factor) if factor > 1 else photo
    except Exception:
        return None
