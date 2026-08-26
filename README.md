# Demand Leadfinder

Сервис для поиска публичного спроса в Telegram и подключения разрешённых
источников из форумов, сайтов, Instagram и других платформ. Telegram-коннектор
использует MTProto от имени отдельного рабочего аккаунта, а не Bot API.

## Что умеет сервис

- ищет публичные группы и сообщения по многоязычным запросам;
- импортирует группы, уже доступные авторизованному аккаунту;
- ведёт отдельный allowlist: мониторятся только явно одобренные источники;
- хранит курсор каждого источника и не обрабатывает одно сообщение повторно;
- обязательно проверяет сообщения через Gemini Embedding 2 и Gemini LLM;
- ограничивает каждый профиль одним языком, набором языков или готовой языковой группой;
- принимает нормализованные сообщения от внешних коннекторов через `/api/ingest`;
- сохраняет релевантное сообщение сначала как `signal`, затем связывает его с лидом;
- извлекает доступные в самом сообщении локацию, намерение, дату и размер группы;
- сохраняет Telegram ID, username, отображаемое имя, текст, дату и прямую ссылку на сообщение;
- выгружает источники, сигналы и лиды в CSV;
- предоставляет JSON API, Swagger и локальную операторскую панель;
- запускается вручную, постоянным worker-процессом или через Docker Compose;
- не вступает в группы, не отправляет сообщения и не запускает массовые контакты.

## Поток данных

```text
search profile
    -> discovery queries
    -> candidate groups + evidence
    -> operator approves source
    -> cursor-based monitoring
    -> rules -> Gemini embeddings -> Gemini structured LLM decision
    -> signal
    -> provisional lead
    -> human review + direct source-message link
    -> CSV / CRM handoff
```

Сервис не пробивает и не сохраняет номера. Оператор переходит к исходному
сообщению по permalink в панели, Telegram-боте или CSV.

## Быстрый локальный запуск

Требуются Python 3.12+ и `uv`.

```bash
uv sync --extra dev
uv run leadfinder init-db
uv run leadfinder seed-profile
uv run leadfinder telegram-status
uv run leadfinder serve
```

Открыть:

- панель: <http://127.0.0.1:8000>
- API: <http://127.0.0.1:8000/docs>
- healthcheck: <http://127.0.0.1:8000/health>

До запуска в `.env` обязательны `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` и
`GEMINI_API_KEY`. Секреты находятся в `.env`, session-файл — в `.sessions/`; обе директории
исключены из Git и Docker build context.

## Первый рабочий проход

1. Импортировать группы, в которых уже состоит аккаунт:

   ```bash
   uv run leadfinder sync-dialogs --profile jetski-miami
   ```

2. Расширить каталог публичным поиском:

   ```bash
   uv run leadfinder discover --profile jetski-miami --max-queries 10 --per-query 20
   ```

   Известную публичную группу можно добавить вручную:

   ```bash
   uv run leadfinder add-source @group_username --profile jetski-miami
   ```

3. Открыть панель и одобрить релевантные источники. То же самое доступно в CLI:

   ```bash
   uv run leadfinder list-sources
   uv run leadfinder set-source-status 1 approved
   ```

4. Отсканировать новые сообщения:

   ```bash
   uv run leadfinder monitor --profile jetski-miami
   ```

   Для поиска в истории одобренных групп:

   ```bash
   uv run leadfinder backfill --profile jetski-miami --lookback-days 365
   ```

5. Для постоянной работы:

   ```bash
   uv run leadfinder worker --profile jetski-miami
   ```

Worker сканирует allowlist каждые `MONITOR_INTERVAL_SECONDS` и периодически
повторяет discovery. Межпроцессная lease-блокировка не позволяет панели и worker
одновременно использовать один session-файл.

### Постоянное сканирование на macOS

Для локальной вертикали jetski используется LaunchAgent
`com.leadfinder.worker`: он запускает worker после входа пользователя в macOS,
перезапускает его после сбоя и каждую минуту сканирует только новые сообщения
из одобренных источников. Широкий discovery в постоянном цикле отключён; каталог
источников расширяется отдельным управляемым проходом.

Панель показывает количество участников, которое Telegram отдаёт для каждой
группы. Это значение может отсутствовать у закрытых или недоступных источников.

Версионируемый plist находится в `deploy/launchd/com.leadfinder.worker.plist`.
Рабочая копия устанавливается в `~/Library/LaunchAgents/`. Из-за системных
ограничений macOS на фоновые процессы из `Documents` runtime находится в
`~/Library/Application Support/Leadfinder`, а база и Telegram session доступны
проекту через символические ссылки. Состояние и логи:

