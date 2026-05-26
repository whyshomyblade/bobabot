# Phase 8.3-8.4 — Fast API Setup + Bybit TESTNET Orders

Эта фаза добавляет быстрый ввод Bybit API через Telegram и отправку подтверждённых TESTNET limit orders.

Бот всё ещё НЕ торгует автоматически.
Mainnet real trading остаётся закрыт Real Trading Gate.

## Быстрый ввод API через Telegram

Команда:

```text
/api_set
```

Бот попросит отправить ключи одним сообщением:

```text
API_KEY|API_SECRET
```

После получения бот:

- попробует удалить сообщение с ключами;
- не покажет secret обратно;
- покажет только masked key;
- сохранит ключи в SQLite;
- запустит `/api_test`.

Если ключи введены через Telegram, `/api_status` покажет предупреждение:

```text
⚠️ API secret хранится локально в базе. Это быстрый режим, не максимальная безопасность.
```

Для production безопаснее использовать Render Environment variables.

## Приоритет ключей

Бот использует ключи в таком порядке:

1. `BYBIT_API_KEY` / `BYBIT_API_SECRET` из Environment.
2. Active credentials из SQLite, добавленные через `/api_set`.
3. Нет ключей.

`/api_status` показывает:

```text
Credential source: ENV / TELEGRAM_DB / NONE
```

## Очистка ключей

```text
/api_clear
```

Команда отключает Telegram-stored credentials.
Environment variables не трогает.

## Переключение режима

```text
/api_mode testnet
/api_mode mainnet
```

Testnet:

```text
✅ API mode set to TESTNET
```

Mainnet:

```text
⚠️ API mode set to MAINNET.
Real trading is still LOCKED.
```

Переключение mainnet НЕ включает real trading.

## Как создать Bybit testnet API key

1. Открой Bybit Testnet.
2. Создай API key.
3. Разреши чтение баланса, позиций и ордеров.
4. Для тестовых лимиток разреши order create/cancel.
5. Не включай вывод средств.

## Как включить TESTNET отправку ордеров

Для Render или Mac:

```text
BYBIT_TESTNET=true
BYBIT_TRADING_ENABLED=true
```

Если `BYBIT_TRADING_ENABLED=false`, бот создаёт только paper order.

## TESTNET order flow

1. Дождись setup alert.
2. Нажми `🧮 Рассчитать ордер`.
3. Проверь order plan.
4. Нажми `✅ Поставить лимитку`.
5. Бот покажет TESTNET confirmation.
6. Нажми `✅ Отправить TESTNET ордер`.

Только после второго подтверждения бот отправит limit order на Bybit TESTNET.

Успешный ответ:

```text
🧪 TESTNET order sent. Это НЕ real market.
```

## TP/SL

В этой фазе TP/SL не выставляются автоматически.

После отправки лимитки бот пишет:

```text
⚠️ TP/SL пока не выставляются автоматически.
Это будет отдельная фаза Order Lifecycle Manager.
```

Настройка:

```text
AUTO_PLACE_TP_SL=false
```

## Отмена TESTNET ордеров

Отменить один ордер:

```text
/cancel_order ORDER_ID
```

Можно передать локальный id, exchange order id или orderLinkId.

Отменить все testnet orders:

```text
/cancel_all_testnet
```

Команда требует подтверждение кнопкой.

## API команды

```text
/api_status
/api_test
/api_help
/api_set
/api_clear
/api_mode testnet
/api_mode mainnet
/api_reload
```

Меню:

```text
📒 Сетапы → 💰 Trading → 🔑 API
```

## Real trading остаётся закрыт

Для real mainnet orders одновременно нужны:

- `BYBIT_TRADING_ENABLED=true`;
- mode `MAINNET`;
- `REAL_TRADING_UNLOCKED=true`;
- double confirmation;
- safety filters.

Если mainnet включён, но статистика не прошла gate, бот отвечает:

```text
пошел нахуй, ждем 80%+
```

Это значит: real trading запрещён, пока нет 80%+ win rate, 100 counted closed setups и нужного Net R.

## Что тестировать без ключей

```text
/api_status
/api_test
/api_help
/api_set
```

Проверь malformed input:

```text
abc|123
```

Ожидаемо: бот отклонит формат и не покажет secret.

## Что тестировать с testnet ключами

```text
/api_set
/api_status
/api_test
/api_mode testnet
```

Потом:

1. дождаться setup;
2. `🧮 Рассчитать ордер`;
3. `✅ Поставить лимитку`;
4. `✅ Отправить TESTNET ордер`;
5. `/orders`;
6. `/cancel_order ORDER_ID`;
7. `/cancel_all_testnet`.

## Безопасность

- API secret не выводится в Telegram.
- API key маскируется.
- Secret из `/api_set` хранится в SQLite plaintext, поэтому это быстрый режим, не максимальная безопасность.
- Для Render production лучше использовать Environment variables.
- Real trading не включается автоматически.
