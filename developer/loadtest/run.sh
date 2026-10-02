#!/usr/bin/env bash
# The whole load test on this machine: a throwaway PostgreSQL (docker), the
# mock model and lab sources, `cra serve` on the given library, the workshop
# accounts made with atlas's script, then the driver. Usage:
#   developer/loadtest/run.sh <library root> <work dir> [driver args…]
# Nothing here costs tokens. LOADTEST_DOWN=elab (or dt) leaves that source
# unstarted, to see the chat carry on without it. Stop the database with:
# docker rm -f cra-loadtest-pg
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
library=$1 work=$2; shift 2
py=${PYTHON:-$repo/.tox/tests-postgres/bin/python}
cra=$(dirname "$py")/cra
atlas=${ATLAS:-$repo/../atlas}
mkdir -p "$work" && cd "$work"
export LOADTEST_PASSWORD=${LOADTEST_PASSWORD:-conference2026}
export LOADTEST_ADMIN_PASSWORD=${LOADTEST_ADMIN_PASSWORD:-load test admin pass}

docker inspect cra-loadtest-pg >/dev/null 2>&1 || docker run -d --rm --name cra-loadtest-pg \
  -e POSTGRES_PASSWORD=loadtest -e POSTGRES_USER=cra -e POSTGRES_DB=cra \
  -p 55432:5432 postgres:16 -c max_connections=100 >/dev/null
until docker exec cra-loadtest-pg pg_isready -U cra >/dev/null 2>&1; do sleep 1; done
docker exec cra-loadtest-pg psql -U cra -qc "drop schema public cascade; create schema public;" >/dev/null

cat > load.env <<ENV
CRA_LIBRARY_PATH=$library
CRA_HISTORY_URL=postgresql+psycopg://cra:loadtest@127.0.0.1:55432/cra
CRA_HISTORY_AUTO_MIGRATE=true
CRA_COOKIE_SECURE=false
CRA_LOG_DIR=$work/logs
CRA_PORT=8766
CRA_LLM_PROVIDER=mock
CRA_LLM_BASE_URL=http://127.0.0.1:8790/v1
CRA_LLM_API_KEY=mock
CRA_LLM_MODEL=mock
CRA_LLM_MODELS=mock
CRA_MCP_ELAB_URL=http://127.0.0.1:8801/mcp
CRA_MCP_DATATAGGER_URL=http://127.0.0.1:8802/mcp
CRA_SOURCE_TOKEN_KEY=$("$cra" secret-key)
${EXTRA_ENV:-}
ENV

pids=()
trap 'kill "${pids[@]}" 2>/dev/null || true' EXIT
"$py" "$here/mock_llm.py" --port 8790 > mock_llm.log 2>&1 & pids+=($!)
[[ ${LOADTEST_DOWN:-} == elab ]] || { "$py" "$here/mock_sources.py" elab --port 8801 > mock_elab.log 2>&1 & pids+=($!); }
[[ ${LOADTEST_DOWN:-} == dt ]] || { "$py" "$here/mock_sources.py" dt --port 8802 > mock_dt.log 2>&1 & pids+=($!); }
"$cra" --env-file load.env db upgrade >/dev/null
"$cra" --env-file load.env serve > serve.log 2>&1 & cra_pid=$!; pids+=($cra_pid)
until curl -fs -o /dev/null http://127.0.0.1:8766/; do sleep 1; done

echo "$LOADTEST_ADMIN_PASSWORD" | "$cra" --env-file load.env users create loadadmin \
  --name "Load admin" --admin --password-stdin >/dev/null
echo "$LOADTEST_PASSWORD" | "$cra" --env-file load.env users create demo-venice \
  --name Demo --password-stdin >/dev/null
for kind in elab dt; do
  echo loadtest-token | "$cra" --env-file load.env users connect-source $kind demo-venice >/dev/null
done
users=50
for arg in "$@"; do [[ ${prev:-} == --users ]] && users=$arg; prev=$arg; done
{ echo "email,name"; for n in $(seq 0 $((users - 1))); do printf 'attendee%02d@uni.test,Attendee %02d\n' $n $n; done; } > attendees.csv
echo "$LOADTEST_PASSWORD" | "$py" "$atlas/scripts/workshop_accounts.py" --cra "$cra --env-file load.env" \
  create attendees.csv --password-stdin --connect elab,dt | tail -3

"$py" "$here/drive.py" --base http://127.0.0.1:8766 --pid "$cra_pid" "$@"
