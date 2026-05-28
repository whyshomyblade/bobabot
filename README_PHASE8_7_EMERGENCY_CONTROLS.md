# Phase 8.7 — Emergency Controls

Эта фаза добавляет жёсткие аварийные переключатели для PAPER, TESTNET и будущего REAL режима.
Real trading по-прежнему заблокирован Real Trading Gate и не включается автоматически.

## Panic Mode

Команда:

```text
/panic
```

Что делает:

- сразу ставит `REAL_TRADING_UNLOCKED=false`;
- включает `PANIC_MODE=true`;
- блокирует создание новых PAPER / TESTNET / REAL ордеров;
- пытается отменить открытые TESTNET ордера, если API доступен;
- не выключает scanner: радар продолжает мониторить рынок.

Ответ:

```text
🚨 PANIC MODE ENABLED

Real trading: locked
New orders: blocked
TESTNET cancel request: done / failed / skipped
Scanner: still monitoring
```

## Выход из Panic Mode

Команда:

```text
/panic_off
```

Она выключает `PANIC_MODE`, но real trading остаётся locked.

Если до этого был включён runtime trading disable, его нужно снять отдельно:

```text
/enable_testnet_trading
```

## Disable Trading

Команда:

```text
/disable_trading
```

Что делает:

- включает runtime-флаг `RUNTIME_TRADING_DISABLED=true`;
- блокирует новые PAPER / TESTNET / REAL ордера;
- не удаляет API ключи;
- не ставит scanner на паузу.

## Enable Testnet Trading

Команда:

```text
/enable_testnet_trading
```

Что делает:

- снимает runtime trading disable;
- разрешает PAPER / TESTNET order flow, если остальные risk rules проходят;
- требует TESTNET mode;
- не включает MAINNET real trading.

Если включён `PANIC_MODE`, сначала используй:

```text
/panic_off
```

## Cancel All

Команды:

```text
/cancel_all_testnet
/cancel_all
/cancel_order ORDER_ID
```

`/cancel_all_testnet` отменяет только TESTNET ордера и всегда требует подтверждение:

```text
[✅ Да, отменить TESTNET]
[❌ Нет]
```

`/cancel_all`:

- в TESTNET mode ведёт себя как `/cancel_all_testnet`;
- в MAINNET mode отвечает `пошел нахуй, ждем 80%+`, если Real Gate закрыт;
- в этой фазе не отменяет MAINNET ордера автоматически.

## Safety Status

Команда:

```text
/safety_status
```

Показывает:

- panic mode;
- runtime trading disabled;
- real trading unlocked;
- `BYBIT_TRADING_ENABLED`;
- текущий режим;
- разрешены ли новые ордера;
- причину блокировки;
- daily loss lock;
- API status.

## Telegram Menu

Trading menu теперь содержит группы:

```text
📋 Orders
🔑 API
🛡 Risk
🚨 Emergency
🐺 Real Gate
```

Emergency submenu:

```text
🚨 Panic
✅ Panic Off
🔒 Disable Trading
🧪 Enable Testnet Trading
❌ Cancel All TESTNET
🛡 Safety Status
```

## Real Trading

Real trading остаётся locked.

Даже если `BYBIT_TRADING_ENABLED=true`, real order невозможен без:

- MAINNET mode;
- `REAL_TRADING_UNLOCKED=true`;
- прохождения performance gate;
- safety checks;
- двойного Telegram confirmation.

Если gate закрыт:

```text
пошел нахуй, ждем 80%+
```

## Recovery after panic

1. Проверь `/safety_status`.
2. Если нужно, отмени TESTNET ордера через `/cancel_all_testnet`.
3. Выключи panic:

```text
/panic_off
```

4. Если trading disable был включён:

```text
/enable_testnet_trading
```

5. Проверь:

```text
/orders
/api_status
/safety_status
```

Scanner всё это время продолжает мониторинг.
