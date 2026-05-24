# Phase 8.2 — Bybit API Control Panel и Real Trading Gate

Phase 8.2 добавляет Telegram-панель контроля Bybit API и жёсткий замок для real trading.

Бот по-прежнему НЕ торгует автоматически.
Любой testnet или real ордер требует явного Telegram-подтверждения.
Real trading заблокирован по умолчанию и не включается одним env-переключателем.

## Как создать Bybit API key

1. Открой Bybit API Management.
2. Создай ключ сначала для TESTNET.
3. Разреши только нужные права:
   - read account balance;
   - read positions;
   - read open orders;
   - create/cancel orders только для testnet-проверки.
4. Не включай withdrawal permissions.
5. Не отправляй API secret в обычные чаты.

## Render env vars

Render → service → Environment:

```text
BYBIT_API_KEY=...
BYBIT_API_SECRET=...
BYBIT_TESTNET=true
BYBIT_TRADING_ENABLED=false
```

После изменения env:

```text
Manual Deploy → Clear build cache & deploy
```

`/api_reload` может перечитать текущие переменные процесса, но Render обычно требует redeploy.

## Локальный Mac запуск

```bash
export BYBIT_API_KEY="..."
export BYBIT_API_SECRET="..."
export BYBIT_TESTNET=true
export BYBIT_TRADING_ENABLED=false
python3 main.py
```

## Команды API

```text
/api_status
/api_test
/api_help
/api_reload
/real_status
/enable_real
/disable_real
/panic
```

Они доступны в меню:

```text
📒 Сетапы → 💰 Trading
```

## Почему BYBIT_TRADING_ENABLED=false по умолчанию

Пока `BYBIT_TRADING_ENABLED=false`, бот создаёт только paper order.
Он не отправляет ордер на Bybit.

Это нужно, чтобы проверить:

- генерацию setup;
- расчёт лимитного ордера;
- журнал;
- статистику;
- backtest;
- testnet API.

## Testnet flow

Для testnet:

```text
BYBIT_TESTNET=true
BYBIT_TRADING_ENABLED=true
```

После alert:

1. Нажать `🧮 Рассчитать ордер`.
2. Проверить план.
3. Нажать `✅ Поставить лимитку`.

Telegram покажет:

```text
🧪 TESTNET order sent. Это НЕ real market.
```

## Real trading lock

Даже если поставить:

```text
BYBIT_TESTNET=false
BYBIT_TRADING_ENABLED=true
```

real orders всё равно заблокированы, пока `REAL_TRADING_UNLOCKED=false`.

Этот флаг хранится в SQLite/runtime state, а не только в env.

## Требования для /enable_real

Real mode можно разблокировать только если выполнены условия:

- counted closed setups >= `REAL_MODE_UNLOCK_MIN_CLOSED_SETUPS` по умолчанию 100;
- win rate >= `REAL_MODE_UNLOCK_MIN_WIN_RATE` по умолчанию 80%;
- Net R >= `REAL_MODE_UNLOCK_MIN_NET_R` по умолчанию +10R;
- API status OK;
- balance/positions/orders checks OK;
- нет critical API errors;
- order lifecycle stable;
- emergency controls доступны.

Win считается так:

- win: `TP1 only`, `TP2 hit`;
- loss: `invalidated before TP1`;
- expired no entry не считается;
- `FAST_MOVE` не считается, если `REAL_MODE_COUNT_FAST_MOVE=false`;
- `AMBIGUOUS` не считается.

## Почему бот отвечает “пошел нахуй, ждем 80%+”

Это защитный real-gate.
Если статистика не прошла фильтр или API/safety checks не готовы, `/enable_real` и попытка mainnet order ответят:

```text
пошел нахуй, ждем 80%+
```

Это значит: real trading нельзя включать, пока нет достаточно сильной paper/testnet статистики.

## Double confirmation для real

Если статистика прошла фильтр:

1. `/enable_real`
2. Нажать `🐺 Выпустить зверя в рынок`
3. Нажать `✅ Да, включить REAL`

Для каждого real order тоже будет отдельное подтверждение:

```text
⚠️ REAL MARKET MODE. Подтверди ещё раз.
```

Без этой кнопки real order не отправляется.

## Real mode safety filters

Даже после unlock бот отклоняет:

- `HIGH` risk, если `ALLOW_HIGH_RISK_ORDERS=false`;
- `EXTREME` risk;
- `FAST_MOVE`;
- `AMBIGUOUS`;
- `TOO_LATE_DO_NOT_CHASE`;
- `NO SETUP`;
- duplicate symbol order;
- already active position;
- daily loss limit reached;
- API status failed;
- balance check failed;
- permissions unknown.

## Panic

`/panic`:

- сразу ставит `REAL_TRADING_UNLOCKED=false`;
- пытается отменить открытые ордера;
- отвечает:

```text
PANIC MODE: real trading locked.
```

## Безопасность ключей

Бот никогда не показывает полный API key или secret.
Key маскируется:

```text
abcd********wxyz
```

Secret не выводится.

Команда `/api_set` не сохраняет secret в Telegram/SQLite.
Для production используй только Render Environment или локальные environment variables.
