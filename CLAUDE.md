# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`ora_doc_monitor` is a Python CLI tool that monitors and downloads Oracle documentation from two sources:
- **MOS (My Oracle Support):** Requires authenticated Selenium-based browser automation with 2FA
- **Public Oracle docs:** No authentication, uses `requests` + BeautifulSoup to scrape and download PDFs

After downloading, it diffs old vs. new versions and copies changed files to a timestamped output folder.

## Package Manager

This project uses **UV**. Use `uv` for all dependency and environment operations:

```bash
uv sync          # Install dependencies
uv sync --dev    # Install including dev tools (ruff, pre-commit)
uv build         # Build the package
```

## Running the Application

```bash
python -m source.cli                  # Download all docs, then run diff
python -m source.cli --auth_docs      # MOS authenticated docs only
python -m source.cli --no_auth_docs   # Public docs only
python -m source.cli --headed         # Show browser window (default: headless)
python -m source.cli --download       # Skip diff comparison after download
```

## Linting & Formatting

```bash
ruff check source/          # Lint
ruff check source/ --fix    # Lint with auto-fix
ruff format source/         # Format
```

Ruff config is in `pyproject.toml`: target Python 3.12, line length 88, double quotes, rules: E, F, UP, B, SIM, I.

## Required Environment Variables

```
MOSUSER=<Oracle Support username>      # or op://vault/item/field
MOSPASS=<Oracle Support password>      # or op://vault/item/field
MOSMFAKEY=<Base32-encoded TOTP key>   # or op://vault/item/field
LOG_LVL=<DEBUG|INFO|WARNING|ERROR>    # optional, default: ERROR
```

Values starting with `op://` are resolved at runtime via the `op` CLI (1Password). Requires the 1Password CLI to be installed and authenticated.

## Architecture

All source code lives in `source/`:

- **`cli.py`** — Click-based entry point. Spawns two threads (auth docs + public docs) in parallel. Each thread runs its own download then immediately triggers its own diff — auth and noauth diffs are fully decoupled and run as soon as their respective downloads finish.
- **`doc_extractor.py`** — Selenium/Firefox automation: logs into MOS with TOTP 2FA, downloads PDFs. Includes retry logic (`execute_with_retry`) and a `watchdog()` to force-kill frozen Firefox processes. `load_page_and_collect_links()` returns `list[dict]` (keys: `href`, `data_href`, `text`) — attributes are extracted atomically via a single JS call to avoid `StaleElementReferenceException` from Oracle JET re-renders. `execute_with_retry` retries on both `NoValidLinksFound` and `StaleElementReferenceException`.
- **`url_extractor.py`** — Downloads PDFs from public Oracle documentation pages by scraping HTML links with BeautifulSoup.
- **`diff_docs.py`** — Compares work vs. base folders by MD5 hash (not filename), copies diffs to `df_YYYYMMDDHHMM/`, and renders a Rich table (LEFT = new, RIGHT = removed). Renamed-but-unchanged files are ignored. Syncs base folder by removing/moving only changed files, then stores `000_checksumfile.md` in the base folder for the next run. Exposes `diff_auth_folders()` and `diff_noauth_folders()` as separate entry points.
- **`interface.py`** — Shared `logger` (loguru + Rich handler), `progressbar` (Rich Progress), and `console` (Rich Console) used across all modules.
- **`doc_sources.json`** — Configuration listing all documentation sources: `auth_req` (MOS doc IDs requiring login) and `noauth_req` (public URLs with folder names). "Xstore Supplemental Documentation Library" is in `noauth_req`.

### Download Output Structure

- `func_docs/` — MOS authenticated docs baseline (persistent); includes `000_checksumfile.md`
- `<name>_docs/` — Public doc baseline per source (persistent); includes `000_checksumfile.md`
- `func_docs_work/` / `<name>_work/` — Temporary download targets; deleted after sync
- `df_YYYYMMDDHHMM/` — Diff output: `*_new.*` and `*_old.*` pairs (only created when differences are detected)

### Key Design Patterns

- **Oracle JET detection:** `doc_extractor.py` waits for Oracle's JavaScript framework to finish rendering before interacting with pages. Downloadable file links are identified by CSS selector `a[data-oce-meta-data], a[data-ucm-meta-data]` — two attribute types are used by MOS depending on the asset backend (OCM vs UCM).
- **Stale element avoidance:** After finding link elements, all attributes (`href`, `data-href`, text) are extracted in a single `execute_script` call before Oracle JET can re-render the DOM. The download loop works with plain dicts. For `javascript:` links (Oracle JET session-authenticated downloads), the element is re-found fresh by index immediately before each `.click()` — `window.open(data_href)` cannot be used here because the download token is only issued through the JET click handler.
- **Firefox PDF auto-save:** WebDriver preferences configure Firefox to auto-download PDFs without dialogs.
- **No test suite** currently exists in this project.
