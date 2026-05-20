# Phase 6 — Semi-Automated Bybit Limit Order Assistant

Фаза 6 добавляет безопасного помощника для лимитных ордеров.
Бот НЕ торгует автоматически: любой ордер требует явного подтверждения кнопкой в Telegram.

## Что добавлено

- Расчёт лимитного order plan по сетапу.
- Paper mode по умолчанию.
- Bybit private client для testnet/real режима.
- Проверки риска: размер позиции, R/R, risk level, execution status, открытые ордера/позиции.
- SQLite-таблица `paper_orders`.
- Команды:
  - `/orders`
  - `/positions`
  - `/balance`
- Чистое Telegram-меню с подменю.

## Как создать Bybit API key

1. Открой Bybit.
2. Перейди в API Management.
3. Создай API key.
4. Для первых тестов используй testnet или read-only режим.
5. Не включай withdrawal permissions.
6. Для реальной отправки лимиток нужны права на derivatives trading.

Рекомендуемый безопасный старт:

- `BYBIT_TESTNET=true`
- `BYBIT_TRADING_ENABLED=false`

Так бот будет считать планы и создавать только paper orders.

## Переменные окружения

Обязательные для Telegram:

```bash
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

Опциональные для Execution Assistant:

```bash
BYBIT_API_KEY=...
BYBIT_API_SECRET=...
BYBIT_TESTNET=true
BYBIT_TRADING_ENABLED=false
ACCOUNT_RISK_PERCENT=1
MAX_POSITION_USDT=50
MIN_POSITION_USDT=5
MAX_LEVERAGE=5
DEFAULT_LEVERAGE=3
MIN_RR_TO_ALLOW_ORDER=1.5
ALLOW_HIGH_RISK_ORDERS=false
```

## Почему real trading выключен по умолчанию

`BYBIT_TRADING_ENABLED=false` защищает от случайной отправки ордеров.
В этом режиме кнопка подтверждения создаёт только запись paper order в SQLite.

Сообщение в Telegram:

```text
🧪 Paper order created. Реальный ордер не отправлен.
Paper mode: ордер НЕ отправлен на Bybit
```

## Как тестировать paper orders

1. Запусти бота:

```bash
python3 main.py
```

2. Дождись алерта с валидным Setup Scenario.
3. Нажми `🧮 Рассчитать ордер`.
4. Проверь план ордера.
5. Нажми `🧪 Paper order only`.
6. Проверь `/orders`.

Пока `BYBIT_TRADING_ENABLED=false`, реальный ордер не отправляется.

## Как убедиться, что реальные ордера не отправляются

Проверь `/config`:

```text
Execution Assistant:
• Trading enabled: false
```

Также в логах при старте должно быть:

```text
Execution assistant started in PAPER mode
```

## Как включить real/testnet позже

Сначала используй testnet:

```bash
BYBIT_TESTNET=true
BYBIT_TRADING_ENABLED=true
BYBIT_API_KEY=...
BYBIT_API_SECRET=...
```

После этого бот всё равно не будет отправлять ордера автоматически.
Нужно вручную нажать:

1. `🧮 Рассчитать ордер`
2. `✅ Поставить лимитку`

Для real режима:

```bash
BYBIT_TESTNET=false
BYBIT_TRADING_ENABLED=true
```

Включай real mode только после тестов на testnet и paper mode.

## Важные предупреждения

- Это не финансовый совет.
- Это не автотрейдинг.
- Бот не гарантирует прибыль.
- Лимитный ордер может не исполниться.
- Быстрые движения могут быть нереалистичны для ручного исполнения.
- Проверяй график вручную перед любым действием.
