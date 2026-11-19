# OpenAPI Breaking Change Diff CLI (Lite)

Zero-dependency **Python 3.9+ stdlib** CLI tool for detecting breaking changes between two **OpenAPI 3.x** specifications (JSON or YAML) in local development and CI/CD pipelines.

## Features

- **100% Python 3.9+ Standard Library** — no `pip install`, no PyYAML, no external dependencies.
- **JSON & YAML Support** — compares OpenAPI 3.x specs in JSON or YAML format.
- **Breaking Change Detection**:
  - Removed endpoints (`/users/{id}`) and HTTP methods (`DELETE`, `POST`, etc.)
  - New required query/path/header parameters or parameters becoming required
  - New required request body fields & request body becoming required
  - Enum value narrowing (removed enum options)
  - Response schema type changes (`integer` -> `string`, etc.) & removed response fields
  - Removed HTTP response status codes (`200`, `201`, etc.)
  - Security scheme removals/changes & global security strengthening/weakening
- **CI/CD Ready** — exits with code `1` when breaking changes are detected, `0` otherwise.
- **Multiple Output Formats** — `text`, `json`, and `markdown`.

## Quick Start

```bash
# Compare two OpenAPI 3.x specs (text output)
python openapi_diff.py sample_specs/v1.json sample_specs/v2.json

# JSON output for CI/CD automation
python openapi_diff.py sample_specs/v1.yaml sample_specs/v2.yaml --format json

# Markdown report for GitHub Pull Requests
python openapi_diff.py sample_specs/v1.json sample_specs/v2.json --format markdown
```

## CI/CD Integration (GitHub Actions)

```yaml
name: OpenAPI Breaking Change Check
on: [pull_request]

jobs:
  openapi-diff:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Check for breaking API changes
        run: |
          python openapi_diff.py api/v1.yaml api/v2.yaml --format markdown >> $GITHUB_STEP_SUMMARY
```

## Running Tests

```bash
python -m unittest discover -s tests -v
```

## Pro Studio Version (Web Diff Viewer + HTML/PDF Changelog + Webhook Alerts)

Need a full-featured API governance suite for your team? Upgrade to **OpenAPI Diff Pro Studio**:

- Interactive **Web Diff Viewer** with side-by-side schema inspection
- Automated **HTML & PDF Changelog** generation for release notes
- **Slack / Discord / Webhook Alerts** on breaking API changes
- Multi-version spec registry & custom rule engine

Get Pro Studio here: **https://funpay.com/users/20096179/**

## License

MIT License — see [LICENSE](LICENSE) for details.
