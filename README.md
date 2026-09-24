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
cra library check          # verify the library bundle
cra serve
```

## The library bundle

`CRA_LIBRARY_PATH` points at the library: `papers.csv` is required, and each of
`abstracts.json`, `fulltexts.json`, `pis.json`, `embeddings.npz`, `graph.json`,
`proposal.md` and `publication_map.json` switches on the tools that need it.
`manifest.json` records a checksum per file and the expected counts; `cra serve`
refuses a bundle that does not match it.

The path may name either a plain bundle, which nothing can write to, or a
versioned root, which an admin can replace while the service runs:

```bash
cra library init-root        # versions/<timestamp>/ plus a `current` link
cra library versions
cra library activate <version>
```

With a root, the console takes an uploaded bundle, unpacks it beside the live
one, verifies it in full, and only then moves the link, in one atomic step. A
bad upload leaves the running service untouched, and the previous versions stay,
so rolling back is moving the link again.

Everything expensive is computed when the bundle is built, never while serving:

```bash
pip install "cluster-research-assist[build]"
cra library build          # projection and clusterings for the publication map
cra library manifest       # refresh checksums and counts
```

## Semantic search

The library ships the paper vectors, so ranking needs no model. Only the query
has to be encoded, and that one forward pass runs on onnxruntime rather than
torch, which keeps a CUDA-capable tensor library out of an image that would
never use it:

```bash
cra encoder fetch           # 127 MB into CRA_ENCODER_PATH
```

The model must be the one the library was built with; a mismatch is refused,
because two models' vectors are not comparable even when the widths agree.
Leaving `CRA_ENCODER_PATH` empty turns semantic search off and leaves keyword
search, the publication map and everything else working.

## Placing a DOI on the map

The publication map also takes a DOI the library does not hold: the metadata is
fetched from Crossref, and from OpenAlex for the abstract Crossref often lacks,
and the paper is placed among the library's nearest work. This needs the query
encoder above; without it the map still pans, zooms and searches.

Both APIs are public and neither owes us an answer, so results and failures are
cached, and a source that starts throttling us is left alone for as long as it
asked rather than retried. Set `CRA_DOI_LOOKUP_CONTACT` to a group address --
it is sent with every request and reaches the pool these services throttle
least. `CRA_OPENALEX_BASE_URL` empty drops the abstract fallback, at the cost
of placing more papers by their title alone.

## Who may do what

Signing in is the rule. The landing page and the sign-in flow answer without an
account; everything else needs one, and a test enumerates every route so a new
one is closed until someone opens it deliberately.

| | signed in | admin |
|---|---|---|
| paper metadata, abstracts, PI profiles, the graph, the publication map | yes | yes |
| chat, within a daily cap | yes | yes |
| full texts and the proposal | yes | yes |
| saved history | yes | yes |
| accounts, allow-list, settings | no | yes |

The outward MCP endpoint is gated separately by a token that a signed-in user
mints, and serves the public tier only: metadata, abstracts, profiles and the
graph. Nothing that reads inside the full texts or the proposal is reachable
through it, passage search included: passages around a chosen query would
reconstruct a paper one query at a time.

Sign-in is `CRA_AUTH_PROVIDER=dev` by default, which signs everyone in as
`CRA_AUTH_DEV_USER` (or as the user named by the trusted proxy header
`CRA_AUTH_USER_HEADER`). Because that signs *anyone* in, the server refuses to
start with it on any address but loopback unless `CRA_AUTH_DEV_INSECURE=true`.
With `CRA_AUTH_PROVIDER=oidc` users log in at the configured OpenID Connect
issuer, and only pre-registered addresses may sign in. `CRA_AUTH_ADMINS` lists
the addresses that become admin the first time they bind an identity, so a
fresh deployment has an administrator without shell access; the claim is not
consulted again afterwards, so the role is changed in the console, not by
editing the list.

An email claim is asserted by the user's home identity provider and verified
by nobody else, so an invitation also says where it may be claimed from:
`CRA_AUTH_HOME_ORGANIZATIONS` names the `schacHomeOrganization` values (for
example `tum.de,lmu.de`) accepted by default, and `--org` on an invitation
overrides that for one address. Leave both empty only if any institution in
the federation should be able to claim any invited address.

The console lists everyone who can sign in on one page: accounts, addresses
invited but not yet used, and the addresses named in `CRA_AUTH_ADMINS`. An
invitation carries the role its account will start with. The same from the
command line:

```bash
cra users add-email someone@university.de --org university.de
cra users list
cra users promote <user-id>
cra users deactivate <user-id>
```

Sessions end after `CRA_SESSION_MAX_AGE_HOURS` unused and in any case after
`CRA_SESSION_ABSOLUTE_HOURS`. Every response carries a Content-Security-Policy
that allows scripts only from the package and the pinned CDN builds, and no
images from anywhere else; state-changing requests are refused when they look
like a cross-site form. Tools a connected eLN offers are listed to the model
only when they are read-only, unless `CRA_REMOTE_WRITE_TOOLS=true`.

## The outward MCP endpoint

Other assistants can use the same tools. With `CRA_MCP_SERVER_ENABLED=true` the
service also answers streamable HTTP MCP at `CRA_MCP_SERVER_PATH` (`/mcp`),
backed by the very same registry, asked at the public tier: an internal tool is
neither listed nor reachable by name, and full texts and the proposal never
leave through it.

```bash
cra token issue "a colleague"     # prints the token; the details go to stderr
cra token check <token>           # who it is for and when it ends
```

Tokens are signed with `CRA_MCP_TOKEN_SECRET` and carry their own expiry, so
verifying one costs no database lookup. A single token cannot be revoked:
rotating the secret ends every one of them. With
`CRA_MCP_SERVER_REQUIRE_TOKEN=false` the endpoint is open, which is a choice for
a network that is already closed. Either way every call is rate-limited
(`CRA_MCP_SERVER_RATE_LIMIT` a minute, per token, or per address without one)
and written to the audit log as one line naming the caller, the tool and how
long it took -- never the arguments, which are someone else's query.

Point a client at `https://<host><base path>/mcp` with
`Authorization: Bearer <token>`.

