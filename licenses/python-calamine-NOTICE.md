# python-calamine notice

Dirracuda's optional Analyst dependency lane installs **python-calamine 0.8.2**
for legacy Excel extraction. The package is an MIT-licensed Python binding to
the MIT-licensed Rust `calamine` library.

## Frozen artifact

- Wheel: `python_calamine-0.8.2-cp314-cp314-manylinux_2_17_x86_64.manylinux2014_x86_64.whl`
- SHA-256: `9d3cfce465ce82eb9100e5e90673a5844fd46eb7b8148c5404c70f941fd8280b`
- Upstream release: <https://github.com/dimastbk/python-calamine/releases/tag/v0.8.2>
- Attested source commit: `cd6f36f4011bec91921d0a51360428f5ef0b1e4d`
- PyPI artifact: <https://pypi.org/project/python-calamine/0.8.2/#files>
- Source distribution SHA-256:
  `b2000c085722afd01d973af3d58325d26cfd798b3665bb0924e3a658351ebfad`

The exact upstream binding license is preserved in
`python-calamine-MIT.txt`. The wheel's upstream CycloneDX SBOM is preserved
byte-for-byte in `python-calamine-0.8.2.cyclonedx.json.txt` (`.txt` keeps this
upstream JSON artifact outside the application's ignored runtime-JSON lane).

## Embedded Rust dependency closure

The preserved SBOM records 43 compiled Rust components. Their declared
license expressions use MIT, Apache-2.0, BSD-3-Clause, Zlib, Unicode-3.0,
LLVM-exception, and Unlicense terms. The build lock includes the exact
`pyo3-file` 0.16.0 Git revision
`e88695f375ea3db95d96efc53707f4e8eb1def00`; it declares `MIT OR
Apache-2.0` licensing.

The controlled installer downloads the attested wheel rather than rebuilding
the Rust graph. Anyone redistributing the wheel outside that installer must
retain its license and SBOM and re-audit the complete third-party notice set
for the intended distribution form.
