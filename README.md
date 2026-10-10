# Disk Sweep

A terminal app for reviewing and removing cleanup candidates. Keyboard and mouse
controls, individual file selection, full-path search, category filters, and a
review step before every removal. Nothing starts selected.

## Start on Windows

Run from source with the Windows helper (Python 3.11+ required):

```powershell
.\start.ps1
```

Build a standalone executable with `./build.ps1`, then launch
`./dist/disk-sweep.exe`. Generated executables are not included in Git.

Try disposable sample files first (about one minute):

```powershell
.\start.ps1 -Demo
```

Choose **Scan**, move with the arrow keys, and press **Space** to select a file.
Clicking a row or pressing Enter also toggles it. Selected files display **[✓]**;
unselected files display **[ ]**. Choose **Review selected** to
inspect the complete selection and choose a removal method.

- **Recycle Bin / Trash:** recoverable through your operating system. Empty it
  yourself when you are ready to free disk space.
- **Permanently delete:** requires typing `DELETE` and clicking the final button.
  This cannot be undone.

Use a terminal at least **80 columns × 24 rows**. A larger window shows more files.
Windows Terminal is a convenient host; PowerShell and Command Prompt also work.

## Choose what to scan

The initial folders are your user temporary folder and Downloads, if present.
The status line lists all initial roots. Changing the folder field replaces these
with that one folder.
The **Temp + Downloads** preset restores the initial locations.

By default, only files modified at least **7 days ago** appear. Set age to **0**
to include recent files. All limits apply in both scan modes.

| Mode | Files shown |
| --- | --- |
| Cleanup candidates | Temporary files, caches, logs/backups, and large files |
| Browse all files | Every regular file that matches your age and size limits |

Large files are **100 MiB or larger** by default. Each row includes size, age,
category, and path. The detail pane explains why it appeared. Press **F2** to read the full path
and metadata, including from the review screen.

**A candidate is a suggestion. Modification age, filename, and size do not prove
a file is unused.** Review paths, close the apps that own caches or temporary
files, and keep anything you need.

Search matches the **full path**. Selections remain selected when you search,
change categories, sort, or change pages. Totals show how many selections are off
the current page. The review screen always includes **all selected files**.

Results are paged in groups of 500 to keep the interface responsive.
**Select page** selects only the currently displayed rows. **Clear all** clears
the entire selection.

## Controls

| Key | Action |
| --- | --- |
| Arrow keys / Page Up / Page Down | Navigate file list |
| Space / Enter / click a row | Toggle one file |
| Tab / Shift+Tab | Move between controls |
| `/` | Focus search |
| `S` / `D` | Scan / review selected |
| Ctrl+A / Ctrl+L | Select current page / clear all selections |
| Escape | Return to file list, cancel a dialog, or stop active work |
| F1 / F2 | Help / full details for the highlighted file |
| Ctrl+Q | Quit; active work is stopped first |

Letter shortcuts apply when you are outside text inputs. Stop keeps partial scan
results or cancels remaining removals; completed deletions cannot be undone.

## CLI examples

```powershell
# Open the TUI for a specific folder
.\dist\disk-sweep.exe "D:\Downloads"

# Browse all files, including recent files
.\dist\disk-sweep.exe tui "D:\Downloads" --mode all --age-days 0

# Read-only scan, including multiple folders
.\dist\disk-sweep.exe scan "D:\Downloads" "D:\Projects" --min-size-mb 10

# Machine-readable report; this command never deletes anything
.\dist\disk-sweep.exe scan "D:\Downloads" --json > report.json

# Add exclusions or change the large-file threshold
.\dist\disk-sweep.exe scan "D:\Projects" --exclude "D:\Projects\keep" --large-mb 250
```

`--limit` defaults to 50,000 candidates. A stopped or limited scan is clearly
marked as partial. Unreadable locations are skipped and reported. The JSON report
includes errors, skipped counts, and the truncation flag. An invalid root exits
with code 1; invalid options exit with code 2.

## What stays protected

Both scan modes skip system folders, links, junctions, Windows reparse points,
credential folders, `.git`, `.hg`, `.svn`, `.venv`, `venv`, `node_modules`,
and OS Trash folders. Custom exclusions can hide additional files or folders.

The app removes **regular files only** and never recursively deletes folders.
Before removing each file it rechecks the scan boundary, parent links, size,
timestamps, and file identity. Changed, missing, locked, and inaccessible files
are skipped and shown in a removal report. Trash failures never fall back to
permanent deletion. Windows builds require the modern recycling API through
pywin32 and refuse the older Send2Trash fallback.

These checks reduce filesystem races; they cannot lock every pathname atomically
against a concurrent process on every platform. Do not clean a folder while
another process is modifying it.

Displayed sizes are **logical file sizes**, not a guaranteed increase in free
space. Sparse files, compression, hard links, and open handles can change the
amount actually reclaimed.

## Install from source (Windows, macOS, Linux)

Python 3.11+ is required. The standalone executable above is built for Windows 10/11 x64.

```sh
python -m venv .venv
# Windows:
.venv\Scripts\python -m pip install -e .
.venv\Scripts\python -m disk_sweep
# macOS / Linux:
.venv/bin/python -m pip install -e .
.venv/bin/python -m disk_sweep
```

Alternatively, `pipx install .` makes `disk-sweep` available on your PATH.
The Windows `start.ps1` helper creates a local environment and installs the app
when needed. Use `./start.ps1 -Demo` for sample data.

The stack is [Python](https://www.python.org/), [Textual](https://textual.textualize.io/)
for the TUI, and [Send2Trash](https://pypi.org/project/Send2Trash/) for native OS
recycling. Scans and removals run in background workers; no file contents are
uploaded or sent to a service. [PyInstaller](https://pyinstaller.org/en/stable/)
builds the Windows executable.

## Develop and verify

```powershell
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\ruff check src tests
.\.venv\Scripts\ruff format --check src tests
```

Tests use disposable fixtures, including real permanent deletion of selected
fixture files. Trash behavior is tested through a substituted adapter; OS Trash
integration requires a manual check on the target machine. Windows is the
verified platform for this build. macOS/Linux paths are implemented but have not
been tested here.

Build a standalone Windows executable:

```powershell
.\build.ps1
```

PyInstaller builds for the platform on which you run it. The generated
`dist/disk-sweep.exe` contains the Python runtime and app dependencies.
