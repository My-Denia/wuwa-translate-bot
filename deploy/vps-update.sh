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

old_image_id="$(docker inspect --format '{{.Image}}' wuwaterm-bot 2>/dev/null || true)"
if [ -n "$old_image_id" ]; then
  docker image tag "$old_image_id" "$rollback_image_ref"
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
  cp -p "$db_path" "$db_backup"
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
  if [ "$db_promoted" -eq 1 ]; then
    if [ "$old_db_present" -eq 1 ]; then
      restore_tmp="$db_path.rollback.$deployment_id"
      cp -p "$db_backup" "$restore_tmp" && mv -f "$restore_tmp" "$db_path"
    else
      rm -f "$db_path"
    fi
  fi

  if [ "$pointer_published" -eq 1 ]; then
    if [ "$old_pointer_present" -eq 1 ]; then
      python3 scripts/deployment_manifest.py publish-pointer \
        --path "$pointer_path" --source-commit "$old_pointer" || true
    else
      rm -f "$pointer_path"
    fi
  fi

  if [ "$runtime_stopped" -eq 1 ]; then
    if [ -n "$old_image_id" ]; then
      WUWATERM_RUNTIME_IMAGE="$rollback_image_ref" compose up -d --no-build \
        --force-recreate wuwaterm || true
    else
      compose stop wuwaterm >/dev/null 2>&1 || true
    fi
  fi

  if [ "$old_pointer_present" -eq 1 ] && [ -f "$manifest_dir/$old_pointer.json" ]; then
    python3 scripts/deployment_manifest.py verify \
      --path "$manifest_dir/$old_pointer.json" \
      --source-commit "$old_pointer" \
      --image-id "$old_image_id" \
      --db "$db_path" || echo "warning: restored deployment binding verification failed" >&2
    python3 scripts/deployment_manifest.py verify-pointer \
      --path "$pointer_path" --source-commit "$old_pointer" \
      || echo "warning: restored deployment pointer verification failed" >&2
  fi
  exit "$status"
}
trap rollback_on_failure EXIT

# Candidate DB and immutable image are both verified before this stop.
compose stop wuwaterm
runtime_stopped=1
mv "$candidate_path" "$db_path"
db_promoted=1

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
  --force-recreate wuwaterm
fail_if start
WUWATERM_RUNTIME_IMAGE="$new_image_ref" compose exec -T \
  -e TELEGRAM_TEST_CHAT_ID= wuwaterm python scripts/deploy_smoke.py
fail_if smoke

running_image_id="$(docker inspect --format '{{.Image}}' wuwaterm-bot)"
if [ "$running_image_id" != "$image_id" ]; then
  echo "running container image does not match validated image" >&2
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

python3 scripts/deployment_manifest.py publish-pointer \
  --path "$pointer_path" --source-commit "$source_commit"
pointer_published=1
python3 scripts/deployment_manifest.py verify-pointer \
  --path "$pointer_path" --source-commit "$source_commit"
fail_if pointer

db_hash="$(sha256sum "$db_path" | awk '{print $1}')"
runtime_stopped=0
trap - EXIT

echo "deployment source commit: $source_commit"
echo "deployment image id: $image_id"
echo "deployment image digest: $image_digest"
echo "deployment database sha256: $db_hash"
echo "deployment manifest: $manifest_path"
echo "deployment timestamp: $deployment_utc"