## Connecting your own eLabFTW or DataTagger

The assistant can also work with the user's *own* data, through the MCP servers
that already sit in front of eLabFTW and DataTagger. A user registers once from
the chat: the dialog asks for the address of their instance and their API key,
the key is passed to the registration service, and the personal token it mints
is what the assistant uses. The key is never stored and the token lives in the
process, tied to that browser session -- signing out or a restart ends it, and
neither ever reaches the database.

Whatever that token unlocks upstream is exactly what the model is offered:
the tools are namespaced (`elab_*`, `dt_*`) so they cannot collide with the
library's own, and the session is pooled, so a whole conversation costs one
handshake instead of one per tool call. A source that stops answering is
replaced by a single entry saying so rather than taking the chat down.

Both are opt-in per deployment: without `CRA_MCP_ELAB_URL` or
`CRA_MCP_DATATAGGER_URL` the connector interface does not appear at all.

## Configuration and settings

Endpoints, keys and paths are configuration: they live in `.env`, and changing
one needs a restart. How the service behaves from day to day is policy, and
admins change it in the console or on the command line without a restart:

```bash
cra policy list                          # every setting and where its value comes from
cra policy set user_chat_daily_limit 50
cra policy set notice "Pilot running until Friday"
cra policy set notice --reset            # back to the configured default
```

## Develop

Everything runs through tox:

```bash
tox -e lint        # ruff and mypy
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
layout rules and the import layering: `cra.core` (library, retrieval,
connectors, tools) never imports `cra.assistant` (llm, chat, mcpclient) or
`cra.app` (web, auth, history, mcpserver, viz), and `cra.assistant` never
imports `cra.app`.

## License

Apache 2.0, see LICENSE.
