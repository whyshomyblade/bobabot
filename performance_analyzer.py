from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from setup_tracker import (
    execution_quality_for_record,
    fee_adjusted_result_r_for_record,
    normalize_final_state,
    raw_result_r_for_record,
    result_for_record,
)


JOURNAL_EXPORT_FIELDS = [
    "id",
    "symbol",
    "side",
    "alert_type",
    "risk_level",
    "setup_status",
    "execution_status",
    "execution_quality",
    "entry_price",
    "stop_price",
    "tp1",
    "tp2",
    "result_type",
    "raw_r",
    "net_r",
    "fee_mode",
    "fee_rate",
    "slippage_percent",
    "created_at",
    "activated_at",
    "closed_at",
    "close_reason",
    "age_minutes",
]

ORDERS_EXPORT_FIELDS = [
    "id",
    "symbol",
    "side",
    "entry_price",
    "stop_price",
    "tp1",
    "tp2",
    "qty",
    "position_size_usdt",
    "risk_usdt",
    "status",
    "mode",
    "source_setup_id",
    "created_at",
    "updated_at",
]

STATS_EXPORT_FIELDS = [
    "group_type",
    "group_name",
    "count",
    "tp1_rate",
    "tp1_only_rate",
    "tp2_rate",
    "sl_before_tp1_rate",
    "broken_after_tp1_rate",
    "total_broken_rate",
    "expired_rate",
    "raw_avg_r",
    "net_avg_r",
    "net_total_r",
    "best_result",
    "worst_result",
]


def analyze_performance(
    journal_records: list[dict[str, Any]],
    active_setups: list[dict[str, Any]] | None = None,
    alert_records: list[dict[str, Any]] | None = None,
    since: datetime | None = None,
) -> dict[str, Any]:
    closed = [
        record
        for record in journal_records
        if isinstance(record, dict) and _in_period(record, since)
    ]
    rows = [journal_export_row(record) for record in closed]
    total = len(rows)
    raw_total = sum(_float(row.get("raw_r")) or 0 for row in rows)
    net_total = sum(_float(row.get("net_r")) or 0 for row in rows)

    tp1_only = _count(rows, {"TP1 only"})
    tp2 = _count(rows, {"+2.5R"})
    sl_before_tp1 = _count_state(rows, {"INVALIDATED_BEFORE_TP1", "AMBIGUOUS_INVALIDATION_FIRST"})
    broken_after_tp1 = _count_state(rows, {"TP1_THEN_INVALIDATED"})
    expired = _count_state(rows, {"EXPIRED_NO_ENTRY", "EXPIRED_AFTER_ENTRY"})

    summary = {
        "total_closed": total,
        "active_setups": len(active_setups or []),
        "activated_count": sum(1 for row in rows if row.get("activated_at")),
        "expired_setups": expired,
        "tp1_only": tp1_only,
        "tp2": tp2,
        "sl_before_tp1": sl_before_tp1,
        "broken_after_tp1": broken_after_tp1,
        "sl_after_tp1": broken_after_tp1,
        "total_broken": sl_before_tp1 + broken_after_tp1,
        "expired_before_entry": _count_state(rows, {"EXPIRED_NO_ENTRY"}),
        "expired_after_entry": _count_state(rows, {"EXPIRED_AFTER_ENTRY"}),
        "raw_total_r": raw_total,
        "net_total_r": net_total,
        "raw_average_r": raw_total / total if total else 0.0,
        "net_average_r": net_total / total if total else 0.0,
        "win_rate_tp1_plus": _rate(_count_tp1_plus(rows), total),
        "tp1_only_rate": _rate(tp1_only, total),
        "tp2_rate": _rate(tp2, total),
        "sl_before_tp1_rate": _rate(sl_before_tp1, total),
        "broken_after_tp1_rate": _rate(broken_after_tp1, total),
        "total_broken_rate": _rate(sl_before_tp1 + broken_after_tp1, total),
        "expired_rate": _rate(expired, total),
        "invalidation_rate": _rate(sl_before_tp1, total),
        "expectancy_net_r": net_total / total if total else 0.0,
        "new_alerts": _count_alerts(alert_records or [], since),
        "dataset_progress": dataset_progress(total),
    }

    groups = {
        "symbol": _group(rows, "symbol"),
        "side": _group(rows, "side"),
        "alert_type": _group(rows, "alert_type"),
        "risk_level": _group(rows, "risk_level"),
        "execution_status": _group(rows, "execution_status"),
        "execution_quality": _group(rows, "execution_quality"),
        "hour_utc": _group(rows, "hour_utc"),
        "weekday": _group(rows, "weekday"),
    }

    return {
        "summary": summary,
        "rows": rows,
        "groups": groups,
        "best_setup": _best(rows),
        "worst_setup": _worst(rows),
    }


