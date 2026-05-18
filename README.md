# Telegram Market Radar Bot для Bybit USDT Perpetual

Phase 2 версия: бот мониторит публичные данные Bybit USDT perpetual futures и отправляет Telegram-алерт, когда одновременно растут Open Interest, quote-volume activity и заметно двигается цена. Когда базовый radar alert уже найден, бот дополнительно запрашивает 1m klines и добавляет RSI, MACD, EMA trend и ATR context.

Бот не использует AI, не торгует, не открывает сделки и не является торговым сигналом.

## Что мониторится

- Активные Bybit linear USDT perpetual symbols.
- Last price.
- 24h volume.
- 24h turnover in USDT, если Bybit отдает `turnover24h`.
- 24h price change percent.
- Open Interest.
- Funding rate, если Bybit отдает это поле.
- Timestamp.

История хранится в `state.json`, окно истории: последние 30 минут.

## Фильтр symbols

Бот загружает активные USDT perpetual symbols, получает tickers и оставляет только ликвидные инструменты:

- `turnover24h >= 5,000,000 USDT`.
- Symbol не содержит исключенные keywords: `1000`, `10000`.
- Symbols сортируются по `turnover24h` по убыванию.
- Мониторятся top 300 symbols.

## Логика алерта

Telegram alert отправляется, если по symbol одновременно:

- Open Interest вырос минимум на 4% за 15 минут.
- Volume spike минимум 2x относительно среднего прироста quote-volume между сканами в истории. Если Bybit отдает `turnover24h`, используется он; иначе fallback на `volume24h`.
- Цена изменилась минимум на 1% за 15 минут.
- По этой монете не было алерта последние 30 минут.

После этого бот запрашивает klines только для symbols, которые уже прошли radar filters:

```text
GET /v5/market/kline
category=linear
interval=1
limit=100
```

Если kline fetch или расчет индикаторов не сработал, обычный alert все равно отправляется с `Indicators: unavailable`.

## Indicator context

- `RSI(14)`: overbought, oversold, neutral или normal.
- `EMA Trend`: bullish, bearish или neutral по EMA20/EMA50 и last price.
- `MACD Histogram`: bullish momentum, bearish momentum или weak momentum.
- `ATR`: absolute ATR и ATR percent с volatility note.

## Пороги

Пороги меняются в `config.py`:

```python
OI_THRESHOLD_PERCENT = 4
PRICE_CHANGE_THRESHOLD_PERCENT = 1
VOLUME_SPIKE_MULTIPLIER = 2
ALERT_COOLDOWN_MINUTES = 30
SCAN_INTERVAL_SECONDS = 60
HISTORY_WINDOW_MINUTES = 30
MIN_24H_TURNOVER_USDT = 5_000_000
MAX_SYMBOLS_TO_MONITOR = 300
ENABLE_INDICATORS = True
RSI_PERIOD = 14
EMA_FAST_PERIOD = 20
EMA_SLOW_PERIOD = 50
MACD_FAST_PERIOD = 12
MACD_SLOW_PERIOD = 26
MACD_SIGNAL_PERIOD = 9
ATR_PERIOD = 14
KLINE_INTERVAL = "1"
KLINE_LIMIT = 100
```

## Telegram команды

- `/start` - проверка, что бот жив.
- `/status` - статус мониторинга, количество monitored symbols, min 24h turnover filter, время последнего скана, alerts today.
- `/config` - текущие thresholds, limits и indicator settings.
- `/last` - последние 10 алертов: time, symbol, price change, OI change, volume spike.
- `/pause` - поставить мониторинг на паузу.
- `/resume` - возобновить мониторинг.
- `/top` - топ-10 монет по росту Open Interest за последние 15 минут.

## Создание Telegram bot через BotFather

1. Откройте Telegram и найдите `@BotFather`.
2. Отправьте команду `/newbot`.
3. Укажите имя бота и username, который заканчивается на `bot`.
4. BotFather выдаст token вида:

```text
123456789:AA...your_token...xyz
```

Это значение нужно сохранить как `TELEGRAM_BOT_TOKEN`.

## Как получить TELEGRAM_CHAT_ID

Для личного чата:

1. Напишите любое сообщение вашему новому боту.
2. Откройте в браузере:

```text
https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/getUpdates
```

3. Найдите поле `message.chat.id`.
4. Это значение сохраните как `TELEGRAM_CHAT_ID`.

Для группы:

1. Добавьте бота в группу.
2. Напишите в группе любое сообщение.
3. Откройте тот же `getUpdates` URL.
4. Возьмите `message.chat.id`. У групп chat id часто отрицательный.

## Replit Secrets

В Replit откройте `Tools -> Secrets` и добавьте:

```text
TELEGRAM_BOT_TOKEN=<token from BotFather>
TELEGRAM_CHAT_ID=<your chat id>
```

Не храните реальные token/chat id в коде.

## Запуск

Установите зависимости:

```bash
pip install -r requirements.txt
```

Запустите:

```bash
python main.py
```

## Локальный запуск на Mac

Можно временно экспортировать переменные окружения:

```bash
export TELEGRAM_BOT_TOKEN="123456789:replace"
export TELEGRAM_CHAT_ID="123456789"
python main.py
```

Файл `.env.example` показывает нужные имена переменных, но реальные секреты в репозиторий добавлять нельзя.

## Важное предупреждение

Этот бот только показывает всплески market activity по публичным данным Bybit и добавляет технический контекст. Это не финансовый совет, не прогноз цены и не торговый сигнал. Перед любыми решениями проверяйте график и риск вручную.
# bobabot
# bobabot
