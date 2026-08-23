# Production deployment

## Vercel + Neon

В Vercel работают FastAPI-панель и устойчивый Python Workflow. Workflow запускает
только один MTProto-скан, ждёт остаток 60-секундного интервала и повторяет цикл.
Поэтому начало соседних быстрых циклов разнесено примерно на минуту, а медленные
циклы не накладываются друг на друга. Обычный Vercel Cron для этого не используется.

Постоянные данные находятся в Neon Postgres. Telegram file-session не отправляется
в Git или build context: локально авторизованная сессия конвертируется в
`TELEGRAM_SESSION_STRING` и хранится как Sensitive production environment variable.

Обязательные production-переменные:

- `DATABASE_URL` — выдаёт подключённый Neon resource;
- `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_SESSION_STRING`;
- `GEMINI_API_KEY`;
- `TELEGRAM_NOTIFICATION_BOT_TOKEN` и `TELEGRAM_NOTIFICATION_ACCESS_KEY`;
- `ADMIN_API_KEY` — необязательный отдельный ключ; без него панель использует
  `TELEGRAM_NOTIFICATION_ACCESS_KEY`;
- `DASHBOARD_PUBLIC_URL` — production URL панели.

Схема и workflow объявлены в `pyproject.toml`. После первой production-публикации:

```bash
curl -fsS https://<production-domain>/health
curl -fsS -X POST https://<production-domain>/api/workflow/start \
  -H "X-Admin-Key: $ADMIN_API_KEY" \
  -H 'Content-Type: application/json' \
  --data '{"profile":"jetski-miami","interval_seconds":60}'
```

Статус доступен через `GET /api/workflow/status` и в шапке панели. Остановка через
панель или `POST /api/workflow/stop` выставляет durable-флаг; текущий цикл безопасно
завершается, следующий уже не запускается. Один и тот же Telegram StringSession нельзя
одновременно использовать локальным worker и production Workflow.

## Docker/PostgreSQL

## Architecture

- `app`: FastAPI dashboard, access protected by `ADMIN_API_KEY`.
- `worker`: one MTProto scanner and one Telegram notification-bot poller.
- `db`: PostgreSQL for profiles, cursors, signals, leads, subscribers and the
  transactional notification outbox.
- A reverse proxy terminates HTTPS and forwards only to `127.0.0.1:8000`.

Only one worker should use the Telegram user session. The database lease protects
MTProto jobs, while notification idempotency prevents duplicate alerts after retries.

## Configure

```bash
cp deploy/production/.env.production.example deploy/production/.env.production
chmod 600 deploy/production/.env.production
```

Fill all required values. Use different long random values for `ADMIN_API_KEY`,
`TELEGRAM_NOTIFICATION_ACCESS_KEY`, `POSTGRES_PASSWORD` and the bot token issued by
BotFather. `DASHBOARD_PUBLIC_URL` must be the final HTTPS URL.

The Telegram MTProto `.session` is not baked into the image. Before starting the
permanent worker, authorize the account directly in a temporary container; the named
volume preserves the resulting session:

```bash
docker compose \
  --env-file deploy/production/.env.production \
  -f deploy/production/docker-compose.yml \
  run --rm --no-deps --entrypoint leadfinder worker auth-qr --timeout 300
```

Never commit the session or the production env file.

Validate without printing secrets:

```bash
sh deploy/production/preflight.sh deploy/production/.env.production
```

## Start and verify

```bash
docker compose \
  --env-file deploy/production/.env.production \
  -f deploy/production/docker-compose.yml \
  up -d --build

curl -fsS http://127.0.0.1:8000/health
docker compose -f deploy/production/docker-compose.yml ps
```

Put an HTTPS reverse proxy in front of localhost port 8000. Do not publish PostgreSQL,
the Telegram session volume, logs containing message text, or the dashboard directly.

## Connect an operator to notifications

1. Open the notification bot and send `/start`.
2. Send `TELEGRAM_NOTIFICATION_ACCESS_KEY` as the next message.
3. The bot deletes the key message when Telegram permits it and subscribes that chat.
4. Use the dashboard's `Тест бота` button.

New candidate signals arrive with `Подтвердить лид` and `Отклонить` buttons. Approval
creates the lead; rejection removes the signal from review. A public group post is not
consent to call, so phone outreach still requires a separate consent event.

## Operations

- Back up PostgreSQL daily and test restores.
- Protect `.session` and env files as credentials and rotate them after exposure.
- Monitor worker exit status, `FLOOD_WAIT`, notification failures and disk usage.
- Keep the worker singleton. Scale the API separately only after moving CSV exports to
  shared object storage.
- Before processing production contacts, document retention/deletion periods and the
  applicable outreach/telemarketing rules.
