# Bybit Telegram Radar Bot: хостинг 24/7

Этот бот можно запускать локально на Mac или как background worker на дешёвом/free cloud hosting.

Важно: бот не торгует, не открывает сделки и не использует приватный Bybit API. Он только отслеживает публичные данные и виртуальные сценарии.

## Локальный запуск

1. Установи зависимости:

```bash
pip install -r requirements.txt
```

2. Задай переменные окружения:

```bash
export TELEGRAM_BOT_TOKEN="токен_бота"
export TELEGRAM_CHAT_ID="твой_chat_id"
```

3. Запусти:

```bash
python3 main.py
```

## Переменные окружения

Обязательные:

```bash
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
```

Опциональные:

```bash
BYBIT_BASE_URL=https://api.bybit.com
SCAN_INTERVAL_SECONDS=60
DATABASE_PATH=bot_state.db
HOSTING_MODE=true
```

Если `HOSTING_MODE=true`, бот пишет в лог `Hosting mode enabled`, использует SQLite базу из `DATABASE_PATH` и подходит для worker/background запуска.

## SQLite база

По умолчанию используется файл:

```bash
bot_state.db
```

В нём хранятся:

- runtime state
- история алертов
- активные сетапы
- журнал сетапов

Если рядом есть старый `state.json`, бот при первом запуске импортирует данные в SQLite и создаёт резервную копию:

```bash
state.backup.json
```

Старый `state.json` автоматически не удаляется.

## Деплой как worker

Для Render/Railway/Fly/других PaaS используй worker/background process, а не web service.

Команда запуска:

```bash
python3 main.py
```

Или через `Procfile`:

```text
worker: python3 main.py
```

Для Docker:

```bash
docker build -t bybit-radar-bot .
docker run --env TELEGRAM_BOT_TOKEN=... --env TELEGRAM_CHAT_ID=... bybit-radar-bot
```

## Важное про Replit и регионы

Replit и некоторые free hosting провайдеры могут быть заблокированы Bybit или получать 403 от API.

Если платформа даёт выбор региона, лучше выбрать Европу, например Frankfurt/Germany.

При старте бот проверяет Bybit `/v5/market/time`. Если регион заблокирован, в логах будет:

```text
Bybit API is blocked from this hosting provider/region.
```

Telegram-команды при этом могут продолжать работать, но сканер не сможет получать рыночные данные до смены региона/провайдера.

## Как смотреть логи

В панели хостинга открой Logs/Runtime logs.

Нормальный старт выглядит примерно так:

```text
Starting Bybit Futures Radar
Hosting mode enabled
Bybit connectivity check passed
Monitoring ... symbols after filters
```

## Проверка Telegram

После запуска отправь боту:

```text
/start
```

Должна появиться клавиатура с кнопками. Проверь:

- `📊 Status`
- `📒 Сетапы`
- `📘 Журнал`
- `📈 Стата`

Для диагностики:

```text
/debug_state
```

## Проверка Bybit connectivity

Если бот не присылает алерты, смотри логи на старте.

Также можно временно снизить пороги в `config.py`, но не оставляй тестовые значения на проде.

## Бэкап базы

Через Telegram:

```text
/backup_db
```

Бот создаст файл вида:

```text
backup_bot_state_YYYYMMDD_HHMMSS.db
```

Также можно вручную скачать/скопировать файл `bot_state.db` из persistent storage хостинга.

## Rollback из backup

1. Останови worker.
2. Сохрани текущий файл `bot_state.db` отдельно.
3. Переименуй нужный backup:

```bash
cp backup_bot_state_YYYYMMDD_HHMMSS.db bot_state.db
```

4. Запусти worker снова.