```bash
launchctl print gui/$(id -u)/com.leadfinder.worker
tail -f "$HOME/Library/Application Support/Leadfinder/logs/worker.log" \
  "$HOME/Library/Application Support/Leadfinder/logs/worker.err.log"
```

## Авторизация Telegram по QR

`TELEGRAM_API_ID` и `TELEGRAM_API_HASH` должны быть в `.env`.

```bash
uv run leadfinder auth-qr --timeout 300
```

Сканировать нужно через **Telegram → Настройки → Устройства → Подключить
устройство**. Если у аккаунта включена двухэтапная проверка, пароль вводится
только в локальном терминале и не сохраняется сервисом.

## Уведомления через Telegram-бота

Локальные секреты уведомлений находятся в `.env.notifications`:

```dotenv
TELEGRAM_NOTIFICATION_BOT_TOKEN=
TELEGRAM_NOTIFICATION_ACCESS_KEY=
```

Первое значение — токен от BotFather, второе — длинный случайный ключ. По умолчанию
этот же ключ открывает админ-панель. Если нужен отдельный ключ панели, задай
`ADMIN_API_KEY`. Подключение оператора:

1. Открыть бота и отправить `/start`.
2. Отправить ключ доступа отдельным сообщением.
3. Бот удалит сообщение с ключом, если Telegram разрешит это для данного чата.
4. Бот ответит `✅ Подключено`. Повторная команда `/start` или команда `/status`
   покажет `✅ Уже подключено`.
5. В панели нажать `Тест бота`.

После первого заполнения или смены этих переменных перезапусти worker:

```bash
launchctl kickstart -k gui/$(id -u)/com.leadfinder.worker
```

Система сохраняет потенциальный сигнал до окончательного решения человека и
транзакционно создаёт запись в `notification_outbox`. В уведомлении есть кнопки
`Подтвердить лид` и `Отклонить`. Подтверждение создаёт карточку лида, отклонение
закрывает сигнал. По умолчанию `AUTO_CREATE_LEADS=false`, поэтому классификатор не
подменяет решение оператора.

Перед постановкой уведомления применяется независимый от LLM фильтр свежести:
сообщения до 7 дней считаются горячими, до 30 дней — актуальными, а сообщения
возрастом 31–90 дней допускаются только при явно извлечённой будущей дате события.
Сообщения старше 90 дней и сообщения без даты могут храниться как исторические
сигналы, но не создают автоматический лид и не отправляются в бот. Исторический
`backfill` по умолчанию вообще не ставит уведомления; включить их можно только
явно через `BACKFILL_NOTIFICATIONS_ENABLED=true`, при этом возрастной фильтр всё
равно остаётся обязательным.

Токен и ключ не записываются в SQLite и не возвращаются API. После пяти неверных
попыток чат блокируется на 15 минут. Команда `/stop` отключает уведомления.

Вместо QR можно авторизоваться по номеру и одноразовому коду:

```bash
uv run leadfinder auth-code
```

Номер телефона, код Telegram и возможный пароль 2FA запрашиваются интерактивно
в локальном терминале и не сохраняются в `.env`, базе или логах. После успешной
авторизации сохраняется только защищаемый Telegram session-файл в `.sessions/`.

## Обязательная Gemini-классификация

Каждое потенциально релевантное сообщение проходит гибридный конвейер:

1. Дешёвые правила отсеивают явную рекламу и находят прямые совпадения.
2. `gemini-embedding-2` помогает находить смысловые формулировки без точных слов.
3. `gemini-3.7-flash` вызывается последним и возвращает решение по JSON-схеме.

Настроить `.env`:

```dotenv
GEMINI_API_KEY=...
GEMINI_EMBEDDING_MODEL=gemini-embedding-2
GEMINI_EMBEDDING_DIMENSIONS=768
GEMINI_LLM_MODEL=gemini-3.7-flash
```

Без ключа классификация, discovery, monitor и backfill не запускаются. Текст
кандидатного публичного сообщения передаётся Google для embeddings и финальной
классификации; секреты и Telegram session-файл не передаются.

## Другие площадки

Внешние коннекторы отправляют сообщения в общий формат через `POST /api/ingest`.
Поддержаны метки платформ: `instagram`, `forum`, `website`, `whatsapp`,
`facebook`, `reddit`, `discord`, `telegram`, `other`. Новый источник создаётся
как `candidate`; пока оператор не одобрит его в панели, текст не классифицируется.

