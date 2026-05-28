# Phase 8.8 — Real Mode Dry Run

Эта фаза добавляет безопасную MAINNET read-only проверку.
Бот может читать данные Bybit mainnet, но не отправляет реальные ордера.

## Что такое Real Dry Run

Real Dry Run нужен, чтобы проверить:

- подключение mainnet API;
- баланс;
- позиции;
- открытые ордера;
- режим аккаунта;
- базовые permission checks;
- как выглядел бы расчёт real order без отправки.

Это не включает real trading.

## Настройки

В `config.py`:

```python
REAL_DRY_RUN_ENABLED = True
MAINNET_READ_ONLY_MODE = True
```

`MAINNET_READ_ONLY_MODE=true` блокирует MAINNET write endpoints даже если кто-то случайно включит `BYBIT_TRADING_ENABLED=true`.

## Команды

```text
/dry_run_status
/mainnet_check
/dry_run_order SYMBOL SIDE ENTRY STOP TP1 TP2
```

Пример:

```text
/dry_run_order BTCUSDT LONG 100000 99000 101000 102000
```

## MAINNET check

Команда:

```text
/mainnet_check
```

Показывает:

- API mode;
- real trading lock;
- balance read;
- positions read;
- orders read;
- trade permission status;
- effective real trading.

Важно: команда не размещает ордер и не вызывает write endpoints.

## Dry Run Order

Команда:

```text
/dry_run_order SYMBOL SIDE ENTRY STOP TP1 TP2
```

Она считает:

- qty;
- position size;
- risk;
- R/R;
- fee/slippage estimate;
- reject reasons.

Ордер не сохраняется и не отправляется на Bybit.

## Почему real orders не отправляются

В этой фазе MAINNET разрешён только для чтения:

- balance;
- positions;
- open orders.

Запрещены:

- create order;
- cancel mainnet order;
- modify mainnet order;
- TP/SL на mainnet.

Если попытаться отправить real order при закрытом gate, бот отвечает:

```text
пошел нахуй, ждем 80%+
```

После этого бот дополнительно напоминает:

```text
Real trading locked.
Use /real_status.
```

## Как проверить готовность

1. Переключи API mode:

```text
/api_mode mainnet
```

2. Проверь:

```text
/api_status
/mainnet_check
/dry_run_status
```

3. Проверь теоретический расчёт:

```text
/dry_run_order BTCUSDT LONG 100000 99000 101000 102000
```

## Real Gate

Real Trading Gate остаётся обязательным.

Даже если:

- mainnet API подключён;
- чтение работает;
- `BYBIT_TRADING_ENABLED=true`;

real order placement всё равно заблокирован, пока:

- статистика не прошла требования;
- `/enable_real` не прошёл двойное подтверждение;
- `MAINNET_READ_ONLY_MODE=false`.

По умолчанию `MAINNET_READ_ONLY_MODE=true`, поэтому эта фаза безопасна для mainnet validation.
