# Basic-Home-Library

A Windows 10 / 11 desktop app for cataloguing a home library. Type in an ISBN or
ISSN, let it look the book up online, correct anything that looks wrong, and it
appends a row to the master **Home Library Catalog.xlsx**.

It uses only free, keyless sources — the Library of Congress catalog, Open
Library and Google Books — and keeps everything in one ordinary Excel file you
can open and edit yourself.

---

## Running it

**The easy way — the standalone app**

### [⬇ Download Basic-Home-Library.exe](../../releases/latest/download/Basic-Home-Library.exe)

That link always serves the newest build; earlier versions are on the
[Releases](../../releases) page. It is a single self-contained file and needs no
Python installation. Copy it wherever you want the library to live and
double-click it.

> **Windows will probably warn you the first time.** A blue "Windows protected
> your PC" box appears for any program that has not been through paid code
> signing, which this one has not. Click **More info**, then **Run anyway**. For
> the same reason an antivirus scanner may occasionally flag it: single-file
> PyInstaller programs share a packing method with some malware, so they draw
> false positives. If you would rather not take that on trust, run it from the
> source files instead — the instructions are below and the code is all here.

The app keeps its files next to the .exe:

```
Basic-Home-Library.exe
Home Library Catalog.xlsx      <- the master spreadsheet (created on first run)
Book Covers\                    <- every cover image, in one folder
old versions\                   <- timestamped archives of the master
```

**`Example Catalog.xlsx`** shows what a filled-in catalog looks like: ten real
books with genuine Library of Congress data, spanning eleven subject categories.
Open it in Excel to see the layout, or point the app at it from the Settings tab
to try the search and edit tabs against real entries. Nothing in the app reads it
otherwise — your own catalog starts empty.

**From the source files**

Needs Python 3.9 or newer with Tkinter (the standard python.org installer
includes it). Then:

```
pip install -r requirements.txt
python "Basic-Home-Library.py"
```

or double-click `Run Basic-Home-Library.bat`.

**Rebuilding the .exe** after changing the code: run `build_exe.bat`, or

```
pip install pyinstaller
pyinstaller --noconfirm --clean "Basic-Home-Library.spec"
```

Build through the `.spec` file, not plain command-line flags. On Anaconda or
Miniconda, Tcl/Tk lives in `<env>\Library\bin`, which PyInstaller does not scan;
without the spec the build appears to succeed and the .exe then dies on start-up
with `ImportError: DLL load failed while importing _tkinter`.

---

## The four tabs

### 1. Look Up by ISBN / ISSN

Type or scan an identifier and press Enter. The app searches, in order:

1. **Library of Congress catalog** (`catalog.loc.gov`), through its public SRU
   service, which returns the full MARC record. This is where the Dewey number,
   the LC classification and the LCCN come from.
2. **Open Library** (`openlibrary.org`), which fills in whatever the Library of
   Congress left blank and usually supplies the cover image.
3. **Google Books**, consulted only if a gap still remains after the first two.

None of the three needs an account or an API key.

Each source is asked only when the ones before it left something missing, so a
book the Library of Congress knows well costs a single request.

**About the Google Books `429 - rate limited` message.** Google caps how often
one address may ask without an API key, and cataloguing several books in a row
trips that cap. Four things now keep the app under it:

- **Requests to the same site are spaced out.** Google Books is asked at most
  once every 2 seconds; the app waits automatically. The wait happens on a
  background thread, so the window stays responsive.
- **Google Books is asked last, and only if needed.** If the Library of Congress
  and Open Library between them answered everything, it is never called at all.
- **Rate limits are retried,** waiting 3s then 6s — or whatever delay Google
  asks for — before giving up.
- **Answers are remembered** for as long as the app is open, so re-searching the
  same ISBN costs nothing.

- **An optional API key lifts the ceiling.** No key ships with the app, and none
  is needed. If you do add one, Google Books is asked every 0.25s rather than
  every 2s and the rate limiting stops happening.

