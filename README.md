# Cluster Research Assistant

LLM research assistant over a research cluster's publications, principal
investigators, proposal and live data sources. One pip-installable package reads
a `.env` file, serves a web assistant and an outward MCP endpoint, and talks to
the external MCP servers a cluster has. e-verse, the e-conversion deployment,
is one configuration of it.

## Install

```bash
pip install .
```

Requires Python 3.11 or newer.

## Run

```bash
cp .env.example .env      # fill in the values
cra check-config          # validate and print the resolved settings
cra serve
```

## Develop

Everything runs through tox:

```bash
tox -e lint        # ruff, mypy, import-linter
tox -e tests       # fast suite: no LLM, no PostgreSQL, no downloads
tox -e build       # sdist and wheel integrity
tox -e format      # apply formatting
```

Two further suites need external resources and are not part of the default
run: `tox -e tests-postgres` (a PostgreSQL server at `CRA_TEST_POSTGRES_URL`)
and `tox -e tests-llm` (a real model endpoint through `CRA_LLM_API_KEY`,
`CRA_LLM_BASE_URL` and `CRA_LLM_MODEL`). See CONTRIBUTING.md for how the LLM
suite gates pull requests.

## License

Apache 2.0, see LICENSE.
