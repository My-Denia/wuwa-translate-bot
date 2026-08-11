#!/bin/sh
set -eu

DEPLOY_ROOT="${WUWATERM_DEPLOY_ROOT:-/opt/wuwaterm/current}"
FAIL_STEP="${WUWATERM_FAIL_STEP:-}"

cd "$DEPLOY_ROOT"

compose() {
  SOURCE_COMMIT="${SOURCE_COMMIT:-unknown}" \
    WUWATERM_RUNTIME_IMAGE="${WUWATERM_RUNTIME_IMAGE:-wuwaterm-runtime:local}" \
    docker compose -f deploy/docker-compose.yml "$@"
}

fail_if() {
  if [ "$FAIL_STEP" = "$1" ]; then
    echo "injected deployment failure at step: $1" >&2
    return 97
  fi
}

env_mode="$(stat -c '%a' .env)"
if [ "$env_mode" != "600" ]; then
  echo "refusing to use $DEPLOY_ROOT/.env with mode $env_mode; expected 600" >&2
  exit 1
fi

if [ -n "$(git status --porcelain --untracked-files=all)" ]; then
  echo "refusing to deploy a source checkout with modifications or untracked files" >&2
  exit 1
fi
git fetch --quiet origin main:refs/remotes/origin/main
source_commit="$(git rev-parse HEAD)"
origin_main="$(git rev-parse refs/remotes/origin/main)"
if [ "$source_commit" != "$origin_main" ]; then
  echo "refusing deployment: HEAD does not match freshly fetched origin/main" >&2
  exit 1
fi
case "$source_commit" in
  *[!0-9a-f]*|'')
    echo "refusing invalid source commit: $source_commit" >&2
    exit 1
    ;;
esac
if [ "${#source_commit}" -ne 40 ]; then
  echo "refusing source commit that is not a full SHA: $source_commit" >&2
  exit 1
fi

# The device store's default moved from state/api/ - inside the directory the
# bot mounts read-write in full - to the sibling state-api/. The library
# refuses to create an empty store next to an old one, but the API container
# only ever sees state-api/, so inside the container the old path does not
# exist and that guard cannot fire. The host is the only place that can see
# both, which makes this the only place the upgrade can be caught.
# The presence of the old file is enough to refuse, whether or not a new one
# exists: an earlier attempt may already have created an empty store at the new
# path, and then having both is precisely the state where nobody can tell which
# one holds the live verifiers.
if [ -f "state/api/devices.db" ]; then
  echo "refusing deployment: a device store still exists at" >&2
  echo "state/api/devices.db, the path the API used before its state" >&2
  echo "directory moved out of the bot's writable mount. The API now reads" >&2
  echo "state-api/devices.db. Move that file, with any -wal and -shm" >&2
  echo "sidecars, to state-api/ if it holds the live verifiers, or delete it" >&2
  echo "if state-api/devices.db already does. Deploying with both in place" >&2
  echo "could start an API that refuses every device ever registered." >&2
  exit 1
fi

deployment_id="$(date -u +%Y%m%dT%H%M%SZ)-$$"
deployment_utc="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
candidate_path="data/candidates/terms.$deployment_id.db"
candidate_container_path="/app/data/candidates/terms.$deployment_id.db"
db_path="data/terms.db"
backup_dir="data/deployment-backups"
manifest_dir=".deployments"
manifest_path="$manifest_dir/$source_commit.json"
pointer_path=".deploy_commit"
new_image_ref="wuwaterm-runtime:$source_commit"
rollback_image_ref="wuwaterm-runtime:rollback-$deployment_id"
# Each surface gets its own rollback tag. They normally run the same image, but
# a host recovered by hand can have them on different ones, and restoring both
# from a single tag would move a surface onto an image it was never running.
rollback_api_image_ref="wuwaterm-runtime:rollback-api-$deployment_id"

mkdir -p data/candidates "$backup_dir" "$manifest_dir" state
if [ -e "$candidate_path" ]; then
  echo "refusing to overwrite existing candidate: $candidate_path" >&2
  exit 1
fi