def journal_export_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [journal_export_row(record) for record in records if isinstance(record, dict)]


def journal_export_row(record: dict[str, Any]) -> dict[str, Any]:
    final_state = normalize_final_state(record)
    created_at = _str(record.get("created_at"))
    entered_at = _str(record.get("entered_at"))
    closed_at = _str(record.get("closed_at") or record.get("updated_at"))
    return {
        "id": record.get("id"),
        "symbol": record.get("symbol"),
        "side": record.get("direction") or record.get("side"),
        "alert_type": record.get("alert_type"),
        "risk_level": record.get("risk_level"),
        "setup_status": record.get("setup_status"),
        "execution_status": record.get("execution_status"),
        "execution_quality": execution_quality_for_record(record),
        "entry_price": record.get("entry_price_virtual") or record.get("entry_price"),
        "stop_price": record.get("invalidation") or record.get("stop_price"),
        "tp1": record.get("tp1"),
        "tp2": record.get("tp2"),
        "result_type": result_for_record(record),
        "final_state": final_state,
        "raw_r": raw_result_r_for_record(record),
        "net_r": fee_adjusted_result_r_for_record(record),
        "fee_mode": record.get("fee_mode"),
        "fee_rate": record.get("fee_rate"),
        "slippage_percent": record.get("slippage_percent"),
        "created_at": created_at,
        "activated_at": entered_at,
        "closed_at": closed_at,
        "close_reason": record.get("close_reason"),
        "age_minutes": _age_minutes(created_at, closed_at),
        "hour_utc": _hour_utc(closed_at or created_at),
        "weekday": _weekday(closed_at or created_at),
    }


def orders_export_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for record in records:
        if not isinstance(record, dict):
            continue
        rows.append({field: record.get(field) for field in ORDERS_EXPORT_FIELDS})
    return rows


