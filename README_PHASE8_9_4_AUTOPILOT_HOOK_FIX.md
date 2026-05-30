# Phase 8.9.4 — Autopilot Execution Hook Fix

Эта фаза соединяет виртуальный setup tracker и TESTNET autopilot.

Важно: setup tracker и autopilot — разные вещи.

- Setup tracker виртуально наблюдает сценарий: вход, TP1, TP2, слом, истечение.
- TESTNET autopilot отправляет реальный Bybit TESTNET limit order, если включён и все safety rules проходят.

Виртуальная активация сетапа больше не считается исполнением ордера.

## Что изменилось

Когда setup tracker переводит сетап в состояние `ENTERED` / `ВХОД АКТИВИРОВАН`, бот вызывает autopilot decision engine.

Каждый активированный сетап получает запись в `autopilot_decisions`:

- `SENT` — TESTNET order отправлен на Bybit;
- `REJECTED` — order не отправлен, причина сохранена;
- `SKIPPED` — autopilot выключен.

Повторная отправка по тому же setup id блокируется.

## Debug

Команда:

```text
/autopilot_debug_last
```

Показывает последний активированный сетап и почему autopilot отправил или не отправил TESTNET order.

## Проверка ордера

Если autopilot отправил ордер, Telegram покажет:

```text
🧪 TESTNET AUTOPILOT ORDER SENT
```

Проверить локальную DB и Bybit open orders:

```text
/orders
/sync_orders
```

Если order не отправлен, Telegram покажет:

```text
❌ TESTNET AUTOPILOT REJECTED
```

с точной причиной.

## Whitelist

По умолчанию:

```text
STRICT_ALERT_TYPE_WHITELIST=false
```

Это значит, что уже активированный валидный LOW/MEDIUM сетап с `REALISTIC` execution может пройти autopilot даже если alert type не в whitelist.

HIGH, EXTREME, FAST_MOVE, AMBIGUOUS, TOO_LATE и дубликаты всё равно блокируются.

## Real trading

Real trading остаётся locked.

Mainnet order placement не добавлялся.

Если попытаться включить real до прохождения gate, бот отвечает:

```text
пошел нахуй, ждем 80%+
```
