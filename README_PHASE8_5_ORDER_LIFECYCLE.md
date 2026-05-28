# Phase 8.5 — TESTNET Order Lifecycle Manager

Эта фаза добавляет наблюдение за TESTNET ордерами после отправки лимитки.
Это всё ещё не real trading: mainnet остаётся закрыт Real Trading Gate.

## Что делает lifecycle manager

- синхронизирует TESTNET ордера с Bybit;
- видит статусы `OPEN`, `PARTIALLY_FILLED`, `FILLED`, `CANCELLED`, `REJECTED`;
- определяет открытие позиции после исполнения;
- отмечает закрытие позиции, если она исчезла;
- отменяет TESTNET лимитку, если она истекла;
- отменяет TESTNET лимитку, если связанный сетап сломан до исполнения;
- показывает детали через Telegram.

## Настройки

В `config.py`:

```python
ORDER_SYNC_ENABLED = True
ORDER_SYNC_INTERVAL_SECONDS = 30
ORDER_MAX_LIFETIME_MINUTES = 30
AUTO_CANCEL_EXPIRED_ORDERS = True
AUTO_CANCEL_INVALIDATED_BEFORE_FILL = True
AUTO_PLACE_TP_SL = False
```

По умолчанию TP/SL не выставляются автоматически.

## Команды

```text
/orders
/order_status ORDER_ID
/sync_orders
/cancel_order ORDER_ID
/cancel_all_testnet
```

`/orders` показывает локальные paper/testnet записи и открытые Bybit ордера.

`/order_status ORDER_ID` показывает локальный статус, Bybit order id, link id, последнюю синхронизацию и ошибку, если она была.

`/sync_orders` запускает синхронизацию вручную.

## Автоотмена

Бот может отменить TESTNET лимитку, если:

- ордер живёт дольше `ORDER_MAX_LIFETIME_MINUTES`;
- сетап сломан до исполнения;
- execution status стал `TOO_LATE_DO_NOT_CHASE`;
- найден дубликат по той же монете.

Автоотмена не применяется к real mainnet ордерам в этой фазе.

## TP/SL

`AUTO_PLACE_TP_SL=false` по умолчанию.

После исполнения TESTNET лимитки бот отправляет напоминание:

```text
TP/SL не выставлены автоматически.
TP1: ...
TP2: ...
Stop: ...
```

Автоматическая постановка TP/SL будет отдельной фазой Order Lifecycle Manager.

## Как тестировать на TESTNET

1. Подключи TESTNET API ключи через `/api_set` или Render env.
2. Проверь `/api_status`.
3. Проверь `/api_test`.
4. Установи `BYBIT_TRADING_ENABLED=true`.
5. Дождись сетапа и нажми `🧮 Рассчитать ордер`.
6. Нажми `✅ Поставить лимитку`.
7. Подтверди `✅ Отправить TESTNET ордер`.
8. Проверь `/orders`.
9. Проверь `/order_status ORDER_ID`.
10. Запусти `/sync_orders`.
11. Отмени через `/cancel_order ORDER_ID`.

## Real trading

Real trading остаётся заблокирован.

Для real order всё равно должны быть выполнены все условия:

- `BYBIT_TRADING_ENABLED=true`;
- режим MAINNET;
- `REAL_TRADING_UNLOCKED=true`;
- Real Trading Gate пройден;
- двойное подтверждение в Telegram.

Если gate закрыт, бот отвечает:

```text
пошел нахуй, ждем 80%+
```

## Важно

Lifecycle manager не гарантирует исполнение лимиток и не заменяет ручной контроль.
TESTNET может отличаться от реального рынка по ликвидности, очереди лимиток и задержкам.