old_pointer_present=0
old_pointer=""
pointer_backup=""
if [ -e "$pointer_path" ]; then
  old_pointer="$(tr -d '\n' < "$pointer_path")"
  case "$old_pointer" in
    *[!0-9a-f]*|'')
      echo "refusing invalid existing deployment pointer" >&2
      exit 1
      ;;
  esac
  if [ "${#old_pointer}" -ne 40 ]; then
    echo "refusing existing deployment pointer that is not a full SHA" >&2
    exit 1
  fi
  old_pointer_present=1
  pointer_backup="$backup_dir/$deployment_id.deploy_commit"
  cp -p "$pointer_path" "$pointer_backup"
fi

old_db_present=0
db_backup=""
if [ -f "$db_path" ]; then
  old_db_present=1
fi

# Each surface is recorded separately, so a host on a pre-api deployment
# (empty value) is handled without pretending the container ever existed.
# Existence is not enough either: a container left stopped by an earlier failed
# upgrade, or stopped deliberately by the operator, still answers
# `docker inspect`. Rollback restores the state this deployment found, so what
# it needs to know is whether each surface was RUNNING.
old_image_id="$(docker inspect --format '{{.Image}}' wuwaterm-bot 2>/dev/null || true)"
old_bot_running="$(docker inspect --format '{{.State.Running}}' wuwaterm-bot 2>/dev/null || true)"
old_api_image_id="$(docker inspect --format '{{.Image}}' wuwaterm-api 2>/dev/null || true)"
old_api_running="$(docker inspect --format '{{.State.Running}}' wuwaterm-api 2>/dev/null || true)"
# Tagged per surface: a host running only one of them still has an image to
# roll back to, and a host whose two containers are on different images keeps
# both of them.
if [ -n "$old_image_id" ]; then
  docker image tag "$old_image_id" "$rollback_image_ref"
fi
if [ -n "$old_api_image_id" ]; then
  docker image tag "$old_api_image_id" "$rollback_api_image_ref"
fi

# The builder tag is intentionally local and mutable, so rebuild it from the
# verified clean source checkout before any data command. Reusing a builder
# left by an older deployment could refresh/build/verify with the wrong pin or
# schema even though the runtime image is built from the new source.
compose build wuwaterm-builder
fail_if builder_image

# The builder has no runtime env_file. This smoke checks secret presence only
# through exit status and never prints values.
compose run --rm --entrypoint sh wuwaterm-builder -c \
  'test -z "${TELEGRAM_BOT_TOKEN+x}${TELEGRAM_TEST_CHAT_ID+x}${OWNER_USER_ID+x}${OPENAI_API_KEY+x}${OPENAI_BASE_URL+x}${WUWATERM_OPENAI_API_KEY+x}${WUWATERM_OPENAI_BASE_URL+x}${WUWATERM_OPENAI_MODEL+x}${WUWATERM_REDACTION_SECRET+x}"'

compose run --rm wuwaterm-builder refresh-data
fail_if refresh
compose run --rm -e "WUWATERM_DB_PATH=$candidate_container_path" \
  wuwaterm-builder build-db --atomic
fail_if build
compose run --rm -e "WUWATERM_DB_PATH=$candidate_container_path" \
  wuwaterm-builder verify-db
compose run --rm wuwaterm-builder python scripts/verify_exact_hits.py \
  "$candidate_container_path" --sample-size 500
compose run --rm wuwaterm-builder python scripts/verify_seed_terms.py \
  "$candidate_container_path" \
  --discrepancies "/app/data/candidates/seed-discrepancies.$deployment_id.json"
if [ "$old_db_present" -eq 1 ]; then
  compose run --rm wuwaterm-builder python scripts/diff_terms_db.py \
    /app/data/terms.db "$candidate_container_path" --json \
    > "$backup_dir/db-diff.$deployment_id.json"
fi
fail_if verify

SOURCE_COMMIT="$source_commit" WUWATERM_RUNTIME_IMAGE="$new_image_ref" \
  compose build wuwaterm
image_id="$(docker image inspect --format '{{.Id}}' "$new_image_ref")"
image_digest="$(docker image inspect --format '{{if .RepoDigests}}{{index .RepoDigests 0}}{{else}}{{.Id}}{{end}}' "$new_image_ref")"
image_revision="$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$new_image_ref")"
if [ -z "$image_id" ] || [ -z "$image_digest" ] || [ "$image_revision" != "$source_commit" ]; then
  echo "runtime image identity or revision label validation failed" >&2
  exit 1
