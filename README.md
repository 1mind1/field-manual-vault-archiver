# Field Manual Vault Archiver

[![Offline unit tests](https://github.com/1mind1/field-manual-vault-archiver/actions/workflows/tests.yml/badge.svg)](https://github.com/1mind1/field-manual-vault-archiver/actions/workflows/tests.yml)

**V5.0.2 release candidate (RC3)** — an unofficial, dependency-free Python tool for Windows, Linux and macOS for selectively archiving public [T.REX ARMS Field Manual Vault](https://www.trex-arms.com/field-manual-vault) publications. Download PDF, EPUB, or both; browse organized subject folders; search your local library; and update the catalog without redownloading everything.

**Not affiliated with or endorsed by T.REX ARMS.** The repository contains **no manuals or copies of the site's catalog**. It only requests public links when you run it. It does not bypass access controls.

> The V5 workflow has been trialed on Windows by a user. RC3 adds offline repair for five legacy metadata anomalies observed in the user-provided cache. The Linux launcher has passed offline checks, but macOS still requires CI and real-device verification before stable support is claimed.

## Quick start (Windows)

1. Download the repository ZIP from GitHub (or clone it), and extract it to its own folder. Install [Python 3.10+](https://www.python.org/downloads/) if needed, including **Add Python to PATH**. No `pip install` is required.
2. Double-click `START_WINDOWS.bat`.
3. Enter your existing **Field Manual Vault** folder, including the root that contains your previous `Organized Library`. The tool does **not** need to live inside that folder.
4. Choose **3 — Selective Bulk Download** to download a few topics, or **7 — Incremental Catalog Update** to check selected categories for new manuals *without downloading any PDFs or EPUBs*.
5. For online operations, choose a discovery pacing profile. The defaults are polite to the publisher: two metadata workers, with a **global 0.55-second minimum** between website HTML request starts. These aren't download concurrency settings.

### Windows launcher menu

| Number | Action | Internet? | Downloads manuals? |
|---|---|---|---|
| **1** | SHA-256 audit of existing PDFs/EPUBs and exact-duplicate report | No | No |
| **2** | Best-effort whole-site catalog scan | Yes | No |
| **3** | **Selective download**: choose site categories, title filter, formats, count, per-run GB cap | Yes | Yes, after confirmation |
| **4** | Legacy popular-search topic downloader | Yes | Yes; no default GB cap |
| **5** | Attempt whole-site organized download | Yes | Yes; potentially over 100 GB |
| **6** | Rebuild searchable catalog, repair legacy badges, and refresh the offline coverage report | **No** | No |
| **7** | **Incrementally update selected categories** and produce coverage report | Yes | **No** |

**Recommended workflow:** Run **7** to check for new entries, then **3** with the same categories, a small GB cap (such as 2 GB), and both formats. Existing valid PDFs and EPUBs are skipped, and interrupted `.part` downloads are resumable when the server supports Range requests.

## Quick start (Linux)

Requires **Bash** and **Python 3.10+**. No external Python packages, Docker, or administrator rights are needed.

1. Download and extract this repository (or clone it).
2. Open a terminal in its directory and run:

   ```bash
   bash START_LINUX.sh
   ```

   Optionally, run `chmod +x START_LINUX.sh` once and then `./START_LINUX.sh`.
3. Choose your archive folder (default: `~/Documents/Field Manual Vault`).
4. Use option **7** for metadata-only updates or option **3** for selective PDF/EPUB downloads and a per-run storage cap.

The Linux menu offers all seven Windows options, including the same polite discovery pacing presets. To use the advanced CLI directly, run `bash START_LINUX.sh --help` or `python3 trex_vault_archiver.py --help`.

## Quick start (macOS)

Requires **macOS Terminal** and **Python 3.10+**. Check `python3 --version`; if it is too old or unavailable, install Python from [python.org](https://www.python.org/downloads/macos/).

1. Download and extract this repository.
2. In Terminal, open the extracted directory and run:

   ```bash
   bash START_MACOS.command
   ```

   Alternatively, run `chmod +x START_MACOS.command` once and double-click it in Finder, subject to macOS security settings. The Finder launcher keeps Terminal open to show the result.
3. Choose your archive folder and use option **7** for metadata discovery or **3** for selective downloads.

The default is `~/Documents/Field Manual Vault`. Depending on macOS privacy settings, Terminal may request access to Documents. The launcher shares the same Python archiver, cache, categorization, resume, and duplicate-audit logic as Windows and Linux.

**Compatibility status:** Linux's offline launcher tests have passed; GitHub Actions adds macOS offline tests. A real Mac user still needs to verify Finder startup and a small live download before macOS support is considered stable. Linux live downloads have not been independently verified.

Start safely with **option 6** (offline index) or **option 1** (offline file audit). Then try **option 7** (metadata only) and **option 3** with a small selection and data cap.
## How your files are organized

```text
Field Manual Vault/                           <- Existing root folder
├── First Aid/                                <- Your own folders: not moved/deleted
├── Organized Library/
│   ├── Medical & First Aid/
│   │   ├── First Aid/
│   │   │   └── FM 4-25.11 - First Aid/
│   │   │       ├── FM 4-25.11 - First Aid (Dec 2002).pdf
│   │   │       └── FM 4-25.11 - First Aid (Dec 2002).epub
│   │   └── Casualty & Trauma Care/
│   ├── Navigation & Terrain/
│   └── Survival & Fieldcraft/
├── Field_Manual_Catalog.html                <- Local title/category/tag search
├── Field_Manual_Coverage_Report.txt          <- How much was actually discovered?
├── Field_Manual_Coverage_Report.json         <- Machine-readable report
├── Field_Manual_Storage_Estimate.json        <- Approximate space requirement
├── trex_cache.sqlite3                       <- Cross-selection discovery cache
├── trex_manifest_selective_*.csv            <- Inspectable per-selection metadata
├── Field_Manual_Existing_Files.csv          <- Local hash inventory
└── Field_Manual_Exact_Duplicates.csv         <- Byte-for-byte duplicates
```

Categories are derived from the Vault's **Function** labels plus conservative title-based subtopic rules. They are filing suggestions, not guaranteed descriptions of every chapter. Multiple catalog tags do not create multiple file copies. The exact-duplicate CSV reports identical contents; two Windows hard links can appear as duplicates even when they share disk storage. Different historical **editions** are preserved as separate publications; identical *bytes* can be detected with SHA-256.

## What's faster and safer in V5

**Incremental metadata discovery**

- The shared SQLite cache from V4 carries forward. The tool also recognizes and imports V2/V3/V4 JSON manifests, so existing local PDFs and EPUBs aren't redownloaded.
- For normal **selective** downloads, a **recent selection manifest loads immediately**, with no metadata lookup. After seven days, the next selective run rechecks the source's function listing and fetches metadata **only for previously unknown manual URLs**. Repeating a category combination does not refetch old detail pages unnecessarily.
- Menu **7** forces the selected Function listings to be checked immediately, regardless of their seven-day cache age. It fetches new metadata, and rechecks **up to 25** previously cached detail pages **at least 30 days old**, oldest-first. This staged rechecking can detect changed descriptions, editions, or URLs, but is **not a guarantee of immediate change detection for every manual**.
- For a complete old-manual metadata revalidation (much slower), the command-line `--refresh` flag is still available. The incremental mode is generally preferable.

**Reliable resumes and honest progress**

- Each finished listing page is committed to SQLite. If scanning a category is interrupted, it resumes at the next unvisited page, instead of starting from page one.
- Completed metadata entries are checkpointed. Only a bounded number of fetch tasks are queued; Ctrl+C doesn't leave thousands of metadata jobs queued in memory.
- Console progress shows listing categories/pages/URLs, cached versus new metadata, checked/changed/failed entries, download counts, and actual GB downloaded.
- A failed listing or metadata pass creates a `trex_incomplete_*.json` marker, so the next run **retries** even if a recent manifest exists. Known URLs are not silently dropped when a listing temporarily returns fewer results.

**Pre-download size estimate**

- V5 checks existing files and previously recorded lengths, and the Windows selective workflow samples up to **six HTTP HEAD headers** for otherwise unknown sizes. No PDF or EPUB body is transferred for the estimate. Two consecutive unsupported HEAD replies stop further probes.
- The screen shows files already saved, files still needed, remaining bytes with **known lengths**, a rough projected total when enough size samples exist, and available disk space. A JSON summary is saved.
- **Estimates are not exact.** A long scanned technical manual can be far larger than the median. Some servers don't report file lengths, in which case the tool explicitly reports an unknown total. The per-run GB cap limits the *actual newly written network bytes* regardless of the estimate; it does not cap the existing archive's total size.

**Catalog coverage report**

- `Field_Manual_Coverage_Report.txt` and `.json` identify URLs discovered and successfully parsed, known unparsed detail links, source listing failures, and cached totals by site Function.
- The site previously advertised **4,215 publications**. The report compares that historical figure with unique known URLs **only when a whole-site scan was attempted**. A count gap is **not** a verified list of missing manuals, because the public filter pages may not expose every edition.
- A scan of only Medical or Fieldcraft is explicitly labeled as partial and isn't misleadingly compared to the whole site's count.

## More details and privacy

- Existing PDFs, EPUBs, and your old `First Aid` folders are **not moved or deleted** by the catalog updater. Exact-duplicate audits only report duplicates. If a byte-identical copy already exists on the same filesystem, the downloader may use an NTFS hard link to save disk space. Two hard-linked paths refer to the *same underlying data*, so in-place edits affect both; keep backups before modifying downloaded files.
- A destination file that does not look like a valid PDF/EPUB is preserved under a `.invalid-1` suffix rather than destroyed when the downloader must write a replacement.
- HTTP **404/410** direct-file URLs are negatively cached for seven days; unexpected errors are logged to `trex_download_errors.csv`. Errors from catalog fetching are separately logged. The next run can resume incomplete transfers.
- The archive, catalog, download log, SHA-256 results, and cache are stored locally. No telemetry, login, tokens, scraping proxies, or authentication bypasses are provided. **Windows Desktop/Documents paths may be OneDrive-synced**: use an unsynced folder if that's not desired.
- File downloads remain **sequential**, while website metadata discovery uses modest worker concurrency. High concurrency does not mean ignoring the publisher's rate limits. Please comply with the site's published terms and access restrictions.

## Command line (Python 3.10+, Windows/Linux/macOS)

```sh
# Show V5 version and arguments
python trex_vault_archiver.py --version
python trex_vault_archiver.py --help

# Bulk-download Medical and Survival; first 40 titles; PDF and EPUB; 2 GB *new* data cap
python trex_vault_archiver.py --dest "D:\Field Manual Vault" --mode selective \
  --site-functions "Medical TCCC,Fieldcraft Survival" --limit 40 --format both \
  --max-new-gb 2 --estimate-samples 6

# Refresh these source categories, discover new links, and recheck 25 old URLs
# that haven't been checked for at least 30 days; NO binary downloads
python trex_vault_archiver.py --dest "D:\Field Manual Vault" --mode selective \
  --site-functions "Medical TCCC,Fieldcraft Survival" --update-only \
  --recheck-existing 25 --metadata-ttl-days 30

# Preview a subset with no document downloads or optional HEAD probes
python trex_vault_archiver.py --dest "D:\Field Manual Vault" --mode selective \
  --site-functions "Medical TCCC" --title-contains "first aid" --dry-run

# Conservative or slightly faster discovery — always globally rate-limited
python trex_vault_archiver.py --dest "D:\Field Manual Vault" --mode selective \
  --site-functions "Medical TCCC" --workers 3 --request-interval 0.5 --dry-run

# Rebuild existing index with NO network access
python trex_vault_archiver.py --dest "D:\Field Manual Vault" --index-only

# Exact duplicate audit, NO network access
python trex_vault_archiver.py --dest "D:\Field Manual Vault" --audit-only
```

The examples are shown with shell line continuation for readability. On **Windows CMD**, type each command on one line (or replace the `\` line continuations with `^`). The `START_WINDOWS.bat` launcher is the easiest option for ordinary use.

## Tests and GitHub publishing checklist

```sh
python -m unittest discover -p 'test_*.py' -v
```

The original 48 offline tests mock network responses, simulate interrupted listing pages, checks metadata changes and 404 handling, confirms old files are preserved, tests storage estimate failure modes, and verifies resume behavior. Four additional Linux/macOS launcher tests cover safe menu behavior, offline catalog rebuilding, CLI startup, and spaces in paths. GitHub Actions runs tests on Windows, Ubuntu, and macOS with Python 3.10 and 3.13.

Before tagging a stable **v1.0** GitHub release:

1. Test V5 on a Windows machine against the live site with one small category and a **low per-run data cap**; confirm HTML parsing, HEAD support, and download resume on real network failures. This development environment couldn't perform live transfers.
2. Validate `Field_Manual_Coverage_Report.txt` and the output catalog; verify that an incremental update the second time is faster and doesn't redownload existing PDFs/EPUBs.
3. Ensure the git repository contains **only application code, tests, and documentation**, not downloaded manuals, caches, manifests, file inventories, or other local archive files. `.gitignore` excludes these by default.
4. Review the repository name/branding, add the maintainer attribution, and optionally add a GitHub Actions status badge once the repository URL is known.

The program is distributed under the Apache License 2.0 used by this repository. See [LICENSE](LICENSE) and [CONTRIBUTING.md](CONTRIBUTING.md). All publishing and usage of third-party materials remains the responsibility of the person running it.

## V5.0.1 report-quality fixes

- Exclude the vault's `/field-manual-vault/citations` utility page from manual discovery, including imported prior listing caches.
- Ignore stray numeric metadata badges and repair older cached fields using their known Function listing when appropriate.
- Report malformed/unrecognized cached Function or Collection values explicitly instead of treating numbers as legitimate categories.
- Clear the previous `trex_metadata_errors.csv` after a successful metadata build so old warnings aren't mistaken for current failures.

The program never deletes any downloaded manual while applying these fixes.

## V5.0.2 RC3 — Offline legacy metadata repair

RC3 corrects the last five malformed metadata fields observed in the test library's SQLite cache:

- Numeric **Function** badges are replaced with the official category only when a saved Function listing associates the manual with exactly one category. If the listing evidence is absent or ambiguous, Function is left blank and remains flagged for review rather than being guessed.
- Numeric **Collection** and **Status** badges are cleared as unknown. They are never silently interpreted as legitimate publication labels. Proper textual values (e.g., `Historic`, `Active`, `Superseded`) are preserved.
- The correction happens offline when the archiver opens an existing vault folder, including during **Option 6**. It is idempotent and leaves the original last-check timestamps unchanged so that scheduled metadata rechecks still work.
- `Field_Manual_Metadata_Repairs.csv` records prior and new field values for each repaired row. A recent selection JSON and the HTML catalog also reuse the corrected cache, rather than reintroducing old bad values. **Option 6 regenerates the coverage report explicitly labeled an offline review** (0 scanned URLs).
- Source URLs, downloaded files, directory structure, and manual editions are not modified or deleted. Different editions and 404 responses are handled as before.

To apply the patch **without network requests**, extract RC3 to a separate application folder, open `START_WINDOWS.bat`, point it to your existing vault root, and select **6 — Rebuild searchable catalog**. Then inspect `Field_Manual_Coverage_Report.txt` and `Field_Manual_Metadata_Repairs.csv`. The repair log stays in your local archive and is excluded from git.
