# Phase 8.9.3 — Runtime Control + Fee Profile

Эта фаза добавляет управление безопасными настройками через Telegram и профиль комиссий Bybit.

Real trading по-прежнему закрыт Real Trading Gate. Эти команды не обходят `panic`, risk control, daily loss lock и статистический gate.

## Комиссии

Текущий профиль можно посмотреть:

```text
/fees
```

Установить derivatives fees:

```text
/set_fees 0.1000 0.0360
```

Где:
- `0.1000` — taker fee в процентах;
- `0.0360` — maker fee в процентах.

Режим расчёта:

```text
/set_fee_mode maker
/set_fee_mode taker
/set_fee_mode worst_case
```

`maker` использует maker fee, `taker` использует taker fee, `worst_case` берёт худшую ставку из двух.

Комиссии учитываются в:
- order plan;
- estimated net R;
- analytics;
- backtest;
- paper/testnet result estimates.

## Runtime control

Статус:

```text
/runtime_status
```

Включить TESTNET trading:

```text
/set_trading_enabled true
```

Отключить Bybit trading и вернуться в paper mode:

```text
/set_trading_enabled false
```

Переключить API mode:

```text
/set_testnet true
/set_testnet false
```

Важно: `set_testnet false` не включает real trading. Mainnet остаётся заблокированным.

Autopilot:

```text
/set_autopilot_enabled true
/set_autopilot_enabled false
```

Autopilot включается только в TESTNET mode, при включённом trading и успешном API test.

Дополнительные runtime-настройки:

```text
/set_scan_interval 60
/set_max_active_orders 2
/set_risk_percent 1
/set_paper_balance 100
```

## Render ENV всё ещё нужен

Для Render лучше хранить стабильные ключи в Environment:

```text
BYBIT_API_KEY=...
BYBIT_API_SECRET=...
BYBIT_TESTNET=true
BYBIT_TRADING_ENABLED=true
```

Telegram DB удобен для быстрого теста, но на Render Free локальная SQLite база может исчезнуть после redeploy/container reset.

## Главное ограничение

Если включить mainnet mode и попытаться открыть real trading до прохождения статистики, бот ответит:

```text
пошел нахуй, ждем 80%+
```

Это ожидаемое поведение. Real trading остаётся locked.