def stats_export_rows(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    summary = analysis.get("summary") or {}
    rows.append(
        {
            "group_type": "summary",
            "group_name": "all",
            "count": summary.get("total_closed", 0),
            "tp1_rate": summary.get("win_rate_tp1_plus", 0),
            "tp1_only_rate": summary.get("tp1_only_rate", 0),
            "tp2_rate": summary.get("tp2_rate", 0),
            "sl_before_tp1_rate": summary.get("sl_before_tp1_rate", 0),
            "broken_after_tp1_rate": summary.get("broken_after_tp1_rate", 0),
            "total_broken_rate": summary.get("total_broken_rate", 0),
            "expired_rate": summary.get("expired_rate", 0),
            "raw_avg_r": summary.get("raw_average_r", 0),
            "net_avg_r": summary.get("net_average_r", 0),
            "net_total_r": summary.get("net_total_r", 0),
            "best_result": "",
            "worst_result": "",
        }
    )
    for group_type, groups in (analysis.get("groups") or {}).items():
        for group_name, metrics in groups.items():
            row = {"group_type": group_type, "group_name": group_name}
            row.update(metrics)
            rows.append(row)
    return rows


def format_analytics(analysis: dict[str, Any]) -> str:
    summary = analysis.get("summary") or {}
    alert_groups = analysis.get("groups", {}).get("alert_type", {})
    execution_groups = analysis.get("groups", {}).get("execution_quality", {})
    total = int(summary.get("total_closed") or 0)

    lines = [
        "🧠 Аналитика сетапов",
        "",
        f"Всего закрыто: {total}",
        f"Net R: {_fmt_r(summary.get('net_total_r'))}",
        f"Avg Net R: {_fmt_r(summary.get('net_average_r'))}",
        f"TP1 rate: {_fmt_pct(summary.get('win_rate_tp1_plus'))}",
        f"TP1 only rate: {_fmt_pct(summary.get('tp1_only_rate'))}",
        f"TP2 rate: {_fmt_pct(summary.get('tp2_rate'))}",
        f"SL before TP1 rate: {_fmt_pct(summary.get('sl_before_tp1_rate'))}",
        f"Broken after TP1 rate: {_fmt_pct(summary.get('broken_after_tp1_rate'))}",
        f"Total broken rate: {_fmt_pct(summary.get('total_broken_rate'))}",
        f"Expired rate: {_fmt_pct(summary.get('expired_rate'))}",
        "",
        format_dataset_progress(total),
    ]
    if total < 30:
        lines.extend(["", "⚠️ Данных мало. Выводы пока слабые."])
    elif total < 100:
        lines.extend(["", "⚠️ Данных средне. Не включать реал только по этой статистике."])

    lines.extend(["", "Лучшие типы:"])
    lines.extend(_format_ranked_groups(_best_groups(alert_groups), positive=True))
    lines.extend(["", "Худшие типы:"])
    lines.extend(_format_ranked_groups(_worst_groups(alert_groups), positive=False))
    lines.extend(["", "Execution:"])
    if execution_groups:
        for name, metrics in sorted(execution_groups.items()):
            suffix = " / не учитывать для ручной торговли" if name == "FAST_MOVE" else ""
            lines.append(f"{name}: Avg {_fmt_r(metrics.get('net_avg_r'))}{suffix}")
    else:
        lines.append("нет данных")

    recommendations = recommendation_summary(analysis)
    lines.extend(["", "Вывод:"])
    lines.append(f"Оставить: {recommendations.get('keep_short') or 'нет сильных выводов'}")
    lines.append(f"Осторожно: {recommendations.get('caution_short') or 'нет сильных выводов'}")
    lines.append(f"Отключить/фильтровать: {recommendations.get('avoid_short') or 'нет сильных выводов'}")
    return "\n".join(lines)


def format_recommendations(analysis: dict[str, Any]) -> str:
    groups = analysis.get("groups") or {}
    alert_groups = groups.get("alert_type", {})
    risk_groups = groups.get("risk_level", {})
    symbol_groups = groups.get("symbol", {})
    hour_groups = groups.get("hour_utc", {})
    execution_groups = groups.get("execution_quality", {})

    keep = _positive_recommendations(alert_groups)
    caution = _negative_recommendations(alert_groups, prefix="⚠️")
    avoid = _negative_recommendations(symbol_groups, prefix="❌", label_prefix="Symbol ")
    risk_avoid = _negative_recommendations(risk_groups, prefix="❌", label_prefix="Risk ")
    bad_execution = _negative_recommendations(execution_groups, prefix="❌")
    best_hours = _positive_recommendations(hour_groups, label_prefix="UTC ")
    worst_hours = _negative_recommendations(hour_groups, prefix="❌", label_prefix="UTC ")

    lines = ["🧪 Рекомендации", "", "Оставить:"]
    lines.extend(keep or ["мало данных"])
    lines.extend(["", "Осторожно:"])
    lines.extend(caution or ["мало данных"])
    lines.extend(["", "Отключить/фильтровать:"])
    lines.extend(avoid or risk_avoid or ["мало данных"])
    if bad_execution:
        lines.extend(["", "Не торговать руками:"])
        lines.extend(bad_execution)
    lines.extend(["", "Лучшие часы UTC:"])
    lines.extend(best_hours or ["мало данных"])
    lines.extend(["", "Худшие часы UTC:"])
    lines.extend(worst_hours or ["мало данных"])
    if "HIGH" in risk_groups and risk_groups["HIGH"].get("net_avg_r", 0) < 0:
        lines.extend(["", "HIGH risk net negative — держать ALLOW_HIGH_RISK_ORDERS=false."])
    return "\n".join(lines)


def format_daily_report(
    analysis: dict[str, Any],
    since: datetime,
) -> str:
    summary = analysis.get("summary") or {}
    best = analysis.get("best_setup")
    worst = analysis.get("worst_setup")
    alert_groups = analysis.get("groups", {}).get("alert_type", {})
    execution_groups = analysis.get("groups", {}).get("execution_quality", {})

    lines = [
        "📊 Daily Report",
        "",
        "Период: last 24h",
        f"Новых алертов: {summary.get('new_alerts', 0)}",
        f"Активировано сетапов: {summary.get('activated_count', 0)}",
        f"Закрыто сетапов: {summary.get('total_closed', 0)}",
        "",
        f"TP1 only: {summary.get('tp1_only', 0)}",
        f"TP2: {summary.get('tp2', 0)}",
        f"SL before TP1: {summary.get('sl_before_tp1', 0)}",
        f"Broken after TP1: {summary.get('broken_after_tp1', 0)}",
        f"Total broken: {summary.get('total_broken', 0)}",
        f"Expired: {summary.get('expired_setups', 0)}",
        "",
        f"Raw R: {_fmt_r(summary.get('raw_total_r'))}",
        f"Net R: {_fmt_r(summary.get('net_total_r'))}",
        "",
        f"TP1 only rate: {_fmt_pct(summary.get('tp1_only_rate'))}",
        f"TP2 rate: {_fmt_pct(summary.get('tp2_rate'))}",
        f"SL before TP1 rate: {_fmt_pct(summary.get('sl_before_tp1_rate'))}",
        f"Broken after TP1 rate: {_fmt_pct(summary.get('broken_after_tp1_rate'))}",
        f"Total broken rate: {_fmt_pct(summary.get('total_broken_rate'))}",
        f"Expired rate: {_fmt_pct(summary.get('expired_rate'))}",
        "",
        format_dataset_progress(int(summary.get("total_closed") or 0)),
        "",
        "Лучший сетап:",
        _format_setup_line(best),
        "",
        "Худший сетап:",
        _format_setup_line(worst),
        "",
        f"Лучший alert type: {_top_group_name(alert_groups, best=True)}",
        f"Худший alert type: {_top_group_name(alert_groups, best=False)}",
        "",
        "Execution:",
    ]
    for name, metrics in sorted(execution_groups.items()):
        lines.append(f"{name}: {metrics.get('count', 0)} | Net R {_fmt_r(metrics.get('net_total_r'))}")
    if not execution_groups:
        lines.append("нет данных")
    lines.extend(["", "Вывод:"])
    rec = recommendation_summary(analysis)
    lines.append(f"- Что работает: {rec.get('keep_short') or 'пока мало данных'}")
    lines.append(f"- Что режет статистику: {rec.get('avoid_short') or 'пока мало данных'}")
    lines.append("- Что наблюдать завтра: набрать больше закрытых сетапов и сравнить REALISTIC vs FAST_MOVE")
    return "\n".join(lines)


def recommendation_summary(analysis: dict[str, Any]) -> dict[str, str]:
    alert_groups = analysis.get("groups", {}).get("alert_type", {})
    keep = _best_groups(alert_groups, min_count=10)[:1]
    avoid = _worst_groups(alert_groups, min_count=10)[:1]
    caution = _worst_groups(alert_groups, min_count=5)[:1]
    return {
        "keep_short": _group_brief(keep[0]) if keep else "",
        "avoid_short": _group_brief(avoid[0]) if avoid else "",
        "caution_short": _group_brief(caution[0]) if caution else "",
    }


def dataset_progress(total_closed: int) -> dict[str, Any]:
    total = max(int(total_closed or 0), 0)
    need_more = max(100 - total, 0)
    progress = min((total / 100) * 100, 100.0)
    if total < 30:
        quality = "очень слабая выборка"
    elif total < 100:
        quality = "ранняя выборка"
    elif total < 300:
        quality = "рабочая выборка"
    else:
        quality = "сильная выборка"
    return {
        "closed": total,
        "target": 100,
        "progress": progress,
        "need_more": need_more,
        "sample_quality": quality,
    }


def format_dataset_progress(total_closed: int) -> str:
    progress = dataset_progress(total_closed)
    return "\n".join(
        [
            "📦 Dataset progress:",
            f"Closed setups: {progress['closed']} / {progress['target']}",
            f"Progress: {progress['progress']:.0f}%",
            f"Need more: {progress['need_more']} setups",
            f"Sample quality: {progress['sample_quality']}",
        ]
    )


def _group(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(key) or "Unknown")].append(row)
    return {name: _metrics(items) for name, items in grouped.items()}


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(rows)
    net_values = [_float(row.get("net_r")) or 0 for row in rows]
    raw_values = [_float(row.get("raw_r")) or 0 for row in rows]
    return {
        "count": count,
        "tp1_rate": _rate(_count_tp1_plus(rows), count),
        "tp1_only_rate": _rate(_count(rows, {"TP1 only"}), count),
        "tp2_rate": _rate(_count(rows, {"+2.5R"}), count),
        "sl_before_tp1_rate": _rate(_count_state(rows, {"INVALIDATED_BEFORE_TP1", "AMBIGUOUS_INVALIDATION_FIRST"}), count),
        "broken_after_tp1_rate": _rate(_count_state(rows, {"TP1_THEN_INVALIDATED"}), count),
        "total_broken_rate": _rate(_count_state(rows, {"INVALIDATED_BEFORE_TP1", "AMBIGUOUS_INVALIDATION_FIRST", "TP1_THEN_INVALIDATED"}), count),
        "invalidation_rate": _rate(_count_state(rows, {"INVALIDATED_BEFORE_TP1", "AMBIGUOUS_INVALIDATION_FIRST"}), count),
        "expired_rate": _rate(_count_state(rows, {"EXPIRED_NO_ENTRY", "EXPIRED_AFTER_ENTRY"}), count),
        "raw_avg_r": sum(raw_values) / count if count else 0,
        "net_avg_r": sum(net_values) / count if count else 0,
        "net_total_r": sum(net_values),
        "best_result": max(net_values) if net_values else 0,
        "worst_result": min(net_values) if net_values else 0,
    }


