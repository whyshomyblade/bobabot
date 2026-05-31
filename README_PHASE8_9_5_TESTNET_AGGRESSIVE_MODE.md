# Phase 8.9.5 — TESTNET Aggressive Execution Mode

Этот режим нужен только для тестовой тренировки автопилота на Bybit TESTNET.
Он не включает real trading и не отправляет ордера на mainnet.

## Что делает aggressive mode

Когда включены:

- `BYBIT_TRADING_ENABLED=true`
- API mode `TESTNET`
- `TESTNET_AUTOPILOT_ENABLED=true`
- `TESTNET_AGGRESSIVE_MODE=true`

бот пытается отправить TESTNET limit order по каждому активированному сетапу, если ордер технически возможен.

## Что становится warning, а не стопором

В aggressive mode эти факторы больше не блокируют TESTNET ордер:

- `HIGH` / `EXTREME` risk
- `FAST_MOVE`
- `MAYBE_NOT_EXECUTABLE`
- `AMBIGUOUS`
- `TOO_LATE_DO_NOT_CHASE`
- alert type вне whitelist
- R/R ниже обычного порога автопилота

Они попадут в список `Warnings` в Telegram и в `autopilot_decisions`.

## Что всё равно блокирует ордер

Даже в aggressive mode бот отклонит ордер, если:

- Bybit API не готов
- нет API ключей
- trading disabled
- режим не TESTNET
- включён `PANIC_MODE`
- включён runtime trading disabled
- нет entry / stop / TP1 / TP2
- невозможно рассчитать qty/risk
- нет instrument info
- уже есть активный TESTNET ордер по этой монете
- уже есть открытая позиция по этой монете

## Telegram команды

Включить:

```text
/testnet_aggressive_on
```

Выключить:

```text
/testnet_aggressive_off
```

Статус:

```text
/testnet_aggressive_status
```

Также кнопки находятся в меню:

```text
💰 Trading → 🤖 Autopilot
```

## Важная безопасность

Aggressive mode работает только для TESTNET автопилота.

Real mainnet trading по-прежнему заблокирован Real Trading Gate. Если попытаться включить real раньше статистических требований, бот должен ответить:

```text
пошел нахуй, ждем 80%+
```

## Переменная окружения

По умолчанию:

```text
TESTNET_AGGRESSIVE_MODE=true
```

На Render можно оставить дефолт и управлять режимом через Telegram.
