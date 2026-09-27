# Shipped Dorkbook collection

Date: 2026-09-27
Status: 52 built-ins implemented; automated catalog validation passed.
Live aggregator evidence and upstream limits are recorded in
[LIVE_BACKEND_CHECKS.md](LIVE_BACKEND_CHECKS.md).

## Editorial contract

These dorks are examples to adapt: show a useful pattern, give one clue, and
suggest nearby ideas. HI selected best-effort discovery over carefully tuned
precision. The collection covers all 22 chosen content ideas and both approved
alternate-title ideas. It does not claim that a match proves the contents of a
directory, or that every upstream engine interprets operators identically.

The original SMB, FTP, and HTTP keys and queries remain unchanged. The full pack
has 27 Shodan dorks (one SMB, one FTP, 25 HTTP) and 25 Self-hosted Search dorks.
There are seven topics: General, Books, Video, Music, Images & design,
Software & games, and 3D & CAD. Installing a dork does not apply it as a default.

## Query forms and evidence

Shodan content examples use a title filter and one HTML clue:

```text
http.title:"Index of /" http.html:".epub"
```

Shodan documents quoted filter values and combined filters. Its advanced-search
reference identifies `http.title` as the page-title filter and `http.html` as
matching front-page HTML. These dorks describe the captured page; they do not
search a recursive file inventory. [Query fundamentals](https://help.shodan.io/the-basics/search-query-fundamentals),
[advanced-search reference](https://www.shodan.io/search/advanced).

Self-hosted Search examples use a title operator and a quoted clue:

```text
intitle:"Index of /" ".epub"
```

SearXNG forwards the query to its upstream search services, whose operator
support can differ. Its own `!` syntax selects an engine or category; the shared
pack intentionally avoids those engine-specific controls. [Search API](https://docs.searxng.org/dev/search_api.html),
[SearXNG search syntax](https://docs.searxng.org/user/search-syntax.html).

Brave and DuckDuckGo both document `intitle` and quoted terms. Brave describes
operators as experimental; DuckDuckGo may return related results when exact
matches are scarce and acknowledges imperfect advanced-syntax behavior. Thus
these combinations are reasonable starting points, not guarantees of strict
filtering. [Brave operators](https://search.brave.com/help/operators),
[DuckDuckGo syntax](https://duckduckgo.com/duckduckgo-help-pages/results/syntax).

DeGoog exposes a query parameter and merges results from its configured engines.
This makes the same readable web-search forms appropriate candidates for both
aggregators; actual operator handling remains an upstream property. This is an
inference from the documented interface, not a claim of universal support.
[DeGoog API](https://degoog-org.github.io/docs/api.html).

Use a filename clue such as `".pdf"` instead of `filetype:pdf`: the intended
result is a directory page mentioning PDFs, not the PDF file itself. Punctuation
may be normalized by an engine, so a filename suffix is a clue rather than a
strict suffix test. The same applies to the slash in the broad title default.

## Selected ideas and representative clues

Each row supplies separate Shodan HTTP and Self-hosted Search dorks. Notes offer
the listed variations without embedding long OR expressions in the query.

| ID | Starting point | Clue | Suggested variations |
|---|---|---|---|
| 1 | Ebook folders | `ebooks` | books, author |
| 2 | EPUB books | `.epub` | author, subject |
| 3 | Kindle formats | `.mobi` | .azw3 |
| 4 | PDF collections | `.pdf` | manuals, magazines, subject |
| 5 | Comics and manga | `.cbz` | .cbr, comics |
| 6 | Audiobooks | `.m4b` | audiobooks |
| 7 | Movies | `movies` | films, genre, year |
| 8 | TV shows | `series` | season, tv, show name |
| 9 | Video by format | `.mkv` | .mp4, .avi |
| 10 | Documentaries | `documentaries` | documentary, subject |
| 12 | Music collections | `music` | albums, artist, genre |
| 13 | Lossless music | `.flac` | artist, album |
| 14 | MP3 music/audio | `.mp3` | performer, topic |
| 19 | Photos | `photos` | pictures, albums, DCIM |
| 20 | Images by format | `.jpg` | .jpeg, .png |
| 21 | Wallpapers/artwork | `wallpapers` | artwork, illustrations |
| 22 | Design assets | `.svg` | .eps, icons, fonts |
| 26 | OS images | `.iso` | linux, distribution name |
| 27 | Software/source mirrors | `releases` | packages, source, .tar.gz |
| 28 | Game mods/assets | `mods` | maps, textures, sprites, game name |
| 29 | CAD/3D models | `.stl` | .obj, .step, .dxf |
| 32 | Public downloads | `downloads` | public, subject |

33A adds `Directory listing` title searches to both providers. Python's standard
directory-server implementation constructs titles from that phrase and the
directory path. 33B adds `Index of` without the slash. Both title families are
accepted by the current Dirracuda verifier; no verification behavior changes
are part of the pack. [Python implementation](https://github.com/python/cpython/blob/3.14/Lib/http/server.py).
The broader Caddy/custom-title investigation, 33C, remains deferred in
[candidate notes](CANDIDATE_DORKS.md#33-selected-variants-and-deferred-exploration).

## Validation and limits

Official documentation was searched/read on 2026-09-27. Shodan's direct advanced
page returned HTTP 403 to the research fetcher; its primary-source search-index
copy provided the filter descriptions. No paid Shodan calls or live directory
targets were contacted for this work. Documentation supports syntax and design;
it does not establish yield for these individual dorks.

Automated tests cover all selected IDs, stable original keys/query text, both
provider forms, unique destination/query identities, required metadata, title
variants, idempotent seeding, and preservation of custom collisions on upgrade.
The provider-filter fixture uses a distinct test topic so adding a legitimate
Books category cannot change its expected result set.

```bash
./venv/bin/python -m pytest shared/tests/test_dorkbook_catalog.py shared/tests/test_dorkbook_store.py shared/tests/test_dorkbook_providers.py -q
```

Live API tests against HI's SearXNG and DeGoog instances are separate from these
mock-free local database tests. Record engine identity, query, returned count,
timing, and upstream errors. Search-result counts are not verified directories;
testing need not retrieve target contents. Stop or back off at upstream limits.
