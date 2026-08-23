#!/bin/sh
set -eu

environment_file=${1:-deploy/production/.env.production}

if [ ! -f "$environment_file" ]; then
  echo "Missing $environment_file"
  echo "Copy deploy/production/.env.production.example and fill every required value."
  exit 1
fi

required_keys="TELEGRAM_API_ID TELEGRAM_API_HASH GEMINI_API_KEY ADMIN_API_KEY TELEGRAM_NOTIFICATION_BOT_TOKEN TELEGRAM_NOTIFICATION_ACCESS_KEY DATABASE_URL POSTGRES_PASSWORD DASHBOARD_PUBLIC_URL"
missing_keys=""
for required_key in $required_keys; do
  if ! grep -Eq "^${required_key}=.+" "$environment_file"; then
    missing_keys="$missing_keys $required_key"
  fi
done

if [ -n "$missing_keys" ]; then
  echo "Missing or empty variables:$missing_keys"
  exit 1
fi

if grep -Eq "=(CHANGE_ME|https://leads\.example\.com)$" "$environment_file"; then
  echo "Replace CHANGE_ME and leads.example.com placeholders."
  exit 1
fi

environment_directory=$(CDPATH= cd -- "$(dirname -- "$environment_file")" && pwd)
environment_path="$environment_directory/$(basename -- "$environment_file")"

LEADFINDER_ENV_FILE="$environment_path" docker compose \
  --env-file "$environment_file" \
  -f deploy/production/docker-compose.yml \
  config --quiet

echo "Production configuration is valid."
