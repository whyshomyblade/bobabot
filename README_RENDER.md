# Render Free Web Service: запуск Bybit Telegram Radar Bot

Этот режим нужен, если хочешь держать бота 24/7 без включённого MacBook. Render Free Web Service может засыпать, поэтому добавлен HTTP endpoint `/health`, который можно пинговать через UptimeRobot.

## 1. Подготовь репозиторий

Залей проект в GitHub.

Убедись, что в репозитории есть:

- `main.py`
- `requirements.txt`
- `Procfile`
- `Dockerfile`
- `health_server.py`

## 2. Создай Render Web Service

1. Открой Render.
2. Нажми `New`.
3. Выбери `Web Service`.
4. Подключи GitHub repo с ботом.
5. Environment: Python.
6. Start Command:

```bash
python3 main.py
```

Render сам передаст переменную `PORT`. Бот поднимет `/health` на этом порту.

## 3. Environment Variables

Добавь в Render:

```bash
TELEGRAM_BOT_TOKEN=токен_бота
TELEGRAM_CHAT_ID=твой_chat_id
HOSTING_MODE=true
DATABASE_PATH=bot_state.db
```

Опционально:

```bash
SCAN_INTERVAL_SECONDS=60
BYBIT_BASE_URL=https://api.bybit.com
```

## 4. Проверка после deploy

После деплоя открой:

```text
https://your-service.onrender.com/health
```

Должно быть:

```text
OK
```

В логах Render должна быть строка:

```text
Health server started on port ...
```

Также проверь Telegram:

```text
/start
/debug_state
```

## 5. UptimeRobot keep-alive

1. Открой UptimeRobot.
2. Создай новый monitor.
3. Тип: `HTTP(s)`.
4. URL:

```text
https://your-service.onrender.com/health
```

5. Interval: `5 minutes`.
6. Сохрани monitor.

Это будет регулярно будить Render Free Web Service.

## 6. Важное про Bybit

Если Bybit блокирует регион Render, в логах будет:

```text
Bybit API is blocked from this hosting provider/region.
```

В этом случае Telegram-команды могут работать, но сканер не сможет получать рыночные данные. Если есть выбор региона, лучше выбирать Европу, например Frankfurt/Germany.

## 7. SQLite и бэкапы

База по умолчанию:

```text
bot_state.db
```

Telegram-команда:

```text
/backup_db
```

создаёт файл:

```text
backup_bot_state_YYYYMMDD_HHMMSS.db
```

На бесплатных PaaS локальная файловая система может быть временной. Для долговременного хранения лучше подключить persistent disk, если тариф/платформа это поддерживает.
