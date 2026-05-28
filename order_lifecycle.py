import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import config


LOGGER = logging.getLogger("OrderLifecycle")

ACTIVE_TESTNET_STATUSES = {
    "SUBMITTED",
    "OPEN",
    "PARTIALLY_FILLED",
    "FILLED",
    "POSITION_OPENED",
    "CANCEL_REQUESTED",
    "UNKNOWN",
}
PREFILL_STATUSES = {
    "SUBMITTED",
    "OPEN",
    "PARTIALLY_FILLED",
    "CANCEL_REQUESTED",
    "UNKNOWN",
}
CLOSED_STATUSES = {
    "CANCELLED",
    "REJECTED",
    "EXPIRED",
    "INVALIDATED_BEFORE_FILL",
    "POSITION_CLOSED",
}


def build_order_lifecycle_keyboard(order_id: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": "📋 Order Status", "callback_data": f"cmd:/order_status {order_id}"}],
            [{"text": "❌ Cancel Order", "callback_data": f"cmd:/cancel_order {order_id}"}],
            [{"text": "🔄 Sync Orders", "callback_data": "cmd:/sync_orders"}],
        ]
    }


def sync_testnet_orders(
    private_client: Any,
    storage: Any,
    telegram: Any | None = None,
    notify: bool = True,
) -> dict[str, Any]:
    summary = {
        "checked": 0,
        "updated": 0,
        "errors": 0,
        "messages": [],
    }
    if not config.ORDER_SYNC_ENABLED:
        summary["messages"].append("Order sync disabled")
        return summary

    active_orders = _active_testnet_orders(storage)
    if not active_orders:
        _reset_failure_counter(storage)
        LOGGER.info("Order sync completed: checked=0 updated=0 errors=0")
        return summary

    if not private_client.can_call_private:
        return _sync_failure(storage, telegram, summary, "Bybit API keys не настроены.", notify)
    if not private_client.testnet:
        return _sync_failure(storage, telegram, summary, "API mode is not TESTNET.", notify)

    LOGGER.info("Order sync started: active_testnet_orders=%s", len(active_orders))
    try:
        open_orders = private_client.get_open_orders()
        positions = private_client.get_positions()
    except Exception as exc:
        return _sync_failure(storage, telegram, summary, _sanitize(exc), notify)

    _reset_failure_counter(storage)
    open_index = _index_open_orders(open_orders)
    duplicate_keeper = _first_prefill_order_by_symbol(active_orders)
    ticker_cache: dict[str, dict[str, Any] | None] = {}

    for order in active_orders:
        summary["checked"] += 1
        try:
            changed = _sync_one_order(
                order,
                open_index,
                positions,
                private_client,
                storage,
                telegram if notify else None,
                duplicate_keeper,
                ticker_cache,
            )
            if changed:
                summary["updated"] += 1
        except Exception as exc:
            summary["errors"] += 1
            order_id = str(order.get("id") or "")
            symbol = str(order.get("symbol") or "")
            storage.update_paper_order(
                order_id,
                {
                    "last_sync_at": _iso_now(),
                    "last_error": _sanitize(exc),
                },
            )
            LOGGER.error("Order sync error symbol=%s order_id=%s error=%s", symbol, order_id, exc)

    LOGGER.info(
        "Order sync completed: checked=%s updated=%s errors=%s",
        summary["checked"],
        summary["updated"],
        summary["errors"],
    )
    return summary


def format_order_sync_summary(summary: dict[str, Any]) -> str:
    lines = [
        "🔄 Order sync completed",
        f"Checked: {summary.get('checked', 0)}",
        f"Updated: {summary.get('updated', 0)}",
        f"Errors: {summary.get('errors', 0)}",
    ]
    messages = summary.get("messages") or []
    if messages:
        lines.extend(["", f"Note: {messages[0]}"])
    return "\n".join(lines)


