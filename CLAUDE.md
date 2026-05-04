# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`ora_doc_monitor` is a Python CLI tool that monitors and downloads Oracle documentation from two sources:
- **MOS (My Oracle Support):** Requires authenticated Playwright-driven Firefox automation with 2FA
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
python -m source.cli --workers 4      # Parallel auth-doc workers (default: 2)
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

- **`cli.py`** — Click-based entry point. Spawns two threads (auth docs + public docs) in parallel. Each thread runs its own download then immediately triggers its own diff — auth and noauth diffs are fully decoupled and run as soon as their respective downloads finish. A `threading.Event` (`login_done`) gates the noauth thread until MOS login finishes, so the two flows do not fight for browser CPU during the SSO handshake.
- **`auth_extractor.py`** — Playwright/Firefox automation: logs into MOS with TOTP 2FA, then downloads PDFs in parallel workers. N worker threads each launch their own Firefox; worker-0 runs the MOS SSO + TOTP flow inside its own browser and publishes the resulting `storage_state` (cookies + localStorage) on a shared dict, then keeps that same browser open to drain the queue — no dedicated bootstrap browser. Workers 1..N-1 launch their browser in parallel with the login, block on a `threading.Event` until `storage_state` is available, hydrate a context with it, and join the queue. All workers pull the next source off a shared `queue.Queue` until it is empty, so slow docs do not stall the others. `_collect_links()` returns `list[dict]` (keys: `href`, `data_href`, `text`) via a single `page.evaluate` call to avoid races against Oracle JET re-renders. `execute_with_retry` retries on `NoValidLinksFound`.
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

- **Oracle JET detection:** Before reading anchors, `_wait_for_jet_ready` blocks on two JET-native readiness signals — `oj.Context.getPageContext().getBusyContext().isReady()` (JET's own busy context) and `oj-vb-content.oj-complete` (Virtual Builder subtree mounted). Both fire strictly after `networkidle`, so this is what prevents catching the DOM mid-render. Only then does `_collect_links` run a `page.wait_for_function` for anchor `href` population. Downloadable file links are identified by CSS selector `a[data-oce-meta-data], a[data-ucm-meta-data]` — two attribute types are used by MOS depending on the asset backend (OCM vs UCM).
- **Stale element avoidance:** After finding link elements, all attributes (`href`, `data-href`, text) are extracted in a single `page.evaluate` call before Oracle JET can re-render the DOM. The download loop works with plain dicts. For `javascript:` hrefs (Oracle JET session-authenticated downloads), the anchor is re-queried fresh by index immediately before `.click()` inside a `page.expect_download` block — `context.request.get(data_href)` cannot be used here because the download token is only issued through the JET click handler. Plain URLs use `context.request.get()` so browser session cookies carry over without opening a new tab.
- **Firefox PDF auto-save:** Playwright's `firefox_user_prefs={"pdfjs.disabled": True}` forces PDFs to download instead of rendering inline.
- **Playwright thread-binding:** `sync_playwright()` handles are bound to the thread that created them, so every browser (each worker) is launched inside its own thread.
- **Shared session across workers:** Worker-0 runs the SSO flow in its own browser and writes the resulting `storage_state` (cookies + localStorage) to a shared dict. Once `login_done_internal` fires, workers 1..N-1 rehydrate a fresh context from that dict — avoiding the cost and risk of N parallel SSO flows, and avoiding a dedicated bootstrap browser that would close and be re-launched. Worker-0's own browser is reused straight into its download loop. Before returning, `_login` blocks until the account menu button (`#mc-id-sptemplate-account-menu-btn`) on the final MOS landing page (`https://support.oracle.com/support/`) is visible — the post-SSO redirect cascade is long and `networkidle` fires on any brief lull mid-cascade, so without this stronger signal the captured `storage_state` sometimes misses cookies that MOS plants late in the cascade and hydrating workers get bounced to `/signin/`. After the wait, an eager URL check (`/signin` or `login.oracle.com` still in the URL) returns `False` rather than exporting a half-authenticated snapshot.
- **Dynamic work dispatch:** Auth workers pull from a shared `queue.Queue` rather than a pre-partitioned slice, so a slow or failing doc on one worker does not leave the others idle.
- **Batched 1Password resolution:** `_resolve_secrets` takes a dict of env vars and resolves every `op://` ref in a single `op inject` subprocess call. The template is `.env`-style (`KEY={{ op://... }}` per line) and parsing splits on the first `=` so values containing `=` stay intact. Values must be single-line (fine for SSO creds and Base32 TOTP keys). Non-zero exits retry with linear backoff, stderr logged each time.
- **Test suite:** `pytest` under `tests/` — config in `pyproject.toml` (`testpaths = ["tests"]`, `pythonpath = ["source"]`). Run with `uv run pytest -q`.
