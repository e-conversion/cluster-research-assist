#!/usr/bin/env bash
# Make the four CI checks required on main. Needs `gh` logged in with admin
# rights on the repository. The `llm` environment with its required reviewers
# is created in the repository settings (Settings > Environments) and holds
# CRA_LLM_API_KEY as a secret plus CRA_LLM_BASE_URL and CRA_LLM_MODEL as
# variables.
set -euo pipefail

repo="${1:-e-conversion/cluster-research-assist}"

gh api --method PUT "repos/${repo}/branches/main/protection" \
  --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["lint", "tests (ubuntu-latest, 3.11)", "tests (ubuntu-latest, 3.13)", "build", "tests-llm"]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": {"required_approving_review_count": 1},
  "restrictions": null
}
JSON
