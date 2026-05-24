# Phase 8 — Historical Backtest / Replay Validator

Phase 8 добавляет исторический replay/backtest для текущей логики радара и сетапов.
Это не новая стратегия и не AI. Backtest пытается проверить те же правила, которые бот использует в live-режиме:

- price change за lookback-окно;
- Open Interest change, если исторический OI доступен через Bybit;
- volume spike по историческим свечам;
- RSI / EMA / MACD / ATR;
- текущая классификация alert type / risk level;
- текущий `setup_generator.py`;
- fee/slippage оценка из конфига;
- execution realism: `REALISTIC`, `FAST_MOVE`, `AMBIGUOUS`.

## Что backtest доказывает

Backtest помогает понять, были ли у текущей логики признаки edge на исторических данных:

- какие alert types давали положительный Net R;
- какие символы работали хуже;
- сколько сетапов доходило до TP1/TP2;
- сколько ломалось до TP1;
- сколько давало частичную отработку `TP1 only`;
- какие результаты были `FAST_MOVE` или `AMBIGUOUS`.

## Что backtest НЕ доказывает

Backtest не гарантирует результат в реале.

Причины:

- исторический OI/funding может быть недоступен или иметь меньшую точность;
- Telegram alert приходит с задержкой;
- лимитный ордер может не исполниться;
- проскальзывание и комиссии меняются;
- в одной 1m свече нельзя точно знать порядок касаний entry / TP / stop;
- рынок меняется, а прошлый edge может исчезнуть.

## Команды

```text
/backtest
/backtest SYMBOL
/backtest SYMBOL DAYS
/backtest_top
/backtest_report
/export_backtest
```

Примеры:

```text
/backtest BTCUSDT 7
/backtest FIDAUSDT 3
/backtest_report
/export_backtest
```

`/backtest` без аргументов запускает тест для `BTCUSDT` за период по умолчанию.

## Ограничения данных

Если Bybit не отдаёт исторический OI, бот не подделывает его.
В таком случае backtest для символа будет пропущен или помечен предупреждением:

```text
⚠️ OI/funding historical data unavailable. Test quality reduced.
```

Это сделано специально, чтобы не создавать ложную точность.

## Как читать execution quality

`REALISTIC` — вход случился не мгновенно после alert, руками теоретически можно было успеть.

`FAST_MOVE` — вход и TP произошли слишком быстро или в той же свече. Для ручной торговли такой результат лучше считать сомнительным.

`AMBIGUOUS` — entry / TP / stop задеты в одной свече или порядок касаний неизвестен. Такой результат не стоит считать чистой победой.

## Как читать Net R

`Raw R` — результат до комиссий и проскальзывания.

`Net R` — результат после taker/maker fee и `SLIPPAGE_PERCENT`.

Если Raw R положительный, но Net R около нуля или отрицательный, значит setup слишком тонкий для реального исполнения.

## Почему live paper data всё ещё важнее

Backtest полезен как фильтр, но live paper tracking ближе к реальности:

- учитывает реальное время alert;
- показывает, можно ли было дождаться entry zone;
- ловит `FAST_MOVE`;
- работает с текущим рынком, а не с прошлым.

Не стоит включать real trading только из-за одного хорошего backtest.
Нужна выборка хотя бы 100+ закрытых сетапов, а лучше 300+.

## Экспорт

`/export_backtest` создаёт:

```text
backtest_export_YYYYMMDD_HHMMSS.csv
backtest_export_YYYYMMDD_HHMMSS.json
```

Поля экспорта:

- run_id
- symbol
- side
- alert_type
- risk_level
- execution_status
- execution_quality
- result_type
- raw_r
- net_r
- opened_at
- closed_at
- close_reason
- data_quality

## Риск

Бот не торгует автоматически.
Backtest не является торговым сигналом.
Даже хороший backtest может сломаться на реальном рынке из-за ликвидности, новостей, задержек и исполнения лимитных ордеров.