fi
fail_if image

if [ "$old_db_present" -eq 1 ]; then
  old_db_hash="$(sha256sum "$db_path" | awk '{print $1}')"
  db_backup="$backup_dir/terms.$deployment_id.$old_db_hash.db"
  db_backup_tmp="$db_path.backup.$deployment_id"
  if [ -e "$db_backup" ] || [ -e "$db_backup_tmp" ]; then
    echo "refusing to overwrite an existing database backup" >&2
    exit 1
  fi
  cp -p "$db_path" "$db_backup_tmp"
  # The source temp lives in data/ while the destination lives in the backup
  # directory. durable-replace therefore fsyncs the backup file, the backup
  # directory, and data/ (which also makes a newly created backup directory
  # durable) before the live database can be promoted.
  python3 scripts/deployment_manifest.py durable-replace \
    --source "$db_backup_tmp" --destination "$db_backup"
  if [ "$(sha256sum "$db_backup" | awk '{print $1}')" != "$old_db_hash" ]; then
    echo "database backup verification failed" >&2
    exit 1
  fi
else
  db_backup="none"
fi
fail_if backup

runtime_stopped=0
db_promoted=0
pointer_published=0

rollback_on_failure() {
  status=$?
  trap - EXIT
  if [ "$status" -eq 0 ]; then
    exit 0
  fi

  echo "deployment failed; restoring previous database, image and pointer" >&2
  rollback_failed=0
  db_binding_restored=1

  # Nothing may serve while the binding is being restored. If the replacement
  # containers were started, they are running NEW code against a database that
  # is about to be rolled back, which is the mixed binding this rollback exists
  # to prevent. Both surfaces therefore go down before the database is touched;
  # only what was running before the deployment comes back afterwards.
  containers_quiesced=1
  if [ "$runtime_stopped" -eq 1 ]; then
    if ! compose stop wuwaterm wuwaterm-api >/dev/null 2>&1; then
      echo "warning: replacement containers could not be stopped before rollback" >&2
      rollback_failed=1
      containers_quiesced=0
    fi
  fi

  # If a container may still be serving, rolling the database back underneath
  # it would CREATE the mixed binding instead of preventing it. Whatever the
  # database and pointer currently are, they match the code that is running, so
  # they are left exactly as they are and the operator is told to intervene.
  if [ "$containers_quiesced" -eq 0 ]; then
    echo "warning: a serving container could not be stopped; the database and" >&2
    echo "commit pointer are left exactly as they are and nothing is" >&2
    echo "restarted. Stop both containers by hand, then roll back." >&2
    echo "warning: rollback completed with errors; manual recovery is required" >&2
    exit "$status"
  fi

  if [ "$db_promoted" -eq 1 ]; then
    if [ "$old_db_present" -eq 1 ]; then
      restore_tmp="$db_path.rollback.$deployment_id"
      if ! cp -p "$db_backup" "$restore_tmp"; then
        echo "warning: could not prepare database rollback copy" >&2
        rollback_failed=1
      elif ! python3 scripts/deployment_manifest.py durable-replace \
        --source "$restore_tmp" --destination "$db_path"; then
        echo "warning: restored database durability could not be confirmed" >&2
        rollback_failed=1
      fi
      if [ ! -f "$db_path" ] || \
        [ "$(sha256sum "$db_path" | awk '{print $1}')" != "$old_db_hash" ]; then
        echo "warning: old database content was not restored" >&2
        rollback_failed=1
        db_binding_restored=0
      fi
    else
      if ! python3 scripts/deployment_manifest.py durable-remove \
        --path "$db_path"; then
        echo "warning: database removal durability could not be confirmed" >&2
        rollback_failed=1
      fi
      if [ -e "$db_path" ]; then
        echo "warning: promoted database still exists after rollback" >&2
        rollback_failed=1
        db_binding_restored=0
      fi
    fi
  fi

  if [ "$pointer_published" -eq 1 ]; then
    if [ "$old_pointer_present" -eq 1 ]; then
      if ! python3 scripts/deployment_manifest.py publish-pointer \
        --path "$pointer_path" --source-commit "$old_pointer"; then
        echo "warning: restored pointer durability could not be confirmed" >&2
        rollback_failed=1
      fi
    else
      if ! python3 scripts/deployment_manifest.py durable-remove \
        --path "$pointer_path"; then
        echo "warning: pointer removal durability could not be confirmed" >&2
        rollback_failed=1
      fi
    fi
  fi

  # Everything the deployment started is already stopped by this point, so this
  # section only decides what may come BACK. Anything not started here stays
  # down, which is the correct outcome for a host that was not running it.
  if [ "$runtime_stopped" -eq 1 ]; then
    if [ "$db_binding_restored" -eq 1 ]; then
      # Same rule for both surfaces, and each is gated on ITS OWN state: a
      # host running only the API has no bot image, and that must not decide
      # whether the API comes back.
      if [ "$old_bot_running" = "true" ] && [ -n "$old_image_id" ]; then
        if ! WUWATERM_RUNTIME_IMAGE="$rollback_image_ref" \
          compose up -d --no-build --force-recreate wuwaterm; then
          echo "warning: old runtime image could not be restarted" >&2
          rollback_failed=1
        fi
      fi
      # Only bring the api surface back if this host was actually RUNNING it
      # when the deployment started. On a first upgrade there is nothing to
      # restore, and a container that existed but was stopped (an earlier
      # failed upgrade, or an operator decision) must not be started here.
      if [ "$old_api_running" = "true" ] && [ -n "$old_api_image_id" ]; then
        if ! WUWATERM_RUNTIME_IMAGE="$rollback_api_image_ref" \
          compose up -d --no-build --force-recreate wuwaterm-api; then
          echo "warning: old api image could not be restarted" >&2
          rollback_failed=1
        fi
      fi
    else
      echo "warning: refusing to restart old runtime without its database" >&2
      rollback_failed=1
    fi
  fi

  # The binding is verified against a surface that was actually RESTORED. A
  # stopped bot container can sit on a different image than the running API,
  # and verifying against the one that stayed down would compare the manifest
  # to something this rollback never brought back. With nothing restored there
  # is nothing to verify, and an empty id would compare against nothing at all.
  verify_image_id=""
  if [ "$old_bot_running" = "true" ] && [ -n "$old_image_id" ]; then
    verify_image_id="$old_image_id"
  elif [ "$old_api_running" = "true" ] && [ -n "$old_api_image_id" ]; then
    verify_image_id="$old_api_image_id"
  fi
  if [ "$old_pointer_present" -eq 1 ] && [ -f "$manifest_dir/$old_pointer.json" ] \
    && [ -n "$verify_image_id" ]; then
    if ! python3 scripts/deployment_manifest.py verify \
      --path "$manifest_dir/$old_pointer.json" \
      --source-commit "$old_pointer" \
      --image-id "$verify_image_id" \
      --db "$db_path"; then
      echo "warning: restored deployment binding verification failed" >&2
      rollback_failed=1
    fi
    if ! python3 scripts/deployment_manifest.py verify-pointer \
      --path "$pointer_path" --source-commit "$old_pointer"; then
      echo "warning: restored deployment pointer verification failed" >&2
      rollback_failed=1
    fi
  fi
  if [ "$rollback_failed" -ne 0 ]; then
    echo "warning: rollback completed with errors; manual recovery is required" >&2
  fi
  exit "$status"
}
trap rollback_on_failure EXIT

