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
cra db upgrade            # create or migrate the database (CRA_HISTORY_URL)
cra corpus check          # verify the corpus bundle
cra serve
```

## The corpus bundle

`CRA_CORPUS_PATH` points at a directory holding the corpus: `papers.csv` is
required, and each of `abstracts.json`, `fulltexts.json`, `pis.json`,
`embeddings.npz`, `graph.json`, `proposal.md` and `corpus_map.json` switches on
the tools that need it. `manifest.json` records a checksum per file and the
expected counts; `cra serve` refuses a bundle that does not match it.

Everything expensive is computed when the bundle is built, never while serving:

```bash
pip install "cluster-research-assist[build]"
cra corpus build          # projection and clusterings for the corpus map
cra corpus manifest       # refresh checksums and counts
```

Sign-in is `CRA_AUTH_PROVIDER=dev` by default, which signs everyone in as
`CRA_AUTH_DEV_USER` (or as the user named by the trusted proxy header
`CRA_AUTH_USER_HEADER`). With `CRA_AUTH_PROVIDER=oidc` users log in at the
configured OpenID Connect issuer, and only pre-registered addresses may sign in:

```bash
cra users add-email someone@university.de
cra users list
cra users deactivate <user-id>
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
`CRA_LLM_BASE_URL` and `CRA_LLM_MODEL`).

In CI the LLM suite is the `tests-llm` job, bound to the GitHub Environment
`llm`. Its required reviewers approve the job as the last action of a review;
only then does it receive the secrets and run. It is a required status check,
so a pull request cannot be merged before that.
`developer/set_branch_protection.sh` configures the required checks.

Conventions: conventional commits, no `os.environ` reads outside
`cra.config.settings`, no module-level per-user state, no two files with the
same basename, logging never `print`. `tests/test_layout.py` enforces the
layout rules and the import layering: `cra.core` (corpus, retrieval,
connectors, tools) never imports `cra.assistant` (llm, chat, mcpclient) or
`cra.app` (web, auth, history, mcpserver, viz), and `cra.assistant` never
imports `cra.app`.

## License

Apache 2.0, see LICENSE.
