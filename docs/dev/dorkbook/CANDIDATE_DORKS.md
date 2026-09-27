# Shipped dork candidates

Date: 2026-09-27
Status: HI selected 22 content ideas plus 33A and 33B.

Selected: **1–10, 12–14, 19–22, 26–29, 32, 33A, 33B**.
Not selected this pass: 11, 15–18, 23–25, 30–31.

## Catalog philosophy

HI wants best-effort starting points that spark curiosity and teach users how
to adapt dorks. Imperfect content matching is acceptable. Favor readable,
editable examples over elaborate attempts at perfect precision. New users
should see a pattern and think of their own variations; experienced users can
bring specialized dorks. Do not make measured live yield a shipping requirement.
Keep syntax correct and application compatibility explicit; explain useful
matching ideas in notes without repetitive warnings about imperfect results.

Keep the three broad Shodan defaults and offer a broad Self-hosted Search dork.
Numbers below identify ideas, not final query counts: an idea may produce
separate Shodan HTTP and Self-hosted Search dorks or several format variants.
Clues are not final query strings. Research syntax for selected ideas; live
yield is not a ship gate; required live backend checks are recorded in
[unified validation](UNIFIED_VALIDATION.md). Do not force SMB/FTP equivalents.

| ID | Candidate | Matching clues / variants |
|---|---|---|
| 1 | Ebook folders | books, ebooks |
| 2 | EPUB books | .epub |
| 3 | Kindle/older ebook formats | .mobi, .azw3 |
| 4 | PDF collections | .pdf; broad and potentially noisy |
| 5 | Comics and manga | .cbz, .cbr; comics folders |
| 6 | Audiobooks | audiobooks; .m4b |
| 7 | Movies | movies, films folders |
| 8 | TV shows | tv, series, season folders |
| 9 | Video by format | .mkv, .mp4, .avi; cannot infer genre |
| 10 | Documentaries | documentary/documentaries folders |
| 11 | Lectures and courses | lectures, courses, tutorials |
| 12 | Music collections | music, albums folders |
| 13 | Lossless music | .flac; separate ALAC research if useful |
| 14 | MP3 music/audio | .mp3; may also find speech |
| 15 | Podcasts and radio archives | podcasts, radio, broadcasts |
| 16 | Audio production | samples, loops, stems; .wav |
| 17 | MIDI and soundfonts | .mid, .midi, .sf2 |
| 18 | Sheet music | scores, sheet music, songbooks |
| 19 | Photo collections | photos, pictures, albums, DCIM |
| 20 | Images by format | .jpg, .jpeg, .png; broad and noisy |
| 21 | Wallpapers and artwork | wallpapers, artwork, illustrations |
| 22 | Vector/design assets | .svg, .eps; icons, fonts |
| 23 | Manuals and technical references | manuals, datasheets, documentation |
| 24 | Magazines and scanned publications | magazines, journals, scans |
| 25 | Historical collections | historical maps, digitized records, oral histories |
| 26 | OS installation images | .iso; Linux/distribution folder clues |
| 27 | Software and source mirrors | packages, releases, source; .tar.gz |
| 28 | Game mods and creative assets | mods, maps, textures, sprites |
| 29 | CAD and 3D models | .stl, .obj, .step, .dxf |
| 30 | Research/public datasets | datasets, .csv, .json; not proof data is public |
| 31 | Astronomy and mapping data | .fits, .fit, .geojson, .gpx |
| 32 | General public/download folders | public, downloads; deliberately broad |
| 33 | Alternate directory-index titles | Directory listing, beyond Index of |

Calibre/Calibre-Web/OPDS application discovery remains deferred because the
directory verifier does not support typical application pages. Plain ebook
directories are covered by 1–4.

Evidence: [existing Shodan directory query pattern](https://github.com/jakejarvis/awesome-shodan-queries#apache-directory-listings)
and [SearXNG upstream syntax caveat](https://docs.searxng.org/dev/search_api.html).
These support the method, not measured yield for the proposed categories.

## #33 selected variants and deferred exploration

- **33A: Directory listing titles.** Shodan `http.title:"Directory listing"`;
  web search `intitle:"Directory listing"`. Python's standard file server uses
  `Directory listing for /...`, so this is a concrete additional title family.
  Topic variants can add `http.html:".epub"` or a web-search `"epub"` term.
- **33B: Less restrictive Index of titles.** Shodan `http.title:"Index of"`;
  web search `intitle:"Index of"`. Removes the existing slash requirement,
  admitting titles such as `Index of books`. Treat added search yield as
  unmeasured; provider punctuation handling may reduce the difference.
- **33C, deferred: Caddy/custom directory browsers.** Caddy's current template
  uses the folder name as its title, rather than an Index of phrase. Discovery
  would need other page clues, and ordinary folder-name titles fail our current
  verifier. Supporting these is a separate verifier/browser compatibility card.
  HI explicitly deferred that deeper exploration. Future work should inventory
  real listing templates, test recognition without overly broad acceptance,
  and check browser traversal, saved paths, and extraction compatibility. Keep
  this investigation separate from shipping dorks and the shared library.

Primary sources:
- [Python directory-title implementation](https://github.com/python/cpython/blob/3.14/Lib/http/server.py#L774-L831)
- [Caddy directory template](https://github.com/caddyserver/caddy/blob/master/modules/caddyhttp/fileserver/browse.html)
- [Caddy file-server documentation](https://caddyserver.com/docs/caddyfile/directives/file_server)

Local synthetic check, 2026-09-27: `validate_index_page` accepted linked HTTP-200
pages titled `Index of /`, `Directory listing for /`, `Directory listing for
/books/`, and `Index of books`; rejected `/books/`. Both Shodan HTTP and
Self-hosted Search classification use this verifier. No network targets tested.