# Candidate DB and immutable image are both verified before this stop. Both
# surfaces mount the same terminology database read-only, so both must be down
# while it is replaced.
#
# The transition is recorded BEFORE the command, for the same reason
# db_promoted is: a combined stop that fails partway may already have stopped
# one surface, and a rollback that believes nothing was touched would leave the
# previously running bot down without saying so.
runtime_stopped=1
compose stop wuwaterm wuwaterm-api
db_promoted=1
python3 scripts/deployment_manifest.py durable-replace \
  --source "$candidate_path" --destination "$db_path"

# Freeze and validate the legacy state only while the old runtime is stopped.
for state_file in chat_settings.json channel_replies.json; do
  if [ -f "data/$state_file" ] && [ -e "state/$state_file" ]; then
    python3 scripts/validate_state_file.py "state/$state_file" "$state_file" >/dev/null || {
      echo "refusing invalid state/$state_file while data/$state_file exists" >&2
      exit 1
    }
  fi
  if [ -f "data/$state_file" ] && [ ! -e "state/$state_file" ]; then
    tmp="state/.$state_file.migrate.$$"
    cp -p "data/$state_file" "$tmp"
    chmod 600 "$tmp"
    python3 scripts/validate_state_file.py "$tmp" "$state_file" >/dev/null || {
      echo "refusing to migrate invalid data/$state_file" >&2
      rm -f "$tmp"
      exit 1
    }
    if ! ln "$tmp" "state/$state_file" 2>/dev/null; then
      python3 scripts/validate_state_file.py "state/$state_file" "$state_file" >/dev/null || {
        echo "refusing invalid state/$state_file" >&2
        rm -f "$tmp"
        exit 1
      }
    fi
    rm -f "$tmp"
  fi