To add a key, create a free one in the [Google Cloud
console](https://console.cloud.google.com/), enable the **Books API** for it,
and paste it into the **Settings** tab. It is saved to your own settings file
and never leaves your machine; clearing the field turns it off again. If a 429
ever does appear it is not a failure — whatever the other two sources found is
already in the form and safe to save.

If the Library of Congress has no record for that exact ISBN (common for later
reprints) but the web search produced a title and author, the app asks the
Library of Congress a second time *by title and author* to recover the Dewey and
LC numbers. Because that match may be a different edition, it says so in the
search log — worth a glance before you confirm.

If a cover image is found it is downloaded automatically and shown straight
away. Nothing is written to `Book Covers\` until you actually add the book, so
abandoning a search never leaves a stray file behind.

Every field is editable before you commit. **Clear all fields** empties the
form; **Confirm and add to catalog** appends the row.

### 2. Manual Entry

The same form with no lookup, for books with no ISBN or that nothing online
knows about.

### 3. Find and Edit

Search the master file by any field, pick from the list of matches, see the full
record and its cover, edit it, and save back to the same row.

**Deleting an entry.** Select it and press **Delete entry...**. The confirmation
window names the book and its row; only then is the row removed. The row is
deleted outright — the rows below move up, so the spreadsheet is left with no
blank line where the entry was. The catalog is archived to `old versions\` first
(once per session, as with any other change), so a deletion made by mistake can
be recovered from there. Deleting does not remove the book's cover image unless
you tick the box offered in the confirmation window.

Search syntax:

| You type  | It means                          |
|-----------|-----------------------------------|
| `test`    | contains "test" anywhere          |
| `test*`   | **starts with** "test"            |
| `*test*`  | contains "test"                   |
| `*test`   | ends with "test"                  |
| `te?t`    | `?` matches any single character  |

Searches ignore case. Tick **Full regular expression** to use regex syntax
exactly as typed instead.

---

## Duplicate checking

The app checks twice, and treats the two situations differently.

**Before searching.** If the ISBN or ISSN you typed is already in the catalog,
it says so and lists the matching entries before spending a search on it. You
can carry on anyway — for a second copy, or a different edition — or stop there.

**Before adding.** The entry is compared against the whole catalog on internal
library number, ISBN 10, ISBN 13, ISSN, LCCN, LCC and DDS, and on title and
author together. Anything that matches is listed with the reason it matched, and
you choose whether to add the book anyway. Most of these are not real
duplicates — two books on the same subject share a Dewey number as a matter of
course — so this is a prompt, not a refusal.

**The internal library number is different.** It identifies one book on your
shelf, so it has to be unique. If the number is already taken the entry is
refused outright, the entry that owns it is named, and a free number is offered
(`PN1994` taken → `PN1994-2`). Since the internal number starts life as a copy
of the LCC, and books do share an LC class, expect to see this — one click on
the suggested number resolves it.

Two details worth knowing:

- Placeholders never count as matches. `zzz` is what the catalog writes when a
  classification is unknown, so treating it as a shared value would make every
  unclassified book look like a duplicate of every other. Blank values are
  ignored for the same reason, including by the internal-number check.
- Identifiers are compared ignoring punctuation, so `978-0-393-92324-7` and
  `9780393923247` are recognised as the same book. Titles are compared ignoring
  case and punctuation, and a title only counts as a match when an author or
  editor matches too.

Editing an existing entry never conflicts with itself — the row being edited is
excluded from both checks.

### 4. Settings

Everything the app lets you configure, in one place: the **Locations**,
**Origins** and **Subject categories** offered on the entry forms, where the
master file lives, the Google Books API key, and the network timeout.

Changes are held until you press **Update and refresh all tabs**, which applies
them and rebuilds the other three tabs so the new choices appear everywhere at
once. **Discard changes** throws away anything not yet applied.

**Order matters for subject categories.** The list is the order the checkboxes
appear in, read top-to-bottom down each column; **Up** and **Down** rearrange
them.

**Removing an option that is still in use is refused**, with a count of the
entries using it. This is not a precaution but a necessity: the app rebuilds
these lists from the master file every time it reads it, so a value still
present there would simply reappear. Change those entries on the Find and Edit
tab first, then remove the option.

Removing an unused subject category also deletes its (empty) column from the
spreadsheet — otherwise the app would find the header again on the next read and
put the category straight back.

---

## The spreadsheet

The master is created for you on first run, in the layout this app was built
around — the same sheet name
(`Books`), the same 54 columns in the same order, and the same conventions:

- names stored as `Last, First`
- subject categories flagged with `yes`
- LC classification reduced to class and number, zero-padded to four digits
  (`AG0005`, `SF0433`, `PN1994`) with any Cutter number dropped
- Dewey numbers abridged at the MARC prime mark (`791.43/75` is stored `791.43`)
- `zzz` where a classification is genuinely unknown
- the LCC cell hyperlinked to its `lccn.loc.gov` record

Five columns are added beyond the example, at the end of the row except where
noted:

| Column                      | What it holds                                             |
|-----------------------------|-----------------------------------------------------------|
| `Internal Library Number`   | Your shelving number, auto-filled from the LCC. Column A. |
| `LCCN`                      | The LC catalog record's control number.                   |
| `ISSN`                      | For periodicals.                                          |
| `Publisher`                 |                                                           |
| `Cover Image`               | File name, hyperlinked to the image in `Book Covers\`.    |
| `Date Added`                | The date you filed the book.                              |

### LCC, LCCN and the internal library number

These are three different things and each gets its own column:

| Column                    | Example      | What it is                                        |
|---------------------------|--------------|---------------------------------------------------|
| `LCC`                     | `PN1994`     | The **classification** — where the book shelves.  |
| `LCCN`                    | `2013016108` | The **control number** — the id of LC's record.   |
| `Internal Library Number` | `PN1994`     | Your own number, seeded from the LCC.             |

The internal library number starts as a copy of the LCC and keeps tracking it
while you edit that field — but the moment you type your own number, it stops
following and is never overwritten. That leaves room for a per-copy suffix
(`PN1994.2`) or a scheme of your own without the app fighting you.

Both `LCC` and `LCCN` are hyperlinked to the book's record at `lccn.loc.gov`,
the way a hand-kept catalog usually does. The internal library number stays
plain text, since it is yours rather than the Library of Congress's.

**Multiple people.** Authors, translators, editors/compilers and illustrators
each accept as many names as you need — press **+** to add a row. The example
file ships with four author slots and one illustrator slot; if a book needs more,
the app adds `Author 5`, `Illustrator 2` and so on to the spreadsheet
automatically.

**Multiple subjects.** Tick every category that applies — a book can be both
`Nonfiction` and `Science`. **Add category...** creates a new one, which becomes
a new column.

**Locations and origins.** Both are dropdowns with an **Add...** button beside
them, and both have fuller editors on the Settings tab. Locations start with
Library, Office and Living Room; origins start empty. Whatever is already used in
the master file is picked up automatically the next time the app reads it, so an
existing catalog fills both lists on its own.

New columns are always appended at the right-hand end rather than inserted, so
existing data, formatting and hyperlinks are never shifted.

---

## Version control

The first time you add or edit an entry in a session, the master file is copied
to `old versions\` with the date and time appended:

```
old versions\Home Library Catalog 2026-08-16 21-08-38.xlsx
```

That happens once per session, not once per book, so a long cataloguing sitting
produces one archive of the state before you started.

---

## Notes

- Closing the app while an entry is half-finished prompts you first, offering to
  stay open so you can finish it.
- Keep the master file **closed in Excel** while adding books. If it is open,
  Windows locks it and the app will say so rather than losing the entry.
- Any column you add to the spreadsheet by hand is left alone. The app writes
  only the columns it manages, so your own extra columns survive edits.
- Settings live in `%APPDATA%\BasicHomeLibrary\config.json` (dropdown options,
  Google Books key, network timeout). Copying the .exe to another machine needs
  nothing else — the dropdown options rebuild themselves from whatever is
  already in the master file.
- A Google Books API key you add is stored as plain text in that settings file.
  It is worth restricting the key to the Books API in the Google Cloud console.
- The master's location follows the .exe unless you pick one explicitly with
  *File → Choose master catalog file*, so you can move the whole folder freely.
- Nothing online is a perfect source. The Library of Congress record may
  describe a different printing than the copy in your hand, so the year,
  publisher and classification are always worth a look before you confirm.

---

## Where the data comes from

The app queries three public catalogues and sends them nothing but the
identifier you type:

- [Library of Congress](https://catalog.loc.gov/) via its public SRU service
- [Open Library](https://openlibrary.org/), run by the Internet Archive
- [Google Books](https://books.google.com/)

Your catalogue, cover images and settings stay on your own machine. Nothing is
uploaded anywhere, and the app has no telemetry.

Cover images are fetched from whichever source supplied them and remain the
property of their publishers; they are stored locally for your own records.

---

## License

Released under the MIT License — see [LICENSE](LICENSE). In short: use it, change
it, share it, build on it, commercially or not; just keep the copyright notice,
and understand it comes with no warranty.