def format_order_status(storage: Any, private_client: Any, order_id: str) -> str:
    order = find_order(storage, order_id)
    if order is None:
        return "❌ Ордер не найден."

    lines = [
        "📋 Order Status",
        "",
        f"ID: {order.get('id')}",
        f"Mode: {order.get('mode', 'PAPER')}",
        f"Symbol: {order.get('symbol')}",
        f"Side: {_direction_ru(order.get('side'))}",
        f"Status: {order.get('status', 'UNKNOWN')}",
        f"Entry: {_fmt(order.get('entry_price'))}",
        f"Qty: {_fmt(order.get('qty'))}",
        f"TP1: {_fmt(order.get('tp1'))}",
        f"TP2: {_fmt(order.get('tp2'))}",
        f"Stop: {_fmt(order.get('stop_price'))}",
        "",
        "Bybit:",
        f"Exchange ID: {order.get('exchange_order_id') or 'n/a'}",
        f"OrderLinkId: {order.get('order_link_id') or 'n/a'}",
        f"Last sync: {_format_time(order.get('last_sync_at'))}",
        f"Error: {order.get('last_error') or 'none'}",
    ]

    position_info = order.get("position_info")
    if isinstance(position_info, dict) and position_info:
        lines.extend(
            [
                "",
                "Position:",
                f"Entry: {_fmt(position_info.get('entry_price'))}",
                f"Size: {_fmt(position_info.get('size'))}",
                f"PnL: {_fmt(position_info.get('unrealized_pnl'))}",
                f"Leverage: {_fmt(position_info.get('leverage'))}",
                f"Liq price: {_fmt(position_info.get('liquidation_price'))}",
            ]
        )

    if order.get("mode") == "TESTNET" and private_client.can_call_private and private_client.testnet:
        try:
            bybit_row = _find_bybit_order(
                private_client.get_open_orders(str(order.get("symbol"))),
                order,
            )
            if bybit_row is None:
                history = private_client.get_order_history(
                    symbol=str(order.get("symbol")),
                    order_id=str(order.get("exchange_order_id") or "") or None,
                    order_link_id=str(order.get("order_link_id") or "") or None,
                    limit=20,
                )
                bybit_row = _find_bybit_order(history, order)
            if bybit_row:
                lines.extend(
                    [
                        "",
                        "Bybit live:",
                        f"Status: {bybit_row.get('orderStatus') or 'n/a'}",
                        f"Price: {bybit_row.get('price') or 'n/a'}",
                        f"Qty: {bybit_row.get('qty') or 'n/a'}",
                        f"Filled: {bybit_row.get('cumExecQty') or '0'}",
                    ]
                )
        except Exception as exc:
            lines.append(f"Bybit live: ошибка API ({_sanitize(exc)})")
    elif order.get("mode") == "TESTNET" and not private_client.can_call_private:
        lines.append("Bybit live: API keys not configured")

    return "\n".join(lines)


def find_order(storage: Any, target: str) -> dict[str, Any] | None:
    target = str(target or "")
    for order in storage.get_paper_orders(limit=None):
        values = {
            str(order.get("id") or ""),
            str(order.get("exchange_order_id") or ""),
            str(order.get("order_link_id") or ""),
        }
        if target in values:
            return order
    return None


