# Phase 7 — Performance Analytics, Export, Daily Reports

Phase 7 добавляет аналитику качества сетапов. Цель — понять, есть ли у радара реальная практическая польза, а не просто красивые алерты.

## Что делает аналитика

Бот анализирует закрытые записи `setup_journal` и считает:

- total closed setups
- TP1 only
- TP2
- SL до TP1
- SL после TP1
- expired before/after entry
- raw R
- fee-adjusted net R
- win rate по TP1+
- TP2 rate
- invalidation rate
- группировки по symbol, side, alert type, risk level, execution status, execution quality, часу UTC и дню недели

Команда:

```text
/analytics
```

## Экспорт журнала

Команды:

```text
/export_journal
/export_stats
/export_orders
```

Бот создаёт CSV и JSON файлы:

```text
journal_export_YYYYMMDD_HHMMSS.csv
journal_export_YYYYMMDD_HHMMSS.json
stats_export_YYYYMMDD_HHMMSS.csv
stats_export_YYYYMMDD_HHMMSS.json
orders_export_YYYYMMDD_HHMMSS.csv
orders_export_YYYYMMDD_HHMMSS.json
```

Если Telegram document upload доступен, бот отправит оба файла в чат.

## Как читать Net R

`Raw R` — теоретический результат сценария:

- TP1 only
- +2.5R
- -1R
- 0R

`Net R` — результат после учёта:

- комиссии Bybit
- fee mode
- slippage

Для ручной торговли важнее смотреть именно `Net R`, потому что быстрые сделки с маленьким stop distance могут выглядеть хорошо по Raw R, но хуже после комиссий и проскальзывания.

## Почему 10 сетапов мало

10 закрытых сетапов — почти всегда шум. Один TP2 или один SL может сильно исказить картину.

До 30 сетапов бот показывает предупреждение:

```text
Данных мало. Выводы пока слабые.
```

## Почему 100+ сетапов лучше

После 100+ закрытых сетапов уже можно осторожно смотреть:

- какие alert types реально дают плюс
- какие symbols режут статистику
- какие часы UTC хуже
- отличаются ли REALISTIC и FAST_MOVE

Даже 100+ сетапов не являются гарантией будущей доходности.

## Рекомендации фильтров

Команда:

```text
/recommend_filters
```

Бот предлагает:

- alert types to keep
- alert types to avoid
- risk levels to avoid
- symbols to blacklist
- execution qualities to ignore
- best/worst hours UTC

Если в группе меньше 10 сделок, бот не делает сильный вывод и пишет `мало данных`.

## Daily Report

Команда ручного запуска:

```text
/daily_report_now
```

Автоотчёт настраивается через:

```bash
DAILY_REPORT_ENABLED=true
DAILY_REPORT_HOUR_UTC=21
DAILY_REPORT_CHAT_ID=<telegram_chat_id>
```

Бот хранит `last_daily_report_date`, чтобы не отправлять дубль после рестарта.

## Blacklist

Команды:

```text
/blacklist
/blacklist_add SYMBOL
/blacklist_remove SYMBOL
```

Если symbol в blacklist, бот может продолжать видеть алерт, но не создаёт tradeable setup/order plan.

## Почему real trading всё ещё опасен

Даже хорошая статистика не гарантирует прибыль:

- рынок меняется
- ликвидность исчезает
- FAST_MOVE может быть нереалистичен руками
- комиссии и slippage портят короткие setups
- Bybit API и сеть могут задерживаться

Phase 7 помогает фильтровать и анализировать. Это не разрешение включать real trading без ручной проверки.
