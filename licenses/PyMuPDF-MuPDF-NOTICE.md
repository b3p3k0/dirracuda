# PyMuPDF and MuPDF notice

Dirracuda's optional Analyst PDF support uses **PyMuPDF 1.28.0**, which embeds
**MuPDF 1.28.0** when built from the source release selected by this project.

- Copyright: Artifex Software, Inc. and PyMuPDF contributors.
- Licence selected by Dirracuda: GNU Affero General Public License, version 3.
- Full licence text: [`AGPL-3.0.txt`](AGPL-3.0.txt).
- PyMuPDF source: https://github.com/pymupdf/PyMuPDF/tree/1.28.0
- MuPDF source: https://github.com/ArtifexSoftware/mupdf/tree/1.28.0
- MuPDF source archive: https://mupdf.com/downloads/archive/mupdf-1.28.0-source.tar.gz
- PyPI source release: https://pypi.org/project/PyMuPDF/1.28.0/

The controlled installer verifies the PyMuPDF source archive as SHA-256
`e53f3567403a92da15caa9e7ae0164327fff48817e9f40175367fb9de524258d` and the separate
MuPDF source archive as SHA-256
`21c7f064903154f1c3a7458bee81f130fc36f9b5147ea13328f9980e02d2dea2`. It builds the
matching 1.28.0/1.28.0 pair with unused OCR support disabled. Dirracuda does not vendor
or modify PyMuPDF or MuPDF source in this repository.

Dirracuda itself remains under GNU GPL v3. GPLv3 §13 permits combining it with an
AGPLv3 component; the AGPL network-source requirements apply to the combination when
that optional component is installed and exposed through a network service. The
development source for the Dirracuda portion, including build/install scripts, is:

https://github.com/b3p3k0/dirracuda

Distributors must preserve this notice and provide the complete corresponding source
required by the applicable GNU licences for the exact version they distribute or run as
a network service. A network-facing release must identify an exact public Dirracuda
commit, tag or source archive rather than relying only on the moving default branch, and
must preserve or host the exact hashed PyMuPDF and MuPDF source archives identified
above.
