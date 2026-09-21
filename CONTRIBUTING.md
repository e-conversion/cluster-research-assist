# Contributing

## Workflow

- Branch from `main`, open a pull request, one approval required.
- Conventional commits (`feat:`, `fix:`, `refactor:`, `test:`, `ci:`, `chore:`),
  imperative subject line, body only for what the diff cannot show.
- `.env` MUST never be committed; `.env.example` MUST list every key with its
  default and a one-line comment. Configuration or API changes MUST be
  reflected there in the same pull request.
- Install the pre-commit hooks once: `pre-commit install`. They run ruff and
  gitleaks.

## Checks that gate a merge

| Check | Runs | Needs |
|---|---|---|
| `lint` | every pull request | nothing |
| `tests` | every pull request, push to main, weekly | nothing |
| `build` | every pull request | nothing |
| `tests-llm` | every pull request, after a maintainer approves the `llm` environment | `CRA_LLM_API_KEY`, `CRA_LLM_BASE_URL`, `CRA_LLM_MODEL` as environment secrets |

The `tests-llm` job is bound to the GitHub Environment `llm`, which has
required reviewers. It waits until a maintainer approves it, usually as the
last action of a review, and only then receives the secrets and runs. Because it
is a required status check, a pull request cannot be merged before that.
`developer/set_branch_protection.sh` configures the required checks.

## Code

- No module-level mutable per-user state. Anything per user lives on the
  request, the session or an object created at startup.
- One event loop. Long-lived clients (HTTP, MCP, database) are created at
  application start and closed at shutdown.
- No `os.environ` reads outside `cra.config.settings`.
- No two files in the repository share a basename; no `utils.py`,
  `helpers.py`, `common.py`, `base.py` or `models.py`. A test enforces this.
- Comments explain why, not what. Docstrings only where the code needs them.
- Logging through `logging`, never `print`.

## Tests

- Test behaviour, not implementation. One concern per test.
- The default suite MUST stay fast: no model downloads, no network, no
  PostgreSQL. Mark such tests `slow`, `llm` or `postgres`.
- Prefer parametrisation over near-identical test functions.
