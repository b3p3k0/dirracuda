# Dependabot Response: Pygments (pip) <= 2.19.2

## Status
- Upstream currently has no patched Pygments release for the reported `AdlLexer` regex-complexity issue.
- Dirracuda runtime does not import or depend on `Pygments`.
- In local development, `Pygments` appears only as a transitive dependency of `pytest`.

## Project Mitigation
- Keep production installs scoped to runtime dependencies in `requirements.txt`.
- Do not add `Pygments` as a direct runtime dependency.
- Restrict test tooling (including `pytest`/`Pygments`) to isolated local/dev environments.

## Operational Guidance
- For production/runtime environments:
  - install only with:
    - `./venv/bin/python -m pip install -r requirements.txt`
- For local test environments:
  - run tests in isolated workspaces/containers/VMs when handling untrusted text inputs.

## Dependabot Handling
- Alert cannot be remediated by version bump until upstream publishes a fix.
- Keep monitoring new Pygments releases and update immediately once a patched version exists.