done
fail_if state

WUWATERM_RUNTIME_IMAGE="$new_image_ref" compose up -d --no-build \
  --force-recreate wuwaterm wuwaterm-api
fail_if start
WUWATERM_RUNTIME_IMAGE="$new_image_ref" compose exec -T \
  -e TELEGRAM_TEST_CHAT_ID= wuwaterm python scripts/deploy_smoke.py
# The api surface binds loopback only, so its smoke runs inside its own
# container and needs nothing exposed to the host. Readiness, not liveness:
# /healthz answers while the terminology database is missing or mounted at the
# wrong path, and publishing a deployment in that state is exactly what this
# check exists to prevent.
WUWATERM_RUNTIME_IMAGE="$new_image_ref" compose exec -T wuwaterm-api \
  python -c "import os, sys, time, urllib.error, urllib.request; port = os.environ.get('WUWATERM_API_PORT', '8788'); url = 'http://127.0.0.1:' + port + '/readyz'; deadline = time.monotonic() + 60.0; last = 'no attempt'
while time.monotonic() < deadline:
    try:
        response = urllib.request.urlopen(url, timeout=5)
    except (urllib.error.URLError, OSError) as exc:
        last = type(exc).__name__
        time.sleep(1.0)
        continue
    if response.status == 200:
        sys.exit(0)
    last = 'status ' + str(response.status)
    time.sleep(1.0)
sys.exit('api readiness never reported ok: ' + last)"
fail_if smoke

running_image_id="$(docker inspect --format '{{.Image}}' wuwaterm-bot)"
if [ "$running_image_id" != "$image_id" ]; then
  echo "running container image does not match validated image" >&2
  exit 1
fi
running_api_image_id="$(docker inspect --format '{{.Image}}' wuwaterm-api)"
if [ "$running_api_image_id" != "$image_id" ]; then
  echo "running api container image does not match validated image" >&2
  exit 1
fi

python3 scripts/deployment_manifest.py create \
  --path "$manifest_path" \
  --source-commit "$source_commit" \
  --image-ref "$new_image_ref" \
  --image-id "$image_id" \
  --image-digest "$image_digest" \
  --image-revision "$image_revision" \
  --db "$db_path" \
  --backup-path "$db_backup" \
  --deployment-utc "$deployment_utc"
fail_if manifest
python3 scripts/deployment_manifest.py verify \
  --path "$manifest_path" \
  --source-commit "$source_commit" \
  --image-id "$running_image_id" \
  --image-digest "$image_digest" \
  --db "$db_path"

pointer_published=1
python3 scripts/deployment_manifest.py publish-pointer \
  --path "$pointer_path" --source-commit "$source_commit"
python3 scripts/deployment_manifest.py verify-pointer \
  --path "$pointer_path" --source-commit "$source_commit"
fail_if pointer

db_hash="$(sha256sum "$db_path" | awk '{print $1}')"
runtime_stopped=0
trap - EXIT

echo "deployment source commit: $source_commit"
echo "deployment image id: $image_id"
echo "deployment bot container image id: $running_image_id"
echo "deployment api container image id: $running_api_image_id"
echo "deployment image digest: $image_digest"
echo "deployment database sha256: $db_hash"
echo "deployment manifest: $manifest_path"
echo "deployment timestamp: $deployment_utc"
