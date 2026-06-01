# Phase 9.0 — Final Pre-Real Integration Pack

Phase 9.0 собирает подготовку перед реальным рынком, но real trading остаётся закрытым Real Trading Gate.

## Что добавлено

- `/version` и `/upgrade_status` показывают текущую фазу, build, режим деплоя, TESTNET aggressive mode, autopilot и real gate.
- TESTNET aggressive autopilot пытается отправлять TESTNET лимитки по активированным сетапам, если нет технических блокеров.
- HIGH / EXTREME risk, FAST_MOVE, AMBIGUOUS, TOO_LATE и alert type вне whitelist становятся предупреждениями в TESTNET aggressive mode.
- Qty для TESTNET aggressive автоматически увеличивается до Bybit minimum, если нужно `minOrderQty` или `minNotionalValue`.
- Runtime controls позволяют менять безопасные настройки через Telegram без Render redeploy.
- Fee profile использует комиссии пользователя:
  - derivatives taker: `0.1000%`
  - derivatives maker: `0.0360%`
  - default fee mode: `maker`
- Cleanup system подготавливает удаление старых bot-сообщений до Telegram 48h лимита.
- Controlled REAL infrastructure есть только для ручного double-confirm flow.

## Важная безопасность

Real autopilot в Phase 9.0 выключен.

Реальный ордер может быть отправлен только если:

- mode MAINNET
- real gate unlocked
- API mainnet checks OK
- panic mode OFF
- runtime trading не disabled
- risk LOW/MEDIUM
- execution REALISTIC
- нет duplicate order / active position
- пройдены два Telegram подтверждения

Если gate не пройден, `/enable_real` отвечает:

```text
пошел нахуй, ждем 80%+
```

## Основные команды

Upgrade:

```text
/version
/upgrade_status
```

Runtime:

```text
/runtime_status
/set_trading_enabled true
/set_testnet true
/set_autopilot_enabled true
/set_testnet_aggressive true
```

Fees:

```text
/fees
/set_fees 0.1000 0.0360
/set_fee_mode maker
/set_fee_mode worst_case
```

Cleanup:

```text
/cleanup_status
/cleanup_on
/cleanup_now
/cleanup_off
/set_cleanup_hours 36
```

Real read-only / controlled infrastructure:

```text
/mainnet_check
/real_status
/real_orders
/real_order_status ORDER_ID
/cancel_real_order ORDER_ID
```

## TESTNET forced execution

Для проверки:

1. `/api_mode testnet`
2. `/set_trading_enabled true`
3. `/set_autopilot_enabled true`
4. `/set_testnet_aggressive true`
5. Дождаться активированного сетапа.

Ожидается:

- `🧪 TESTNET AUTOPILOT ORDER SENT`
- или `❌ TESTNET AUTOPILOT REJECTED` только с технической причиной.

Не должно быть reject только из-за:

- HIGH / EXTREME
- FAST_MOVE / AMBIGUOUS
- alert type not whitelisted
- position size below local minimum

## Cleanup

Cleanup хранит bot message id в SQLite `bot_messages`.

По умолчанию:

- cleanup выключен
- удаление после 36 часов
- не пытается удалять сообщения старше 47 часов
- защищённые setup/order сообщения не удаляются

Telegram не позволяет удалять слишком старые сообщения, поэтому cleanup надо включать заранее.

## Render

Для стабильного TESTNET API на Render лучше хранить ключи в Environment:

```text
BYBIT_API_KEY=...
BYBIT_API_SECRET=...
BYBIT_TESTNET=true
BYBIT_TRADING_ENABLED=true
```

Telegram DB credentials удобны для быстрого теста, но на free hosting могут пропасть после redeploy.