```json
{
  "profile": "jetski-miami",
  "platform": "forum",
  "source_external_id": "miami-travel/water-activities",
  "source_title": "Miami Travel Forum",
  "source_url": "https://example.com/miami",
  "message_external_id": "post-42",
  "message_url": "https://example.com/miami/post-42",
  "text": "Where can I rent two jet skis in Miami tomorrow?",
  "language": "en",
  "author_external_id": "member-9",
  "author_username": "traveler9"
}
```

Коннектор обязан использовать официальный API, разрешённый экспорт, RSS или
допустимый обход публичных страниц. Закрытые аккаунты, приватные сообщения и
обход защит не входят в систему.

## Универсальные поисковые профили

Профиль можно создать в панели или импортировать из JSON:

```json
{
  "slug": "boat-rental-miami",
  "name": "Boat rental Miami",
  "description": "People looking to rent a boat in Miami",
  "services": ["boat rental", "rent a boat"],
  "locations": ["Miami", "Miami Beach"],
  "intents": ["rent", "book", "recommend"],
  "languages": ["en", "es"],
  "negative_terms": ["we offer", "our fleet", "for sale"],
  "positive_examples": ["Where can I rent a boat in Miami tomorrow?"],
  "classifier_prompt": "Find prospective customers seeking a boat rental in Miami. Reject ads."
}
```

```bash
uv run leadfinder import-profile profile.json --max-queries 120
```

`languages` принимает ISO-коды (`en`, `es`, `ru`, `pt`) и готовые группы:

- `group:miami` — английский, испанский, португальский и русский;
- `group:slavic` — славянские языки;
- `group:romance` — романские языки;
- `group:germanic`, `group:turkic`, `group:east-asian`, `group:middle-east`.

Можно перечислить собственный набор, например `["ru"]`. Пустой
список или `any` означает любой язык. Фильтр меняет языковые шаблоны поиска,
добавляется в embedding-запрос и проверяется финальной Gemini-классификацией.
Gemini сохраняет доминирующий язык сообщения как ISO-код в сигнале, лиде и CSV;
сообщение на языке вне профиля не становится лидом. Для профиля jetski целевая
аудитория строго русскоязычная: английские названия услуги и мест разрешены в
русской фразе, а полностью английское сообщение принимается только из источника,
который вручную отмечен как русскоязычный. Внешний коннектор может передать
необязательную языковую подсказку `language`.

Перед Gemini действует вертикальный prefilter: в сообщении должен присутствовать
явный вариант названия услуги (`jetski`, `гидроцикл`, `Sea-Doo`, `гидрик`,
`водный скутер` и т. п.). После этого embedding и Gemini обязательны и отсеивают
рекламу, продавцов и нерелевантные упоминания.

Список групп, куда нужен ручной вход или заявка, хранится в
`exports/telegram_manual_join_list.csv`. После вступления нужно выполнить
`uv run leadfinder sync-dialogs --profile jetski-miami` и одобрить найденную
группу в панели.

## CSV

```bash
uv run leadfinder export-sources exports/sources.csv
uv run leadfinder export-signals exports/signals.csv
uv run leadfinder export-leads exports/leads.csv
```

CSV лидов содержит исходное сообщение, дату и permalink. Телефоны в эту
выгрузку не входят.

## Docker Compose

Локальная SQLite подходит для одного процесса. Compose использует PostgreSQL,
запускает API и worker:

```bash
docker compose up --build
```

Панель публикуется только на `127.0.0.1:8000`. Session-файл монтируется из
`.sessions/`. Gemini HTTP-клиент входит в обычную установку.

Перед публикацией API за пределами localhost обязательно задать сильный
`ADMIN_API_KEY`, HTTPS и сетевые ограничения.

Production-варианты для Vercel + Neon и Docker/PostgreSQL, проверка обязательных
секретов и инструкция находятся в [DEPLOYMENT.md](DEPLOYMENT.md).

## Основные команды

```bash
uv run leadfinder --help
uv run leadfinder stats
uv run leadfinder list-queries
uv run leadfinder list-sources
uv run leadfinder classify-text "Where can I rent two jet skis in Miami tomorrow?"
```

## Проверка разработки

```bash
uv run ruff check .
uv run pytest
docker compose config
```

## Ограничения

- MTProto видит только то, что доступно авторизованному аккаунту.
- Автоматическое вступление в группы намеренно отсутствует.
- Внешние сервисы пробива номеров не подключаются.
