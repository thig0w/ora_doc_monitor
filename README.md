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

> **Note:** The diff step requires a previous run to exist. On the first run, no `*_old/` folders are present, so the diff output will be empty.

## How It Works

1. **Download** — Fetches PDFs in parallel from two source types:
   - **MOS (authenticated):** Uses Selenium to log into Oracle Support with 2FA, then downloads PDFs attached to knowledge articles.
   - **Public docs:** Scrapes PDF links from public `docs.oracle.com` pages and downloads them via HTTP.
2. **Diff** — Compares the newly downloaded files against the previous run's files and copies changed documents to a timestamped folder (`df_YYYYMMDDHHMM/`), with a Rich-formatted summary table.

Sources are configured in `source/doc_sources.json`.

## Output

| Path | Description |
|---|---|
| `func_docs/` | Downloaded MOS PDFs (current run) |
| `func_docs_old/` | MOS PDFs from previous run (used for diff) |
| `<name>_docs/` | Downloaded public PDFs per source |
| `df_YYYYMMDDHHMM/` | Diff output: `*_new.*` and `*_old.*` pairs for changed files |

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