def _sync_one_order(
    order: dict[str, Any],
    open_index: dict[str, dict[str, Any]],
    positions: list[dict[str, Any]],
    private_client: Any,
    storage: Any,
    telegram: Any | None,
    duplicate_keeper: dict[str, str],
    ticker_cache: dict[str, dict[str, Any] | None],
) -> bool:
    order_id = str(order.get("id") or "")
    symbol = str(order.get("symbol") or "")
    original_status = str(order.get("status") or "UNKNOWN")
    updates: dict[str, Any] = {
        "last_sync_at": _iso_now(),
        "last_error": None,
    }

    if original_status in PREFILL_STATUSES:
        cancel_reason = _auto_cancel_reason(order, storage, duplicate_keeper, private_client, ticker_cache)
        if cancel_reason:
            return _cancel_before_fill(order, cancel_reason, private_client, storage, telegram)

    open_order = _match_indexed_order(open_index, order)
    if open_order is not None:
        mapped_status = _map_bybit_order_status(open_order.get("orderStatus"))
        updates.update(
            {
                "status": mapped_status,
                "exchange_order_id": open_order.get("orderId") or order.get("exchange_order_id"),
                "order_link_id": open_order.get("orderLinkId") or order.get("order_link_id"),
                "raw_response_json": open_order,
            }
        )
    elif original_status in PREFILL_STATUSES:
        history_order = _fetch_history_order(private_client, order)
        if history_order is not None:
            mapped_status = _map_bybit_order_status(history_order.get("orderStatus"))
            if _has_execution(history_order):
                mapped_status = "FILLED"
                updates.setdefault("filled_at", _iso_now())
            updates.update(
                {
                    "status": mapped_status,
                    "exchange_order_id": history_order.get("orderId") or order.get("exchange_order_id"),
                    "order_link_id": history_order.get("orderLinkId") or order.get("order_link_id"),
                    "raw_response_json": history_order,
                }
            )
            if mapped_status in CLOSED_STATUSES:
                updates.setdefault("closed_at", _iso_now())
        elif original_status == "SUBMITTED":
            updates["status"] = "UNKNOWN"

    position = _matching_position(order, positions)
    if position is not None:
        updates["position_info"] = _position_info(position)
        if original_status != "POSITION_OPENED":
            updates["status"] = "POSITION_OPENED"
            updates.setdefault("filled_at", order.get("filled_at") or _iso_now())
            if not order.get("filled_notification_sent"):
                updates["filled_notification_sent"] = True
                _send_filled_message(telegram, order, updates)
                LOGGER.info("Order filled: %s", symbol)
    elif original_status == "POSITION_OPENED":
        updates["status"] = "POSITION_CLOSED"
        updates["closed_at"] = _iso_now()
        if not order.get("position_closed_notification_sent"):
            updates["position_closed_notification_sent"] = True
            _send_position_closed_message(telegram, order)
            LOGGER.info("Position closed: %s", symbol)

    next_status = updates.get("status", original_status)
    if next_status in {"FILLED", "POSITION_OPENED"} and not order.get("filled_notification_sent"):
        updates["filled_notification_sent"] = True
        updates.setdefault("filled_at", order.get("filled_at") or _iso_now())
        _send_filled_message(telegram, order, updates)
        LOGGER.info("Order filled: %s", symbol)

    changed = _meaningful_update(order, updates)
    if changed:
        storage.update_paper_order(order_id, updates)
        LOGGER.info(
            "Order status changed: %s %s -> %s",
            symbol,
            original_status,
            updates.get("status", original_status),
        )
    else:
        storage.update_paper_order(order_id, {"last_sync_at": updates["last_sync_at"], "last_error": None})
    return changed


def _cancel_before_fill(
    order: dict[str, Any],
    reason: str,
    private_client: Any,
    storage: Any,
    telegram: Any | None,
) -> bool:
    symbol = str(order.get("symbol") or "")
    order_id = str(order.get("id") or "")
    exchange_order_id = str(order.get("exchange_order_id") or "") or None
    order_link_id = str(order.get("order_link_id") or "") or None
    target_status = {
        "expired": "EXPIRED",
        "invalidated before fill": "INVALIDATED_BEFORE_FILL",
        "duplicate": "CANCELLED",
        "too late": "CANCELLED",
    }.get(reason, "CANCELLED")
    if not getattr(private_client, "trading_enabled", False):
        storage.update_paper_order(
            order_id,
            {
                "last_sync_at": _iso_now(),
                "last_error": "BYBIT_TRADING_ENABLED=false; auto-cancel not sent",
                "notes": _append_note(order, f"auto-cancel skipped: {reason}"),
            },
        )
        LOGGER.warning("Auto-cancel skipped because trading is disabled symbol=%s reason=%s", symbol, reason)
        return True
    updates = {
        "status": target_status,
        "cancelled_at": _iso_now(),
        "closed_at": _iso_now(),
        "last_sync_at": _iso_now(),
        "last_error": None,
        "notes": _append_note(order, f"auto-cancel: {reason}"),
    }
    try:
        private_client.cancel_order(
            symbol=symbol,
            order_id=exchange_order_id,
            order_link_id=order_link_id,
        )
    except Exception as exc:
        storage.update_paper_order(
            order_id,
            {
                "last_sync_at": _iso_now(),
                "last_error": _sanitize(exc),
                "notes": _append_note(order, f"auto-cancel failed: {reason}"),
            },
        )
        LOGGER.warning("Auto-cancel API error symbol=%s reason=%s error=%s", symbol, reason, exc)
        return True

    storage.update_paper_order(order_id, updates)
    _send_auto_cancel_message(telegram, order, reason)
    LOGGER.warning("Order auto-cancelled: %s reason=%s", symbol, reason)
    return True


