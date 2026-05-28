# Phase 8.9 — Controlled TESTNET Autopilot

Эта фаза добавляет строго ограниченный autopilot только для Bybit TESTNET.
Это тренировочный режим перед Phase 9, не real trading.

## Что делает autopilot

Если включён, бот может автоматически отправить TESTNET limit order после нового radar setup alert.
Перед отправкой проходит whitelist и safety checks.

Autopilot не работает в MAINNET.
Autopilot не обходит Real Trading Gate.
Autopilot не отправляет реальные ордера.

## Настройки

В `config.py`:

```python
TESTNET_AUTOPILOT_ENABLED = False
TESTNET_AUTOPILOT_REQUIRE_REALISTIC = True
TESTNET_AUTOPILOT_ALLOW_HIGH_RISK = False
TESTNET_AUTOPILOT_ALLOW_FAST_MOVE = False
TESTNET_AUTOPILOT_ALLOW_AMBIGUOUS = False
TESTNET_AUTOPILOT_MAX_ORDERS_PER_DAY = 3
TESTNET_AUTOPILOT_MAX_ACTIVE_ORDERS = 2
TESTNET_AUTOPILOT_MIN_RR = 1.5
TESTNET_AUTOPILOT_ALLOWED_ALERT_TYPES = [
    "Clean Bullish Momentum",
    "Clean Bearish Pressure",
    "Possible Short Squeeze",
    "Possible Long Squeeze",
]
```

По умолчанию autopilot выключен.

## Команды

```text
/autopilot_status
/autopilot_on
/autopilot_off
/autopilot_rules
/autopilot_journal
```

## Как включить

Нужно:

1. API mode TESTNET:

```text
/api_mode testnet
```

2. `BYBIT_TRADING_ENABLED=true`
3. TESTNET API keys подключены.
4. `/api_test` проходит balance / positions / orders.
5. Panic mode выключен.
6. Runtime trading не disabled.

Потом:

```text
/autopilot_on
```

Ответ:

```text
🧪 TESTNET Autopilot enabled.
Real trading remains locked.
```

## Whitelist rules

Autopilot отправляет TESTNET order только если:

- API mode TESTNET;
- `BYBIT_TRADING_ENABLED=true`;
- API test OK;
- alert type в whitelist;
- risk не EXTREME;
- HIGH risk запрещён, если `TESTNET_AUTOPILOT_ALLOW_HIGH_RISK=false`;
- execution status не `TOO_LATE_DO_NOT_CHASE`;
- FAST_MOVE / AMBIGUOUS запрещены по умолчанию;
- нет дубликата ордера по символу;
- нет активной позиции по символу;
- дневной лимит не превышен;
- max active TESTNET orders не превышен;
- R/R >= `TESTNET_AUTOPILOT_MIN_RR`.

## Journal

Команда:

```text
/autopilot_journal
```

Показывает последние решения:

- `SENT`;
- `REJECTED`;
- symbol;
- side;
- reason;
- linked order id.

Rejections сохраняются, но бот не спамит Telegram каждым отказом.

## Почему это TESTNET only

Autopilot перед отправкой проверяет TESTNET mode.
Если mode MAINNET:

```text
TESTNET Autopilot works only in TESTNET mode.
```

Если MAINNET + trading enabled + real locked:

```text
пошел нахуй, ждем 80%+
```

## Real trading

Real trading остаётся locked.

Для real market всё ещё нужны:

- Real Trading Gate;
- статистика;
- двойное подтверждение;
- отключённый MAINNET read-only mode;
- safety checks.

Phase 8.9 не включает real trading и не размещает mainnet orders.