def _count(rows: list[dict[str, Any]], result_types: set[str]) -> int:
    return sum(1 for row in rows if row.get("result_type") in result_types)


def _count_state(rows: list[dict[str, Any]], states: set[str]) -> int:
    return sum(1 for row in rows if row.get("final_state") in states)


def _count_tp1_plus(rows: list[dict[str, Any]]) -> int:
    return sum(1 for row in rows if row.get("result_type") in {"TP1 only", "+2.5R"})


def _count_alerts(alerts: list[dict[str, Any]], since: datetime | None) -> int:
    if since is None:
        return len(alerts)
    return sum(1 for alert in alerts if _parse_iso(alert.get("timestamp")) and _parse_iso(alert.get("timestamp")) >= since)


def _in_period(record: dict[str, Any], since: datetime | None) -> bool:
    if since is None:
        return True
    value = _parse_iso(record.get("closed_at") or record.get("created_at"))
    return value is not None and value >= since


def _age_minutes(start: Any, end: Any) -> int | None:
    start_dt = _parse_iso(start)
    end_dt = _parse_iso(end)
    if start_dt is None or end_dt is None:
        return None
    return max(int((end_dt - start_dt).total_seconds() // 60), 0)


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _hour_utc(value: Any) -> str:
    parsed = _parse_iso(value)
    return f"{parsed.hour:02d}:00" if parsed else "Unknown"


def _weekday(value: Any) -> str:
    parsed = _parse_iso(value)
    return parsed.strftime("%A") if parsed else "Unknown"


def _best(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    return max(rows, key=lambda row: _float(row.get("net_r")) or 0)


def _worst(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    return min(rows, key=lambda row: _float(row.get("net_r")) or 0)


def _best_groups(groups: dict[str, dict[str, Any]], min_count: int = 1) -> list[tuple[str, dict[str, Any]]]:
    rows = [(name, data) for name, data in groups.items() if data.get("count", 0) >= min_count]
    return sorted(rows, key=lambda item: item[1].get("net_avg_r", 0), reverse=True)


def _worst_groups(groups: dict[str, dict[str, Any]], min_count: int = 1) -> list[tuple[str, dict[str, Any]]]:
    rows = [(name, data) for name, data in groups.items() if data.get("count", 0) >= min_count]
    return sorted(rows, key=lambda item: item[1].get("net_avg_r", 0))


def _format_ranked_groups(groups: list[tuple[str, dict[str, Any]]], positive: bool) -> list[str]:
    if not groups:
        return ["нет данных"]
    lines = []
    for name, data in groups:
        if not positive and data.get("net_avg_r", 0) >= 0:
            continue
        index = len(lines) + 1
        extra = f"TP2 {_fmt_pct(data.get('tp2_rate'))}" if positive else f"SL {_fmt_pct(data.get('invalidation_rate'))}"
        lines.append(
            f"{index}. {name}: {data.get('count', 0)} сделок | Avg {_fmt_r(data.get('net_avg_r'))} | {extra}"
        )
        if len(lines) >= 3:
            break
    if not lines and not positive:
        return ["нет отрицательных групп"]
    return lines or ["нет данных"]


def _positive_recommendations(
    groups: dict[str, dict[str, Any]],
    label_prefix: str = "",
) -> list[str]:
    rows = []
    for name, data in _best_groups(groups, min_count=10):
        if data.get("net_avg_r", 0) > 0:
            rows.append(f"✅ {label_prefix}{name}: Avg {_fmt_r(data.get('net_avg_r'))}, {data.get('count', 0)} сделок")
    return rows[:5]


def _negative_recommendations(
    groups: dict[str, dict[str, Any]],
    prefix: str,
    label_prefix: str = "",
) -> list[str]:
    rows = []
    for name, data in _worst_groups(groups, min_count=10):
        if data.get("net_avg_r", 0) < 0:
            rows.append(f"{prefix} {label_prefix}{name}: Avg {_fmt_r(data.get('net_avg_r'))}, {data.get('count', 0)} сделок")
    return rows[:5]


def _top_group_name(groups: dict[str, dict[str, Any]], best: bool) -> str:
    ranked = _best_groups(groups) if best else _worst_groups(groups)
    if not ranked:
        return "нет данных"
    name, data = ranked[0]
    if not best and data.get("net_avg_r", 0) >= 0:
        return "нет отрицательных групп"
    return f"{name} | Avg {_fmt_r(data.get('net_avg_r'))}"


def _group_brief(item: tuple[str, dict[str, Any]]) -> str:
    name, data = item
    return f"{name} Avg {_fmt_r(data.get('net_avg_r'))}, {data.get('count', 0)} сделок"


def _format_setup_line(row: dict[str, Any] | None) -> str:
    if not row:
        return "нет данных"
    return f"{row.get('symbol')} | {row.get('alert_type')} | {_fmt_r(row.get('net_r'))}"


def _rate(count: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return (count / total) * 100


def _fmt_pct(value: Any) -> str:
    return f"{(_float(value) or 0):.0f}%"


def _fmt_r(value: Any) -> str:
    return f"{(_float(value) or 0):+.2f}R"


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _str(value: Any) -> str:
    return "" if value is None else str(value)