def _auto_cancel_reason(
    order: dict[str, Any],
    storage: Any,
    duplicate_keeper: dict[str, str],
    private_client: Any,
    ticker_cache: dict[str, dict[str, Any] | None],
) -> str | None:
    if config.AUTO_CANCEL_EXPIRED_ORDERS and _is_expired(order):
        return "expired"
    if config.AUTO_CANCEL_INVALIDATED_BEFORE_FILL and _setup_invalidated_before_fill(order, storage):
        return "invalidated before fill"
    if str(order.get("execution_status") or "") == "TOO_LATE_DO_NOT_CHASE":
        return "too late"
    if _price_too_late_before_fill(order, private_client, ticker_cache):
        return "too late"
    symbol = str(order.get("symbol") or "")
    keeper_id = duplicate_keeper.get(symbol)
    if keeper_id and keeper_id != str(order.get("id") or ""):
        return "duplicate"
    return None


def _price_too_late_before_fill(
    order: dict[str, Any],
    private_client: Any,
    ticker_cache: dict[str, dict[str, Any] | None],
) -> bool:
    symbol = str(order.get("symbol") or "")
    if not symbol:
        return False
    try:
        if symbol not in ticker_cache:
            ticker_cache[symbol] = private_client.get_ticker(symbol)
        ticker = ticker_cache.get(symbol) or {}
    except Exception as exc:
        LOGGER.warning("Could not check latest ticker for %s: %s", symbol, exc)
        return False

    latest_price = _to_float(ticker.get("lastPrice"))
    entry_low = _to_float(order.get("entry_low"))
    entry_high = _to_float(order.get("entry_high"))
    side = str(order.get("side") or "")
    if latest_price is None or entry_low is None or entry_high is None:
        return False

    chase = config.MAX_CHASE_DISTANCE_PERCENT / 100
    if side == "LONG":
        return latest_price > entry_high * (1 + chase)
    if side == "SHORT":
        return latest_price < entry_low * (1 - chase)
    return False


def _active_testnet_orders(storage: Any) -> list[dict[str, Any]]:
    return [
        order
        for order in storage.get_paper_orders(limit=None)
        if order.get("mode") == "TESTNET"
        and str(order.get("status") or "") in ACTIVE_TESTNET_STATUSES
    ]


