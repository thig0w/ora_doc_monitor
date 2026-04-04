# ora_doc_monitor

A CLI tool for monitoring Oracle Retail documentation updates. It downloads PDFs from MOS (My Oracle Support) and public Oracle documentation pages, then generates a diff report highlighting what changed between runs.

## Setup

**Requirements:** Python >= 3.11, [UV](https://github.com/astral-sh/uv), Firefox, and [`geckodriver`](https://github.com/mozilla/geckodriver/releases) on your `PATH`.

```bash
uv sync
```

Create a `.env` file in the project root — `python-dotenv` loads it automatically:

```env
MOSUSER=<your Oracle Support username>
MOSPASS=<your Oracle Support password>
MOSMFAKEY=<Base32-encoded TOTP secret key>
LOG_LVL=ERROR   # optional: DEBUG | INFO | WARNING | ERROR
```

`MOSMFAKEY` is the Base32 secret from your MOS two-factor authentication setup (the key used to generate TOTP codes).

## Usage

```bash
# Download all sources and run diff
python -m source.cli

# Download MOS (authenticated) docs only
python -m source.cli --auth_docs

# Download public docs only
python -m source.cli --no_auth_docs

# Show the Firefox browser window instead of running headless
python -m source.cli --headed

# Download only, skip the diff step
python -m source.cli --download
```

## How It Works

1. **Download** — Fetches PDFs in parallel from two source types into temporary `_work` folders:
   - **MOS (authenticated):** Uses Selenium to log into Oracle Support with 2FA, then downloads PDFs attached to knowledge articles into `func_docs_work/`.
   - **Public docs:** Scrapes PDF links from public `docs.oracle.com` pages and downloads them via HTTP into `<name>_work/`.
2. **Diff (decoupled)** — Each source type runs its own diff immediately after its own download completes, without waiting for the other. Public doc diffs finish well before the slower MOS download completes.
3. **Checksum** — Generates `000_checksumfile.md` inside each work folder (MD5 hash per file, `000_checksumfile.md` itself excluded).
4. **Diff** — Compares hash sets between the work folder and the base folder's stored `000_checksumfile.md`. Only genuine content changes are reported — renamed-but-unchanged files are ignored. Changed documents are copied to a timestamped folder (`df_YYYYMMDDHHMM/`), with a Rich-formatted summary table.
5. **Sync** — Targeted update of the base folder: removes files whose hashes are gone, moves in new files, and updates `000_checksumfile.md`. Unchanged files are untouched. The work folder is then removed.

Sources are configured in `source/doc_sources.json`.

## Output

| Path | Description |
|---|---|
| `func_docs/` | Persistent MOS PDF baseline — updated after each run |
| `<name>_docs/` | Persistent public PDF baseline per source — updated after each run |
| `func_docs/000_checksumfile.md` | MD5 checksums of the current MOS baseline (used for next-run comparison) |
| `<name>_docs/000_checksumfile.md` | MD5 checksums of the current public baseline per source |
| `func_docs_work/` | Temporary download target for MOS docs — deleted after sync |
| `<name>_work/` | Temporary download target per public source — deleted after sync |
| `df_YYYYMMDDHHMM/` | Diff output: `*_new.*` and `*_old.*` pairs for changed files (only created when differences are detected) |

> **Note:** On the first run, base folders are empty, so all downloaded files will appear as new (`LEFT`) in the diff output.

## Development

```bash
# Install dev dependencies
uv sync --dev

# Lint and format
ruff check source/ --fix
ruff format source/

# Set up pre-commit hooks
pre-commit install
```