def _index_open_orders(open_orders: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for order in open_orders:
        for key in ("orderId", "orderLinkId"):
            value = str(order.get(key) or "")
            if value:
                indexed[value] = order
        symbol = str(order.get("symbol") or "")
        price = str(order.get("price") or "")
        qty = str(order.get("qty") or "")
        side = str(order.get("side") or "")
        if symbol and price and qty and side:
            indexed[f"{symbol}:{side}:{price}:{qty}"] = order
    return indexed


def _match_indexed_order(
    open_index: dict[str, dict[str, Any]],
    order: dict[str, Any],
) -> dict[str, Any] | None:
    for key in (order.get("exchange_order_id"), order.get("order_link_id")):
        value = str(key or "")
        if value and value in open_index:
            return open_index[value]
    symbol = str(order.get("symbol") or "")
    side = "Buy" if str(order.get("side") or "") == "LONG" else "Sell"
    price = str(order.get("entry_price") or "")
    qty = str(order.get("qty") or "")
    return open_index.get(f"{symbol}:{side}:{price}:{qty}")


def _find_bybit_order(
    rows: list[dict[str, Any]],
    order: dict[str, Any],
) -> dict[str, Any] | None:
    order_id = str(order.get("exchange_order_id") or "")
    order_link_id = str(order.get("order_link_id") or "")
    for row in rows:
        if order_id and str(row.get("orderId") or "") == order_id:
            return row
        if order_link_id and str(row.get("orderLinkId") or "") == order_link_id:
            return row
    return None


def _fetch_history_order(private_client: Any, order: dict[str, Any]) -> dict[str, Any] | None:
    try:
        rows = private_client.get_order_history(
            symbol=str(order.get("symbol") or ""),
            order_id=str(order.get("exchange_order_id") or "") or None,
            order_link_id=str(order.get("order_link_id") or "") or None,
            limit=20,
        )
    except Exception as exc:
        LOGGER.warning("Order history fetch failed symbol=%s error=%s", order.get("symbol"), exc)
        return None
    return _find_bybit_order(rows, order)


def _map_bybit_order_status(value: Any) -> str:
    status = str(value or "")
    mapping = {
        "Created": "OPEN",
        "New": "OPEN",
        "Untriggered": "OPEN",
        "PartiallyFilled": "PARTIALLY_FILLED",
        "Filled": "FILLED",
        "Cancelled": "CANCELLED",
        "Deactivated": "CANCELLED",
        "Rejected": "REJECTED",
    }
    return mapping.get(status, "UNKNOWN")


def _has_execution(row: dict[str, Any]) -> bool:
    return (_to_float(row.get("cumExecQty")) or 0) > 0 or (_to_float(row.get("avgPrice")) or 0) > 0


def _matching_position(
    order: dict[str, Any],
    positions: list[dict[str, Any]],
) -> dict[str, Any] | None:
    symbol = str(order.get("symbol") or "")
    expected_side = "Buy" if str(order.get("side") or "") == "LONG" else "Sell"
    for position in positions:
        if str(position.get("symbol") or "") != symbol:
            continue
        if str(position.get("side") or "") != expected_side:
            continue
        if (_to_float(position.get("size")) or 0) > 0:
            return position
    return None


def _position_info(position: dict[str, Any]) -> dict[str, Any]:
    return {
        "entry_price": _to_float(position.get("avgPrice")),
        "size": _to_float(position.get("size")),
        "unrealized_pnl": _to_float(position.get("unrealisedPnl")),
        "leverage": _to_float(position.get("leverage")),
        "liquidation_price": _to_float(position.get("liqPrice")),
    }


def _first_prefill_order_by_symbol(orders: list[dict[str, Any]]) -> dict[str, str]:
    keeper: dict[str, str] = {}
    sorted_orders = sorted(orders, key=lambda item: str(item.get("created_at") or ""))
    for order in sorted_orders:
        if str(order.get("status") or "") not in PREFILL_STATUSES:
            continue
        symbol = str(order.get("symbol") or "")
        if symbol and symbol not in keeper:
            keeper[symbol] = str(order.get("id") or "")
    return keeper


def _is_expired(order: dict[str, Any]) -> bool:
    created_at = _parse_iso(order.get("submitted_at") or order.get("created_at"))
    if created_at is None:
        return False
    return datetime.now(UTC) - created_at > timedelta(minutes=config.ORDER_MAX_LIFETIME_MINUTES)


def _setup_invalidated_before_fill(order: dict[str, Any], storage: Any) -> bool:
    source_setup_id = str(order.get("source_setup_id") or "")
    symbol = str(order.get("symbol") or "")
    if not source_setup_id and not symbol:
        return False
    active_ids = {str(setup.get("id") or "") for setup in storage.get_active_setups()}
    if source_setup_id and source_setup_id in active_ids:
        return False

    for record in storage.get_setup_journal(limit=None):
        if str(record.get("symbol") or "") != symbol:
            continue
        if not _same_setup_reference(source_setup_id, record):
            continue
        final_state = str(record.get("final_state") or record.get("state") or "")
        if final_state in {"INVALIDATED_BEFORE_TP1", "AMBIGUOUS_INVALIDATION_FIRST", "INVALIDATED"}:
            return True
    return False


def _same_setup_reference(source_setup_id: str, record: dict[str, Any]) -> bool:
    source_norm = _normalize_reference(source_setup_id)
    if not source_norm:
        return False
    candidates = [
        _normalize_reference(record.get("id")),
        _normalize_reference(record.get("source_setup_id")),
    ]
    for candidate in candidates:
        if candidate and (candidate in source_norm or source_norm in candidate):
            return True
    return False


def _normalize_reference(value: Any) -> str:
    return "".join(char for char in str(value or "") if char.isalnum()).lower()


def _meaningful_update(order: dict[str, Any], updates: dict[str, Any]) -> bool:
    ignored = {"last_sync_at", "last_error"}
    for key, value in updates.items():
        if key in ignored:
            continue
        if order.get(key) != value:
            return True
    return False


def _append_note(order: dict[str, Any], note: str) -> list[str]:
    notes = order.get("notes")
    if not isinstance(notes, list):
        notes = []
    return [*notes, note]


def _send_auto_cancel_message(telegram: Any | None, order: dict[str, Any], reason: str) -> None:
    if telegram is None or order.get("auto_cancel_notification_sent"):
        return
    telegram.send_message(
        "\n".join(
            [
                "⚠️ TESTNET order auto-cancelled",
                "",
                f"Монета: {order.get('symbol')}",
                f"Причина: {reason}",
                f"Order ID: {order.get('exchange_order_id') or order.get('order_link_id') or order.get('id')}",
            ]
        )
    )


def _send_filled_message(
    telegram: Any | None,
    order: dict[str, Any],
    updates: dict[str, Any],
) -> None:
    if telegram is None:
        return
    lines = [
        "✅ TESTNET order filled",
        "",
        f"Монета: {order.get('symbol')}",
        f"Side: {_direction_ru(order.get('side'))}",
        f"Entry: {_fmt(order.get('entry_price'))}",
        f"Qty: {_fmt(order.get('qty'))}",
        "",
    ]
    if not config.AUTO_PLACE_TP_SL:
        lines.extend(
            [
                "⚠️ TP/SL не выставлены автоматически.",
                f"TP1: {_fmt(order.get('tp1'))}",
                f"TP2: {_fmt(order.get('tp2'))}",
                f"Stop: {_fmt(order.get('stop_price'))}",
            ]
        )
    else:
        lines.append("⚠️ AUTO_PLACE_TP_SL=true, но авто TP/SL для REAL рынка в этой фазе запрещён.")
    telegram.send_message(
        "\n".join(lines),
        reply_markup=build_order_lifecycle_keyboard(str(order.get("id") or "")),
    )


def _send_position_closed_message(telegram: Any | None, order: dict[str, Any]) -> None:
    if telegram is None:
        return
    telegram.send_message(
        "\n".join(
            [
                "✅ TESTNET position closed",
                "",
                f"Монета: {order.get('symbol')}",
                f"Side: {_direction_ru(order.get('side'))}",
                f"Order ID: {order.get('exchange_order_id') or order.get('order_link_id') or order.get('id')}",
            ]
        )
    )


def _sync_failure(
    storage: Any,
    telegram: Any | None,
    summary: dict[str, Any],
    reason: str,
    notify: bool,
) -> dict[str, Any]:
    summary["errors"] += 1
    summary["messages"].append(reason)
    count = int(storage.get_runtime_state("order_sync_failure_count", 0) or 0) + 1
    storage.save_runtime_state("order_sync_failure_count", count)
    LOGGER.error("Order sync failed: %s", reason)
    if notify and telegram is not None and count == 3:
        telegram.send_message(f"⚠️ Order sync error x3\nПричина: {reason}")
    return summary


def _reset_failure_counter(storage: Any) -> None:
    if storage.get_runtime_state("order_sync_failure_count", 0):
        storage.save_runtime_state("order_sync_failure_count", 0)


def _direction_ru(value: Any) -> str:
    return {"LONG": "ЛОНГ", "SHORT": "ШОРТ"}.get(str(value or ""), str(value or "n/a"))


def _fmt(value: Any) -> str:
    if value is None or value == "":
        return "n/a"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _format_time(value: Any) -> str:
    parsed = _parse_iso(value)
    if parsed is None:
        return "n/a"
    return parsed.strftime("%H:%M:%S UTC")


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


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sanitize(value: Any) -> str:
    text = str(value)
    for secret in (config.BYBIT_API_SECRET, config.BYBIT_API_KEY):
        if secret:
            text = text.replace(secret, "[hidden]")
    if len(text) > 180:
        return text[:177] + "..."
    return text
