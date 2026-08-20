#!/usr/bin/env python3
"""日経225・TOPIX・NT予測の純計算と不変条件検証を行う。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from pathlib import Path
from statistics import NormalDist
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


MODEL_VERSION = "5.0.0"
SCHEMA_VERSION = "6.0.0"
DISPLAY_DATETIME_FORMAT = "%Y/%m/%d %H:%M:%S"
DISPLAY_DATETIME_SUFFIX = " (JST)"
TOP_LEVEL_FIELDS = frozenset(
    {
        "schema_version",
        "sample_only",
        "_sample_notice",
        "as_of_jst",
        "timing",
        "probability",
        "calibration",
        "nt",
        "daily_forecast",
        "market_data_freshness",
        "coverage",
        "other_sources",
    }
)
JST = timezone(timedelta(hours=9), name="JST")
CATEGORIES = (
    "nk_up_topix_up",
    "nk_up_topix_down",
    "nk_down_topix_up",
    "nk_down_topix_down",
)
CATEGORY_LABELS = {
    "nk_up_topix_up": "日経買い・TOPIX買い",
    "nk_up_topix_down": "日経買い・TOPIX売り",
    "nk_down_topix_up": "日経売り・TOPIX買い",
    "nk_down_topix_down": "日経売り・TOPIX売り",
}
EVIDENCE_BLOCKS = ("fundamental", "supply_demand")
EVIDENCE_WEIGHTS = {"fundamental": 0.5, "supply_demand": 0.5}
EVIDENCE_TEMPERATURE = 2.0
DOMINANCE_FLOOR_BP = 5_010
TOTAL_PROBABILITY_BP = 10_000
NIKKEI_MULTIPLIERS = {"mini": Decimal("100"), "micro": Decimal("10")}
TOPIX_MINI_MULTIPLIER = Decimal("1000")
COVERAGE_ITEMS = (
    "news",
    "realtime_fx",
    "economic_calendar",
    "polymarket",
    "overseas_markets",
    "jgb",
    "ust",
    "oil",
    "gold",
    "crypto",
    "jquants",
    "options",
)
MIN_CALIBRATION_SAMPLE_COUNT = 200
MIN_CALIBRATION_QUADRANT_COUNT = 20
COVERAGE_CHECK_MAX_AGE_HOURS = 24.0
RUNTIME_AS_OF_MAX_AGE_SECONDS = 120.0
RUNTIME_FUTURE_SKEW_SECONDS = 30.0
COVERAGE_USED_MAX_AGE_HOURS = {
    "news": 24.0,
    "realtime_fx": 5.0 / 60.0,
    "economic_calendar": 24.0,
    "polymarket": 15.0 / 60.0,
    "overseas_markets": 15.0 / 60.0,
    "jgb": 72.0,
    "ust": 72.0,
    "oil": 30.0 / 60.0,
    "gold": 30.0 / 60.0,
    "crypto": 5.0 / 60.0,
    "jquants": 168.0,
    "options": 5.0 / 60.0,
}
FUTURES_SESSION_KINDS = ("day_session", "night_session")
FUTURES_SESSION_PHASES = ("regular", "pre_closing")
CASH_MARKET_STATES = ("open", "recess", "closed", "holiday_closed")
HOLIDAY_DIVISIONS = ("0", "1", "2", "3")
MARKET_DATA_PROVIDER_PRIORITY = {
    "kabus": 1,
    "225225": 2,
    "jquants": 3,
    "other_market": 4,
}
LIVE_PROVIDER_MAX_AGE_SECONDS = {
    "kabus": 120.0,
    "225225": 300.0,
}
KABUS_MARKET_CODES = {
    "day_session": (2, 23),
    "night_session": (2, 24),
}
MARKET_DATA_ROLES = (
    "execution_anchor",
    "near_realtime_context",
    "scheduled_history",
)
MARKET_DATA_SOURCE_STATUSES = (
    "valid",
    "stale",
    "unavailable",
    "invalid",
    "not_supported",
)
PRICE_COVERAGE_ITEMS = (
    "realtime_fx",
    "overseas_markets",
    "jgb",
    "ust",
    "oil",
    "gold",
    "crypto",
    "options",
)
JQUANTS_UPDATE_PROFILES = {
    "daily_prices_1630": (16, 30, "daily", 10, 10),
    "investor_type_weekly_1800": (18, 0, "weekly", 8, 21),
    "trading_breakdown_1800": (18, 0, "daily", 10, 10),
    "issue_open_interest_2115": (21, 15, "daily", 10, 10),
}
JQUANTS_DATASET_PROFILES = {
    "daily-prices": "daily_prices_1630",
    "investor-type": "investor_type_weekly_1800",
    "trading-breakdown": "trading_breakdown_1800",
    "issue-open-interest": "issue_open_interest_2115",
}
JQUANTS_PUBLICATION_CALENDAR_START_OFFSET_DAYS = 21
JQUANTS_PUBLICATION_CALENDAR_BASE_END_OFFSET_DAYS = 27
JQUANTS_PUBLICATION_CALENDAR_MAX_END_OFFSET_DAYS = 68


class InputValidationError(ValueError):
    """入力JSONが計算契約を満たさない場合の例外。"""


# ----------------------------------------


def _runtime_current_jst() -> datetime:
    """
    機能:
        分析入力の現在性を検証するため、OS実時計の現在JSTを取得する。

    引数:
        なし。

    返り値:
        datetime: JSTへ変換したタイムゾーン付きのOS現在時刻。
    """
    return datetime.now(tz=JST)


# ----------------------------------------


def _require_mapping(value: Any, name: str) -> Mapping[str, Any]:
    """
    機能:
        値がマッピングであることを検証する。

    引数:
        value (Any): 検証対象の値。
        name (str): エラー表示用の項目名。

    返り値:
        Mapping[str, Any]: 検証済みのマッピング。
    """
    if not isinstance(value, Mapping):
        raise InputValidationError(f"{name} はオブジェクトで指定してください。")
    return value


# ----------------------------------------


def _require_sequence(value: Any, name: str) -> Sequence[Any]:
    """
    機能:
        値が文字列以外の配列であることを検証する。

    引数:
        value (Any): 検証対象の値。
        name (str): エラー表示用の項目名。

    返り値:
        Sequence[Any]: 検証済みの配列。
    """
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise InputValidationError(f"{name} は配列で指定してください。")
    return value


# ----------------------------------------


def _normalized_identifier_list(value: Any, name: str) -> list[str]:
    """
    機能:
        識別子配列を前後空白除去・大文字小文字非依存の重複検査付きで正規化する。

    引数:
        value (Any): 識別子の配列。
        name (str): エラー表示用の項目名。

    返り値:
        list[str]: 入力順を保った正規化済み識別子。
    """
    raw_values = _require_sequence(value, name)
    result: list[str] = []
    seen: set[str] = set()
    for index, raw_value in enumerate(raw_values):
        if not isinstance(raw_value, str) or not raw_value.strip():
            raise InputValidationError(f"{name}[{index}] は非空文字列にしてください。")
        normalized = raw_value.strip()
        deduplication_key = normalized.casefold()
        if deduplication_key in seen:
            raise InputValidationError(f"{name} は正規化後も重複させないでください。")
        seen.add(deduplication_key)
        result.append(normalized)
    return result


# ----------------------------------------


def _normalized_source_links(
    value: Any,
    name: str,
    as_of_jst: datetime,
) -> list[dict[str, str]]:
    """
    機能:
        モデル・分散等の情報源リンクを時刻・識別子・重複付きで正規化する。

    引数:
        value (Any): coverage項目、source_id、対象時刻を持つ配列。
        name (str): エラー表示用の項目名。
        as_of_jst (datetime): 未来参照を防ぐ分析基準時刻。

    返り値:
        list[dict[str, str]]: 正規化済みの情報源リンク。
    """
    raw_links = _require_sequence(value, name)
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for index, raw_link in enumerate(raw_links):
        link = _require_mapping(raw_link, f"{name}[{index}]")
        coverage_item = link.get("coverage_item")
        if coverage_item not in (*COVERAGE_ITEMS, "other"):
            raise InputValidationError(f"{name}[{index}].coverage_item が不正です。")
        source_id_raw = link.get("source_id")
        if not isinstance(source_id_raw, str) or not source_id_raw.strip():
            raise InputValidationError(f"{name}[{index}].source_id を指定してください。")
        source_id = source_id_raw.strip()
        data_as_of = _parse_jst(link.get("data_as_of_jst"), f"{name}[{index}].data_as_of_jst")
        if data_as_of > as_of_jst:
            raise InputValidationError(f"{name}[{index}] の参照時刻が分析時刻より未来です。")
        deduplication_key = (str(coverage_item), source_id.casefold())
        if deduplication_key in seen:
            raise InputValidationError(f"{name} のcoverage_itemとsource_idが重複しています。")
        seen.add(deduplication_key)
        result.append(
            {
                "coverage_item": str(coverage_item),
                "source_id": source_id,
                "data_as_of_jst": _format_jst(data_as_of),
            }
        )
    if not result:
        raise InputValidationError(f"{name} を1件以上指定してください。")
    return result


# ----------------------------------------


def _validated_model_provenance(
    spec: Mapping[str, Any],
    context: str,
    as_of_jst: datetime,
    expected_horizon_definition: str,
    expected_horizon_seconds: int | None,
) -> dict[str, Any]:
    """
    機能:
        予測モデルの版、構造、学習打切時刻、期限、検証状態、原典リンクを検証する。

    引数:
        spec (Mapping[str, Any]): モデル来歴の入力。
        context (str): エラー表示用の項目名。
        as_of_jst (datetime): 学習データの未来参照を防ぐ分析基準時刻。
        expected_horizon_definition (str): 必須とする期限定義ID。
        expected_horizon_seconds (int | None): 一致を必須とする秒数。未指定時は秒数項目を使わない。

    返り値:
        dict[str, Any]: 正規化済みのモデル来歴。
    """
    text_values: dict[str, str] = {}
    for field_name in (
        "model_id",
        "model_version",
        "calibration_version",
        "method",
        "model_structure_id",
        "model_artifact_sha256",
        "horizon_definition",
    ):
        raw_value = spec.get(field_name)
        if not isinstance(raw_value, str) or not raw_value.strip():
            raise InputValidationError(f"{context}.{field_name} は非空文字列にしてください。")
        text_values[field_name] = raw_value.strip()
    model_artifact_sha256 = text_values["model_artifact_sha256"]
    if len(model_artifact_sha256) != 64 or any(
        character not in "0123456789abcdef"
        for character in model_artifact_sha256
    ):
        raise InputValidationError(f"{context}.model_artifact_sha256 は小文字16進64文字にしてください。")
    if text_values["horizon_definition"] != expected_horizon_definition:
        raise InputValidationError(
            f"{context}.horizon_definition は {expected_horizon_definition} にしてください。"
        )
    training_cutoff = _parse_jst(
        spec.get("training_data_cutoff_jst"),
        f"{context}.training_data_cutoff_jst",
    )
    if training_cutoff > as_of_jst:
        raise InputValidationError(f"{context}.training_data_cutoff_jst が分析時刻より未来です。")
    horizon_seconds: int | None = None
    if expected_horizon_seconds is not None:
        horizon_seconds = _bounded_integer(
            spec.get("horizon_seconds"),
            f"{context}.horizon_seconds",
            1,
            31_536_000,
        )
        if horizon_seconds != expected_horizon_seconds:
            raise InputValidationError(f"{context}.horizon_seconds を今回の予測期限と一致させてください。")
    validation_passed = spec.get("validation_passed")
    if not isinstance(validation_passed, bool):
        raise InputValidationError(f"{context}.validation_passed は真偽値にしてください。")
    source_links = _normalized_source_links(
        spec.get("source_links"),
        f"{context}.source_links",
        as_of_jst,
    )
    supported_origin_sessions = _normalized_identifier_list(
        spec.get("supported_origin_sessions"),
        f"{context}.supported_origin_sessions",
    )
    if any(value not in FUTURES_SESSION_KINDS for value in supported_origin_sessions):
        raise InputValidationError(
            f"{context}.supported_origin_sessions はday_session/night_sessionだけにしてください。"
        )
    session_path_definition_id = _required_text(
        spec.get("session_path_definition_id"),
        f"{context}.session_path_definition_id",
    )
    if session_path_definition_id != "current_continuous_session_no_recess_crossing_v1":
        raise InputValidationError(
            f"{context}.session_path_definition_id は current_continuous_session_no_recess_crossing_v1 にしてください。"
        )
    return {
        **text_values,
        "training_data_cutoff_jst": _format_jst(training_cutoff),
        "horizon_seconds": horizon_seconds,
        "supported_origin_sessions": supported_origin_sessions,
        "session_path_definition_id": session_path_definition_id,
        "validation_passed": validation_passed,
        "source_links": source_links,
    }


# ----------------------------------------


def _normalized_attributions(
    value: Any,
    context: str,
    reference_field: str,
) -> list[dict[str, Any]]:
    """
    機能:
        日次またはNT相対ドリフトの寄与を根拠識別子とbpへ正規化する。

    引数:
        value (Any): 根拠識別子とcontribution_bpsを持つ配列。
        context (str): エラー表示用の項目名。
        reference_field (str): evidence_keyまたはevent_id。

    返り値:
        list[dict[str, Any]]: 正規化済み寄与行。
    """
    raw_rows = _require_sequence(value, context)
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw_row in enumerate(raw_rows):
        row = _require_mapping(raw_row, f"{context}[{index}]")
        reference_raw = row.get(reference_field)
        if not isinstance(reference_raw, str) or not reference_raw.strip():
            raise InputValidationError(f"{context}[{index}].{reference_field} を指定してください。")
        reference = reference_raw.strip()
        deduplication_key = reference.casefold()
        if deduplication_key in seen:
            raise InputValidationError(f"{context} の{reference_field}が重複しています。")
        seen.add(deduplication_key)
        contribution_bps = _bounded_float(
            row.get("contribution_bps"),
            f"{context}[{index}].contribution_bps",
            -2_000.0,
            2_000.0,
        )
        if math.isclose(contribution_bps, 0.0, rel_tol=0.0, abs_tol=1e-12):
            raise InputValidationError(f"{context}[{index}].contribution_bps は0以外にしてください。")
        result.append({reference_field: reference, "contribution_bps": contribution_bps})
    return result


# ----------------------------------------


def _finite_float(value: Any, name: str) -> float:
    """
    機能:
        値を有限の浮動小数点数へ変換する。

    引数:
        value (Any): 数値へ変換する値。
        name (str): エラー表示用の項目名。

    返り値:
        float: 有限性を確認した数値。
    """
    if isinstance(value, bool):
        raise InputValidationError(f"{name} は真偽値ではなく数値で指定してください。")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise InputValidationError(f"{name} は数値で指定してください。") from exc
    if not math.isfinite(result):
        raise InputValidationError(f"{name} は有限値で指定してください。")
    return result


# ----------------------------------------


def _bounded_float(value: Any, name: str, lower: float, upper: float) -> float:
    """
    機能:
        値を有限数へ変換し、指定した閉区間に含まれることを検証する。

    引数:
        value (Any): 数値へ変換する値。
        name (str): エラー表示用の項目名。
        lower (float): 許容する最小値。
        upper (float): 許容する最大値。

    返り値:
        float: 範囲検証済みの数値。
    """
    result = _finite_float(value, name)
    if result < lower or result > upper:
        raise InputValidationError(f"{name} は {lower} 以上 {upper} 以下で指定してください。")
    return result


# ----------------------------------------


def _positive_decimal(value: Any, name: str) -> Decimal:
    """
    機能:
        値を正のDecimalへ変換する。

    引数:
        value (Any): Decimalへ変換する値。
        name (str): エラー表示用の項目名。

    返り値:
        Decimal: 正値であることを確認したDecimal。
    """
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise InputValidationError(f"{name} は数値文字列または数値で指定してください。") from exc
    if not result.is_finite() or result <= 0:
        raise InputValidationError(f"{name} は正の有限値で指定してください。")
    return result


# ----------------------------------------


def _bounded_integer(value: Any, name: str, lower: int, upper: int) -> int:
    """
    機能:
        真偽値や小数を許容せず、指定範囲内の整数へ変換する。

    引数:
        value (Any): 整数へ変換する値。
        name (str): エラー表示用の項目名。
        lower (int): 許容する最小値。
        upper (int): 許容する最大値。

    返り値:
        int: 整数性と範囲を確認した値。
    """
    if isinstance(value, bool):
        raise InputValidationError(f"{name} は真偽値ではなく整数で指定してください。")
    try:
        numeric = Decimal(str(value))
    except Exception as exc:
        raise InputValidationError(f"{name} は整数で指定してください。") from exc
    if not numeric.is_finite() or numeric != numeric.to_integral_value():
        raise InputValidationError(f"{name} は整数で指定してください。")
    result = int(numeric)
    if result < lower or result > upper:
        raise InputValidationError(f"{name} は {lower} 以上 {upper} 以下で指定してください。")
    return result


# ----------------------------------------


def _parse_jst(value: Any, name: str) -> datetime:
    """
    機能:
        ISO 8601入力または画面表示用のJST日時を解析し、日本時間へ正規化する。

    引数:
        value (Any): タイムゾーン付きISO 8601、またはyyyy/MM/dd HH:mm:ss (JST)形式の文字列。
        name (str): エラー表示用の項目名。

    返り値:
        datetime: JSTへ変換したタイムゾーン付き日時。
    """
    if not isinstance(value, str):
        raise InputValidationError(
            f"{name} はタイムゾーン付きISO 8601またはyyyy/MM/dd HH:mm:ss (JST)文字列で指定してください。"
        )
    try:
        stripped = value.strip()
        if stripped.endswith(DISPLAY_DATETIME_SUFFIX):
            display_value = stripped[: -len(DISPLAY_DATETIME_SUFFIX)]
            parsed = datetime.strptime(display_value, DISPLAY_DATETIME_FORMAT).replace(tzinfo=JST)
        elif len(stripped) == 19 and stripped[4] == "/" and stripped[7] == "/":
            parsed = datetime.strptime(stripped, DISPLAY_DATETIME_FORMAT).replace(tzinfo=JST)
        else:
            normalized = stripped[:-4] + "+09:00" if stripped.endswith(" JST") else stripped.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise InputValidationError(f"{name} の日時形式が不正です。") from exc
    if parsed.tzinfo is None:
        raise InputValidationError(f"{name} にはタイムゾーンを含めてください。")
    return parsed.astimezone(JST)


# ----------------------------------------


def _parse_aware_datetime(value: Any, name: str) -> datetime:
    """
    機能:
        UTC offsetを保持したISO 8601日時またはスラッシュ区切り日時を解析する。

    引数:
        value (Any): UTC offset付きISO 8601またはyyyy/MM/dd HH:mm:ss±HH:MM文字列。
        name (str): エラー表示用の項目名。

    返り値:
        datetime: 入力されたUTC offsetを保持するタイムゾーン付き日時。
    """
    if not isinstance(value, str):
        raise InputValidationError(f"{name} はUTC offset付き日時文字列で指定してください。")
    try:
        normalized = value.strip().replace("/", "-")
        normalized = normalized[:-4] + "+09:00" if normalized.endswith(" JST") else normalized.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise InputValidationError(f"{name} の日時形式が不正です。") from exc
    if parsed.tzinfo is None:
        raise InputValidationError(f"{name} にはUTC offsetを含めてください。")
    return parsed


# ----------------------------------------


def _nth_weekday_of_month(year: int, month: int, weekday: int, occurrence: int) -> date:
    """
    機能:
        指定年月の第N回目の曜日を計算する。

    引数:
        year (int): 西暦年。
        month (int): 月。
        weekday (int): 月曜0〜日曜6の曜日番号。
        occurrence (int): 1から始まる出現回数。

    返り値:
        date: 条件に一致する日付。
    """
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (occurrence - 1))


# ----------------------------------------


def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    """
    機能:
        指定年月の最後の指定曜日を計算する。

    引数:
        year (int): 西暦年。
        month (int): 月。
        weekday (int): 月曜0〜日曜6の曜日番号。

    返り値:
        date: 条件に一致する日付。
    """
    next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    last = next_month - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


# ----------------------------------------


def _fallback_timezone_offsets(local_datetime: datetime, timezone_name: str) -> set[timedelta]:
    """
    機能:
        OSのIANA tzdataがない環境で主要経済カレンダー地域の有効UTC offset候補を計算する。

    引数:
        local_datetime (datetime): offsetを除いた発表元の壁時計日時。
        timezone_name (str): IANA timezone名。

    返り値:
        set[timedelta]: 当該壁時計で有効なUTC offset集合。非存在時刻は空集合。
    """
    fixed_offsets = {
        "Asia/Tokyo": 9,
        "Asia/Seoul": 9,
        "Asia/Shanghai": 8,
        "Asia/Hong_Kong": 8,
        "Asia/Singapore": 8,
        "UTC": 0,
        "Etc/UTC": 0,
    }
    if timezone_name in fixed_offsets:
        return {timedelta(hours=fixed_offsets[timezone_name])}

    us_timezone_offsets = {
        "America/New_York": (-5, -4),
        "America/Chicago": (-6, -5),
        "America/Denver": (-7, -6),
        "America/Los_Angeles": (-8, -7),
    }
    if timezone_name in us_timezone_offsets:
        standard_offset, daylight_offset = us_timezone_offsets[timezone_name]
        start_date = _nth_weekday_of_month(local_datetime.year, 3, 6, 2)
        end_date = _nth_weekday_of_month(local_datetime.year, 11, 6, 1)
        start = datetime.combine(start_date, datetime.min.time()).replace(hour=2)
        end = datetime.combine(end_date, datetime.min.time()).replace(hour=2)
        if start <= local_datetime < start + timedelta(hours=1):
            return set()
        if end - timedelta(hours=1) <= local_datetime < end:
            return {timedelta(hours=daylight_offset), timedelta(hours=standard_offset)}
        if start + timedelta(hours=1) <= local_datetime < end - timedelta(hours=1):
            return {timedelta(hours=daylight_offset)}
        return {timedelta(hours=standard_offset)}

    european_base_offsets = {
        "Europe/London": 0,
        "Europe/Brussels": 1,
        "Europe/Berlin": 1,
        "Europe/Paris": 1,
        "Europe/Rome": 1,
        "Europe/Madrid": 1,
        "Europe/Vienna": 1,
        "Europe/Zurich": 1,
    }
    if timezone_name in european_base_offsets:
        base = european_base_offsets[timezone_name]
        start_date = _last_weekday_of_month(local_datetime.year, 3, 6)
        end_date = _last_weekday_of_month(local_datetime.year, 10, 6)
        start = datetime.combine(start_date, datetime.min.time()).replace(hour=1 + base)
        end = datetime.combine(end_date, datetime.min.time()).replace(hour=2 + base)
        if start <= local_datetime < start + timedelta(hours=1):
            return set()
        if end - timedelta(hours=1) <= local_datetime < end:
            return {timedelta(hours=base), timedelta(hours=base + 1)}
        if start + timedelta(hours=1) <= local_datetime < end - timedelta(hours=1):
            return {timedelta(hours=base + 1)}
        return {timedelta(hours=base)}

    if timezone_name == "Australia/Sydney":
        end_date = _nth_weekday_of_month(local_datetime.year, 4, 6, 1)
        start_date = _nth_weekday_of_month(local_datetime.year, 10, 6, 1)
        end = datetime.combine(end_date, datetime.min.time()).replace(hour=3)
        start = datetime.combine(start_date, datetime.min.time()).replace(hour=2)
        if start <= local_datetime < start + timedelta(hours=1):
            return set()
        if end - timedelta(hours=1) <= local_datetime < end:
            return {timedelta(hours=10), timedelta(hours=11)}
        in_summer = local_datetime < end - timedelta(hours=1) or local_datetime >= start + timedelta(hours=1)
        return {timedelta(hours=11 if in_summer else 10)}

    raise InputValidationError(
        f"source_timezone={timezone_name} を検証するIANA tzdataがありません。tzdataを導入するか対応地域を使ってください。"
    )


# ----------------------------------------


def _source_timezone_offset_is_valid(source_datetime: datetime, timezone_name: str) -> bool:
    """
    機能:
        発表元の壁時計とUTC offsetがIANA timezoneの当日規則に一致するか検証する。

    引数:
        source_datetime (datetime): UTC offset付きの原時刻。
        timezone_name (str): IANA timezone名。

    返り値:
        bool: DSTを含むtimezone規則と一致する場合はtrue。
    """
    try:
        source_zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        valid_offsets = _fallback_timezone_offsets(source_datetime.replace(tzinfo=None), timezone_name)
        return source_datetime.utcoffset() in valid_offsets
    source_wall_clock = source_datetime.replace(tzinfo=None)
    round_trip_wall_clock = source_datetime.astimezone(source_zone).replace(tzinfo=None)
    return source_wall_clock == round_trip_wall_clock


# ----------------------------------------


def _format_jst(value: datetime) -> str:
    """
    機能:
        日時を秒精度の絶対JST表記へ整形する。

    引数:
        value (datetime): タイムゾーン付き日時。

    返り値:
        str: yyyy/MM/dd HH:mm:ss (JST)形式の文字列。
    """
    return value.astimezone(JST).strftime(DISPLAY_DATETIME_FORMAT) + DISPLAY_DATETIME_SUFFIX


# ----------------------------------------


def _required_text(value: Any, name: str) -> str:
    """
    機能:
        必須の文字列を前後空白除去付きで検証する。

    引数:
        value (Any): 検証対象の値。
        name (str): エラー表示用の項目名。

    返り値:
        str: 空でないことを確認した文字列。
    """
    if not isinstance(value, str) or not value.strip():
        raise InputValidationError(f"{name} は非空文字列にしてください。")
    return value.strip()


# ----------------------------------------


def _parse_iso_date(value: Any, name: str) -> date:
    """
    機能:
        YYYY-MM-DD形式の日付を解析する。

    引数:
        value (Any): 解析対象の値。
        name (str): エラー表示用の項目名。

    返り値:
        date: 解析済みの日付。
    """
    if not isinstance(value, str):
        raise InputValidationError(f"{name} はYYYY-MM-DD文字列にしてください。")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise InputValidationError(f"{name} はYYYY-MM-DD文字列にしてください。") from exc


# ----------------------------------------


def validate_session_context(spec: Mapping[str, Any], as_of_jst: datetime) -> dict[str, Any]:
    """
    機能:
        日中・ナイト先物の公式時刻、JPX取引日、現物市場状態を検証する。

    引数:
        spec (Mapping[str, Any]): timingブロックに含まれるセッション情報。
        as_of_jst (datetime): 分析基準の日本時間。

    返り値:
        dict[str, Any]: D1起算日と入口可否を含む正規化済みセッション情報。
    """
    session_kind = spec.get("futures_session_kind")
    if session_kind not in FUTURES_SESSION_KINDS:
        raise InputValidationError(
            "timing.futures_session_kind は day_session または night_session にしてください。"
        )
    session_phase = spec.get("futures_session_phase")
    if session_phase not in FUTURES_SESSION_PHASES:
        raise InputValidationError(
            "timing.futures_session_phase は regular または pre_closing にしてください。"
        )
    cash_market_state = spec.get("cash_market_state")
    if cash_market_state not in CASH_MARKET_STATES:
        raise InputValidationError(
            "timing.cash_market_state は open、recess、closed、holiday_closed のいずれかにしてください。"
        )

    session_start = _parse_jst(spec.get("session_start_jst"), "timing.session_start_jst")
    continuous_end = _parse_jst(spec.get("continuous_end_jst"), "timing.continuous_end_jst")
    session_end = _parse_jst(spec.get("session_end_jst"), "timing.session_end_jst")
    next_session_start = _parse_jst(
        spec.get("next_session_start_jst"),
        "timing.next_session_start_jst",
    )
    if not session_start <= as_of_jst < session_end:
        raise InputValidationError("as_of_jst を申告した先物セッションの開始以上・終了未満にしてください。")
    if session_start.weekday() >= 5:
        raise InputValidationError("土日に開始する指数先物セッションは指定できません。")
    if not session_start < continuous_end < session_end <= next_session_start:
        raise InputValidationError(
            "先物セッション時刻は開始 < ザラバ終了 < セッション終了 <= 次セッション開始にしてください。"
        )

    if session_kind == "day_session":
        expected_start = datetime.combine(session_start.date(), datetime.min.time(), JST).replace(
            hour=8,
            minute=45,
        )
        expected_continuous_end = expected_start.replace(hour=15, minute=40)
        expected_end = expected_start.replace(hour=15, minute=45)
        expected_next_start = expected_start.replace(hour=17, minute=0)
        if (
            session_start != expected_start
            or continuous_end != expected_continuous_end
            or session_end != expected_end
            or next_session_start != expected_next_start
        ):
            raise InputValidationError(
                "日中先物は08:45開始・15:40ザラバ終了・15:45終了・17:00次セッション開始に固定してください。"
            )
    else:
        expected_start = datetime.combine(session_start.date(), datetime.min.time(), JST).replace(
            hour=17,
            minute=0,
        )
        expected_continuous_end = expected_start + timedelta(hours=12, minutes=55)
        expected_end = expected_start + timedelta(hours=13)
        if (
            session_start != expected_start
            or continuous_end != expected_continuous_end
            or session_end != expected_end
        ):
            raise InputValidationError(
                "ナイト先物は17:00開始・翌05:55ザラバ終了・翌06:00終了に固定してください。"
            )
        if (
            next_session_start < session_end
            or (next_session_start.hour, next_session_start.minute, next_session_start.second) != (8, 45, 0)
            or next_session_start.weekday() >= 5
            or next_session_start - session_end > timedelta(days=10)
        ):
            raise InputValidationError(
                "ナイト後の次セッション開始は06:00以後10日以内の営業日08:45にしてください。"
            )

    expected_phase = "regular" if as_of_jst < continuous_end else "pre_closing"
    if session_phase != expected_phase:
        raise InputValidationError("timing.futures_session_phase をas_of_jstの公式時間帯と一致させてください。")

    holiday_division = str(spec.get("holiday_division", "")).strip()
    if holiday_division not in HOLIDAY_DIVISIONS:
        raise InputValidationError("timing.holiday_division はJ-Quants定義の0、1、2、3で指定してください。")
    cash_market_holiday_division = str(spec.get("cash_market_holiday_division", "")).strip()
    if cash_market_holiday_division not in HOLIDAY_DIVISIONS:
        raise InputValidationError(
            "timing.cash_market_holiday_division はas_of_jst当日のJ-Quants定義0、1、2、3で指定してください。"
        )
    is_holiday_trading = spec.get("is_derivatives_holiday_trading")
    if not isinstance(is_holiday_trading, bool):
        raise InputValidationError("timing.is_derivatives_holiday_trading は真偽値にしてください。")
    if holiday_division == "3" and not is_holiday_trading:
        raise InputValidationError(
            "holiday_division=3 では is_derivatives_holiday_trading=true にしてください。"
        )
    if cash_market_holiday_division == "3" and not is_holiday_trading:
        raise InputValidationError(
            "as_of_jst当日がholiday_division=3なら is_derivatives_holiday_trading=true にしてください。"
        )
    if cash_market_holiday_division == "2":
        raise InputValidationError("半日立会日はcash_market_holiday_division=2として自動予測できません。")
    if holiday_division not in ("1", "3"):
        raise InputValidationError("開場中の先物セッションはholiday_division=1または3にしてください。")

    exchange_trading_day = _parse_iso_date(
        spec.get("exchange_trading_day"),
        "timing.exchange_trading_day",
    )
    if exchange_trading_day.weekday() >= 5:
        raise InputValidationError("timing.exchange_trading_day はJPX営業日にしてください。")
    session_calendar_raw = _require_sequence(
        spec.get("session_calendar"),
        "timing.session_calendar",
    )
    expected_session_calendar_length = (exchange_trading_day - session_start.date()).days + 1
    if expected_session_calendar_length < 1 or expected_session_calendar_length > 11:
        raise InputValidationError(
            "timing.session_calendarはセッション開始日からexchange_trading_dayまで11暦日以内にしてください。"
        )
    if len(session_calendar_raw) != expected_session_calendar_length:
        raise InputValidationError(
            "timing.session_calendarはセッション開始日からexchange_trading_dayまで全暦日を指定してください。"
        )
    session_calendar: list[dict[str, Any]] = []
    for calendar_index, raw_calendar_row in enumerate(session_calendar_raw):
        calendar_row = _require_mapping(
            raw_calendar_row,
            f"timing.session_calendar[{calendar_index}]",
        )
        calendar_date = _parse_iso_date(
            calendar_row.get("date"),
            f"timing.session_calendar[{calendar_index}].date",
        )
        expected_calendar_date = session_start.date() + timedelta(days=calendar_index)
        if calendar_date != expected_calendar_date:
            raise InputValidationError("timing.session_calendarの日付をセッション開始日から連続させてください。")
        calendar_holiday_division = str(calendar_row.get("holiday_division", "")).strip()
        if calendar_holiday_division not in HOLIDAY_DIVISIONS:
            raise InputValidationError("timing.session_calendarのholiday_divisionが不正です。")
        if calendar_holiday_division == "2":
            raise InputValidationError("半日立会を含むsession_calendarは現行予測profileで扱えません。")
        cash_is_trading_day = calendar_row.get("cash_is_trading_day")
        derivatives_session_open = calendar_row.get("derivatives_session_open")
        if not isinstance(cash_is_trading_day, bool) or not isinstance(derivatives_session_open, bool):
            raise InputValidationError(
                "timing.session_calendarのcash_is_trading_dayとderivatives_session_openは真偽値にしてください。"
            )
        if cash_is_trading_day != (calendar_holiday_division == "1"):
            raise InputValidationError(
                "timing.session_calendarのcash_is_trading_dayをholiday_division=1と一致させてください。"
            )
        if derivatives_session_open != (calendar_holiday_division in ("1", "3")):
            raise InputValidationError(
                "timing.session_calendarの先物開場状態をholiday_division=1または3と一致させてください。"
            )
        if calendar_date.weekday() >= 5 and (
            cash_is_trading_day or derivatives_session_open or calendar_holiday_division != "0"
        ):
            raise InputValidationError("timing.session_calendarの土日を現物・先物開場日にできません。")
        session_calendar.append(
            {
                "date": calendar_date.isoformat(),
                "cash_is_trading_day": cash_is_trading_day,
                "derivatives_session_open": derivatives_session_open,
                "holiday_division": calendar_holiday_division,
            }
        )
    if session_calendar[0]["holiday_division"] != holiday_division:
        raise InputValidationError("timing.holiday_divisionをsession_calendar開始日と一致させてください。")
    as_of_calendar_rows = [row for row in session_calendar if row["date"] == as_of_jst.date().isoformat()]
    if not as_of_calendar_rows or as_of_calendar_rows[0]["holiday_division"] != cash_market_holiday_division:
        raise InputValidationError(
            "timing.cash_market_holiday_divisionをsession_calendarのas_of_jst暦日と一致させてください。"
        )
    future_derivatives_rows = [
        row
        for row in session_calendar
        if row["date"] >= session_end.date().isoformat() and row["derivatives_session_open"]
    ]
    if not future_derivatives_rows or future_derivatives_rows[0]["date"] != next_session_start.date().isoformat():
        raise InputValidationError(
            "timing.next_session_start_jstをsession_calendar上の最初の後続先物開場日へ一致させてください。"
        )
    cash_search_rows = session_calendar if session_kind == "day_session" else session_calendar[1:]
    first_cash_row = next((row for row in cash_search_rows if row["cash_is_trading_day"]), None)
    if first_cash_row is None or first_cash_row["date"] != exchange_trading_day.isoformat():
        raise InputValidationError(
            "timing.exchange_trading_dayをsession_calendar上の次の現物営業日へ一致させてください。"
        )
    chain_has_holiday_trading = any(row["holiday_division"] == "3" for row in session_calendar)
    if is_holiday_trading != chain_has_holiday_trading:
        raise InputValidationError(
            "timing.is_derivatives_holiday_tradingをsession_calendarのHolidayDivision=3有無と一致させてください。"
        )
    calendar_start_date = (
        exchange_trading_day + timedelta(days=1)
        if session_kind == "day_session" and holiday_division == "1"
        else exchange_trading_day
    )

    if cash_market_holiday_division != "1":
        expected_cash_state = "holiday_closed"
    elif session_kind == "night_session":
        expected_cash_state = "closed"
    else:
        current_time = as_of_jst.timetz().replace(tzinfo=None)
        if datetime.min.time().replace(hour=9) <= current_time < datetime.min.time().replace(hour=11, minute=30):
            expected_cash_state = "open"
        elif datetime.min.time().replace(hour=12, minute=30) <= current_time < datetime.min.time().replace(hour=15, minute=30):
            expected_cash_state = "open"
        elif datetime.min.time().replace(hour=11, minute=30) <= current_time < datetime.min.time().replace(hour=12, minute=30):
            expected_cash_state = "recess"
        else:
            expected_cash_state = "closed"
    if cash_market_state != expected_cash_state:
        raise InputValidationError("timing.cash_market_state を現物市場の公式時間帯と一致させてください。")

    session_checked_at = _parse_jst(
        spec.get("session_checked_at_jst"),
        "timing.session_checked_at_jst",
    )
    if session_checked_at > as_of_jst:
        raise InputValidationError("timing.session_checked_at_jst が分析時刻より未来です。")
    if (as_of_jst - session_checked_at).total_seconds() > COVERAGE_CHECK_MAX_AGE_HOURS * 3600:
        raise InputValidationError("先物セッション予定の確認が24時間より古いです。")

    return {
        "cash_market_state": cash_market_state,
        "futures_session_kind": session_kind,
        "futures_session_phase": session_phase,
        "exchange_trading_day": exchange_trading_day.isoformat(),
        "holiday_division": holiday_division,
        "cash_market_holiday_division": cash_market_holiday_division,
        "is_derivatives_holiday_trading": is_holiday_trading,
        "session_calendar": session_calendar,
        "session_start_jst": _format_jst(session_start),
        "continuous_end_jst": _format_jst(continuous_end),
        "session_end_jst": _format_jst(session_end),
        "next_session_start_jst": _format_jst(next_session_start),
        "session_schedule_source_id": _required_text(
            spec.get("session_schedule_source_id"),
            "timing.session_schedule_source_id",
        ),
        "session_schedule_source_url": _required_text(
            spec.get("session_schedule_source_url"),
            "timing.session_schedule_source_url",
        ),
        "session_checked_at_jst": _format_jst(session_checked_at),
        "calendar_start_date": calendar_start_date.isoformat(),
        "entry_phase_valid": session_phase == "regular",
    }


# ----------------------------------------


def _derive_jquants_publication_calendar_end(
    jquants_updates_raw: Sequence[Any],
    as_of_jst: datetime,
) -> date:
    """
    機能:
        週次公式公表予定の最大公表日から共有J-Quantsカレンダーの必要終端日を導出する。

    引数:
        jquants_updates_raw (Sequence[Any]): dataset別の未正規化J-Quants更新監査列。
        as_of_jst (datetime): 分析基準の日本時間。

    返り値:
        date: 基本49暦日と全週次公式公表予定を過不足なく覆う共有カレンダー終端日。
    """
    week_monday = as_of_jst.date() - timedelta(days=as_of_jst.weekday())
    base_end = week_monday + timedelta(
        days=JQUANTS_PUBLICATION_CALENDAR_BASE_END_OFFSET_DAYS
    )
    maximum_end = week_monday + timedelta(
        days=JQUANTS_PUBLICATION_CALENDAR_MAX_END_OFFSET_DAYS
    )
    shared_end = base_end
    for update_index, raw_update in enumerate(jquants_updates_raw):
        update = _require_mapping(
            raw_update,
            f"jquants_dataset_updates[{update_index}]",
        )
        dataset_id = _required_text(
            update.get("dataset_id"),
            f"jquants_dataset_updates[{update_index}].dataset_id",
        )
        profile_id = JQUANTS_DATASET_PROFILES.get(dataset_id.casefold())
        if profile_id is None or JQUANTS_UPDATE_PROFILES[profile_id][2] != "weekly":
            continue
        raw_schedule = _require_sequence(
            update.get("official_publication_schedule"),
            f"J-Quants週次更新 '{dataset_id}' の official_publication_schedule",
        )
        if not raw_schedule:
            raise InputValidationError(
                f"J-Quants週次更新 '{dataset_id}' の公式公表予定を1件以上指定してください。"
            )
        release_dates: list[date] = []
        for schedule_index, raw_schedule_row in enumerate(raw_schedule):
            schedule_row = _require_mapping(
                raw_schedule_row,
                f"J-Quants週次更新 '{dataset_id}' official_publication_schedule[{schedule_index}]",
            )
            release_at = _parse_jst(
                schedule_row.get("release_at_jst"),
                f"J-Quants週次更新 '{dataset_id}' official_publication_schedule[{schedule_index}].release_at_jst",
            )
            release_dates.append(release_at.date())
        dataset_end = max(base_end, max(release_dates))
        if dataset_end > maximum_end:
            raise InputValidationError(
                f"J-Quants週次更新 '{dataset_id}' の公式公表予定が共有カレンダーの最大90暦日を超えています。"
            )
        shared_end = max(shared_end, dataset_end)
    return shared_end


# ----------------------------------------


def _derive_daily_jquants_expected_state(
    dataset_id: str,
    as_of_jst: datetime,
    publication_cash_dates: Sequence[date],
    expected_hour: int,
    expected_minute: int,
    latest_available_reference_date: date,
    latest_available_published_at_jst: datetime,
) -> dict[str, Any]:
    """
    機能:
        日次J-Quantsの通常予定と同日早着を分け、支配的な次回予定・期待対象日を導出する。

    引数:
        dataset_id (str): エラー表示用のJ-Quants dataset識別子。
        as_of_jst (datetime): 分析基準の日本時間。
        publication_cash_dates (Sequence[date]): 共有カレンダー内の現物営業日列。
        expected_hour (int): 公式profileの更新目安時。
        expected_minute (int): 公式profileの更新目安分。
        latest_available_reference_date (date): 実際に取得できた最新対象日。
        latest_available_published_at_jst (datetime): 実際に取得できた最新公表時刻。

    返り値:
        dict[str, Any]: 支配的な予定時刻、期待対象日、同日早着監査値。
    """
    ordered_cash_dates = sorted(publication_cash_dates)
    if latest_available_published_at_jst > as_of_jst:
        raise InputValidationError(
            f"J-Quants日次更新 '{dataset_id}' の実公表時刻を分析時刻より未来にできません。"
        )
    future_or_current_cash_dates = [
        calendar_date
        for calendar_date in ordered_cash_dates
        if calendar_date >= as_of_jst.date()
    ]
    if not future_or_current_cash_dates:
        raise InputValidationError(
            f"J-Quants日次更新 '{dataset_id}' の次回公表営業日を共有カレンダーから導出できません。"
        )
    current_schedule_date = future_or_current_cash_dates[0]
    current_schedule_update = datetime.combine(
        current_schedule_date,
        datetime.min.time(),
        tzinfo=JST,
    ).replace(hour=expected_hour, minute=expected_minute)
    early_arrival_observed = (
        latest_available_reference_date == current_schedule_date
        and latest_available_published_at_jst.date() == current_schedule_date
        and latest_available_published_at_jst < current_schedule_update
    )
    if early_arrival_observed:
        later_cash_dates = [
            calendar_date
            for calendar_date in ordered_cash_dates
            if calendar_date > current_schedule_date
        ]
        if not later_cash_dates:
            raise InputValidationError(
                f"J-Quants日次更新 '{dataset_id}' の同日早着後の次回公表営業日を共有カレンダーから導出できません。"
            )
        next_schedule_date = later_cash_dates[0]
        expected_update = datetime.combine(
            next_schedule_date,
            datetime.min.time(),
            tzinfo=JST,
        ).replace(hour=expected_hour, minute=expected_minute)
        expected_reference = current_schedule_date
    else:
        expected_update = current_schedule_update
        if expected_update <= as_of_jst:
            expected_reference = current_schedule_date
        else:
            previous_cash_dates = [
                calendar_date
                for calendar_date in ordered_cash_dates
                if calendar_date < current_schedule_date
            ]
            if not previous_cash_dates:
                raise InputValidationError(
                    f"J-Quants日次更新 '{dataset_id}' の直前対象日を共有カレンダーから導出できません。"
                )
            expected_reference = previous_cash_dates[-1]
    return {
        "expected_update": expected_update,
        "expected_reference": expected_reference,
        "early_arrival_observed": early_arrival_observed,
        "observed_schedule_update": (
            current_schedule_update if early_arrival_observed else None
        ),
        "observed_schedule_reference": (
            current_schedule_date if early_arrival_observed else None
        ),
    }


# ----------------------------------------


def _derive_weekly_jquants_arrival_state(
    dataset_id: str,
    release_rows: Sequence[tuple[datetime, date]],
    as_of_jst: datetime,
    latest_available_reference_date: date,
    latest_available_published_at_jst: datetime,
    strict_current_source: bool,
) -> dict[str, Any]:
    """
    機能:
        週次公式予定の時計到来と実データ到着を分離し、支配予定・状態・監査値を導出する。

    引数:
        dataset_id (str): エラー表示用のJ-Quants dataset識別子。
        release_rows (Sequence[tuple[datetime, date]]): 時刻・対象日昇順の公式予定列。
        as_of_jst (datetime): 分析基準の日本時間。
        latest_available_reference_date (date): 実際に取得できた最新対象日。
        latest_available_published_at_jst (datetime): 実際に取得できた最新公表時刻。
        strict_current_source (bool): 有効かつ選択済みsourceとして予定へ厳密結合するか。

    返り値:
        dict[str, Any]: 支配的な予定、時計到来、次回予定、実到着監査値。
    """
    reference_release_map = {
        reference_date: release_at
        for release_at, reference_date in release_rows
    }
    if latest_available_published_at_jst > as_of_jst:
        raise InputValidationError(
            f"J-Quants週次更新 '{dataset_id}' の実公表時刻を分析時刻より未来にできません。"
        )
    due_releases = [row for row in release_rows if row[0] <= as_of_jst]
    if not due_releases:
        raise InputValidationError(
            f"J-Quants週次更新 '{dataset_id}' の直近到来済み公表を公式予定列から導出できません。"
        )
    last_due_update, last_due_reference = due_releases[-1]
    if strict_current_source and latest_available_reference_date not in reference_release_map:
        raise InputValidationError(
            f"J-Quants週次更新 '{dataset_id}' の有効な選択sourceの最新取得対象日を6件の公式予定対象日へ一致させてください。"
        )
    observed_schedule_update: datetime | None = None
    observed_schedule_reference: date | None = None
    early_arrival_observed = False
    if strict_current_source:
        observed_schedule_update = reference_release_map[
            latest_available_reference_date
        ]
        observed_schedule_reference = latest_available_reference_date
        if observed_schedule_update.date() > latest_available_published_at_jst.date():
            raise InputValidationError(
                f"J-Quants週次更新 '{dataset_id}' の最新取得対象日に対応する公式公表日が実際の最新公表日より後です。"
            )
        early_arrival_observed = observed_schedule_update > as_of_jst
    if (
        latest_available_reference_date > last_due_reference
        and not early_arrival_observed
    ):
        raise InputValidationError(
            f"J-Quants週次更新 '{dataset_id}' の最新取得対象日は未到来の公式公表対象日です。"
        )
    effective_arrived_reference = (
        latest_available_reference_date
        if early_arrival_observed
        else last_due_reference
    )
    future_releases = [
        row
        for row in release_rows
        if row[1] > effective_arrived_reference
    ]
    next_scheduled_update: datetime | None = None
    next_scheduled_reference: date | None = None
    if future_releases:
        next_scheduled_update, next_scheduled_reference = future_releases[0]
    if latest_available_reference_date < last_due_reference:
        expected_update = last_due_update
        expected_reference = last_due_reference
        expected_availability_status = "delayed"
    elif next_scheduled_update is not None:
        expected_update = next_scheduled_update
        expected_reference = latest_available_reference_date
        expected_availability_status = "awaiting_scheduled_update"
    elif early_arrival_observed:
        expected_update = observed_schedule_update
        expected_reference = latest_available_reference_date
        expected_availability_status = "updated"
    else:
        expected_update = last_due_update
        expected_reference = last_due_reference
        expected_availability_status = "updated"
    return {
        "expected_update": expected_update,
        "expected_reference": expected_reference,
        "expected_availability_status": expected_availability_status,
        "last_due_update": last_due_update,
        "last_due_reference": last_due_reference,
        "next_scheduled_update": next_scheduled_update,
        "next_scheduled_reference": next_scheduled_reference,
        "early_arrival_observed": early_arrival_observed,
        "observed_schedule_update": observed_schedule_update,
        "observed_schedule_reference": observed_schedule_reference,
        "reference_release_map": reference_release_map,
        "strict_current_source": strict_current_source,
    }


# ----------------------------------------


def _validate_weekly_publication_schedule(
    update: Mapping[str, Any],
    dataset_id: str,
    as_of_jst: datetime,
    publication_calendar_by_date: Mapping[date, Mapping[str, Any]],
    latest_available_reference_date: date,
    latest_available_published_at_jst: datetime,
    strict_current_source: bool = True,
) -> dict[str, Any]:
    """
    機能:
        週次J-Quantsの公式公表予定を検証し、特例日を含む支配的な更新時刻と期待対象日を導出する。

    引数:
        update (Mapping[str, Any]): dataset別の更新監査入力。
        dataset_id (str): エラー表示用のJ-Quants dataset識別子。
        as_of_jst (datetime): 分析基準の日本時間。
        publication_calendar_by_date (Mapping[date, Mapping[str, Any]]): 共有公表営業日カレンダー。
        latest_available_reference_date (date): 実際に取得できた最新統計対象日。
        latest_available_published_at_jst (datetime): 実際に取得できた最新公表時刻。
        strict_current_source (bool): 有効かつ選択済みsourceとして6件予定へ厳密結合するか。

    返り値:
        dict[str, Any]: 支配的な予定、到来済み・次回予定、正規化済み公式予定列。
    """
    if update.get("official_schedule_complete") is not True:
        raise InputValidationError(
            f"J-Quants週次更新 '{dataset_id}' はofficial_schedule_complete=trueにしてください。"
        )
    schedule_profile_id = JQUANTS_DATASET_PROFILES.get(dataset_id.casefold())
    if schedule_profile_id is None:
        raise InputValidationError(
            f"J-Quants週次更新 '{dataset_id}' の公式更新profileが未対応です。"
        )
    expected_hour, expected_minute, _, _, _ = JQUANTS_UPDATE_PROFILES[schedule_profile_id]
    week_monday = as_of_jst.date() - timedelta(days=as_of_jst.weekday())
    expected_window_start = week_monday - timedelta(days=14)
    base_window_end = week_monday + timedelta(
        days=JQUANTS_PUBLICATION_CALENDAR_BASE_END_OFFSET_DAYS
    )
    maximum_window_end = week_monday + timedelta(
        days=JQUANTS_PUBLICATION_CALENDAR_MAX_END_OFFSET_DAYS
    )
    window_start = _parse_iso_date(
        update.get("official_schedule_window_start_date"),
        f"J-Quants週次更新 '{dataset_id}' の official_schedule_window_start_date",
    )
    window_end = _parse_iso_date(
        update.get("official_schedule_window_end_date"),
        f"J-Quants週次更新 '{dataset_id}' の official_schedule_window_end_date",
    )
    if window_start != expected_window_start:
        raise InputValidationError(
            f"J-Quants週次更新 '{dataset_id}' の公式予定確認窓の開始日を分析週月曜14日前へ一致させてください。"
        )
    if not base_window_end <= window_end <= maximum_window_end:
        raise InputValidationError(
            f"J-Quants週次更新 '{dataset_id}' の公式予定確認窓終端を基本終端以上・最大90暦日以内にしてください。"
        )
    raw_schedule = _require_sequence(
        update.get("official_publication_schedule"),
        f"J-Quants週次更新 '{dataset_id}' の official_publication_schedule",
    )
    if not raw_schedule:
        raise InputValidationError(
            f"J-Quants週次更新 '{dataset_id}' の公式公表予定を1件以上指定してください。"
        )
    schedule: list[dict[str, Any]] = []
    release_rows: list[tuple[datetime, date]] = []
    previous_release: datetime | None = None
    previous_reference_date: date | None = None
    for schedule_index, raw_schedule_row in enumerate(raw_schedule):
        schedule_row = _require_mapping(
            raw_schedule_row,
            f"J-Quants週次更新 '{dataset_id}' official_publication_schedule[{schedule_index}]",
        )
        release_at = _parse_jst(
            schedule_row.get("release_at_jst"),
            f"J-Quants週次更新 '{dataset_id}' official_publication_schedule[{schedule_index}].release_at_jst",
        )
        reference_date = _parse_iso_date(
            schedule_row.get("reference_date"),
            f"J-Quants週次更新 '{dataset_id}' official_publication_schedule[{schedule_index}].reference_date",
        )
        if (
            release_at.hour,
            release_at.minute,
            release_at.second,
            release_at.microsecond,
        ) != (expected_hour, expected_minute, 0, 0):
            raise InputValidationError(
                f"J-Quants週次更新 '{dataset_id}' の公式公表日へJ-Quants更新目安{expected_hour:02d}:{expected_minute:02d} JSTを適用してください。"
            )
        if not window_start <= release_at.date() <= window_end:
            raise InputValidationError(
                f"J-Quants週次更新 '{dataset_id}' の公式公表予定を確認窓内にしてください。"
            )
        if release_at.date() > maximum_window_end:
            raise InputValidationError(
                f"J-Quants週次更新 '{dataset_id}' の公式公表予定が共有カレンダーの最大90暦日を超えています。"
            )
        calendar_row = publication_calendar_by_date.get(release_at.date())
        if calendar_row is None or not calendar_row["cash_is_trading_day"]:
            raise InputValidationError(
                f"J-Quants週次更新 '{dataset_id}' の公式公表日を共有カレンダーの現物営業日にしてください。"
            )
        if reference_date >= release_at.date():
            raise InputValidationError(
                f"J-Quants週次更新 '{dataset_id}' の対象日を公表日より前にしてください。"
            )
        reference_calendar_row = publication_calendar_by_date.get(reference_date)
        if reference_calendar_row is None or not reference_calendar_row["cash_is_trading_day"]:
            raise InputValidationError(
                f"J-Quants週次更新 '{dataset_id}' の対象日を共有カレンダー内の現物営業日にしてください。"
            )
        reference_week_monday = reference_date - timedelta(days=reference_date.weekday())
        reference_week_cash_dates = [
            calendar_date
            for calendar_date, calendar_row in publication_calendar_by_date.items()
            if reference_week_monday <= calendar_date <= reference_week_monday + timedelta(days=6)
            and calendar_row["cash_is_trading_day"]
        ]
        if not reference_week_cash_dates or reference_date != reference_week_cash_dates[-1]:
            raise InputValidationError(
                f"J-Quants週次更新 '{dataset_id}' の対象日を当該週の最終現物営業日へ一致させてください。"
            )
        if previous_release is not None and release_at <= previous_release:
            raise InputValidationError(
                f"J-Quants週次更新 '{dataset_id}' の公式公表予定を重複なしの時刻昇順にしてください。"
            )
        if previous_reference_date is not None and reference_date <= previous_reference_date:
            raise InputValidationError(
                f"J-Quants週次更新 '{dataset_id}' の対象日を重複なしの日付昇順にしてください。"
            )
        previous_release = release_at
        previous_reference_date = reference_date
        release_rows.append((release_at, reference_date))
        schedule.append(
            {
                "release_at_jst": _format_jst(release_at),
                "reference_date": reference_date.isoformat(),
            }
        )
    expected_window_end = max(base_window_end, max(row[0].date() for row in release_rows))
    if window_end != expected_window_end:
        raise InputValidationError(
            f"J-Quants週次更新 '{dataset_id}' の公式予定確認窓終端を基本終端と6件の最大公表日の遅い方へ一致させてください。"
        )
    expected_reference_dates: list[date] = []
    first_reference_week_monday = window_start - timedelta(days=7)
    for reference_week_index in range(6):
        reference_week_monday = first_reference_week_monday + timedelta(
            days=7 * reference_week_index
        )
        reference_week_cash_dates = [
            calendar_date
            for calendar_date, calendar_row in publication_calendar_by_date.items()
            if reference_week_monday
            <= calendar_date
            <= reference_week_monday + timedelta(days=6)
            and calendar_row["cash_is_trading_day"]
        ]
        if not reference_week_cash_dates:
            raise InputValidationError(
                f"J-Quants週次更新 '{dataset_id}' の対象週に現物営業日がなく、現行profileで導出できません。"
            )
        expected_reference_dates.append(reference_week_cash_dates[-1])
    if [reference_date for _, reference_date in release_rows] != expected_reference_dates:
        raise InputValidationError(
            f"J-Quants週次更新 '{dataset_id}' の公式予定列を6対象週の最終現物営業日と完全一致させてください。"
        )
    arrival_state = _derive_weekly_jquants_arrival_state(
        dataset_id,
        release_rows,
        as_of_jst,
        latest_available_reference_date,
        latest_available_published_at_jst,
        strict_current_source,
    )
    return {**arrival_state, "schedule": schedule}


# ----------------------------------------


def validate_market_data_freshness(
    spec: Mapping[str, Any],
    nt_spec: Mapping[str, Any],
    session_context: Mapping[str, Any],
    as_of_jst: datetime,
) -> dict[str, Any]:
    """
    機能:
        価格情報源の固定優先順位、板アンカー、J-Quants定時更新を検証する。

    引数:
        spec (Mapping[str, Any]): 情報源台帳、選択監査、J-Quants更新監査。
        nt_spec (Mapping[str, Any]): 両脚の銘柄・限月・板時刻・source_id。
        session_context (Mapping[str, Any]): 検証済みの日中またはナイトセッション。
        as_of_jst (datetime): 分析基準の日本時間。

    返り値:
        dict[str, Any]: 固定tier、選択結果、板アンカー期限を含む鮮度監査。
    """
    registry_raw = _require_mapping(spec.get("source_registry"), "market_data_freshness.source_registry")
    if not registry_raw:
        raise InputValidationError("market_data_freshness.source_registry を1件以上指定してください。")
    registry: dict[str, dict[str, Any]] = {}
    seen_source_ids: set[str] = set()
    for raw_source_id in sorted(registry_raw):
        source_id = _required_text(raw_source_id, "market_data_freshness.source_registry のsource_id")
        source_key = source_id.casefold()
        if source_key in seen_source_ids:
            raise InputValidationError("market_data_freshness.source_registry のsource_idが正規化後に重複しています。")
        seen_source_ids.add(source_key)
        row = _require_mapping(
            registry_raw[raw_source_id],
            f"market_data_freshness.source_registry.{source_id}",
        )
        provider_family = row.get("provider_family")
        if provider_family not in MARKET_DATA_PROVIDER_PRIORITY:
            raise InputValidationError(f"情報源 '{source_id}' の provider_family が不正です。")
        data_role = row.get("data_role")
        if data_role not in MARKET_DATA_ROLES:
            raise InputValidationError(f"情報源 '{source_id}' の data_role が不正です。")
        if provider_family == "kabus" and data_role != "execution_anchor":
            raise InputValidationError(f"Kabus情報源 '{source_id}' はexecution_anchorにしてください。")
        if provider_family == "225225" and data_role != "near_realtime_context":
            raise InputValidationError(f"225225.jp系情報源 '{source_id}' はnear_realtime_contextにしてください。")
        if provider_family == "jquants" and data_role != "scheduled_history":
            raise InputValidationError(f"J-Quants情報源 '{source_id}' はscheduled_historyにしてください。")
        status = row.get("status")
        if status not in MARKET_DATA_SOURCE_STATUSES:
            raise InputValidationError(f"情報源 '{source_id}' の status が不正です。")
        status_reason = _required_text(row.get("status_reason"), f"情報源 '{source_id}' の status_reason")
        coverage_items_raw = _require_sequence(
            row.get("coverage_items", []),
            f"情報源 '{source_id}' の coverage_items",
        )
        coverage_item_links: list[str] = []
        for coverage_index, raw_coverage_item in enumerate(coverage_items_raw):
            coverage_item_link = _required_text(
                raw_coverage_item,
                f"情報源 '{source_id}' の coverage_items[{coverage_index}]",
            )
            if coverage_item_link not in (*PRICE_COVERAGE_ITEMS, "jquants"):
                raise InputValidationError(f"情報源 '{source_id}' の coverage_items に未対応項目があります。")
            if coverage_item_link in coverage_item_links:
                raise InputValidationError(f"情報源 '{source_id}' の coverage_items が重複しています。")
            coverage_item_links.append(coverage_item_link)
        if data_role == "execution_anchor" and coverage_item_links:
            raise InputValidationError(f"execution_anchor情報源 '{source_id}' にcoverage_itemsを指定できません。")
        if provider_family == "jquants" and coverage_item_links != ["jquants"]:
            raise InputValidationError(f"J-Quants情報源 '{source_id}' のcoverage_itemsはjquantsだけにしてください。")
        checked_at = _parse_jst(row.get("checked_at_jst"), f"情報源 '{source_id}' の checked_at_jst")
        if checked_at > as_of_jst:
            raise InputValidationError(f"情報源 '{source_id}' の checked_at_jst が分析時刻より未来です。")
        if (as_of_jst - checked_at).total_seconds() > COVERAGE_CHECK_MAX_AGE_HOURS * 3600:
            raise InputValidationError(f"情報源 '{source_id}' の確認が24時間より古いです。")
        normalized: dict[str, Any] = {
            "source_id": source_id,
            "provider_family": provider_family,
            "tier": MARKET_DATA_PROVIDER_PRIORITY[provider_family],
            "data_role": data_role,
            "coverage_items": coverage_item_links,
            "series_key": _required_text(row.get("series_key"), f"情報源 '{source_id}' の series_key"),
            "instrument_type": _required_text(
                row.get("instrument_type"),
                f"情報源 '{source_id}' の instrument_type",
            ),
            "symbol": _required_text(row.get("symbol"), f"情報源 '{source_id}' の symbol"),
            "contract_month": _required_text(
                row.get("contract_month"),
                f"情報源 '{source_id}' の contract_month",
            ),
            "quote_type": _required_text(row.get("quote_type"), f"情報源 '{source_id}' の quote_type"),
            "status": status,
            "status_reason": status_reason,
            "checked_at_jst": _format_jst(checked_at),
            "data_as_of_jst": None,
            "fetched_at_jst": None,
            "max_age_seconds": None,
            "data_age_seconds": None,
            "valid_until_jst": None,
            "kabus_market_code": None,
            "dataset_id": None,
        }
        if status in ("valid", "stale", "invalid"):
            data_as_of = _parse_jst(row.get("data_as_of_jst"), f"情報源 '{source_id}' の data_as_of_jst")
            fetched_at = _parse_jst(row.get("fetched_at_jst"), f"情報源 '{source_id}' の fetched_at_jst")
            if not data_as_of <= fetched_at <= checked_at <= as_of_jst:
                raise InputValidationError(
                    f"情報源 '{source_id}' はdata_as_of <= fetched_at <= checked_at <= as_ofにしてください。"
                )
            max_age_seconds = _bounded_float(
                row.get("max_age_seconds"),
                f"情報源 '{source_id}' の max_age_seconds",
                0.001,
                31_536_000.0,
            )
            if provider_family in LIVE_PROVIDER_MAX_AGE_SECONDS:
                provider_max = LIVE_PROVIDER_MAX_AGE_SECONDS[provider_family]
                if max_age_seconds > provider_max:
                    raise InputValidationError(
                        f"情報源 '{source_id}' のmax_age_secondsは{provider_family}上限{provider_max:.0f}秒以下にしてください。"
                    )
            data_age_seconds = (as_of_jst - data_as_of).total_seconds()
            if status == "valid" and data_age_seconds > max_age_seconds:
                raise InputValidationError(f"情報源 '{source_id}' はTTL超過のためstatus=validにできません。")
            if status == "stale" and data_age_seconds <= max_age_seconds:
                raise InputValidationError(f"情報源 '{source_id}' はTTL内のためstatus=staleと矛盾します。")
            normalized.update(
                {
                    "data_as_of_jst": _format_jst(data_as_of),
                    "fetched_at_jst": _format_jst(fetched_at),
                    "max_age_seconds": max_age_seconds,
                    "data_age_seconds": round(data_age_seconds, 3),
                    "valid_until_jst": _format_jst(data_as_of + timedelta(seconds=max_age_seconds)),
                }
            )
        if provider_family == "kabus":
            market_code = _bounded_integer(
                row.get("kabus_market_code"),
                f"情報源 '{source_id}' の kabus_market_code",
                1,
                99,
            )
            allowed_codes = KABUS_MARKET_CODES[str(session_context["futures_session_kind"])]
            if market_code not in allowed_codes:
                raise InputValidationError(
                    f"情報源 '{source_id}' のkabus_market_codeを現在セッション{allowed_codes}と一致させてください。"
                )
            normalized["kabus_market_code"] = market_code
        if provider_family == "jquants" and normalized["quote_type"] == "bid_ask":
            raise InputValidationError("J-Quantsを現在のbid/ask板として登録できません。")
        if provider_family == "jquants":
            dataset_id = _required_text(
                row.get("dataset_id"),
                f"情報源 '{source_id}' の dataset_id",
            )
            if dataset_id.casefold() not in JQUANTS_DATASET_PROFILES:
                raise InputValidationError(f"情報源 '{source_id}' のJ-Quants dataset_idが未対応です。")
            normalized["dataset_id"] = dataset_id
        registry[source_id] = normalized

    selections_raw = _require_sequence(
        spec.get("source_selection_audit"),
        "market_data_freshness.source_selection_audit",
    )
    selections: list[dict[str, Any]] = []
    selections_by_key: dict[str, dict[str, Any]] = {}
    for index, raw_selection in enumerate(selections_raw):
        selection = _require_mapping(
            raw_selection,
            f"market_data_freshness.source_selection_audit[{index}]",
        )
        selection_key = _required_text(selection.get("selection_key"), f"選択監査[{index}] selection_key")
        normalized_selection_key = selection_key.casefold()
        if normalized_selection_key in selections_by_key:
            raise InputValidationError("source_selection_audit.selection_key が重複しています。")
        purpose = selection.get("purpose")
        if purpose not in ("execution_anchor", "current_price_context", "scheduled_history"):
            raise InputValidationError(f"選択監査 '{selection_key}' の purpose が不正です。")
        series_key = _required_text(selection.get("series_key"), f"選択監査 '{selection_key}' の series_key")
        selected_source_id = _required_text(
            selection.get("selected_source_id"),
            f"選択監査 '{selection_key}' の selected_source_id",
        )
        candidate_source_ids = _normalized_identifier_list(
            selection.get("candidate_source_ids"),
            f"選択監査 '{selection_key}' の candidate_source_ids",
        )
        if selected_source_id not in candidate_source_ids:
            raise InputValidationError(f"選択監査 '{selection_key}' の選択元を候補一覧へ含めてください。")
        unknown_sources = [source_id for source_id in candidate_source_ids if source_id not in registry]
        if unknown_sources:
            raise InputValidationError(
                f"選択監査 '{selection_key}' に未登録source_idがあります: " + ", ".join(unknown_sources)
            )
        candidate_rows = [registry[source_id] for source_id in candidate_source_ids]
        if any(row["series_key"] != series_key for row in candidate_rows):
            raise InputValidationError(f"選択監査 '{selection_key}' の全候補を同じseries_keyにしてください。")
        registered_series_sources = {
            source_id
            for source_id, row in registry.items()
            if row["series_key"] == series_key
        }
        if set(candidate_source_ids) != registered_series_sources:
            raise InputValidationError(
                f"選択監査 '{selection_key}' は同じseries_keyの登録候補を省略・追加せず列挙してください。"
            )
        selected_row = registry[selected_source_id]
        if selected_row["status"] != "valid":
            raise InputValidationError(f"選択監査 '{selection_key}' はstatus=validの情報源を選択してください。")
        valid_candidates = [row for row in candidate_rows if row["status"] == "valid"]
        best_tier = min(row["tier"] for row in valid_candidates)
        if selected_row["tier"] != best_tier:
            raise InputValidationError(
                f"選択監査 '{selection_key}' は有効な上位tierを飛ばして下位情報源を選べません。"
            )
        best_tier_candidates = [row for row in valid_candidates if row["tier"] == best_tier]
        freshest_best_tier_time = max(
            _parse_jst(row["data_as_of_jst"], f"選択監査 '{selection_key}' の候補時刻")
            for row in best_tier_candidates
        )
        selected_time = _parse_jst(
            selected_row["data_as_of_jst"],
            f"選択監査 '{selection_key}' の選択時刻",
        )
        if selected_time != freshest_best_tier_time:
            raise InputValidationError(
                f"選択監査 '{selection_key}' は同一tier内で最も新しい観測を選択してください。"
            )
        if purpose == "execution_anchor" and (
            selected_row["provider_family"] != "kabus"
            or selected_row["data_role"] != "execution_anchor"
        ):
            raise InputValidationError("現在板のexecution_anchorはKabusの有効なbid/askに限定します。")
        if purpose == "current_price_context" and selected_row["provider_family"] == "jquants":
            raise InputValidationError("J-Quantsの定時更新データを現在価格へ昇格できません。")
        if purpose == "scheduled_history" and selected_row["provider_family"] != "jquants":
            raise InputValidationError("scheduled_historyの選択元はJ-Quantsにしてください。")
        normalized_selection = {
            "selection_key": selection_key,
            "purpose": purpose,
            "series_key": series_key,
            "selected_source_id": selected_source_id,
            "selected_provider_family": selected_row["provider_family"],
            "selected_tier": selected_row["tier"],
            "candidate_source_ids": candidate_source_ids,
            "selection_reason": _required_text(
                selection.get("selection_reason"),
                f"選択監査 '{selection_key}' の selection_reason",
            ),
        }
        selections.append(normalized_selection)
        selections_by_key[normalized_selection_key] = normalized_selection

    required_board_sources = {
        "nikkei_execution_anchor": (
            _required_text(nt_spec.get("nikkei_board_source_id"), "nt.nikkei_board_source_id"),
            _required_text(nt_spec.get("nikkei_symbol"), "nt.nikkei_symbol"),
            _parse_jst(nt_spec.get("nikkei_snapshot_jst"), "nt.nikkei_snapshot_jst"),
            "nikkei225_futures",
        ),
        "topix_execution_anchor": (
            _required_text(nt_spec.get("topix_board_source_id"), "nt.topix_board_source_id"),
            _required_text(nt_spec.get("topix_symbol"), "nt.topix_symbol"),
            _parse_jst(nt_spec.get("topix_snapshot_jst"), "nt.topix_snapshot_jst"),
            "topix_futures",
        ),
    }
    anchor_expiries: list[datetime] = []
    for selection_key, (source_id, symbol, snapshot, instrument_type) in required_board_sources.items():
        selection = selections_by_key.get(selection_key)
        if selection is None:
            raise InputValidationError(f"source_selection_audit に {selection_key} がありません。")
        if selection["purpose"] != "execution_anchor" or selection["selected_source_id"] != source_id:
            raise InputValidationError(f"{selection_key} をntの板source_idと一致させてください。")
        source = registry.get(source_id)
        if source is None:
            raise InputValidationError(f"ntの板source_id '{source_id}' がsource_registryにありません。")
        if (
            source["provider_family"] != "kabus"
            or source["data_role"] != "execution_anchor"
            or source["status"] != "valid"
            or source["quote_type"] != "bid_ask"
            or source["symbol"] != symbol
            or source["instrument_type"] != instrument_type
            or source["contract_month"] != str(nt_spec.get("nikkei_contract_month" if instrument_type == "nikkei225_futures" else "topix_contract_month"))
        ):
            raise InputValidationError(f"{selection_key} の商品・限月・Kabus板属性をnt入力と一致させてください。")
        source_as_of = _parse_jst(source["data_as_of_jst"], f"{selection_key}.data_as_of_jst")
        if source_as_of != snapshot:
            raise InputValidationError(f"{selection_key} のdata_as_of_jstをnt板時刻と一致させてください。")
        anchor_expiries.append(_parse_jst(source["valid_until_jst"], f"{selection_key}.valid_until_jst"))

    registered_jquants_source_ids = {
        source_id
        for source_id, row in registry.items()
        if row["provider_family"] == "jquants"
    }
    jquants_updates_raw = _require_sequence(
        spec.get("jquants_dataset_updates", []),
        "market_data_freshness.jquants_dataset_updates",
    )
    publication_calendar: list[dict[str, Any]] = []
    publication_calendar_by_date: dict[date, dict[str, Any]] = {}
    publication_calendar_source_id: str | None = None
    publication_calendar_checked_at: datetime | None = None
    publication_week_monday = as_of_jst.date() - timedelta(days=as_of_jst.weekday())
    publication_calendar_start = publication_week_monday - timedelta(
        days=JQUANTS_PUBLICATION_CALENDAR_START_OFFSET_DAYS
    )
    publication_calendar_base_end = publication_week_monday + timedelta(
        days=JQUANTS_PUBLICATION_CALENDAR_BASE_END_OFFSET_DAYS
    )
    publication_calendar_end = publication_calendar_base_end
    if registered_jquants_source_ids:
        publication_calendar_end = _derive_jquants_publication_calendar_end(
            jquants_updates_raw,
            as_of_jst,
        )
        publication_calendar_source_id = _required_text(
            spec.get("jquants_publication_calendar_source_id"),
            "market_data_freshness.jquants_publication_calendar_source_id",
        )
        publication_calendar_checked_at = _parse_jst(
            spec.get("jquants_publication_calendar_checked_at_jst"),
            "market_data_freshness.jquants_publication_calendar_checked_at_jst",
        )
        if publication_calendar_checked_at > as_of_jst:
            raise InputValidationError("J-Quants公表営業日カレンダーの確認時刻が分析時刻より未来です。")
        if (
            as_of_jst - publication_calendar_checked_at
        ).total_seconds() > COVERAGE_CHECK_MAX_AGE_HOURS * 3600:
            raise InputValidationError("J-Quants公表営業日カレンダーの確認が24時間より古いです。")
        publication_calendar_raw = _require_sequence(
            spec.get("jquants_publication_calendar"),
            "market_data_freshness.jquants_publication_calendar",
        )
        expected_calendar_length = (
            publication_calendar_end - publication_calendar_start
        ).days + 1
        if len(publication_calendar_raw) != expected_calendar_length:
            raise InputValidationError(
                "jquants_publication_calendarは分析週月曜の21日前から、基本49暦日または週次公式公表6件の最大公表日までを過不足なく指定してください。"
            )
        for calendar_index, raw_calendar_row in enumerate(publication_calendar_raw):
            calendar_row = _require_mapping(
                raw_calendar_row,
                f"jquants_publication_calendar[{calendar_index}]",
            )
            calendar_date = _parse_iso_date(
                calendar_row.get("date"),
                f"jquants_publication_calendar[{calendar_index}].date",
            )
            if calendar_date != publication_calendar_start + timedelta(days=calendar_index):
                raise InputValidationError(
                    "jquants_publication_calendarを分析週月曜の21日前から連続させてください。"
                )
            holiday_division = str(calendar_row.get("holiday_division", "")).strip()
            if holiday_division not in HOLIDAY_DIVISIONS:
                raise InputValidationError("jquants_publication_calendarのHolidayDivisionが不正です。")
            cash_is_trading_day = calendar_row.get("cash_is_trading_day")
            if not isinstance(cash_is_trading_day, bool):
                raise InputValidationError(
                    "jquants_publication_calendar.cash_is_trading_dayは真偽値にしてください。"
                )
            if cash_is_trading_day != (holiday_division in ("1", "2")):
                raise InputValidationError(
                    "jquants_publication_calendarの営業日とHolidayDivisionが矛盾します。"
                )
            if calendar_date.weekday() >= 5 and (
                cash_is_trading_day or holiday_division != "0"
            ):
                raise InputValidationError(
                    "jquants_publication_calendarの土日を現物営業日にできません。"
                )
            normalized_calendar_row = {
                "date": calendar_date.isoformat(),
                "cash_is_trading_day": cash_is_trading_day,
                "holiday_division": holiday_division,
            }
            publication_calendar.append(normalized_calendar_row)
            publication_calendar_by_date[calendar_date] = normalized_calendar_row
        if date.fromisoformat(publication_calendar[-1]["date"]) != publication_calendar_end:
            raise InputValidationError(
                "jquants_publication_calendarの終端を週次公式公表予定から導出した最大公表日へ一致させてください。"
            )
        for session_row in session_context["session_calendar"]:
            session_date = date.fromisoformat(session_row["date"])
            publication_row = publication_calendar_by_date.get(session_date)
            if publication_row is None or (
                publication_row["cash_is_trading_day"] != session_row["cash_is_trading_day"]
                or publication_row["holiday_division"] != session_row["holiday_division"]
            ):
                raise InputValidationError(
                    "J-Quants公表営業日カレンダーをtiming.session_calendarと一致させてください。"
                )
    elif any(
        spec.get(field_name) is not None
        for field_name in (
            "jquants_publication_calendar_source_id",
            "jquants_publication_calendar_checked_at_jst",
            "jquants_publication_calendar",
        )
    ):
        raise InputValidationError(
            "J-Quants情報源がない場合はjquants_publication_calendarを指定しないでください。"
        )

    jquants_updates: list[dict[str, Any]] = []
    seen_jquants_update_sources: set[str] = set()
    selected_scheduled_history_source_ids = {
        row["selected_source_id"]
        for row in selections
        if row["purpose"] == "scheduled_history"
    }
    for index, raw_update in enumerate(jquants_updates_raw):
        update = _require_mapping(raw_update, f"jquants_dataset_updates[{index}]")
        dataset_id = _required_text(update.get("dataset_id"), f"jquants_dataset_updates[{index}].dataset_id")
        source_id = _required_text(update.get("source_id"), f"J-Quants更新 '{dataset_id}' の source_id")
        if source_id.casefold() in seen_jquants_update_sources:
            raise InputValidationError("jquants_dataset_updates.source_id をdatasetごとに一意にしてください。")
        seen_jquants_update_sources.add(source_id.casefold())
        source = registry.get(source_id)
        if (
            source is None
            or source["provider_family"] != "jquants"
            or source["data_role"] != "scheduled_history"
            or source["status"] not in ("valid", "stale", "invalid")
        ):
            raise InputValidationError(f"J-Quants更新 '{dataset_id}' のsource_idをJ-Quants台帳へ結び付けてください。")
        schedule_profile_id = _required_text(
            update.get("schedule_profile_id"),
            f"J-Quants更新 '{dataset_id}' の schedule_profile_id",
        )
        if schedule_profile_id not in JQUANTS_UPDATE_PROFILES:
            raise InputValidationError(f"J-Quants更新 '{dataset_id}' のschedule_profile_idが未対応です。")
        expected_profile_id = JQUANTS_DATASET_PROFILES.get(dataset_id.casefold())
        if expected_profile_id is None or schedule_profile_id != expected_profile_id:
            raise InputValidationError(
                f"J-Quants更新 '{dataset_id}' のdataset_idとschedule_profile_idが対応していません。"
            )
        if str(source["dataset_id"]).casefold() != dataset_id.casefold():
            raise InputValidationError(
                f"J-Quants更新 '{dataset_id}' のdataset_idをsource_registryのdataset_idと一致させてください。"
            )
        expected_hour, expected_minute, expected_frequency, max_future_days, max_past_days = (
            JQUANTS_UPDATE_PROFILES[schedule_profile_id]
        )
        frequency = _required_text(
            update.get("frequency"),
            f"J-Quants更新 '{dataset_id}' の frequency",
        )
        if frequency != expected_frequency:
            raise InputValidationError(
                f"J-Quants更新 '{dataset_id}' のfrequencyをprofileの{expected_frequency}と一致させてください。"
            )
        latest_reference = _parse_iso_date(
            update.get("latest_available_reference_date"),
            f"J-Quants更新 '{dataset_id}' の latest_available_reference_date",
        )
        latest_published_at = _parse_jst(
            update.get("latest_available_published_at_jst"),
            f"J-Quants更新 '{dataset_id}' の latest_available_published_at_jst",
        )
        if latest_reference > as_of_jst.date():
            raise InputValidationError(
                f"J-Quants更新 '{dataset_id}' の対象日を分析日より未来にできません。"
            )
        if latest_published_at.date() < latest_reference:
            raise InputValidationError(
                f"J-Quants更新 '{dataset_id}' の最新公表日を最新取得対象日以後にしてください。"
            )
        expected_update = _parse_jst(
            update.get("latest_expected_update_jst"),
            f"J-Quants更新 '{dataset_id}' の latest_expected_update_jst",
        )
        publication_cash_dates = [
            calendar_date
            for calendar_date, calendar_row in publication_calendar_by_date.items()
            if calendar_row["cash_is_trading_day"]
        ]
        official_schedule_complete: bool | None = None
        official_schedule_window_start_date: str | None = None
        official_schedule_window_end_date: str | None = None
        official_publication_schedule: list[dict[str, Any]] = []
        weekly_expected_availability_status: str | None = None
        weekly_reference_release_map: Mapping[date, datetime] = {}
        last_due_update: datetime | None = None
        last_due_reference: date | None = None
        next_scheduled_update: datetime | None = None
        next_scheduled_reference: date | None = None
        early_arrival_observed = False
        observed_schedule_update: datetime | None = None
        observed_schedule_reference: date | None = None
        strict_weekly_current_source = False
        if expected_frequency == "daily":
            if (
                expected_update.hour,
                expected_update.minute,
                expected_update.second,
                expected_update.microsecond,
            ) != (expected_hour, expected_minute, 0, 0):
                raise InputValidationError(
                    f"J-Quants更新 '{dataset_id}' の予定時刻を{expected_hour:02d}:{expected_minute:02d} JSTへ一致させてください。"
                )
            daily_schedule_context = _derive_daily_jquants_expected_state(
                dataset_id,
                as_of_jst,
                publication_cash_dates,
                expected_hour,
                expected_minute,
                latest_reference,
                latest_published_at,
            )
            derived_expected_update = daily_schedule_context["expected_update"]
            derived_expected_reference = daily_schedule_context[
                "expected_reference"
            ]
            early_arrival_observed = daily_schedule_context[
                "early_arrival_observed"
            ]
            observed_schedule_update = daily_schedule_context[
                "observed_schedule_update"
            ]
            observed_schedule_reference = daily_schedule_context[
                "observed_schedule_reference"
            ]
            if expected_update != derived_expected_update:
                raise InputValidationError(
                    f"J-Quants日次更新 '{dataset_id}' の最新期待更新日時を共有公表営業日カレンダーと同日早着からの導出値へ一致させてください。"
                )
            if any(
                update.get(field_name) is not None
                for field_name in (
                    "official_schedule_complete",
                    "official_schedule_window_start_date",
                    "official_schedule_window_end_date",
                    "official_publication_schedule",
                )
            ):
                raise InputValidationError(
                    f"J-Quants日次更新 '{dataset_id}' に週次公式公表予定フィールドを指定しないでください。"
                )
        else:
            strict_weekly_current_source = (
                source["status"] == "valid"
                and source_id in selected_scheduled_history_source_ids
            )
            weekly_schedule_context = _validate_weekly_publication_schedule(
                update,
                dataset_id,
                as_of_jst,
                publication_calendar_by_date,
                latest_reference,
                latest_published_at,
                strict_weekly_current_source,
            )
            derived_expected_update = weekly_schedule_context["expected_update"]
            derived_expected_reference = weekly_schedule_context["expected_reference"]
            official_publication_schedule = weekly_schedule_context["schedule"]
            weekly_expected_availability_status = weekly_schedule_context[
                "expected_availability_status"
            ]
            weekly_reference_release_map = weekly_schedule_context[
                "reference_release_map"
            ]
            last_due_update = weekly_schedule_context["last_due_update"]
            last_due_reference = weekly_schedule_context["last_due_reference"]
            next_scheduled_update = weekly_schedule_context["next_scheduled_update"]
            next_scheduled_reference = weekly_schedule_context[
                "next_scheduled_reference"
            ]
            early_arrival_observed = weekly_schedule_context[
                "early_arrival_observed"
            ]
            observed_schedule_update = weekly_schedule_context[
                "observed_schedule_update"
            ]
            observed_schedule_reference = weekly_schedule_context[
                "observed_schedule_reference"
            ]
            if expected_update != derived_expected_update:
                raise InputValidationError(
                    f"J-Quants週次更新 '{dataset_id}' のlatest_expected_update_jstを公式公表予定列からの導出値へ一致させてください。"
                )
            official_schedule_complete = True
            official_schedule_window_start_date = _parse_iso_date(
                update.get("official_schedule_window_start_date"),
                f"J-Quants週次更新 '{dataset_id}' の official_schedule_window_start_date",
            ).isoformat()
            official_schedule_window_end_date = _parse_iso_date(
                update.get("official_schedule_window_end_date"),
                f"J-Quants週次更新 '{dataset_id}' の official_schedule_window_end_date",
            ).isoformat()
        if expected_frequency == "daily":
            schedule_offset = expected_update - as_of_jst
            if schedule_offset > timedelta(days=max_future_days) or schedule_offset < -timedelta(days=max_past_days):
                raise InputValidationError(
                    f"J-Quants更新 '{dataset_id}' の予定日がprofileの監査可能期間外です。"
                )
        if update.get("publication_week_calendar") is not None:
            raise InputValidationError(
                f"J-Quants更新 '{dataset_id}' のpublication_week_calendarは廃止済みです。共有カレンダーを使ってください。"
            )
        update_checked_at = _parse_jst(
            update.get("update_checked_at_jst"),
            f"J-Quants更新 '{dataset_id}' の update_checked_at_jst",
        )
        if update_checked_at > as_of_jst:
            raise InputValidationError(f"J-Quants更新 '{dataset_id}' の確認時刻が分析時刻より未来です。")
        if (as_of_jst - update_checked_at).total_seconds() > COVERAGE_CHECK_MAX_AGE_HOURS * 3600:
            raise InputValidationError(f"J-Quants更新 '{dataset_id}' の確認時刻が24時間より古いです。")
        availability_status = update.get("availability_status")
        if availability_status not in ("updated", "awaiting_scheduled_update", "delayed", "unavailable"):
            raise InputValidationError(f"J-Quants更新 '{dataset_id}' の availability_status が不正です。")
        is_latest_available = update.get("is_latest_available")
        if not isinstance(is_latest_available, bool):
            raise InputValidationError(f"J-Quants更新 '{dataset_id}' の is_latest_available は真偽値にしてください。")
        expected_reference = _parse_iso_date(
            update.get("expected_latest_reference_date"),
            f"J-Quants更新 '{dataset_id}' の expected_latest_reference_date",
        )
        if latest_reference > as_of_jst.date() or expected_reference > as_of_jst.date():
            raise InputValidationError(f"J-Quants更新 '{dataset_id}' の対象日を分析日より未来にできません。")
        if expected_reference != derived_expected_reference:
            raise InputValidationError(
                f"J-Quants更新 '{dataset_id}' のexpected_latest_reference_dateを公式公表予定・共有カレンダー由来の対象日へ一致させてください。"
            )
        source_data_as_of = _parse_jst(
            source["data_as_of_jst"],
            f"J-Quants台帳 '{source_id}' の data_as_of_jst",
        )
        if latest_published_at != source_data_as_of:
            raise InputValidationError(
                f"J-Quants更新 '{dataset_id}' の最新公表時刻をsource_registry.data_as_of_jstと一致させてください。"
            )
        if latest_published_at > update_checked_at:
            raise InputValidationError(
                f"J-Quants更新 '{dataset_id}' の最新公表時刻を確認時刻より後にできません。"
            )
        if expected_frequency == "weekly" and strict_weekly_current_source:
            reference_release_at = weekly_reference_release_map.get(latest_reference)
            if reference_release_at is None:
                raise InputValidationError(
                    f"J-Quants週次更新 '{dataset_id}' の最新取得対象日を公式予定対象日へ結び付けてください。"
                )
            if reference_release_at.date() > latest_published_at.date():
                raise InputValidationError(
                    f"J-Quants週次更新 '{dataset_id}' の最新取得対象日に対応する公式公表日が実際の最新公表日より後です。"
                )
            if availability_status != weekly_expected_availability_status:
                raise InputValidationError(
                    f"J-Quants週次更新 '{dataset_id}' のavailability_statusを実到着対象日と到来済み・次回公式予定から導出した{weekly_expected_availability_status}へ一致させてください。"
                )
        if (
            expected_frequency == "daily"
            and early_arrival_observed
            and availability_status != "awaiting_scheduled_update"
        ):
            raise InputValidationError(
                f"J-Quants日次更新 '{dataset_id}' の同日早着後は次の現物営業日を待つawaiting_scheduled_updateにしてください。"
            )
        if availability_status == "updated" and (
            (
                (
                    expected_update > as_of_jst
                    or update_checked_at < expected_update
                )
                and not (
                    expected_frequency == "weekly"
                    and early_arrival_observed
                    and next_scheduled_update is None
                )
            )
            or not is_latest_available
            or latest_reference != expected_reference
        ):
            raise InputValidationError(f"J-Quants更新 '{dataset_id}' のupdated状態と公表予定・対象日が矛盾します。")
        if availability_status == "awaiting_scheduled_update" and expected_update <= as_of_jst:
            raise InputValidationError(f"J-Quants更新 '{dataset_id}' は公表予定後にawaitingを指定できません。")
        if availability_status == "delayed" and expected_update > as_of_jst:
            raise InputValidationError(f"J-Quants更新 '{dataset_id}' は公表予定前にdelayedを指定できません。")
        if availability_status == "delayed" and update_checked_at < expected_update:
            raise InputValidationError(f"J-Quants更新 '{dataset_id}' は公表予定後に確認してからdelayedとしてください。")
        if is_latest_available != (latest_reference == expected_reference):
            raise InputValidationError(f"J-Quants更新 '{dataset_id}' のis_latest_availableと対象日が矛盾します。")
        jquants_updates.append(
            {
                "dataset_id": dataset_id,
                "source_id": source_id,
                "schedule_profile_id": schedule_profile_id,
                "frequency": frequency,
                "official_update_time_description": _required_text(
                    update.get("official_update_time_description"),
                    f"J-Quants更新 '{dataset_id}' の official_update_time_description",
                ),
                "latest_expected_update_jst": _format_jst(expected_update),
                "update_checked_at_jst": _format_jst(update_checked_at),
                "latest_available_published_at_jst": _format_jst(latest_published_at),
                "latest_available_reference_date": latest_reference.isoformat(),
                "expected_latest_reference_date": expected_reference.isoformat(),
                "availability_status": availability_status,
                "is_latest_available": is_latest_available,
                "publication_schedule_source_id": _required_text(
                    update.get("publication_schedule_source_id"),
                    f"J-Quants更新 '{dataset_id}' の publication_schedule_source_id",
                ),
                "official_schedule_complete": official_schedule_complete,
                "official_schedule_window_start_date": official_schedule_window_start_date,
                "official_schedule_window_end_date": official_schedule_window_end_date,
                "official_publication_schedule": official_publication_schedule,
                "last_due_update_jst": (
                    _format_jst(last_due_update)
                    if last_due_update is not None
                    else None
                ),
                "last_due_reference_date": (
                    last_due_reference.isoformat()
                    if last_due_reference is not None
                    else None
                ),
                "next_scheduled_update_jst": (
                    _format_jst(next_scheduled_update)
                    if next_scheduled_update is not None
                    else None
                ),
                "next_scheduled_reference_date": (
                    next_scheduled_reference.isoformat()
                    if next_scheduled_reference is not None
                    else None
                ),
                "early_arrival_observed": early_arrival_observed,
                "observed_schedule_update_jst": (
                    _format_jst(observed_schedule_update)
                    if observed_schedule_update is not None
                    else None
                ),
                "observed_schedule_reference_date": (
                    observed_schedule_reference.isoformat()
                    if observed_schedule_reference is not None
                    else None
                ),
            }
        )

    dataset_global_contracts: dict[str, dict[str, Any]] = {}
    for row in jquants_updates:
        dataset_key = row["dataset_id"].casefold()
        dataset_contract = {
            "schedule_profile_id": row["schedule_profile_id"],
            "frequency": row["frequency"],
            "publication_schedule_source_id": row[
                "publication_schedule_source_id"
            ],
            "official_schedule_window_start_date": row[
                "official_schedule_window_start_date"
            ],
            "official_schedule_window_end_date": row[
                "official_schedule_window_end_date"
            ],
            "official_publication_schedule": row[
                "official_publication_schedule"
            ],
        }
        existing_contract = dataset_global_contracts.get(dataset_key)
        if existing_contract is not None and dataset_contract != existing_contract:
            raise InputValidationError(
                f"J-Quants dataset '{row['dataset_id']}' の複数sourceでdataset-global公式契約が一致していません。"
            )
        dataset_global_contracts[dataset_key] = dataset_contract

    if registered_jquants_source_ids:
        normalized_weekly_calendar_ends = [
            _parse_iso_date(
                row["official_schedule_window_end_date"],
                f"J-Quants週次更新 '{row['dataset_id']}' の正規化済み公式予定終端",
            )
            for row in jquants_updates
            if row["frequency"] == "weekly"
        ]
        expected_shared_calendar_end = max(
            [publication_calendar_base_end, *normalized_weekly_calendar_ends]
        )
        actual_shared_calendar_end = date.fromisoformat(
            publication_calendar[-1]["date"]
        )
        if actual_shared_calendar_end != expected_shared_calendar_end:
            raise InputValidationError(
                "jquants_publication_calendar終端を全週次datasetの公式予定終端の最大日へ一致させてください。"
            )

    selected_jquants_sources = {
        row["selected_source_id"]
        for row in selections
        if row["purpose"] == "scheduled_history"
    }
    candidate_jquants_sources = {
        source_id
        for row in selections
        if row["purpose"] == "scheduled_history"
        for source_id in row["candidate_source_ids"]
        if registry[source_id]["provider_family"] == "jquants"
    }
    registered_jquants_sources = {
        source_id
        for source_id, row in registry.items()
        if row["provider_family"] == "jquants"
    }
    audited_jquants_sources = {row["source_id"] for row in jquants_updates}
    update_required_jquants_sources = {
        source_id
        for source_id in candidate_jquants_sources
        if registry[source_id]["status"] in ("valid", "stale", "invalid")
    }
    if (
        registered_jquants_sources != candidate_jquants_sources
        or update_required_jquants_sources != audited_jquants_sources
    ):
        missing_updates = sorted(update_required_jquants_sources - audited_jquants_sources)
        orphan_registry = sorted(registered_jquants_sources - candidate_jquants_sources)
        unregistered_updates = sorted(audited_jquants_sources - update_required_jquants_sources)
        raise InputValidationError(
            "J-Quantsのregistry・scheduled_history候補・データ保有候補のdataset更新監査をsource_id単位で一致させてください。"
            f" 更新欠落={missing_updates}、候補外registry={orphan_registry}、候補外更新={unregistered_updates}"
        )
    updates_by_source = {row["source_id"]: row for row in jquants_updates}
    unusable_selected_sources = [
        source_id
        for source_id in sorted(selected_jquants_sources)
        if (
            updates_by_source[source_id]["availability_status"]
            in ("updated", "awaiting_scheduled_update")
            and not updates_by_source[source_id]["is_latest_available"]
        )
        or updates_by_source[source_id]["availability_status"] == "unavailable"
        or (
            updates_by_source[source_id]["availability_status"] == "delayed"
            and updates_by_source[source_id]["is_latest_available"]
        )
    ]
    if unusable_selected_sources:
        raise InputValidationError(
            "J-Quantsのselected scheduled_historyは最新到着済み、更新待ち、または監査済み遅延状態に限ります: "
            + ", ".join(unusable_selected_sources)
        )

    return {
        "provider_priority": [
            {"provider_family": provider, "tier": tier}
            for provider, tier in sorted(MARKET_DATA_PROVIDER_PRIORITY.items(), key=lambda item: item[1])
        ],
        "priority_scope": "同一series_keyの価格観測。公式仕様・カレンダーの権威順位には適用しない。",
        "execution_anchor_rule": "現在板・現在NT・枚数はKabusの有効な両側気配だけをアンカーにする。",
        "source_registry": registry,
        "source_selection_audit": selections,
        "jquants_publication_calendar_source_id": publication_calendar_source_id,
        "jquants_publication_calendar_checked_at_jst": (
            _format_jst(publication_calendar_checked_at)
            if publication_calendar_checked_at is not None
            else None
        ),
        "jquants_publication_calendar": publication_calendar,
        "jquants_dataset_updates": jquants_updates,
        "execution_anchor_valid_until_jst": _format_jst(min(anchor_expiries)),
    }


# ----------------------------------------


def _format_utc_offset(value: datetime) -> str:
    """
    機能:
        日時が持つUTC offsetを±HH:MM形式へ整形する。

    引数:
        value (datetime): UTC offset付き日時。

    返り値:
        str: 符号付きUTC offset文字列。
    """
    offset = value.utcoffset()
    if offset is None:
        raise InputValidationError("UTC offsetを持たない日時は整形できません。")
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    absolute_minutes = abs(total_minutes)
    hours, minutes = divmod(absolute_minutes, 60)
    return f"{sign}{hours:02d}:{minutes:02d}"


# ----------------------------------------


def _decimal_string(value: Decimal, places: int | None = None) -> str:
    """
    機能:
        Decimalを指数表記を使わない文字列へ整形する。

    引数:
        value (Decimal): 整形対象の数値。
        places (int | None): 小数桁数。未指定時は末尾ゼロを除去する。

    返り値:
        str: 整形済み数値文字列。
    """
    if places is not None:
        quantum = Decimal("1").scaleb(-places)
        return format(value.quantize(quantum, rounding=ROUND_HALF_UP), f".{places}f")
    normalized = format(value.normalize(), "f")
    return "0" if normalized == "-0" else normalized


# ----------------------------------------


def _round_to_tick(value: float, tick: Decimal) -> str:
    """
    機能:
        価格を指定呼値へ四捨五入し、文字列で返す。

    引数:
        value (float): 丸める価格。
        tick (Decimal): 正の呼値単位。

    返り値:
        str: 呼値へ丸めた価格文字列。
    """
    units = (Decimal(str(value)) / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    rounded = units * tick
    places = max(0, -tick.as_tuple().exponent)
    return _decimal_string(rounded, places)


# ----------------------------------------


def _normal_cdf(value: float) -> float:
    """
    機能:
        標準正規分布の累積分布関数を計算する。

    引数:
        value (float): 累積確率を求める標準化値。

    返り値:
        float: 標準正規累積確率。
    """
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


# ----------------------------------------


def _normal_pdf(value: float) -> float:
    """
    機能:
        標準正規分布の確率密度を計算する。

    引数:
        value (float): 密度を求める標準化値。

    返り値:
        float: 標準正規確率密度。
    """
    return math.exp(-0.5 * value * value) / math.sqrt(2.0 * math.pi)


# ----------------------------------------


def bivariate_normal_cdf(a: float, b: float, rho: float, steps: int = 4096) -> float:
    """
    機能:
        二変量標準正規分布の累積確率を条件付き分布の数値積分で計算する。

    引数:
        a (float): 第1変数の上限。
        b (float): 第2変数の上限。
        rho (float): 相関係数。
        steps (int): Simpson積分の偶数分割数。

    返り値:
        float: P(X <= a, Y <= b) の近似値。
    """
    if rho < -1.0 or rho > 1.0:
        raise InputValidationError("相関係数は -1 以上 1 以下で指定してください。")
    if rho >= 0.999999:
        return _normal_cdf(min(a, b))
    if rho <= -0.999999:
        return max(0.0, _normal_cdf(a) - _normal_cdf(-b))
    if a <= -10.0 or b <= -10.0:
        return 0.0
    if a >= 10.0:
        return _normal_cdf(b)
    if b >= 10.0:
        return _normal_cdf(a)

    lower = -10.0
    upper = min(a, 10.0)
    if upper <= lower:
        return 0.0
    if steps < 2:
        steps = 2
    if steps % 2:
        steps += 1
    width = (upper - lower) / steps
    conditional_scale = math.sqrt(1.0 - rho * rho)

    def integrand(x_value: float) -> float:
        """
        機能:
            条件付き正規分布を用いた1次元積分の被積分関数を計算する。

        引数:
            x_value (float): 第1変数の積分位置。

        返り値:
            float: 当該位置の確率密度寄与。
        """
        conditional = (b - rho * x_value) / conditional_scale
        return _normal_pdf(x_value) * _normal_cdf(conditional)

    total = integrand(lower) + integrand(upper)
    for index in range(1, steps):
        coefficient = 4.0 if index % 2 else 2.0
        total += coefficient * integrand(lower + index * width)
    result = total * width / 3.0
    return min(1.0, max(0.0, result))


# ----------------------------------------


def _normal_joint_probabilities(spec: Mapping[str, Any]) -> dict[str, float]:
    """
    機能:
        平均・標準偏差・相関から4象限の二変量正規事前確率を計算する。

    引数:
        spec (Mapping[str, Any]): 各指数の期限収益率平均・標準偏差と相関。

    返り値:
        dict[str, float]: 4象限の合計1となる確率。
    """
    nikkei_mean = _bounded_float(
        spec.get("nikkei_mean_pct", 0.0), "joint_normal.nikkei_mean_pct", -100.0, 100.0
    )
    topix_mean = _bounded_float(
        spec.get("topix_mean_pct", 0.0), "joint_normal.topix_mean_pct", -100.0, 100.0
    )
    nikkei_vol = _bounded_float(spec.get("nikkei_vol_pct"), "joint_normal.nikkei_vol_pct", 0.0, 100.0)
    topix_vol = _bounded_float(spec.get("topix_vol_pct"), "joint_normal.topix_vol_pct", 0.0, 100.0)
    rho = _bounded_float(spec.get("correlation"), "joint_normal.correlation", -0.95, 0.95)
    if nikkei_vol <= 0.0 or topix_vol <= 0.0:
        raise InputValidationError("joint_normal の標準偏差は正値で指定してください。")

    a = -nikkei_mean / nikkei_vol
    b = -topix_mean / topix_vol
    both_down = bivariate_normal_cdf(a, b, rho)
    nikkei_down = _normal_cdf(a)
    topix_down = _normal_cdf(b)
    probabilities = {
        "nk_up_topix_up": 1.0 - nikkei_down - topix_down + both_down,
        "nk_up_topix_down": topix_down - both_down,
        "nk_down_topix_up": nikkei_down - both_down,
        "nk_down_topix_down": both_down,
    }
    sanitized = {key: max(0.0, value) for key, value in probabilities.items()}
    total = sum(sanitized.values())
    if total <= 0.0:
        raise InputValidationError("二変量正規分布から有効な4象限確率を計算できません。")
    return {key: sanitized[key] / total for key in CATEGORIES}


# ----------------------------------------


def _selected_base_method(spec: Mapping[str, Any]) -> str:
    """
    機能:
        probability.baseで選択された排他的な推定方式を確定する。

    引数:
        spec (Mapping[str, Any]): probability.base の設定。

    返り値:
        str: probabilities、historical_counts、joint_normal、scenario_mixtureのいずれか。
    """
    methods = [
        name
        for name in ("probabilities", "historical_counts", "joint_normal", "scenario_mixture")
        if name in spec
    ]
    if len(methods) != 1:
        raise InputValidationError(
            "probability.base には probabilities、historical_counts、joint_normal、scenario_mixture のいずれか1つだけを指定してください。"
        )
    return methods[0]


# ----------------------------------------


def _base_probabilities(
    spec: Mapping[str, Any],
    as_of_jst: datetime,
    expected_horizon_seconds: int,
    base_provenance: Mapping[str, Any],
) -> tuple[dict[str, float], str, list[dict[str, Any]]]:
    """
    機能:
        明示確率、履歴件数、二変量正規、検証済みシナリオ混合のいずれかから4象限の事前分布を作る。

    引数:
        spec (Mapping[str, Any]): probability.base の設定。
        as_of_jst (datetime): 分析基準の日本時間。
        expected_horizon_seconds (int): 分析時刻から4象限評価期限までの秒数。
        base_provenance (Mapping[str, Any]): 混合全体の検証済みモデル来歴。

    返り値:
        tuple[dict[str, float], str, list[dict[str, Any]]]: 合計1の事前確率、方式名、シナリオ監査行。
    """
    method = _selected_base_method(spec)
    if method == "scenario_mixture":
        scenarios_raw = _require_sequence(spec[method], "probability.base.scenario_mixture")
        if len(scenarios_raw) < 2:
            raise InputValidationError("scenario_mixture は相互排他的な2シナリオ以上にしてください。")
        combined = {key: 0.0 for key in CATEGORIES}
        total_weight = 0.0
        seen_ids: set[str] = set()
        partition_definition_id: str | None = None
        scenario_audit: list[dict[str, Any]] = []
        for index, raw_scenario in enumerate(scenarios_raw):
            scenario = _require_mapping(raw_scenario, f"scenario_mixture[{index}]")
            scenario_id_raw = scenario.get("scenario_id")
            if not isinstance(scenario_id_raw, str) or not scenario_id_raw.strip():
                raise InputValidationError("scenario_mixture の scenario_id は重複しない非空文字列にしてください。")
            scenario_id = scenario_id_raw.strip()
            scenario_key = scenario_id.casefold()
            if scenario_key in seen_ids:
                raise InputValidationError("scenario_mixture の scenario_id は正規化後も重複させないでください。")
            seen_ids.add(scenario_key)
            if scenario.get("resolution_verified") is not True:
                raise InputValidationError(f"シナリオ '{scenario_id}' は判定規則を確認してください。")
            if scenario.get("variance_double_count_checked") is not True:
                raise InputValidationError(f"シナリオ '{scenario_id}' はOPとの分散二重計上を確認してください。")
            if scenario.get("mutually_exclusive_verified") is not True:
                raise InputValidationError(f"シナリオ '{scenario_id}' は集合内の相互排他を確認してください。")
            if scenario.get("exhaustive_verified") is not True:
                raise InputValidationError(f"シナリオ '{scenario_id}' は補集合を含む網羅性を確認してください。")
            scenario_partition_raw = scenario.get("partition_definition_id")
            if not isinstance(scenario_partition_raw, str) or not scenario_partition_raw.strip():
                raise InputValidationError(f"シナリオ '{scenario_id}' の partition_definition_id を指定してください。")
            scenario_partition = scenario_partition_raw.strip()
            if partition_definition_id is None:
                partition_definition_id = scenario_partition
            elif scenario_partition != partition_definition_id:
                raise InputValidationError("scenario_mixture の partition_definition_id を全行で一致させてください。")
            coverage_item = scenario.get("coverage_item")
            if coverage_item not in (*COVERAGE_ITEMS, "other"):
                raise InputValidationError(f"シナリオ '{scenario_id}' の coverage_item が不正です。")
            source_id = scenario.get("source_id")
            if not isinstance(source_id, str) or not source_id.strip():
                raise InputValidationError(f"シナリオ '{scenario_id}' の source_id を指定してください。")
            observed_at = _parse_jst(
                scenario.get("observed_at_jst"),
                f"シナリオ '{scenario_id}' の observed_at_jst",
            )
            if observed_at > as_of_jst:
                raise InputValidationError(f"シナリオ '{scenario_id}' の観測時刻が分析時刻より未来です。")
            if coverage_item != "other":
                age_hours = (as_of_jst - observed_at).total_seconds() / 3600.0
                if age_hours > COVERAGE_USED_MAX_AGE_HOURS[coverage_item]:
                    raise InputValidationError(f"シナリオ '{scenario_id}' の重み情報が鮮度上限を超えています。")
            weight_source = scenario.get("weight_source")
            if not isinstance(weight_source, str) or not weight_source.strip():
                raise InputValidationError(f"シナリオ '{scenario_id}' の weight_source を指定してください。")
            weight = _bounded_float(
                scenario.get("weight"),
                f"シナリオ '{scenario_id}' の weight",
                0.0,
                1.0,
            )
            if weight <= 0.0:
                raise InputValidationError(
                    f"シナリオ '{scenario_id}' の weight は正値にし、重み0の形だけの分岐を除外してください。"
                )
            conditional_basis = _validated_model_provenance(
                _require_mapping(
                    scenario.get("conditional_probability_basis"),
                    f"シナリオ '{scenario_id}' の conditional_probability_basis",
                ),
                f"scenario_mixture[{index}].conditional_probability_basis",
                as_of_jst,
                "forecast_valid_until_midpoint_direction",
                expected_horizon_seconds,
            )
            for field_name in (
                "model_id",
                "model_version",
                "calibration_version",
                "method",
                "model_structure_id",
                "model_artifact_sha256",
                "training_data_cutoff_jst",
            ):
                if conditional_basis[field_name] != base_provenance[field_name]:
                    raise InputValidationError(
                        "scenario_mixture の条件付き分布モデルを混合全体のbase_provenanceと一致させてください。"
                    )
            if conditional_basis["validation_passed"] is not True:
                raise InputValidationError(
                    f"シナリオ '{scenario_id}' の条件付き4象限モデルは検証合格済みにしてください。"
                )
            if not any(
                link["coverage_item"] != "polymarket"
                for link in conditional_basis["source_links"]
            ):
                raise InputValidationError(
                    f"シナリオ '{scenario_id}' の条件付き4象限モデルにPolymarket以外の推定元を含めてください。"
                )
            conditional_raw = _require_mapping(
                scenario.get("conditional_probabilities"),
                f"シナリオ '{scenario_id}' の conditional_probabilities",
            )
            if set(conditional_raw) != set(CATEGORIES):
                raise InputValidationError(f"シナリオ '{scenario_id}' は4象限確率を過不足なく指定してください。")
            conditional = {
                key: _bounded_float(
                    conditional_raw[key],
                    f"シナリオ '{scenario_id}' の conditional_probabilities.{key}",
                    0.0,
                    1.0,
                )
                for key in CATEGORIES
            }
            if not math.isclose(sum(conditional.values()), 1.0, rel_tol=0.0, abs_tol=1e-8):
                raise InputValidationError(f"シナリオ '{scenario_id}' の4象限確率合計は1にしてください。")
            total_weight += weight
            for key in CATEGORIES:
                combined[key] += weight * conditional[key]
            scenario_audit.append(
                {
                    "scenario_id": scenario_id.strip(),
                    "weight": weight,
                    "weight_source": weight_source.strip(),
                    "coverage_item": coverage_item,
                    "source_id": source_id.strip(),
                    "observed_at_jst": _format_jst(observed_at),
                    "resolution_verified": True,
                    "variance_double_count_checked": True,
                    "mutually_exclusive_verified": True,
                    "exhaustive_verified": True,
                    "partition_definition_id": scenario_partition,
                    "conditional_probability_basis": conditional_basis,
                    "conditional_probability_bp": _round_probabilities_to_bp(conditional),
                }
            )
        if not math.isclose(total_weight, 1.0, rel_tol=0.0, abs_tol=1e-8):
            raise InputValidationError("scenario_mixture のシナリオ重み合計は1にしてください。")
        return combined, method, scenario_audit

    raw = _require_mapping(spec[method], f"probability.base.{method}")

    if method == "joint_normal":
        return _normal_joint_probabilities(raw), method, []

    if set(raw) != set(CATEGORIES):
        raise InputValidationError(f"probability.base.{method} は4象限を過不足なく指定してください。")
    if method == "probabilities":
        values = {key: _bounded_float(raw[key], f"base.probabilities.{key}", 0.0, 1.0) for key in CATEGORIES}
        total = sum(values.values())
        if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-8):
            raise InputValidationError("base.probabilities の合計は1にしてください。")
        return values, method, []

    alpha = _bounded_float(spec.get("dirichlet_alpha", 1.0), "base.dirichlet_alpha", 0.0, 1_000.0)
    counts: dict[str, float] = {}
    for key in CATEGORIES:
        counts[key] = float(
            _bounded_integer(raw[key], f"base.historical_counts.{key}", 0, 10_000_000)
        )
    denominator = sum(counts.values()) + alpha * len(CATEGORIES)
    if denominator <= 0.0:
        raise InputValidationError("historical_counts と事前強度の合計は正値にしてください。")
    return {key: (counts[key] + alpha) / denominator for key in CATEGORIES}, method, []


# ----------------------------------------


def _aggregate_evidence(
    evidence: Sequence[Any],
    as_of_jst: datetime,
) -> tuple[dict[str, dict[str, float]], list[dict[str, Any]]]:
    """
    機能:
        重複のない証拠をファンダメンタルズと需給へ分離し、時間減衰後の象限スコアを集計する。

    引数:
        evidence (Sequence[Any]): 証拠キー、ブロック、象限スコア、品質、観測時刻、半減期の配列。
        as_of_jst (datetime): 分析基準の日本時間。

    返り値:
        tuple[dict[str, dict[str, float]], list[dict[str, Any]]]: ブロック別スコアと証拠監査行。
    """
    totals = {block: {key: 0.0 for key in CATEGORIES} for block in EVIDENCE_BLOCKS}
    block_counts = {block: 0 for block in EVIDENCE_BLOCKS}
    seen_keys: set[str] = set()
    audit_rows: list[dict[str, Any]] = []

    for index, raw_item in enumerate(evidence):
        item = _require_mapping(raw_item, f"probability.evidence[{index}]")
        evidence_key_raw = item.get("evidence_key")
        if not isinstance(evidence_key_raw, str) or not evidence_key_raw.strip():
            raise InputValidationError(f"probability.evidence[{index}].evidence_key は非空文字列にしてください。")
        evidence_key = evidence_key_raw.strip()
        evidence_deduplication_key = evidence_key.casefold()
        if evidence_deduplication_key in seen_keys:
            raise InputValidationError(f"evidence_key '{evidence_key}' が正規化後に重複しています。")
        seen_keys.add(evidence_deduplication_key)

        block = item.get("block")
        if block not in EVIDENCE_BLOCKS:
            raise InputValidationError(f"証拠 '{evidence_key}' の block が不正です。")
        coverage_item = item.get("coverage_item")
        if coverage_item not in (*COVERAGE_ITEMS, "other"):
            raise InputValidationError(f"証拠 '{evidence_key}' の coverage_item が不正です。")
        text_fields: dict[str, str] = {}
        for field_name in ("source_id", "fact", "inference", "counterevidence"):
            field_value = item.get(field_name)
            if not isinstance(field_value, str) or not field_value.strip():
                raise InputValidationError(f"証拠 '{evidence_key}' の {field_name} は非空文字列にしてください。")
            text_fields[field_name] = field_value.strip()
        scores_raw = _require_mapping(item.get("category_scores"), f"証拠 '{evidence_key}' の category_scores")
        if set(scores_raw) != set(CATEGORIES):
            raise InputValidationError(f"証拠 '{evidence_key}' は4象限スコアを過不足なく指定してください。")
        scores = {
            key: _bounded_float(scores_raw[key], f"証拠 '{evidence_key}' の {key}", -3.0, 3.0)
            for key in CATEGORIES
        }
        quality = _bounded_float(item.get("quality", 1.0), f"証拠 '{evidence_key}' の quality", 0.0, 1.0)
        independence = _bounded_float(
            item.get("independence", 1.0), f"証拠 '{evidence_key}' の independence", 0.0, 1.0
        )
        observed_at = _parse_jst(item.get("observed_at_jst"), f"証拠 '{evidence_key}' の observed_at_jst")
        if observed_at > as_of_jst:
            raise InputValidationError(f"証拠 '{evidence_key}' の観測時刻が分析時刻より未来です。")
        half_life = _finite_float(item.get("half_life_hours"), f"証拠 '{evidence_key}' の half_life_hours")
        if half_life <= 0.0:
            raise InputValidationError(f"証拠 '{evidence_key}' の half_life_hours は正値にしてください。")
        age_hours = (as_of_jst - observed_at).total_seconds() / 3600.0
        freshness = 2.0 ** (-age_hours / half_life)
        multiplier = quality * independence * freshness
        neutral_observation = item.get("neutral_observation") is True
        score_values = tuple(scores.values())
        score_is_constant = math.isclose(
            max(score_values),
            min(score_values),
            rel_tol=0.0,
            abs_tol=1e-12,
        )
        all_scores_zero = all(score == 0.0 for score in score_values)
        if score_is_constant and not all_scores_zero:
            raise InputValidationError(
                f"証拠 '{evidence_key}' の4象限スコアが全て同値で、中心化後の方向差がありません。"
            )
        if all_scores_zero and not neutral_observation:
            raise InputValidationError(
                f"証拠 '{evidence_key}' の全スコアが0です。確認済み中立なら neutral_observation=true を指定してください。"
            )
        if neutral_observation and any(score != 0.0 for score in scores.values()):
            raise InputValidationError(
                f"証拠 '{evidence_key}' は neutral_observation=true のため4象限スコアを全0にしてください。"
            )
        for key in CATEGORIES:
            totals[block][key] += scores[key] * multiplier
        if multiplier >= 1e-6:
            block_counts[block] += 1
        audit_rows.append(
            {
                "evidence_key": evidence_key,
                "block": block,
                "coverage_item": coverage_item,
                **text_fields,
                "observed_at_jst": _format_jst(observed_at),
                "age_hours": round(age_hours, 4),
                "freshness_factor": round(freshness, 6),
                "quality": quality,
                "independence": independence,
                "effective_multiplier": round(multiplier, 6),
                "neutral_observation": neutral_observation,
                "category_scores": scores,
            }
        )

    missing_blocks = [block for block, count in block_counts.items() if count == 0]
    if missing_blocks:
        raise InputValidationError(
            "4象限計算にはファンダメンタルズと需給の両ブロックが必要です。不足: " + ", ".join(missing_blocks)
        )

    for block in EVIDENCE_BLOCKS:
        mean_score = sum(totals[block].values()) / len(CATEGORIES)
        totals[block] = {key: totals[block][key] - mean_score for key in CATEGORIES}
    audit_rows.sort(key=lambda row: row["evidence_key"])
    return totals, audit_rows


# ----------------------------------------


def _softmax_evidence_update(
    base: Mapping[str, float],
    evidence_totals: Mapping[str, Mapping[str, float]],
) -> dict[str, float]:
    """
    機能:
        事前確率をファンダメンタルズ・需給の等重み対数プールで更新する。

    引数:
        base (Mapping[str, float]): 4象限の事前確率。
        evidence_totals (Mapping[str, Mapping[str, float]]): ブロック別の中心化済み象限スコア。

    返り値:
        dict[str, float]: 合計1の生モデル確率。
    """
    logits: dict[str, float] = {}
    for key in CATEGORIES:
        prior = max(base[key], 1e-12)
        evidence_score = sum(EVIDENCE_WEIGHTS[block] * evidence_totals[block][key] for block in EVIDENCE_BLOCKS)
        logits[key] = math.log(prior) + evidence_score / EVIDENCE_TEMPERATURE
    maximum = max(logits.values())
    exponentials = {key: math.exp(logits[key] - maximum) for key in CATEGORIES}
    denominator = sum(exponentials.values())
    return {key: exponentials[key] / denominator for key in CATEGORIES}


# ----------------------------------------


def _round_probabilities_to_bp(probabilities: Mapping[str, float], total_bp: int = TOTAL_PROBABILITY_BP) -> dict[str, int]:
    """
    機能:
        最大剰余法で確率を整数ベーシスポイントへ変換し、合計を厳密にそろえる。

    引数:
        probabilities (Mapping[str, float]): 合計1の4象限確率。
        total_bp (int): 変換後の合計ベーシスポイント。

    返り値:
        dict[str, int]: 合計がtotal_bpとなる整数確率。
    """
    scaled = {key: max(0.0, probabilities[key]) * total_bp for key in CATEGORIES}
    floors = {key: int(math.floor(scaled[key])) for key in CATEGORIES}
    remainder = total_bp - sum(floors.values())
    order = sorted(CATEGORIES, key=lambda key: (scaled[key] - floors[key], -CATEGORIES.index(key)), reverse=True)
    for key in order[:remainder]:
        floors[key] += 1
    if sum(floors.values()) != total_bp:
        raise RuntimeError("確率の最大剰余丸めに失敗しました。")
    return floors


# ----------------------------------------


def _project_dominant_share(raw_bp: Mapping[str, int], dominant: str) -> tuple[dict[str, int], bool]:
    """
    機能:
        最大象限が50%超となるよう、他象限の相対比を保った四択比較値へ射影する。

    引数:
        raw_bp (Mapping[str, int]): 合計10,000bpの生モデル確率。
        dominant (str): 射影対象となる最大象限。

    返り値:
        tuple[dict[str, int], bool]: 合計10,000bpの比較値と制約適用有無。
    """
    if raw_bp[dominant] >= DOMINANCE_FLOOR_BP:
        return dict(raw_bp), False
    remaining_raw = sum(raw_bp[key] for key in CATEGORIES if key != dominant)
    available = TOTAL_PROBABILITY_BP - DOMINANCE_FLOOR_BP
    projected = {key: 0 for key in CATEGORIES}
    projected[dominant] = DOMINANCE_FLOOR_BP
    if remaining_raw <= 0:
        raise RuntimeError("非最大象限の確率がないため射影できません。")
    scaled = {
        key: raw_bp[key] * available / remaining_raw
        for key in CATEGORIES
        if key != dominant
    }
    for key, value in scaled.items():
        projected[key] = int(math.floor(value))
    remainder = TOTAL_PROBABILITY_BP - sum(projected.values())
    order = sorted(
        (key for key in CATEGORIES if key != dominant),
        key=lambda key: (scaled[key] - math.floor(scaled[key]), -CATEGORIES.index(key)),
        reverse=True,
    )
    for key in order[:remainder]:
        projected[key] += 1
    return projected, True


# ----------------------------------------


def _validate_calibration(
    spec: Mapping[str, Any],
    as_of_jst: datetime,
    expected_model_version: str,
    expected_horizon_seconds: int,
    expected_base_method: str,
    expected_model_id: str,
    expected_model_structure_id: str,
    expected_base_model_artifact_sha256: str,
) -> dict[str, Any]:
    """
    機能:
        校正状態と学習終了日の未来参照有無を検証する。

    引数:
        spec (Mapping[str, Any]): 校正状態、学習終了日、有効標本数。
        as_of_jst (datetime): 分析基準の日本時間。
        expected_model_version (str): 現在の4象限確率入力が使用するモデル版。
        expected_horizon_seconds (int): 今回の分析時刻から予測有効期限までの秒数。
        expected_base_method (str): 現在の事前分布推定方式。
        expected_model_id (str): 現在の事前分布推定器ID。
        expected_model_structure_id (str): 現在の確率モデル構造ID。
        expected_base_model_artifact_sha256 (str): 現在のbase推定モデル成果物のSHA-256。

    返り値:
        dict[str, Any]: 表現可否を含む校正監査情報。
    """
    status = spec.get("status", "uncalibrated")
    if status not in ("walk_forward", "uncalibrated"):
        raise InputValidationError("calibration.status は walk_forward または uncalibrated にしてください。")
    trained_through_raw = spec.get("trained_through")
    trained_through: date | None = None
    if trained_through_raw is not None:
        if not isinstance(trained_through_raw, str):
            raise InputValidationError("calibration.trained_through はYYYY-MM-DD文字列にしてください。")
        try:
            trained_through = date.fromisoformat(trained_through_raw)
        except ValueError as exc:
            raise InputValidationError("calibration.trained_through の日付形式が不正です。") from exc
        if trained_through >= as_of_jst.date():
            raise InputValidationError("calibration.trained_through は分析日より前でなければなりません。")
    effective_sample_count = _bounded_integer(
        spec.get("effective_sample_count", 0),
        "calibration.effective_sample_count",
        0,
        10_000_000,
    )
    if status == "uncalibrated":
        return {
            "status": status,
            "trained_through": trained_through.isoformat() if trained_through else None,
            "effective_sample_count": effective_sample_count,
            "horizon_seconds": expected_horizon_seconds,
            "probability_wording_allowed": False,
            "win_rate_wording_allowed": False,
            "wording_definition": "予測有効期限時点の4象限方向。取引損益の勝率ではない。",
            "wording_block_reasons": ["ウォークフォワード校正未実施"],
        }

    if trained_through is None:
        raise InputValidationError("walk_forward校正では calibration.trained_through が必要です。")
    required_text: dict[str, str] = {}
    for key in (
        "model_version",
        "calibration_version",
        "method",
        "horizon_definition",
        "session_path_definition_id",
        "base_method",
        "model_id",
        "model_structure_id",
        "base_model_artifact_sha256",
    ):
        value = spec.get(key)
        if not isinstance(value, str) or not value.strip():
            raise InputValidationError(f"calibration.{key} は非空文字列にしてください。")
        required_text[key] = value.strip()
    if required_text["model_version"] != expected_model_version:
        raise InputValidationError("calibration.model_version を probability.model_version と一致させてください。")
    if required_text["method"] not in ("rolling_origin", "expanding_window"):
        raise InputValidationError("calibration.method は rolling_origin または expanding_window にしてください。")
    if required_text["horizon_definition"] != "forecast_valid_until_midpoint_direction":
        raise InputValidationError(
            "calibration.horizon_definition は forecast_valid_until_midpoint_direction にしてください。"
        )
    if required_text["session_path_definition_id"] != "current_continuous_session_no_recess_crossing_v1":
        raise InputValidationError(
            "calibration.session_path_definition_id は current_continuous_session_no_recess_crossing_v1 にしてください。"
        )
    supported_origin_sessions = _normalized_identifier_list(
        spec.get("supported_origin_sessions"),
        "calibration.supported_origin_sessions",
    )
    if any(value not in FUTURES_SESSION_KINDS for value in supported_origin_sessions):
        raise InputValidationError(
            "calibration.supported_origin_sessions はday_session/night_sessionだけにしてください。"
        )
    if required_text["base_method"] != expected_base_method:
        raise InputValidationError("calibration.base_method を現在の probability.base と一致させてください。")
    if required_text["model_id"] != expected_model_id:
        raise InputValidationError("calibration.model_id を probability.base_provenance.model_id と一致させてください。")
    if required_text["model_structure_id"] != expected_model_structure_id:
        raise InputValidationError(
            "calibration.model_structure_id を現在の確率モデル構造と一致させてください。"
        )
    if required_text["base_model_artifact_sha256"] != expected_base_model_artifact_sha256:
        raise InputValidationError(
            "calibration.base_model_artifact_sha256 を現在のbaseモデル成果物と一致させてください。"
        )
    horizon_seconds = _bounded_integer(
        spec.get("horizon_seconds"),
        "calibration.horizon_seconds",
        1,
        31_536_000,
    )
    if horizon_seconds != expected_horizon_seconds:
        raise InputValidationError(
            "calibration.horizon_seconds を今回の分析時刻から予測有効期限までの秒数と一致させてください。"
        )
    if spec.get("validation_passed") is not True:
        raise InputValidationError("walk_forward校正では calibration.validation_passed=true が必要です。")

    quadrant_counts_raw = _require_mapping(
        spec.get("quadrant_observation_counts"),
        "calibration.quadrant_observation_counts",
    )
    if set(quadrant_counts_raw) != set(CATEGORIES):
        raise InputValidationError("calibration.quadrant_observation_counts は4象限を過不足なく指定してください。")
    quadrant_counts = {
        key: _bounded_integer(
            quadrant_counts_raw[key],
            f"calibration.quadrant_observation_counts.{key}",
            0,
            10_000_000,
        )
        for key in CATEGORIES
    }
    if sum(quadrant_counts.values()) != effective_sample_count:
        raise InputValidationError("4象限の観測件数合計を effective_sample_count と一致させてください。")

    metrics_raw = _require_mapping(spec.get("metrics"), "calibration.metrics")
    metrics = {
        "multiclass_brier": _bounded_float(
            metrics_raw.get("multiclass_brier"), "calibration.metrics.multiclass_brier", 0.0, 2.0
        ),
        "log_loss": _bounded_float(metrics_raw.get("log_loss"), "calibration.metrics.log_loss", 0.0, 100.0),
        "top_class_accuracy": _bounded_float(
            metrics_raw.get("top_class_accuracy"), "calibration.metrics.top_class_accuracy", 0.0, 1.0
        ),
        "reliability_max_abs_error": _bounded_float(
            metrics_raw.get("reliability_max_abs_error"),
            "calibration.metrics.reliability_max_abs_error",
            0.0,
            1.0,
        ),
    }
    reliability_raw = _require_sequence(spec.get("reliability_bins"), "calibration.reliability_bins")
    if len(reliability_raw) < 3:
        raise InputValidationError("calibration.reliability_bins は3区間以上指定してください。")
    reliability_bins: list[dict[str, Any]] = []
    reliability_count = 0
    previous_predicted: float | None = None
    for index, raw_bin in enumerate(reliability_raw):
        bin_spec = _require_mapping(raw_bin, f"calibration.reliability_bins[{index}]")
        count = _bounded_integer(
            bin_spec.get("count"),
            f"calibration.reliability_bins[{index}].count",
            1,
            10_000_000,
        )
        predicted = _bounded_float(
            bin_spec.get("mean_predicted_probability"),
            f"calibration.reliability_bins[{index}].mean_predicted_probability",
            0.25,
            1.0,
        )
        if previous_predicted is not None and predicted <= previous_predicted:
            raise InputValidationError(
                "calibration.reliability_bins の mean_predicted_probability は厳密な昇順にしてください。"
            )
        previous_predicted = predicted
        realized = _bounded_float(
            bin_spec.get("realized_frequency"),
            f"calibration.reliability_bins[{index}].realized_frequency",
            0.0,
            1.0,
        )
        reliability_count += count
        reliability_bins.append(
            {
                "count": count,
                "mean_predicted_probability": predicted,
                "realized_frequency": realized,
            }
        )
    if reliability_count != effective_sample_count:
        raise InputValidationError("reliability_bins の件数合計を effective_sample_count と一致させてください。")

    weighted_top_class_accuracy = sum(
        row["count"] * row["realized_frequency"]
        for row in reliability_bins
    ) / effective_sample_count
    if not math.isclose(
        metrics["top_class_accuracy"],
        weighted_top_class_accuracy,
        rel_tol=0.0,
        abs_tol=1e-6,
    ):
        raise InputValidationError(
            "calibration.metrics.top_class_accuracy を reliability_bins の件数加重実現率と一致させてください。"
        )

    calculated_reliability_error = max(
        abs(row["mean_predicted_probability"] - row["realized_frequency"])
        for row in reliability_bins
    )
    if not math.isclose(
        metrics["reliability_max_abs_error"],
        calculated_reliability_error,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise InputValidationError(
            "calibration.metrics.reliability_max_abs_error を reliability_bins からの再計算値と一致させてください。"
        )

    wording_block_reasons: list[str] = []
    if effective_sample_count < MIN_CALIBRATION_SAMPLE_COUNT:
        wording_block_reasons.append(f"有効標本数が{MIN_CALIBRATION_SAMPLE_COUNT}未満")
    if min(quadrant_counts.values()) < MIN_CALIBRATION_QUADRANT_COUNT:
        wording_block_reasons.append(f"象限別観測件数の最小が{MIN_CALIBRATION_QUADRANT_COUNT}未満")
    if metrics["multiclass_brier"] >= 0.75:
        wording_block_reasons.append("多クラスBrier得点が一様予測基準以上")
    if metrics["log_loss"] >= math.log(4.0):
        wording_block_reasons.append("対数損失が一様予測基準以上")
    if metrics["top_class_accuracy"] <= 0.25:
        wording_block_reasons.append("トップ分類的中率が一様予測基準以下")
    if calculated_reliability_error > 0.10:
        wording_block_reasons.append("確率帯別の最大校正誤差が10%超")
    wording_allowed = not wording_block_reasons
    return {
        "status": status,
        "trained_through": trained_through.isoformat() if trained_through else None,
        "effective_sample_count": effective_sample_count,
        "horizon_seconds": horizon_seconds,
        "supported_origin_sessions": supported_origin_sessions,
        "session_path_definition_id": required_text["session_path_definition_id"],
        **required_text,
        "validation_passed": True,
        "quadrant_observation_counts": quadrant_counts,
        "metrics": metrics,
        "calculated_weighted_top_class_accuracy": weighted_top_class_accuracy,
        "calculated_reliability_max_abs_error": calculated_reliability_error,
        "reliability_bins": reliability_bins,
        "probability_wording_allowed": wording_allowed,
        "win_rate_wording_allowed": wording_allowed,
        "wording_definition": "予測有効期限時点の4象限方向。取引損益の勝率ではない。",
        "wording_block_reasons": wording_block_reasons,
    }


# ----------------------------------------


def _calculate_relative_value(
    spec: Mapping[str, Any],
    as_of_jst: datetime,
    expected_horizon_seconds: int,
) -> dict[str, Any]:
    """
    機能:
        日経リターンとTOPIXリターンの差からNTロング・ショート・コスト帯内の確率を計算する。

    引数:
        spec (Mapping[str, Any]): ファンダ・需給・イベントの相対ドリフト、相対σ、往復コスト。
        as_of_jst (datetime): モデル来歴と分散ソースの未来参照を防ぐ分析基準時刻。
        expected_horizon_seconds (int): 分析時刻から相対価値評価期限までの秒数。

    返り値:
        dict[str, Any]: 3状態の合計10,000bpとなる確率と寄与内訳。
    """
    fundamental_bps = _bounded_float(
        spec.get("fundamental_spread_mean_bps"),
        "probability.relative_value.fundamental_spread_mean_bps",
        -2_000.0,
        2_000.0,
    )
    supply_bps = _bounded_float(
        spec.get("supply_demand_spread_mean_bps"),
        "probability.relative_value.supply_demand_spread_mean_bps",
        -2_000.0,
        2_000.0,
    )
    event_bps = _bounded_float(
        spec.get("event_spread_mean_bps", 0.0),
        "probability.relative_value.event_spread_mean_bps",
        -2_000.0,
        2_000.0,
    )
    spread_vol_pct = _bounded_float(
        spec.get("spread_vol_pct"),
        "probability.relative_value.spread_vol_pct",
        0.0,
        100.0,
    )
    cost_pct = _bounded_float(
        spec.get("round_trip_cost_pct", 0.0),
        "probability.relative_value.round_trip_cost_pct",
        0.0,
        100.0,
    )
    if spread_vol_pct <= 0.0:
        raise InputValidationError("relative_value の相対σは正値、往復コストは0以上にしてください。")

    attributions_raw = _require_mapping(
        spec.get("attributions"),
        "probability.relative_value.attributions",
    )
    if set(attributions_raw) != {"fundamental", "supply_demand", "event"}:
        raise InputValidationError(
            "probability.relative_value.attributions はfundamental、supply_demand、eventを過不足なく指定してください。"
        )
    attributions = {
        "fundamental": _normalized_attributions(
            attributions_raw["fundamental"],
            "probability.relative_value.attributions.fundamental",
            "evidence_key",
        ),
        "supply_demand": _normalized_attributions(
            attributions_raw["supply_demand"],
            "probability.relative_value.attributions.supply_demand",
            "evidence_key",
        ),
        "event": _normalized_attributions(
            attributions_raw["event"],
            "probability.relative_value.attributions.event",
            "event_id",
        ),
    }
    declared_contributions = {
        "fundamental": fundamental_bps,
        "supply_demand": supply_bps,
        "event": event_bps,
    }
    for component, declared_value in declared_contributions.items():
        calculated_value = sum(row["contribution_bps"] for row in attributions[component])
        if not math.isclose(calculated_value, declared_value, rel_tol=0.0, abs_tol=1e-9):
            raise InputValidationError(
                f"probability.relative_value の{component}帰属合計を申告平均と一致させてください。"
            )
    combined_bps = fundamental_bps + supply_bps + event_bps
    if abs(combined_bps) > 3_000.0:
        raise InputValidationError("relative_value の統合相対ドリフトは絶対値3,000bp以内にしてください。")

    model_provenance = _validated_model_provenance(
        _require_mapping(
            spec.get("model_provenance"),
            "probability.relative_value.model_provenance",
        ),
        "probability.relative_value.model_provenance",
        as_of_jst,
        "forecast_valid_until_nt_spread_return",
        expected_horizon_seconds,
    )
    if any(abs(value) > 1e-12 for value in declared_contributions.values()) and not model_provenance[
        "validation_passed"
    ]:
        raise InputValidationError("非ゼロのNT相対ドリフトには検証合格済みモデル来歴が必要です。")
    spread_vol_source_links = _normalized_source_links(
        spec.get("spread_vol_source_links"),
        "probability.relative_value.spread_vol_source_links",
        as_of_jst,
    )

    mean_pct = combined_bps / 100.0
    long_probability = 1.0 - _normal_cdf((cost_pct - mean_pct) / spread_vol_pct)
    short_probability = _normal_cdf((-cost_pct - mean_pct) / spread_vol_pct)
    neutral_probability = max(0.0, 1.0 - long_probability - short_probability)
    probabilities = {
        "nt_long_after_cost": long_probability,
        "nt_short_after_cost": short_probability,
        "inside_cost_band": neutral_probability,
    }
    normalized_total = sum(probabilities.values())
    normalized = {key: value / normalized_total for key, value in probabilities.items()}
    scaled = {key: normalized[key] * TOTAL_PROBABILITY_BP for key in normalized}
    result_bp = {key: int(math.floor(value)) for key, value in scaled.items()}
    remainder = TOTAL_PROBABILITY_BP - sum(result_bp.values())
    order = sorted(
        normalized,
        key=lambda key: (scaled[key] - result_bp[key], key),
        reverse=True,
    )
    for key in order[:remainder]:
        result_bp[key] += 1
    return {
        "definition": "D=日経リターン-TOPIXリターン。NTロングはD>往復コスト、NTショートはD<-往復コスト。",
        "fundamental_spread_mean_bps": fundamental_bps,
        "supply_demand_spread_mean_bps": supply_bps,
        "event_spread_mean_bps": event_bps,
        "combined_spread_mean_bps": combined_bps,
        "spread_vol_pct": spread_vol_pct,
        "round_trip_cost_pct": cost_pct,
        "attributions": attributions,
        "model_provenance": model_provenance,
        "spread_vol_source_links": spread_vol_source_links,
        "analysis_gate_passed": model_provenance["validation_passed"],
        "probability_bp": result_bp,
    }


# ----------------------------------------


def calculate_probabilities(
    spec: Mapping[str, Any],
    as_of_jst: datetime,
    calibration_spec: Mapping[str, Any],
    expected_horizon_end_jst: datetime,
) -> dict[str, Any]:
    """
    機能:
        4象限の生モデル確率と50%超制約後の四択比較値を計算する。

    引数:
        spec (Mapping[str, Any]): 事前分布、証拠、同率時の優先象限。
        as_of_jst (datetime): 分析基準の日本時間。
        calibration_spec (Mapping[str, Any]): ウォークフォワード校正情報。
        expected_horizon_end_jst (datetime): 時刻計算で確定した4象限の評価期限。

    返り値:
        dict[str, Any]: 生確率、制約後比較値、証拠寄与、監査フラグ。
    """
    horizon_end = _parse_jst(spec.get("horizon_end_jst"), "probability.horizon_end_jst")
    if horizon_end != expected_horizon_end_jst:
        raise InputValidationError("probability.horizon_end_jst を予測の有効期限と完全に一致させてください。")
    model_version = spec.get("model_version")
    if not isinstance(model_version, str) or not model_version.strip():
        raise InputValidationError("probability.model_version は非空文字列にしてください。")
    model_version = model_version.strip()
    horizon_seconds = int((horizon_end - as_of_jst).total_seconds())
    base_spec = _require_mapping(spec.get("base"), "probability.base")
    base_method = _selected_base_method(base_spec)
    base_canonical = json.dumps(
        base_spec,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    calculated_base_prediction_output_sha256 = hashlib.sha256(
        base_canonical.encode("utf-8")
    ).hexdigest()
    base_provenance_input = _require_mapping(
        spec.get("base_provenance"),
        "probability.base_provenance",
    )
    if base_provenance_input.get("base_method") != base_method:
        raise InputValidationError(
            "probability.base_provenance.base_method を現在の probability.base と一致させてください。"
        )
    declared_base_prediction_output_sha256 = base_provenance_input.get("prediction_output_sha256")
    if (
        not isinstance(declared_base_prediction_output_sha256, str)
        or len(declared_base_prediction_output_sha256) != 64
        or any(character not in "0123456789abcdef" for character in declared_base_prediction_output_sha256)
    ):
        raise InputValidationError(
            "probability.base_provenance.prediction_output_sha256 は小文字16進64文字にしてください。"
        )
    if declared_base_prediction_output_sha256 != calculated_base_prediction_output_sha256:
        raise InputValidationError(
            "probability.base_provenance.prediction_output_sha256 を現在の probability.base から再計算してください。"
        )
    base_provenance = _validated_model_provenance(
        base_provenance_input,
        "probability.base_provenance",
        as_of_jst,
        "forecast_valid_until_midpoint_direction",
        horizon_seconds,
    )
    base_provenance["base_method"] = base_method
    base_provenance["prediction_output_sha256"] = calculated_base_prediction_output_sha256
    if base_provenance["model_version"] != model_version:
        raise InputValidationError(
            "probability.base_provenance.model_version を probability.model_version と一致させてください。"
        )
    base, base_method, base_scenario_audit = _base_probabilities(
        base_spec,
        as_of_jst,
        horizon_seconds,
        base_provenance,
    )
    calibration = _validate_calibration(
        calibration_spec,
        as_of_jst,
        model_version,
        horizon_seconds,
        base_method,
        base_provenance["model_id"],
        base_provenance["model_structure_id"],
        base_provenance["model_artifact_sha256"],
    )
    if calibration["status"] == "walk_forward":
        if base_provenance["validation_passed"] is not True:
            raise InputValidationError("walk_forward校正済み確率には検証合格済みbase_provenanceが必要です。")
        if base_provenance["calibration_version"] != calibration["calibration_version"]:
            raise InputValidationError(
                "probability.base_provenance.calibration_version を calibration.calibration_version と一致させてください。"
            )
        base_cutoff_date = _parse_jst(
            base_provenance["training_data_cutoff_jst"],
            "probability.base_provenance.training_data_cutoff_jst",
        ).date()
        if base_cutoff_date != date.fromisoformat(calibration["trained_through"]):
            raise InputValidationError(
                "probability.base_provenance の学習打切日を calibration.trained_through と一致させてください。"
            )
    evidence = _require_sequence(spec.get("evidence"), "probability.evidence")
    totals, evidence_audit = _aggregate_evidence(evidence, as_of_jst)
    block_only_probability_bp: dict[str, dict[str, int]] = {}
    for selected_block in EVIDENCE_BLOCKS:
        isolated_totals = {
            block: {
                key: totals[block][key] if block == selected_block else 0.0
                for key in CATEGORIES
            }
            for block in EVIDENCE_BLOCKS
        }
        block_only_probability_bp[selected_block] = _round_probabilities_to_bp(
            _softmax_evidence_update(base, isolated_totals)
        )
    raw = _softmax_evidence_update(base, totals)
    raw_bp = _round_probabilities_to_bp(raw)
    relative_value = _calculate_relative_value(
        _require_mapping(spec.get("relative_value"), "probability.relative_value"),
        as_of_jst,
        horizon_seconds,
    )

    maximum = max(raw.values())
    tied = [key for key in CATEGORIES if math.isclose(raw[key], maximum, rel_tol=0.0, abs_tol=1e-12)]
    requested_dominant = spec.get("dominant_category")
    if len(tied) > 1:
        if requested_dominant not in tied:
            raise InputValidationError(
                "生モデル確率が同率です。dominant_category に同率首位の象限を明示してください。"
            )
        dominant = str(requested_dominant)
    else:
        dominant = tied[0]
        if requested_dominant is not None and requested_dominant != dominant:
            raise InputValidationError("dominant_category を生モデル確率の一意な首位と異なる象限へ変更できません。")

    forced_bp, constraint_applied = _project_dominant_share(raw_bp, dominant)
    sorted_raw = sorted(raw.values(), reverse=True)
    l1_adjustment = sum(abs(forced_bp[key] - raw_bp[key]) for key in CATEGORIES)
    return {
        "horizon_end_jst": _format_jst(horizon_end),
        "probability_model_version": model_version,
        "category_labels": CATEGORY_LABELS,
        "base_method": base_method,
        "base_provenance": base_provenance,
        "base_scenario_audit": base_scenario_audit,
        "base_probability_bp": _round_probabilities_to_bp(base),
        "raw_model_probability_bp": raw_bp,
        "forced_decision_share_bp": forced_bp,
        "dominant_category": dominant,
        "dominant_label": CATEGORY_LABELS[dominant],
        "winner_tie": len(tied) > 1,
        "raw_winner_probability_bp": raw_bp[dominant],
        "raw_winner_margin_bp": int(round((sorted_raw[0] - sorted_raw[1]) * TOTAL_PROBABILITY_BP)),
        "constraint_applied": constraint_applied,
        "dominance_floor_bp": DOMINANCE_FLOOR_BP,
        "dominance_adjustment_l1_bp": l1_adjustment,
        "evidence_block_weights": EVIDENCE_WEIGHTS,
        "evidence_temperature": EVIDENCE_TEMPERATURE,
        "evidence_block_only_probability_bp": block_only_probability_bp,
        "evidence_totals": {
            block: {key: round(totals[block][key], 6) for key in CATEGORIES}
            for block in EVIDENCE_BLOCKS
        },
        "evidence_audit": evidence_audit,
        "calibration": calibration,
        "relative_value": relative_value,
    }


# ----------------------------------------


def calculate_timing(
    spec: Mapping[str, Any],
    as_of_jst: datetime,
    search_end_jst: datetime | None = None,
    session_context: Mapping[str, Any] | None = None,
    execution_anchor_valid_until_jst: datetime | None = None,
) -> dict[str, Any]:
    """
    機能:
        板の入口期限、予測有効期限、次の材料変動候補窓を相互に分離して計算する。

    引数:
        spec (Mapping[str, Any]): 板時刻、TTL、セッション終了、再推定時刻、イベント候補。
        as_of_jst (datetime): 分析基準の日本時間。
        search_end_jst (datetime | None): 次の変動候補を探す上限。通常はD5終了時刻。
        session_context (Mapping[str, Any] | None): 検証済みの先物セッション情報。
        execution_anchor_valid_until_jst (datetime | None): Kabus板sourceの絶対鮮度期限。

    返り値:
        dict[str, Any]: 3種類の時刻、採用イベント、保留状態。
    """
    board_snapshot = _parse_jst(spec.get("board_snapshot_jst"), "timing.board_snapshot_jst")
    if board_snapshot > as_of_jst:
        raise InputValidationError("timing.board_snapshot_jst が分析時刻より未来です。")
    if session_context is None:
        session_context = validate_session_context(spec, as_of_jst)
    session_start = _parse_jst(session_context["session_start_jst"], "timing.session_start_jst")
    continuous_end = _parse_jst(session_context["continuous_end_jst"], "timing.continuous_end_jst")
    session_end = _parse_jst(session_context["session_end_jst"], "timing.session_end_jst")
    if not session_start <= board_snapshot < continuous_end:
        raise InputValidationError("timing.board_snapshot_jst を現在先物セッションのザラバ時間内にしてください。")
    if not session_context["entry_phase_valid"]:
        raise InputValidationError("プレクロージング中の板を通常の新規入口アンカーにできません。")
    board_ttl_minutes = _finite_float(spec.get("board_ttl_minutes", 30), "timing.board_ttl_minutes")
    if board_ttl_minutes <= 0.0 or board_ttl_minutes > 30.0:
        raise InputValidationError("timing.board_ttl_minutes は0超30分以下にしてください。")
    if session_end <= board_snapshot:
        raise InputValidationError("timing.session_end_jst は板スナップショットより後にしてください。")
    provider_expires_raw = spec.get("provider_expires_jst")
    provider_expires: datetime | None = None
    if provider_expires_raw is not None:
        provider_expires = _parse_jst(provider_expires_raw, "timing.provider_expires_jst")
        if provider_expires <= board_snapshot:
            raise InputValidationError("timing.provider_expires_jst は板スナップショットより後にしてください。")
    model_refresh = _parse_jst(spec.get("model_refresh_jst"), "timing.model_refresh_jst")
    if model_refresh <= as_of_jst:
        raise InputValidationError("timing.model_refresh_jst は分析時刻より後にしてください。")
    board_deadlines = [board_snapshot + timedelta(minutes=board_ttl_minutes), continuous_end]
    if provider_expires is not None:
        board_deadlines.append(provider_expires)
    if execution_anchor_valid_until_jst is not None:
        board_deadlines.append(execution_anchor_valid_until_jst)
    board_valid_until = min(board_deadlines)

    threshold = _bounded_float(spec.get("materiality_threshold", 0.35), "timing.materiality_threshold", 0.0, 1.0)
    if not math.isclose(threshold, 0.35, rel_tol=0.0, abs_tol=1e-12):
        raise InputValidationError("timing.materiality_threshold は固定値0.35にしてください。")
    events_raw = _require_sequence(spec.get("events", []), "timing.events")
    event_rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw_event in enumerate(events_raw):
        event = _require_mapping(raw_event, f"timing.events[{index}]")
        event_id_raw = event.get("event_id")
        title = event.get("title")
        if not isinstance(event_id_raw, str) or not event_id_raw.strip():
            raise InputValidationError("timing.events の event_id は重複しない非空文字列にしてください。")
        event_id = event_id_raw.strip()
        event_deduplication_key = event_id.casefold()
        if event_deduplication_key in seen_ids:
            raise InputValidationError("timing.events の event_id は正規化後も重複させないでください。")
        if not isinstance(title, str) or not title.strip():
            raise InputValidationError(f"イベント '{event_id}' の title は非空文字列にしてください。")
        seen_ids.add(event_deduplication_key)
        coverage_item = event.get("coverage_item")
        if coverage_item not in (*COVERAGE_ITEMS, "other"):
            raise InputValidationError(f"イベント '{event_id}' の coverage_item が不正です。")
        event_text: dict[str, str] = {}
        for field_name in (
            "source_id",
            "source_url_or_document_id",
            "source_timezone",
            "reference_period",
            "scheduled_at_source",
            "previous_value",
            "official_source",
        ):
            field_value = event.get(field_name)
            if not isinstance(field_value, str) or not field_value.strip():
                raise InputValidationError(f"イベント '{event_id}' の {field_name} は非空文字列にしてください。")
            event_text[field_name] = field_value.strip()
        checked_at = _parse_jst(event.get("checked_at_jst"), f"イベント '{event_id}' の checked_at_jst")
        if checked_at > as_of_jst:
            raise InputValidationError(f"イベント '{event_id}' の確認時刻が分析時刻より未来です。")
        if (as_of_jst - checked_at).total_seconds() > 24 * 3600:
            raise InputValidationError(f"イベント '{event_id}' の予定確認が24時間より古いです。")
        release_status = event.get("release_status")
        if release_status not in ("scheduled", "preliminary", "revised", "conditional", "market_session"):
            raise InputValidationError(f"イベント '{event_id}' の release_status が不正です。")
        consensus_status = event.get("consensus_status")
        if consensus_status not in ("used", "unavailable", "not_applicable"):
            raise InputValidationError(f"イベント '{event_id}' の consensus_status が不正です。")
        consensus_source = None
        consensus_checked_at = None
        if consensus_status == "used":
            consensus_source_raw = event.get("consensus_source")
            if not isinstance(consensus_source_raw, str) or not consensus_source_raw.strip():
                raise InputValidationError(f"イベント '{event_id}' の consensus_source を指定してください。")
            consensus_source = consensus_source_raw.strip()
            consensus_checked_at = _parse_jst(
                event.get("consensus_checked_at_jst"),
                f"イベント '{event_id}' の consensus_checked_at_jst",
            )
            if consensus_checked_at > as_of_jst:
                raise InputValidationError(f"イベント '{event_id}' のコンセンサス取得時刻が未来です。")
            if (as_of_jst - consensus_checked_at).total_seconds() > COVERAGE_CHECK_MAX_AGE_HOURS * 3600:
                raise InputValidationError(f"イベント '{event_id}' のコンセンサス取得が24時間より古いです。")
        start = _parse_jst(event.get("window_start_jst"), f"イベント '{event_id}' の window_start_jst")
        end = _parse_jst(event.get("window_end_jst"), f"イベント '{event_id}' の window_end_jst")
        if end < start:
            raise InputValidationError(f"イベント '{event_id}' の終了時刻が開始時刻より前です。")
        scheduled_at_jst = _parse_jst(
            event.get("scheduled_at_jst"),
            f"イベント '{event_id}' の scheduled_at_jst",
        )
        scheduled_at_source = _parse_aware_datetime(
            event_text["scheduled_at_source"],
            f"イベント '{event_id}' の scheduled_at_source",
        )
        if not _source_timezone_offset_is_valid(scheduled_at_source, event_text["source_timezone"]):
            raise InputValidationError(
                f"イベント '{event_id}' の scheduled_at_source offset が source_timezone と一致しません。"
            )
        if scheduled_at_source.astimezone(JST) != scheduled_at_jst:
            raise InputValidationError(
                f"イベント '{event_id}' の scheduled_at_source と scheduled_at_jst が同じ瞬間ではありません。"
            )
        if not start <= scheduled_at_jst <= end:
            raise InputValidationError(f"イベント '{event_id}' の scheduled_at_jst を候補窓内にしてください。")
        buffer_minutes = _bounded_float(
            event.get("safety_buffer_minutes", 5), f"イベント '{event_id}' の safety_buffer_minutes", 0.0, 1_440.0
        )
        occurrence = _bounded_float(
            event.get("occurrence_probability", 1.0), f"イベント '{event_id}' の occurrence_probability", 0.0, 1.0
        )
        occurrence_basis_type = "fixed_schedule"
        occurrence_audit = {
            "resolution_verified": None,
            "expiry_aligned": None,
            "liquidity_verified": None,
            "spread_verified": None,
        }
        if release_status in ("scheduled", "preliminary", "revised", "market_session"):
            if not math.isclose(occurrence, 1.0, rel_tol=0.0, abs_tol=1e-12):
                raise InputValidationError(
                    f"イベント '{event_id}' の release_status={release_status} では occurrence_probability=1 が必要です。"
                )
            explicit_basis = event.get("occurrence_basis_type")
            if explicit_basis not in (None, "fixed_schedule"):
                raise InputValidationError(
                    f"イベント '{event_id}' の予定済み発表は occurrence_basis_type=fixed_schedule にしてください。"
                )
        else:
            occurrence_basis_raw = event.get("occurrence_basis_type")
            if occurrence_basis_raw not in ("official_condition", "verified_prediction_market"):
                raise InputValidationError(
                    f"条件付きイベント '{event_id}' は occurrence_basis_type を official_condition または verified_prediction_market にしてください。"
                )
            occurrence_basis_type = occurrence_basis_raw
            if occurrence_basis_type == "official_condition":
                if coverage_item == "polymarket":
                    raise InputValidationError(
                        f"条件付きイベント '{event_id}' のPolymarket価格は verified_prediction_market としてください。"
                    )
                if occurrence not in (0.0, 1.0):
                    raise InputValidationError(
                        f"公式条件だけを根拠にするイベント '{event_id}' の occurrence_probability は0または1にしてください。"
                    )
            else:
                if coverage_item != "polymarket":
                    raise InputValidationError(
                        f"条件付きイベント '{event_id}' の予測市場確率は coverage_item=polymarket にしてください。"
                    )
                for flag_name in occurrence_audit:
                    if event.get(flag_name) is not True:
                        raise InputValidationError(
                            f"条件付きイベント '{event_id}' の {flag_name}=true が必要です。"
                        )
                    occurrence_audit[flag_name] = True
        impact = _bounded_float(event.get("impact_score"), f"イベント '{event_id}' の impact_score", 0.0, 5.0)
        relevance = _bounded_float(event.get("relevance", 1.0), f"イベント '{event_id}' の relevance", 0.0, 1.0)
        source_quality = _bounded_float(
            event.get("source_quality", 1.0), f"イベント '{event_id}' の source_quality", 0.0, 1.0
        )
        materiality = occurrence * (impact / 5.0) * relevance * source_quality
        event_rows.append(
            {
                "event_id": event_id,
                "title": title.strip(),
                "coverage_item": coverage_item,
                **{key: value for key, value in event_text.items() if key != "scheduled_at_source"},
                "scheduled_at_source_jst": _format_jst(scheduled_at_source),
                "source_utc_offset": _format_utc_offset(scheduled_at_source),
                "checked_at_jst": _format_jst(checked_at),
                "scheduled_at_jst": _format_jst(scheduled_at_jst),
                "release_status": release_status,
                "consensus_status": consensus_status,
                "consensus_source": consensus_source,
                "consensus_checked_at_jst": (
                    _format_jst(consensus_checked_at) if consensus_checked_at is not None else None
                ),
                "window_start": start,
                "window_end": end,
                "safety_buffer_minutes": buffer_minutes,
                "occurrence_probability": occurrence,
                "occurrence_basis_type": occurrence_basis_type,
                **occurrence_audit,
                "impact_score": impact,
                "relevance": relevance,
                "source_quality": source_quality,
                "materiality_score": materiality,
                "is_material": materiality >= threshold,
            }
        )

    material_events = [
        row
        for row in event_rows
        if row["is_material"]
        and row["window_end"] >= as_of_jst
        and (search_end_jst is None or row["window_start"] <= search_end_jst)
    ]
    material_events.sort(key=lambda row: (row["window_start"], -row["materiality_score"], row["event_id"]))
    next_event = material_events[0] if material_events else None
    forecast_valid_until = min(model_refresh, continuous_end)
    board_entry_status = "valid" if board_valid_until > as_of_jst else "expired"
    hold_reasons: list[str] = []
    if board_entry_status == "expired":
        hold_reasons.append("入口板の有効期限切れ")
    deadline_reasons = ["モデル再推定時刻"] if model_refresh <= continuous_end else ["現在先物セッションのザラバ終了"]
    if search_end_jst is not None and search_end_jst < forecast_valid_until:
        forecast_valid_until = search_end_jst
        deadline_reasons = ["D5イベント探索上限"]
    if material_events:
        deadline_event = min(
            material_events,
            key=lambda row: (
                row["window_start"] - timedelta(minutes=row["safety_buffer_minutes"]),
                row["event_id"],
            ),
        )
        event_deadline = deadline_event["window_start"] - timedelta(
            minutes=deadline_event["safety_buffer_minutes"]
        )
        if event_deadline <= as_of_jst:
            forecast_valid_until = as_of_jst
            hold_reasons.append("重要イベントの安全余裕内")
            deadline_reasons = [f"{deadline_event['title']}の安全余裕内"]
        elif event_deadline < forecast_valid_until:
            forecast_valid_until = event_deadline
            deadline_reasons = [f"{deadline_event['title']}の安全余裕前"]

    serialized_events = []
    for row in sorted(event_rows, key=lambda item: (item["window_start"], item["event_id"])):
        serialized_events.append(
            {
                **{key: value for key, value in row.items() if key not in ("window_start", "window_end")},
                "window_start_jst": _format_jst(row["window_start"]),
                "window_end_jst": _format_jst(row["window_end"]),
                "materiality_score": round(row["materiality_score"], 6),
                "inside_search_horizon": search_end_jst is None or row["window_start"] <= search_end_jst,
            }
        )
    next_window = None
    if next_event is not None:
        next_window = {
            "event_id": next_event["event_id"],
            "title": next_event["title"],
            "coverage_item": next_event["coverage_item"],
            "source_id": next_event["source_id"],
            "source_url_or_document_id": next_event["source_url_or_document_id"],
            "checked_at_jst": next_event["checked_at_jst"],
            "scheduled_at_source_jst": next_event["scheduled_at_source_jst"],
            "source_utc_offset": next_event["source_utc_offset"],
            "scheduled_at_jst": next_event["scheduled_at_jst"],
            "from_jst": _format_jst(next_event["window_start"]),
            "to_jst": _format_jst(next_event["window_end"]),
            "materiality_score": round(next_event["materiality_score"], 6),
            "definition": "確認済み候補のうち最も早い重要イベント窓",
        }

    return {
        "status": "hold" if hold_reasons else "ok",
        "hold_reasons": hold_reasons,
        "cash_market_state": session_context["cash_market_state"],
        "futures_session_kind": session_context["futures_session_kind"],
        "futures_session_phase": session_context["futures_session_phase"],
        "exchange_trading_day": session_context["exchange_trading_day"],
        "holiday_division": session_context["holiday_division"],
        "cash_market_holiday_division": session_context["cash_market_holiday_division"],
        "is_derivatives_holiday_trading": session_context["is_derivatives_holiday_trading"],
        "session_calendar": session_context["session_calendar"],
        "session_start_jst": session_context["session_start_jst"],
        "continuous_end_jst": session_context["continuous_end_jst"],
        "session_end_jst": session_context["session_end_jst"],
        "next_session_start_jst": session_context["next_session_start_jst"],
        "session_schedule_source_id": session_context["session_schedule_source_id"],
        "session_schedule_source_url": session_context["session_schedule_source_url"],
        "session_checked_at_jst": session_context["session_checked_at_jst"],
        "board_entry_status": board_entry_status,
        "board_entry_valid_until_jst": _format_jst(board_valid_until),
        "execution_anchor_valid_until_jst": (
            _format_jst(execution_anchor_valid_until_jst)
            if execution_anchor_valid_until_jst is not None
            else None
        ),
        "provider_expires_jst": _format_jst(provider_expires) if provider_expires is not None else None,
        "forecast_valid_until_jst": _format_jst(forecast_valid_until),
        "model_refresh_jst": _format_jst(model_refresh),
        "deadline_reasons": deadline_reasons,
        "next_material_move_window": next_window,
        "event_search_end_jst": _format_jst(search_end_jst) if search_end_jst is not None else None,
        "materiality_threshold": threshold,
        "events": serialized_events,
    }


# ----------------------------------------


def _position_prices(nt_spec: Mapping[str, Any], strategy: str) -> tuple[Decimal, Decimal]:
    """
    機能:
        NTロングまたはNTショートの新規建てに使用する各脚の執行側価格を選ぶ。

    引数:
        nt_spec (Mapping[str, Any]): 両指数の買い気配・売り気配。
        strategy (str): nt_long または nt_short。

    返り値:
        tuple[Decimal, Decimal]: 日経脚価格とTOPIX脚価格。
    """
    if strategy == "nt_long":
        return _positive_decimal(nt_spec["nikkei_ask"], "nt.nikkei_ask"), _positive_decimal(
            nt_spec["topix_bid"], "nt.topix_bid"
        )
    if strategy == "nt_short":
        return _positive_decimal(nt_spec["nikkei_bid"], "nt.nikkei_bid"), _positive_decimal(
            nt_spec["topix_ask"], "nt.topix_ask"
        )
    raise InputValidationError("NT戦略は nt_long または nt_short にしてください。")


# ----------------------------------------


def _find_hedge_pair(
    nikkei_price: Decimal,
    topix_price: Decimal,
    nikkei_product: str,
    max_topix_quantity: int = 50,
) -> dict[str, Any]:
    """
    機能:
        TOPIX miniと日経miniまたはmicroの名目差が小さい最小整数構成を探索する。

    引数:
        nikkei_price (Decimal): 日経脚の執行側価格。
        topix_price (Decimal): TOPIX脚の執行側価格。
        nikkei_product (str): mini または micro。
        max_topix_quantity (int): 探索するTOPIX miniの最大枚数。

    返り値:
        dict[str, Any]: 採用枚数、各脚名目、名目差率、許容帯。
    """
    multiplier = NIKKEI_MULTIPLIERS[nikkei_product]
    candidates: list[dict[str, Any]] = []
    for topix_quantity in range(1, max_topix_quantity + 1):
        exact_nikkei = (
            Decimal(topix_quantity) * TOPIX_MINI_MULTIPLIER * topix_price / (multiplier * nikkei_price)
        )
        quantity_methods: dict[int, list[str]] = {}
        for method_name, rounding_mode in (
            ("floor", ROUND_FLOOR),
            ("round_half_up", ROUND_HALF_UP),
            ("ceil", ROUND_CEILING),
        ):
            nikkei_quantity = int(exact_nikkei.to_integral_value(rounding=rounding_mode))
            if nikkei_quantity < 1:
                continue
            quantity_methods.setdefault(nikkei_quantity, []).append(method_name)
        for nikkei_quantity, rounding_methods in quantity_methods.items():
            nikkei_notional = Decimal(nikkei_quantity) * multiplier * nikkei_price
            topix_notional = Decimal(topix_quantity) * TOPIX_MINI_MULTIPLIER * topix_price
            average = (nikkei_notional + topix_notional) / Decimal("2")
            mismatch = abs(nikkei_notional - topix_notional) / average
            candidates.append(
                {
                    "nikkei_quantity": nikkei_quantity,
                    "topix_quantity": topix_quantity,
                    "exact_nikkei_quantity": exact_nikkei,
                    "rounding_methods": rounding_methods,
                    "nikkei_notional": nikkei_notional,
                    "topix_notional": topix_notional,
                    "mismatch": mismatch,
                    "gross": nikkei_notional + topix_notional,
                }
            )
    if not candidates:
        raise InputValidationError(f"{nikkei_product} の整数ヘッジ候補を作れません。")

    within_three = [candidate for candidate in candidates if candidate["mismatch"] <= Decimal("0.03")]
    within_five = [candidate for candidate in candidates if candidate["mismatch"] <= Decimal("0.05")]
    pool = within_three or within_five or candidates
    selected = min(pool, key=lambda candidate: (candidate["gross"], candidate["mismatch"], candidate["topix_quantity"]))
    tolerance = "3%以内" if within_three else "5%以内" if within_five else "5%超・理論例のみ"
    selected_topix_candidates = sorted(
        (candidate for candidate in candidates if candidate["topix_quantity"] == selected["topix_quantity"]),
        key=lambda candidate: candidate["nikkei_quantity"],
    )
    return {
        "nikkei_product": nikkei_product,
        "nikkei_quantity": selected["nikkei_quantity"],
        "topix_mini_quantity": selected["topix_quantity"],
        "exact_nikkei_quantity": _decimal_string(selected["exact_nikkei_quantity"], 6),
        "rounding_methods": selected["rounding_methods"],
        "nikkei_notional_yen": _decimal_string(selected["nikkei_notional"], 0),
        "topix_notional_yen": _decimal_string(selected["topix_notional"], 0),
        "gross_notional_yen": _decimal_string(selected["gross"], 0),
        "notional_mismatch_pct": _decimal_string(selected["mismatch"] * Decimal("100"), 4),
        "tolerance_result": tolerance,
        "rounding_candidates_for_selected_topix": [
            {
                "nikkei_quantity": candidate["nikkei_quantity"],
                "rounding_methods": candidate["rounding_methods"],
                "notional_mismatch_pct": _decimal_string(candidate["mismatch"] * Decimal("100"), 4),
            }
            for candidate in selected_topix_candidates
        ],
    }


# ----------------------------------------


def _marginal_probability_bp(probability_bp: Mapping[str, int], leg: str, direction: str) -> int:
    """
    機能:
        4象限分布から日経またはTOPIXの上昇・下落限界確率を計算する。

    引数:
        probability_bp (Mapping[str, int]): 4象限の整数ベーシスポイント。
        leg (str): nikkei または topix。
        direction (str): buy または sell。

    返り値:
        int: 指定方向が有利となる確率ベーシスポイント。
    """
    if leg == "nikkei":
        up = probability_bp["nk_up_topix_up"] + probability_bp["nk_up_topix_down"]
    elif leg == "topix":
        up = probability_bp["nk_up_topix_up"] + probability_bp["nk_down_topix_up"]
    else:
        raise InputValidationError("残余脚は nikkei または topix にしてください。")
    if direction == "buy":
        return up
    if direction == "sell":
        return TOTAL_PROBABILITY_BP - up
    raise InputValidationError("残余方向は buy または sell にしてください。")


# ----------------------------------------


def calculate_nt(
    spec: Mapping[str, Any],
    probability_result: Mapping[str, Any],
    as_of_jst: datetime,
) -> dict[str, Any]:
    """
    機能:
        NT倍率、TOPIX mini 1枚の等価枚数、整数構成、端数残存方向を計算する。

    引数:
        spec (Mapping[str, Any]): 両脚の板、戦略、任意の採用枚数。
        probability_result (Mapping[str, Any]): 端数方向評価に使う4象限分布。
        as_of_jst (datetime): 板時刻と取引最終日の検証基準。

    返り値:
        dict[str, Any]: 中値換算、執行側換算、候補枚数、残余監査。
    """
    identifiers: dict[str, str] = {}
    for field_name in (
        "nikkei_symbol",
        "topix_symbol",
        "nikkei_board_source_id",
        "topix_board_source_id",
    ):
        field_value = spec.get(field_name)
        if not isinstance(field_value, str) or not field_value.strip():
            raise InputValidationError(f"nt.{field_name} は非空文字列にしてください。")
        identifiers[field_name] = field_value.strip()
    nikkei_board_product = spec.get("nikkei_board_product")
    if nikkei_board_product not in NIKKEI_MULTIPLIERS:
        raise InputValidationError("nt.nikkei_board_product は mini または micro にしてください。")
    nikkei_contract = spec.get("nikkei_contract_month")
    topix_contract = spec.get("topix_contract_month")
    contract_months: dict[str, date] = {}
    for field_name, field_value in (
        ("nikkei_contract_month", nikkei_contract),
        ("topix_contract_month", topix_contract),
    ):
        if not isinstance(field_value, str):
            raise InputValidationError(f"nt.{field_name} はYYYY-MM文字列にしてください。")
        try:
            parsed_month = date.fromisoformat(f"{field_value}-01")
        except ValueError as exc:
            raise InputValidationError(f"nt.{field_name} はYYYY-MM文字列にしてください。") from exc
        if parsed_month.month not in (3, 6, 9, 12):
            raise InputValidationError("TOPIX miniとのNT比較は共通する四半期限月を指定してください。")
        contract_months[field_name] = parsed_month
    if nikkei_contract != topix_contract:
        raise InputValidationError("日経とTOPIXは同一四半期限月にしてください。")

    last_trading_dates: dict[str, date] = {}
    for field_name in ("nikkei_last_trading_day", "topix_last_trading_day"):
        field_value = spec.get(field_name)
        if not isinstance(field_value, str):
            raise InputValidationError(f"nt.{field_name} はYYYY-MM-DD文字列にしてください。")
        try:
            parsed_date = date.fromisoformat(field_value)
        except ValueError as exc:
            raise InputValidationError(f"nt.{field_name} はYYYY-MM-DD文字列にしてください。") from exc
        if parsed_date < as_of_jst.date():
            raise InputValidationError(f"nt.{field_name} は分析日以後にしてください。")
        last_trading_dates[field_name] = parsed_date
    if last_trading_dates["nikkei_last_trading_day"] != last_trading_dates["topix_last_trading_day"]:
        raise InputValidationError("共通限月の取引最終日が両脚で一致しません。公式仕様を再確認してください。")
    contract_month_date = contract_months["nikkei_contract_month"]
    if (
        last_trading_dates["nikkei_last_trading_day"].year != contract_month_date.year
        or last_trading_dates["nikkei_last_trading_day"].month != contract_month_date.month
    ):
        raise InputValidationError("取引最終日の年月を指定した限月と一致させてください。")
    days_until_first_friday = (4 - contract_month_date.weekday()) % 7
    second_friday = contract_month_date + timedelta(days=days_until_first_friday + 7)
    common_last_trading_day = last_trading_dates["nikkei_last_trading_day"]
    days_before_nominal_sq = (second_friday - common_last_trading_day).days
    if common_last_trading_day.weekday() >= 5 or not 1 <= days_before_nominal_sq <= 7:
        raise InputValidationError(
            "取引最終日は当該四半期限月の名目第2金曜日より1〜7日前の平日にしてください。公式休業日も再確認してください。"
        )
    last_trading_session_end = _parse_jst(
        spec.get("last_trading_session_end_jst"),
        "nt.last_trading_session_end_jst",
    )
    if last_trading_session_end.date() != common_last_trading_day:
        raise InputValidationError("nt.last_trading_session_end_jst の日付を共通取引最終日と一致させてください。")
    if (
        last_trading_session_end.hour,
        last_trading_session_end.minute,
        last_trading_session_end.second,
        last_trading_session_end.microsecond,
    ) != (15, 45, 0, 0):
        raise InputValidationError(
            "nt.last_trading_session_end_jst は現行JPX仕様の日中立会終了15:45 JSTと一致させてください。"
        )
    if last_trading_session_end <= as_of_jst:
        raise InputValidationError("共通限月の最終取引可能時刻を経過しています。次限月の板を取得してください。")

    nikkei_snapshot = _parse_jst(spec.get("nikkei_snapshot_jst"), "nt.nikkei_snapshot_jst")
    topix_snapshot = _parse_jst(spec.get("topix_snapshot_jst"), "nt.topix_snapshot_jst")
    if nikkei_snapshot > as_of_jst or topix_snapshot > as_of_jst:
        raise InputValidationError("nt の板スナップショットが分析時刻より未来です。")
    max_skew_seconds = _bounded_float(
        spec.get("max_snapshot_skew_seconds", 15),
        "nt.max_snapshot_skew_seconds",
        0.0,
        60.0,
    )
    snapshot_skew_seconds = abs((nikkei_snapshot - topix_snapshot).total_seconds())
    if snapshot_skew_seconds > max_skew_seconds:
        raise InputValidationError("日経とTOPIXの板時刻差が許容値を超えています。")

    board_quantities = {
        "nikkei_bid_quantity": _bounded_integer(
            spec.get("nikkei_bid_quantity"), "nt.nikkei_bid_quantity", 1, 10_000_000
        ),
        "nikkei_ask_quantity": _bounded_integer(
            spec.get("nikkei_ask_quantity"), "nt.nikkei_ask_quantity", 1, 10_000_000
        ),
        "topix_bid_quantity": _bounded_integer(
            spec.get("topix_bid_quantity"), "nt.topix_bid_quantity", 1, 10_000_000
        ),
        "topix_ask_quantity": _bounded_integer(
            spec.get("topix_ask_quantity"), "nt.topix_ask_quantity", 1, 10_000_000
        ),
    }

    nikkei_bid = _positive_decimal(spec.get("nikkei_bid"), "nt.nikkei_bid")
    nikkei_ask = _positive_decimal(spec.get("nikkei_ask"), "nt.nikkei_ask")
    topix_bid = _positive_decimal(spec.get("topix_bid"), "nt.topix_bid")
    topix_ask = _positive_decimal(spec.get("topix_ask"), "nt.topix_ask")
    if nikkei_ask < nikkei_bid or topix_ask < topix_bid:
        raise InputValidationError("nt の売り気配は買い気配以上でなければなりません。")
    nikkei_mid = (nikkei_bid + nikkei_ask) / Decimal("2")
    topix_mid = (topix_bid + topix_ask) / Decimal("2")
    observed_round_trip_cost_pct = (
        (nikkei_ask - nikkei_bid) / nikkei_mid
        + (topix_ask - topix_bid) / topix_mid
    ) * Decimal("100")
    declared_round_trip_cost_pct = Decimal(
        str(probability_result["relative_value"]["round_trip_cost_pct"])
    )
    if declared_round_trip_cost_pct < observed_round_trip_cost_pct:
        raise InputValidationError(
            "probability.relative_value.round_trip_cost_pct が両脚の観測スプレッドから得る最低往復コスト未満です。"
        )
    nt_mid = nikkei_mid / topix_mid
    mini_equivalent = Decimal("10") / nt_mid
    micro_equivalent = Decimal("100") / nt_mid

    strategy = spec.get("strategy")
    if strategy not in (None, "nt_long", "nt_short"):
        raise InputValidationError("nt.strategy は nt_long、nt_short、または未指定にしてください。")
    if strategy is None:
        execution_nikkei = nikkei_mid
        execution_topix = topix_mid
        execution_label = "中値"
    else:
        execution_nikkei, execution_topix = _position_prices(spec, strategy)
        execution_label = "NTロング新規" if strategy == "nt_long" else "NTショート新規"
    execution_nt = execution_nikkei / execution_topix
    execution_mini_equivalent = Decimal("10") / execution_nt
    execution_micro_equivalent = Decimal("100") / execution_nt

    result: dict[str, Any] = {
        **identifiers,
        "nikkei_board_product": nikkei_board_product,
        "contract_month": str(nikkei_contract),
        "last_trading_day": last_trading_dates["nikkei_last_trading_day"].isoformat(),
        "last_trading_session_end_jst": _format_jst(last_trading_session_end),
        "nikkei_snapshot_jst": _format_jst(nikkei_snapshot),
        "topix_snapshot_jst": _format_jst(topix_snapshot),
        "oldest_snapshot_jst": _format_jst(min(nikkei_snapshot, topix_snapshot)),
        "snapshot_skew_seconds": snapshot_skew_seconds,
        "max_snapshot_skew_seconds": max_skew_seconds,
        "board_quantities": board_quantities,
        "nikkei_mid": _decimal_string(nikkei_mid),
        "topix_mid": _decimal_string(topix_mid),
        "observed_minimum_round_trip_cost_pct": _decimal_string(observed_round_trip_cost_pct, 6),
        "declared_round_trip_cost_pct": _decimal_string(declared_round_trip_cost_pct, 6),
        "nt_mid": _decimal_string(nt_mid, 6),
        "topix_mini_1_to_nikkei_mini_exact": _decimal_string(mini_equivalent, 6),
        "topix_mini_1_to_nikkei_mini_1dp": _decimal_string(mini_equivalent, 1),
        "topix_mini_1_to_nikkei_micro_exact": _decimal_string(micro_equivalent, 6),
        "topix_mini_1_to_nikkei_micro_1dp": _decimal_string(micro_equivalent, 1),
        "execution_basis": execution_label,
        "execution_nt_ratio": _decimal_string(execution_nt, 6),
        "execution_topix_mini_1_to_nikkei_mini": _decimal_string(execution_mini_equivalent, 6),
        "execution_topix_mini_1_to_nikkei_micro": _decimal_string(execution_micro_equivalent, 6),
        "smallest_hedge_candidates": {},
        "selected_position": None,
    }
    for candidate_product in ("mini", "micro"):
        candidate = _find_hedge_pair(execution_nikkei, execution_topix, candidate_product)
        candidate["execution_board_verified"] = candidate_product == nikkei_board_product
        candidate["board_note"] = (
            "指定商品の実板で計算"
            if candidate_product == nikkei_board_product
            else "同じ指数水準による理論換算。採用には当該商品の実板再取得が必要"
        )
        result["smallest_hedge_candidates"][candidate_product] = candidate

    selected_raw = spec.get("selected_position")
    if selected_raw is None:
        if strategy is not None:
            raise InputValidationError("nt.strategy を指定する場合は selected_position と端数監査が必要です。")
        return result
    if strategy is None:
        raise InputValidationError("selected_position を指定する場合は nt.strategy が必要です。")
    selected = _require_mapping(selected_raw, "nt.selected_position")
    product = selected.get("nikkei_product")
    if product not in NIKKEI_MULTIPLIERS:
        raise InputValidationError("selected_position.nikkei_product は mini または micro にしてください。")
    if product != nikkei_board_product:
        raise InputValidationError("selected_position.nikkei_product を取得済みの日経板商品と一致させてください。")
    nikkei_quantity = _bounded_integer(
        selected.get("nikkei_quantity"), "selected_position.nikkei_quantity", 1, 1_000_000
    )
    topix_quantity = _bounded_integer(
        selected.get("topix_mini_quantity"), "selected_position.topix_mini_quantity", 1, 1_000_000
    )

    nikkei_multiplier = NIKKEI_MULTIPLIERS[product]
    nikkei_notional = Decimal(nikkei_quantity) * nikkei_multiplier * execution_nikkei
    topix_notional = Decimal(topix_quantity) * TOPIX_MINI_MULTIPLIER * execution_topix
    sign_nikkei = Decimal("1") if strategy == "nt_long" else Decimal("-1")
    sign_topix = -sign_nikkei
    signed_residual = sign_nikkei * nikkei_notional + sign_topix * topix_notional
    average_notional = (nikkei_notional + topix_notional) / Decimal("2")
    mismatch_pct = abs(nikkei_notional - topix_notional) / average_notional * Decimal("100")
    exact_nikkei_quantity = Decimal(topix_quantity) * TOPIX_MINI_MULTIPLIER * execution_topix / (
        nikkei_multiplier * execution_nikkei
    )
    fractional_difference = Decimal(nikkei_quantity) - exact_nikkei_quantity
    if strategy == "nt_long":
        available_nikkei_quantity = board_quantities["nikkei_ask_quantity"]
        available_topix_quantity = board_quantities["topix_bid_quantity"]
    else:
        available_nikkei_quantity = board_quantities["nikkei_bid_quantity"]
        available_topix_quantity = board_quantities["topix_ask_quantity"]
    liquidity_sufficient = (
        available_nikkei_quantity >= nikkei_quantity and available_topix_quantity >= topix_quantity
    )
    passes_preferred_notional_gate = mismatch_pct <= Decimal("3")
    passes_notional_gate = mismatch_pct <= Decimal("5")

    if signed_residual == 0:
        residual_leg = "none"
        residual_direction = "neutral"
        residual_label = "端数残存なし"
        raw_favorable = None
        forced_favorable = None
        forecast = "中立"
    elif sign_nikkei * signed_residual > 0:
        residual_leg = "nikkei"
        residual_direction = "buy" if sign_nikkei > 0 else "sell"
        residual_label = f"日経{('買い' if residual_direction == 'buy' else '売り')}超過"
        raw_favorable = _marginal_probability_bp(
            probability_result["raw_model_probability_bp"], residual_leg, residual_direction
        )
        forced_favorable = _marginal_probability_bp(
            probability_result["forced_decision_share_bp"], residual_leg, residual_direction
        )
        forecast = "有利方向" if raw_favorable > 5_000 else "不利方向" if raw_favorable < 5_000 else "中立"
    else:
        residual_leg = "topix"
        residual_direction = "buy" if sign_topix > 0 else "sell"
        residual_label = f"TOPIX{('買い' if residual_direction == 'buy' else '売り')}超過"
        raw_favorable = _marginal_probability_bp(
            probability_result["raw_model_probability_bp"], residual_leg, residual_direction
        )
        forced_favorable = _marginal_probability_bp(
            probability_result["forced_decision_share_bp"], residual_leg, residual_direction
        )
        forecast = "有利方向" if raw_favorable > 5_000 else "不利方向" if raw_favorable < 5_000 else "中立"

    relative_value = _require_mapping(probability_result.get("relative_value"), "quadrants.relative_value")
    relative_probabilities = _require_mapping(
        relative_value.get("probability_bp"),
        "quadrants.relative_value.probability_bp",
    )
    selected_probability_key = "nt_long_after_cost" if strategy == "nt_long" else "nt_short_after_cost"
    opposite_probability_key = "nt_short_after_cost" if strategy == "nt_long" else "nt_long_after_cost"
    selected_relative_probability_bp = int(relative_probabilities[selected_probability_key])
    opposite_relative_probability_bp = int(relative_probabilities[opposite_probability_key])
    inside_cost_probability_bp = int(relative_probabilities["inside_cost_band"])
    relative_probability_gate = selected_relative_probability_bp > max(
        opposite_relative_probability_bp,
        inside_cost_probability_bp,
    )
    combined_spread_mean_bps = float(relative_value["combined_spread_mean_bps"])
    round_trip_cost_bps = float(relative_value["round_trip_cost_pct"]) * 100.0
    relative_drift_gate = (
        combined_spread_mean_bps > round_trip_cost_bps
        if strategy == "nt_long"
        else combined_spread_mean_bps < -round_trip_cost_bps
    )
    relative_model_gate = relative_value.get("analysis_gate_passed") is True
    relative_value_gate = relative_probability_gate and relative_drift_gate and relative_model_gate
    local_position_gate_passed = passes_notional_gate and liquidity_sufficient and relative_value_gate

    result["selected_position"] = {
        "strategy": strategy,
        "nikkei_product": product,
        "nikkei_quantity": nikkei_quantity,
        "topix_mini_quantity": topix_quantity,
        "exact_nikkei_quantity_for_selected_topix": _decimal_string(exact_nikkei_quantity, 6),
        "fractional_difference_nikkei_contracts": _decimal_string(fractional_difference, 6),
        "nikkei_notional_yen": _decimal_string(nikkei_notional, 0),
        "topix_notional_yen": _decimal_string(topix_notional, 0),
        "notional_mismatch_pct": _decimal_string(mismatch_pct, 4),
        "passes_preferred_notional_gate": passes_preferred_notional_gate,
        "passes_notional_gate": passes_notional_gate,
        "available_nikkei_quantity": available_nikkei_quantity,
        "available_topix_quantity": available_topix_quantity,
        "liquidity_sufficient": liquidity_sufficient,
        "selected_relative_probability_bp": selected_relative_probability_bp,
        "opposite_relative_probability_bp": opposite_relative_probability_bp,
        "inside_cost_probability_bp": inside_cost_probability_bp,
        "combined_spread_mean_bps": combined_spread_mean_bps,
        "round_trip_cost_bps": round_trip_cost_bps,
        "relative_probability_gate": relative_probability_gate,
        "relative_drift_gate": relative_drift_gate,
        "relative_model_gate": relative_model_gate,
        "relative_value_gate": relative_value_gate,
        "local_position_gate_passed": local_position_gate_passed,
        "signed_residual_yen": _decimal_string(signed_residual, 0),
        "residual_leg": residual_leg,
        "residual_direction": residual_direction,
        "residual_label": residual_label,
        "raw_favorable_probability_bp": raw_favorable,
        "forced_share_favorable_bp": forced_favorable,
        "residual_forecast": forecast,
        "warning": (
            "名目差5%超"
            if not passes_notional_gate
            else "板数量不足"
            if not liquidity_sufficient
            else "NT相対価値ゲート未達"
            if not relative_value_gate
            else "名目差3%超5%以内"
            if not passes_preferred_notional_gate
            else None
        ),
    }
    return result


# ----------------------------------------


def _daily_index_path(
    anchor: Decimal,
    tick: Decimal,
    day_rows: Sequence[Mapping[str, Any]],
    index_name: str,
    as_of_jst: datetime,
    drift_model_validation_passed: bool,
) -> list[dict[str, Any]]:
    """
    機能:
        日別のファンダ・需給・イベント寄与と分散からD1〜D5の中心値・予測区間を累積計算する。

    引数:
        anchor (Decimal): 現在値アンカー。
        tick (Decimal): 対象商品の呼値。
        day_rows (Sequence[Mapping[str, Any]]): 5営業日分のモデル入力。
        index_name (str): nikkei または topix。
        as_of_jst (datetime): 分析基準の日本時間。
        drift_model_validation_passed (bool): 日次方向モデルが検証合格済みか。

    返り値:
        list[dict[str, Any]]: 各営業日の中心値、68%区間、90%区間、寄与内訳。
    """
    normal = NormalDist()
    z68 = normal.inv_cdf(0.84)
    z90 = normal.inv_cdf(0.95)
    cumulative_mu = 0.0
    cumulative_variance = 0.0
    previous_log_width68: float | None = None
    previous_log_width90: float | None = None
    output: list[dict[str, Any]] = []

    for index, day in enumerate(day_rows, start=1):
        index_spec = _require_mapping(day.get(index_name), f"daily_forecast.days[{index - 1}].{index_name}")
        fundamental_bps = _bounded_float(
            index_spec.get("fundamental_drift_bps"),
            f"{index_name} D{index} fundamental_drift_bps",
            -2_000.0,
            2_000.0,
        )
        supply_bps = _bounded_float(
            index_spec.get("supply_demand_drift_bps"),
            f"{index_name} D{index} supply_demand_drift_bps",
            -2_000.0,
            2_000.0,
        )
        event_bps = _bounded_float(
            index_spec.get("event_drift_bps", 0.0),
            f"{index_name} D{index} event_drift_bps",
            -2_000.0,
            2_000.0,
        )
        base_sigma_pct = _bounded_float(
            index_spec.get("incremental_base_sigma_pct"),
            f"{index_name} D{index} incremental_base_sigma_pct",
            0.0,
            20.0,
        )
        event_sigma_pct = _bounded_float(
            index_spec.get("incremental_event_sigma_pct", 0.0),
            f"{index_name} D{index} incremental_event_sigma_pct",
            0.0,
            20.0,
        )
        if base_sigma_pct <= 0.0:
            raise InputValidationError(f"{index_name} D{index} の基本σは正、イベントσは0以上にしてください。")

        attributions_raw = _require_mapping(
            index_spec.get("drift_attributions"),
            f"{index_name} D{index} drift_attributions",
        )
        if set(attributions_raw) != {"fundamental", "supply_demand", "event"}:
            raise InputValidationError(
                f"{index_name} D{index} drift_attributions はfundamental、supply_demand、eventを過不足なく指定してください。"
            )
        attributions = {
            "fundamental": _normalized_attributions(
                attributions_raw["fundamental"],
                f"{index_name} D{index} drift_attributions.fundamental",
                "evidence_key",
            ),
            "supply_demand": _normalized_attributions(
                attributions_raw["supply_demand"],
                f"{index_name} D{index} drift_attributions.supply_demand",
                "evidence_key",
            ),
            "event": _normalized_attributions(
                attributions_raw["event"],
                f"{index_name} D{index} drift_attributions.event",
                "event_id",
            ),
        }
        declared_contributions = {
            "fundamental": fundamental_bps,
            "supply_demand": supply_bps,
            "event": event_bps,
        }
        for component, declared_value in declared_contributions.items():
            calculated_value = sum(row["contribution_bps"] for row in attributions[component])
            if not math.isclose(calculated_value, declared_value, rel_tol=0.0, abs_tol=1e-9):
                raise InputValidationError(
                    f"{index_name} D{index} の{component}帰属合計を申告ドリフトと一致させてください。"
                )
        combined_daily_bps = fundamental_bps + supply_bps + event_bps
        if abs(combined_daily_bps) > 3_000.0:
            raise InputValidationError(f"{index_name} D{index} の統合日次ドリフトは絶対値3,000bp以内にしてください。")
        if any(abs(value) > 1e-12 for value in declared_contributions.values()) and not drift_model_validation_passed:
            raise InputValidationError(
                f"{index_name} D{index} の非ゼロ方向ドリフトには検証合格済みdrift_model_provenanceが必要です。"
            )
        event_variance_event_ids = _normalized_identifier_list(
            index_spec.get("event_variance_event_ids"),
            f"{index_name} D{index} event_variance_event_ids",
        )
        major_event_keys = {event_id.casefold() for event_id in day["major_events"]}
        event_attribution_keys = {
            row["event_id"].casefold()
            for row in attributions["event"]
        }
        event_variance_keys = {event_id.casefold() for event_id in event_variance_event_ids}
        if event_attribution_keys - major_event_keys:
            raise InputValidationError(
                f"{index_name} D{index} のeventドリフト帰属を当日のmajor_eventsに限定してください。"
            )
        if event_variance_keys - major_event_keys:
            raise InputValidationError(
                f"{index_name} D{index} のevent分散帰属を当日のmajor_eventsに限定してください。"
            )
        if not day["major_events"]:
            if not math.isclose(event_bps, 0.0, rel_tol=0.0, abs_tol=1e-12):
                raise InputValidationError(
                    f"{index_name} D{index} は major_events が空のため event_drift_bps を0にしてください。"
                )
            if not math.isclose(event_sigma_pct, 0.0, rel_tol=0.0, abs_tol=1e-12):
                raise InputValidationError(
                    f"{index_name} D{index} は major_events が空のため incremental_event_sigma_pct を0にしてください。"
                )
        if event_sigma_pct > 0.0 and not event_variance_event_ids:
            raise InputValidationError(
                f"{index_name} D{index} の非ゼロイベントσにはevent_variance_event_idsが必要です。"
            )
        if math.isclose(event_sigma_pct, 0.0, rel_tol=0.0, abs_tol=1e-12) and event_variance_event_ids:
            raise InputValidationError(
                f"{index_name} D{index} のイベントσが0ならevent_variance_event_idsを空にしてください。"
            )
        source = index_spec.get("expected_move_source")
        if not isinstance(source, str) or not source.strip():
            raise InputValidationError(f"{index_name} D{index} の expected_move_source を指定してください。")
        source_links_raw = _require_sequence(
            index_spec.get("expected_move_source_links"),
            f"{index_name} D{index} expected_move_source_links",
        )
        source_links: list[dict[str, str]] = []
        seen_source_links: set[tuple[str, str]] = set()
        for source_index, raw_source_link in enumerate(source_links_raw):
            source_link = _require_mapping(
                raw_source_link,
                f"{index_name} D{index} expected_move_source_links[{source_index}]",
            )
            coverage_item = source_link.get("coverage_item")
            if coverage_item not in (*COVERAGE_ITEMS, "other"):
                raise InputValidationError(
                    f"{index_name} D{index} expected_move_source_links[{source_index}].coverage_item が不正です。"
                )
            source_id = source_link.get("source_id")
            if not isinstance(source_id, str) or not source_id.strip():
                raise InputValidationError(
                    f"{index_name} D{index} expected_move_source_links[{source_index}].source_id を指定してください。"
                )
            data_as_of = _parse_jst(
                source_link.get("data_as_of_jst"),
                f"{index_name} D{index} expected_move_source_links[{source_index}].data_as_of_jst",
            )
            if data_as_of > as_of_jst:
                raise InputValidationError(f"{index_name} D{index} の変動幅ソース時刻が分析時刻より未来です。")
            deduplication_key = (str(coverage_item), source_id.strip())
            if deduplication_key in seen_source_links:
                raise InputValidationError(f"{index_name} D{index} の変動幅ソース対応が重複しています。")
            seen_source_links.add(deduplication_key)
            source_links.append(
                {
                    "coverage_item": str(coverage_item),
                    "source_id": source_id.strip(),
                    "data_as_of_jst": _format_jst(data_as_of),
                }
            )
        if not source_links:
            raise InputValidationError(f"{index_name} D{index} の変動幅ソース対応を1件以上指定してください。")

        daily_mu = (fundamental_bps + supply_bps + event_bps) / 10_000.0
        daily_variance = (base_sigma_pct / 100.0) ** 2 + (event_sigma_pct / 100.0) ** 2
        cumulative_mu += daily_mu
        cumulative_variance += daily_variance
        cumulative_sigma = math.sqrt(cumulative_variance)
        center = float(anchor) * math.exp(cumulative_mu)
        quantiles_raw = index_spec.get("cumulative_quantile_log_return_pct")
        if quantiles_raw is None:
            lower68 = center * math.exp(-z68 * cumulative_sigma)
            upper68 = center * math.exp(z68 * cumulative_sigma)
            lower90 = center * math.exp(-z90 * cumulative_sigma)
            upper90 = center * math.exp(z90 * cumulative_sigma)
            log_width68 = 2.0 * z68 * cumulative_sigma
            log_width90 = 2.0 * z90 * cumulative_sigma
            scale_ratio68 = 1.0
            scale_ratio90 = 1.0
            range_method = "累積増分分散による対数正規近似"
        else:
            quantiles_spec = _require_mapping(
                quantiles_raw,
                f"{index_name} D{index} cumulative_quantile_log_return_pct",
            )
            if set(quantiles_spec) != {"p05", "p16", "p84", "p95"}:
                raise InputValidationError(
                    f"{index_name} D{index} の非対称分位はp05、p16、p84、p95を過不足なく指定してください。"
                )
            quantiles = {
                key: _bounded_float(
                    quantiles_spec[key],
                    f"{index_name} D{index} cumulative_quantile_log_return_pct.{key}",
                    -1_000.0,
                    1_000.0,
                )
                / 100.0
                for key in ("p05", "p16", "p84", "p95")
            }
            if not (
                quantiles["p05"] < quantiles["p16"] < 0.0 < quantiles["p84"] < quantiles["p95"]
            ):
                raise InputValidationError(f"{index_name} D{index} の非対称分位の順序が不正です。")
            log_width68 = quantiles["p84"] - quantiles["p16"]
            log_width90 = quantiles["p95"] - quantiles["p05"]
            expected_width68 = 2.0 * z68 * cumulative_sigma
            expected_width90 = 2.0 * z90 * cumulative_sigma
            scale_ratio68 = log_width68 / expected_width68
            scale_ratio90 = log_width90 / expected_width90
            if not 0.5 <= scale_ratio68 <= 2.0 or not 0.5 <= scale_ratio90 <= 2.0:
                raise InputValidationError(
                    f"{index_name} D{index} の非対称分位幅を累積σの0.5〜2.0倍相当へ整合させてください。"
                )
            lower68 = center * math.exp(quantiles["p16"])
            upper68 = center * math.exp(quantiles["p84"])
            lower90 = center * math.exp(quantiles["p05"])
            upper90 = center * math.exp(quantiles["p95"])
            range_method = "入力済み非対称累積分位"
        if previous_log_width68 is not None and log_width68 + 1e-12 < previous_log_width68:
            raise InputValidationError(f"{index_name} D{index} の68%累積分位幅が前日より縮小しています。")
        if previous_log_width90 is not None and log_width90 + 1e-12 < previous_log_width90:
            raise InputValidationError(f"{index_name} D{index} の90%累積分位幅が前日より縮小しています。")
        previous_log_width68 = log_width68
        previous_log_width90 = log_width90
        rounded_center = Decimal(_round_to_tick(center, tick))
        rounded_lower68 = Decimal(_round_to_tick(lower68, tick))
        rounded_upper68 = Decimal(_round_to_tick(upper68, tick))
        rounded_lower90 = Decimal(_round_to_tick(lower90, tick))
        rounded_upper90 = Decimal(_round_to_tick(upper90, tick))
        if min(rounded_center, rounded_lower68, rounded_upper68, rounded_lower90, rounded_upper90) <= 0:
            raise InputValidationError(f"{index_name} D{index} の呼値丸め後価格は全て正値にしてください。")
        if not (
            rounded_lower90
            <= rounded_lower68
            <= rounded_center
            <= rounded_upper68
            <= rounded_upper90
        ):
            raise InputValidationError(
                f"{index_name} D{index} の丸め後予測帯を中心包含・90%帯包含の順序へ整合させてください。"
            )
        if rounded_lower68 >= rounded_upper68 or rounded_lower90 >= rounded_upper90:
            raise InputValidationError(f"{index_name} D{index} の丸め後予測帯に正の幅が必要です。")
        output.append(
            {
                "label": day["label"],
                "target_at_jst": day["target_at_jst"],
                "center_price_p50": _decimal_string(rounded_center),
                "current_anchor_change_pct": round((center / float(anchor) - 1.0) * 100.0, 4),
                "range68": [
                    _decimal_string(rounded_lower68),
                    _decimal_string(rounded_upper68),
                ],
                "range90": [
                    _decimal_string(rounded_lower90),
                    _decimal_string(rounded_upper90),
                ],
                "cumulative_sigma_pct": round(cumulative_sigma * 100.0, 4),
                "range_scale_ratio68": round(scale_ratio68, 6),
                "range_scale_ratio90": round(scale_ratio90, 6),
                "daily_fundamental_contribution_bps": fundamental_bps,
                "daily_supply_demand_contribution_bps": supply_bps,
                "daily_event_contribution_bps": event_bps,
                "drift_attributions": attributions,
                "incremental_base_sigma_pct": base_sigma_pct,
                "incremental_event_sigma_pct": event_sigma_pct,
                "event_variance_event_ids": event_variance_event_ids,
                "expected_move_source": source,
                "expected_move_source_links": source_links,
                "range_method": range_method,
                "major_events": day["major_events"],
            }
        )
    return output


# ----------------------------------------


def calculate_daily_forecast(
    spec: Mapping[str, Any],
    as_of_jst: datetime,
    session_context: Mapping[str, Any],
) -> dict[str, Any]:
    """
    機能:
        J-Quants営業日で確定したD1〜D5の日経・TOPIX予測パスを構築する。

    引数:
        spec (Mapping[str, Any]): アンカー、呼値、営業日、日別ドリフト・分散。
        as_of_jst (datetime): 将来日とカレンダー取得時刻の検証基準。
        session_context (Mapping[str, Any]): D1起算に使う検証済みJPX取引日。

    返り値:
        dict[str, Any]: 日経・TOPIXそれぞれの5営業日予測表。
    """
    if spec.get("calendar_verified") is not True:
        raise InputValidationError("daily_forecast.calendar_verified は true にしてください。")
    target_definition_id = _required_text(
        spec.get("target_definition_id"),
        "daily_forecast.target_definition_id",
    )
    if target_definition_id != "tse_cash_close_15_30":
        raise InputValidationError("daily_forecast.target_definition_id は tse_cash_close_15_30 にしてください。")
    target_venue = _required_text(spec.get("target_venue"), "daily_forecast.target_venue")
    if target_venue != "JPX_TSE":
        raise InputValidationError("daily_forecast.target_venue は JPX_TSE にしてください。")
    target_phase = _required_text(spec.get("target_phase"), "daily_forecast.target_phase")
    if target_phase != "cash_close":
        raise InputValidationError("daily_forecast.target_phase は cash_close にしてください。")
    d1_origin_rule = _required_text(spec.get("d1_origin_rule"), "daily_forecast.d1_origin_rule")
    if d1_origin_rule != "day_next_cash_trading_day_or_night_exchange_trading_day":
        raise InputValidationError(
            "daily_forecast.d1_origin_rule は day_next_cash_trading_day_or_night_exchange_trading_day にしてください。"
        )
    drift_model_provenance = _validated_model_provenance(
        _require_mapping(
            spec.get("drift_model_provenance"),
            "daily_forecast.drift_model_provenance",
        ),
        "daily_forecast.drift_model_provenance",
        as_of_jst,
        "next_trading_day_incremental_log_return",
        None,
    )
    calendar_source = spec.get("calendar_source")
    if not isinstance(calendar_source, str) or not calendar_source.strip():
        raise InputValidationError("daily_forecast.calendar_source を指定してください。")
    calendar_coverage_item = spec.get("calendar_coverage_item")
    if calendar_coverage_item not in (*COVERAGE_ITEMS, "other"):
        raise InputValidationError("daily_forecast.calendar_coverage_item が不正です。")
    calendar_source_id = spec.get("calendar_source_id")
    if not isinstance(calendar_source_id, str) or not calendar_source_id.strip():
        raise InputValidationError("daily_forecast.calendar_source_id を指定してください。")
    calendar_data_as_of = _parse_jst(
        spec.get("calendar_data_as_of_jst"),
        "daily_forecast.calendar_data_as_of_jst",
    )
    if calendar_data_as_of > as_of_jst:
        raise InputValidationError("daily_forecast.calendar_data_as_of_jst が分析時刻より未来です。")
    calendar_fetched_at = _parse_jst(
        spec.get("calendar_fetched_at_jst"),
        "daily_forecast.calendar_fetched_at_jst",
    )
    if calendar_fetched_at > as_of_jst:
        raise InputValidationError("daily_forecast.calendar_fetched_at_jst が分析時刻より未来です。")
    if calendar_data_as_of > calendar_fetched_at:
        raise InputValidationError(
            "daily_forecast.calendar_data_as_of_jst を calendar_fetched_at_jst より後にしないでください。"
        )
    if (as_of_jst - calendar_fetched_at).total_seconds() > COVERAGE_CHECK_MAX_AGE_HOURS * 3600:
        raise InputValidationError("daily_forecast.calendar_fetched_at_jst の確認が24時間より古いです。")
    verified_dates_raw = _require_sequence(
        spec.get("verified_trading_dates"),
        "daily_forecast.verified_trading_dates",
    )
    if len(verified_dates_raw) != 5:
        raise InputValidationError("daily_forecast.verified_trading_dates は5営業日にしてください。")
    verified_dates: list[date] = []
    for index, raw_date in enumerate(verified_dates_raw):
        if not isinstance(raw_date, str):
            raise InputValidationError(f"verified_trading_dates[{index}] はYYYY-MM-DD文字列にしてください。")
        try:
            parsed_date = date.fromisoformat(raw_date)
        except ValueError as exc:
            raise InputValidationError(f"verified_trading_dates[{index}] の日付形式が不正です。") from exc
        if parsed_date.weekday() >= 5:
            raise InputValidationError("verified_trading_dates に土日を含めないでください。")
        verified_dates.append(parsed_date)
    calendar_sessions_raw = _require_sequence(
        spec.get("calendar_sessions"),
        "daily_forecast.calendar_sessions",
    )
    first_calendar_date = _parse_iso_date(
        session_context.get("calendar_start_date"),
        "session_context.calendar_start_date",
    )
    final_calendar_date = verified_dates[-1]
    expected_calendar_length = (final_calendar_date - first_calendar_date).days + 1
    if expected_calendar_length < 5 or expected_calendar_length > 15:
        raise InputValidationError("D1〜D5はセッション基準日から15暦日以内の次の5現物営業日にしてください。")
    if len(calendar_sessions_raw) != expected_calendar_length:
        raise InputValidationError("calendar_sessions はセッション基準日からD5までの全暦日を省略せず指定してください。")
    calendar_sessions: list[dict[str, Any]] = []
    derived_trading_dates: list[date] = []
    for session_index, raw_session in enumerate(calendar_sessions_raw):
        session = _require_mapping(raw_session, f"daily_forecast.calendar_sessions[{session_index}]")
        session_date_raw = session.get("date")
        if not isinstance(session_date_raw, str):
            raise InputValidationError(
                f"daily_forecast.calendar_sessions[{session_index}].date はYYYY-MM-DD文字列にしてください。"
            )
        try:
            session_date = date.fromisoformat(session_date_raw)
        except ValueError as exc:
            raise InputValidationError(
                f"daily_forecast.calendar_sessions[{session_index}].date の形式が不正です。"
            ) from exc
        expected_session_date = first_calendar_date + timedelta(days=session_index)
        if session_date != expected_session_date:
            raise InputValidationError("calendar_sessions の日付をセッション基準日からD5まで連続させてください。")
        cash_is_trading_day = session.get("cash_is_trading_day")
        if not isinstance(cash_is_trading_day, bool):
            raise InputValidationError(
                f"daily_forecast.calendar_sessions[{session_index}].cash_is_trading_day は真偽値にしてください。"
            )
        derivatives_session_open = session.get("derivatives_session_open")
        if not isinstance(derivatives_session_open, bool):
            raise InputValidationError(
                f"daily_forecast.calendar_sessions[{session_index}].derivatives_session_open は真偽値にしてください。"
            )
        holiday_division = str(session.get("holiday_division", "")).strip()
        if holiday_division not in HOLIDAY_DIVISIONS:
            raise InputValidationError(
                f"daily_forecast.calendar_sessions[{session_index}].holiday_division が不正です。"
            )
        if cash_is_trading_day != (holiday_division in ("1", "2")):
            raise InputValidationError(
                "calendar_sessionsのcash_is_trading_dayをholiday_division=1または2と一致させてください。"
            )
        if holiday_division == "2":
            raise InputValidationError(
                "東証半日立会日は15:30終値targetの対象外です。半日用target profileを追加してから使用してください。"
            )
        if derivatives_session_open != (holiday_division in ("1", "3")):
            raise InputValidationError(
                "calendar_sessionsのderivatives_session_openをholiday_division=1または3と一致させてください。"
            )
        origin_must_be_cash_trading_day = session_index == 0 and (
            session_context["futures_session_kind"] == "night_session"
            or session_context["holiday_division"] == "3"
        )
        if origin_must_be_cash_trading_day and not cash_is_trading_day:
            raise InputValidationError(
                "ナイト・祝日日中のcalendar_sessions先頭はexchange_trading_dayの現物営業日にしてください。"
            )
        if session_date.weekday() >= 5 and (
            cash_is_trading_day or derivatives_session_open or holiday_division != "0"
        ):
            raise InputValidationError("calendar_sessions の土日を現物・先物取引日として指定できません。")
        if cash_is_trading_day:
            derived_trading_dates.append(session_date)
        calendar_sessions.append(
            {
                "date": session_date.isoformat(),
                "cash_is_trading_day": cash_is_trading_day,
                "derivatives_session_open": derivatives_session_open,
                "holiday_division": holiday_division,
            }
        )
    if derived_trading_dates != verified_dates:
        raise InputValidationError(
            "verified_trading_dates をcalendar_sessionsから導出した次の5営業日と一致させてください。"
        )
    days_raw = _require_sequence(spec.get("days"), "daily_forecast.days")
    if len(days_raw) != 5:
        raise InputValidationError("daily_forecast.days はD1〜D5の5行にしてください。")

    days: list[dict[str, Any]] = []
    previous_target_date: date | None = None
    for index, raw_day in enumerate(days_raw, start=1):
        day = dict(_require_mapping(raw_day, f"daily_forecast.days[{index - 1}]"))
        expected_label = f"D{index}"
        if day.get("label") != expected_label:
            raise InputValidationError(f"daily_forecast.days[{index - 1}].label は {expected_label} にしてください。")
        target = _parse_jst(day.get("target_at_jst"), f"daily_forecast {expected_label} target_at_jst")
        if target <= as_of_jst:
            raise InputValidationError(f"daily_forecast {expected_label} target_at_jst は分析時刻より後にしてください。")
        if previous_target_date is not None and target.date() <= previous_target_date:
            raise InputValidationError("D1〜D5は日付単位で厳密な昇順にしてください。")
        if target.date() != verified_dates[index - 1]:
            raise InputValidationError(f"{expected_label}の対象日を検証済み営業日と一致させてください。")
        if (target.hour, target.minute, target.second, target.microsecond) != (15, 30, 0, 0):
            raise InputValidationError(f"{expected_label} target_at_jst はTSE現物終値15:30 JSTにしてください。")
        major_events_raw = _require_sequence(day.get("major_events"), f"daily_forecast {expected_label} major_events")
        major_events: list[str] = []
        seen_major_events: set[str] = set()
        for event_index, event_name in enumerate(major_events_raw):
            if not isinstance(event_name, str) or not event_name.strip():
                raise InputValidationError(
                    f"daily_forecast {expected_label} major_events[{event_index}] は非空文字列にしてください。"
                )
            normalized_event_name = event_name.strip()
            event_deduplication_key = normalized_event_name.casefold()
            if event_deduplication_key in seen_major_events:
                raise InputValidationError(f"daily_forecast {expected_label} major_events が重複しています。")
            seen_major_events.add(event_deduplication_key)
            major_events.append(normalized_event_name)
        previous_target_date = target.date()
        day["target_at_jst"] = _format_jst(target)
        day["major_events"] = major_events
        days.append(day)

    nikkei_anchor = _positive_decimal(spec.get("nikkei_anchor"), "daily_forecast.nikkei_anchor")
    topix_anchor = _positive_decimal(spec.get("topix_anchor"), "daily_forecast.topix_anchor")
    nikkei_tick = _positive_decimal(spec.get("nikkei_tick", "5"), "daily_forecast.nikkei_tick")
    topix_tick = _positive_decimal(spec.get("topix_tick", "0.25"), "daily_forecast.topix_tick")
    if nikkei_tick != Decimal("5"):
        raise InputValidationError("daily_forecast.nikkei_tick は日経225 mini/microの5にしてください。")
    if topix_tick != Decimal("0.25"):
        raise InputValidationError("daily_forecast.topix_tick はTOPIX miniの0.25にしてください。")
    return {
        "definition": "収束予想中心値は条件付き分布の中央値。確定値ではない。",
        "target_definition_id": target_definition_id,
        "target_venue": target_venue,
        "target_phase": target_phase,
        "d1_origin_rule": d1_origin_rule,
        "calendar_origin_date": first_calendar_date.isoformat(),
        "origin_futures_session_kind": session_context["futures_session_kind"],
        "origin_exchange_trading_day": session_context["exchange_trading_day"],
        "calendar_source": calendar_source,
        "calendar_coverage_item": calendar_coverage_item,
        "calendar_source_id": calendar_source_id.strip(),
        "calendar_data_as_of_jst": _format_jst(calendar_data_as_of),
        "calendar_fetched_at_jst": _format_jst(calendar_fetched_at),
        "calendar_sessions": calendar_sessions,
        "verified_trading_dates": [value.isoformat() for value in verified_dates],
        "calendar_verified": True,
        "drift_model_provenance": drift_model_provenance,
        "nikkei": _daily_index_path(
            nikkei_anchor,
            nikkei_tick,
            days,
            "nikkei",
            as_of_jst,
            drift_model_provenance["validation_passed"],
        ),
        "topix": _daily_index_path(
            topix_anchor,
            topix_tick,
            days,
            "topix",
            as_of_jst,
            drift_model_provenance["validation_passed"],
        ),
    }


# ----------------------------------------


def validate_coverage(spec: Mapping[str, Any], as_of_jst: datetime) -> dict[str, Any]:
    """
    機能:
        必須の市場横断データが利用・不採用・取得不能・非該当のいずれかで監査済みか検証する。

    引数:
        spec (Mapping[str, Any]): データ項目ごとの状態、理由、確認時刻、情報源。
        as_of_jst (datetime): 分析基準の日本時間。

    返り値:
        dict[str, Any]: 固定順に正規化したデータ網羅性監査結果。
    """
    if set(spec) != set(COVERAGE_ITEMS):
        missing = sorted(set(COVERAGE_ITEMS) - set(spec))
        extra = sorted(set(spec) - set(COVERAGE_ITEMS))
        raise InputValidationError(f"coverage の項目が不一致です。不足={missing}、余分={extra}")
    result: dict[str, Any] = {}
    for item_name in COVERAGE_ITEMS:
        item = _require_mapping(spec[item_name], f"coverage.{item_name}")
        status = item.get("status")
        if status not in ("used", "excluded", "unavailable", "not_applicable"):
            raise InputValidationError(
                f"coverage.{item_name}.status は used、excluded、unavailable、not_applicable のいずれかにしてください。"
            )
        if status == "not_applicable" and item_name != "polymarket":
            raise InputValidationError(
                f"coverage.{item_name} は確認対象です。非該当ではなく excluded または unavailable の理由を記録してください。"
            )
        reason = item.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise InputValidationError(f"coverage.{item_name}.reason は非空文字列にしてください。")
        checked_at = _parse_jst(item.get("checked_at_jst"), f"coverage.{item_name}.checked_at_jst")
        if checked_at > as_of_jst:
            raise InputValidationError(f"coverage.{item_name}.checked_at_jst が分析時刻より未来です。")
        checked_age_hours = (as_of_jst - checked_at).total_seconds() / 3600.0
        if checked_age_hours > COVERAGE_CHECK_MAX_AGE_HOURS:
            raise InputValidationError(f"coverage.{item_name} の確認時刻が24時間より古いです。")
        source_ids: list[str] = []
        if status in ("used", "excluded"):
            source_ids_raw = _require_sequence(item.get("source_ids"), f"coverage.{item_name}.source_ids")
            for source_index, raw_source_id in enumerate(source_ids_raw):
                if not isinstance(raw_source_id, str) or not raw_source_id.strip():
                    raise InputValidationError(
                        f"coverage.{item_name}.source_ids[{source_index}] は非空文字列にしてください。"
                    )
                normalized_source_id = raw_source_id.strip()
                if normalized_source_id in source_ids:
                    raise InputValidationError(f"coverage.{item_name}.source_ids が重複しています。")
                source_ids.append(normalized_source_id)
            if not source_ids:
                raise InputValidationError(
                    f"取得した coverage.{item_name}.source_ids を1件以上指定してください。"
                )
        data_as_of = None
        data_age_hours = None
        if status in ("used", "excluded"):
            data_as_of = _parse_jst(item.get("data_as_of_jst"), f"coverage.{item_name}.data_as_of_jst")
            if data_as_of > as_of_jst:
                raise InputValidationError(f"coverage.{item_name}.data_as_of_jst が分析時刻より未来です。")
            if data_as_of > checked_at:
                raise InputValidationError(
                    f"coverage.{item_name}.data_as_of_jst を checked_at_jst より後にしないでください。"
                )
            data_age_hours = (as_of_jst - data_as_of).total_seconds() / 3600.0
        if status == "used" and data_age_hours is not None:
            max_age_hours = COVERAGE_USED_MAX_AGE_HOURS[item_name]
            if data_age_hours > max_age_hours:
                raise InputValidationError(
                    f"coverage.{item_name} の利用データが鮮度上限 {max_age_hours:.4f} 時間を超えています。"
                )
        result[item_name] = {
            "status": status,
            "reason": reason.strip(),
            "checked_at_jst": _format_jst(checked_at),
            "checked_age_hours": round(checked_age_hours, 6),
            "data_as_of_jst": _format_jst(data_as_of) if data_as_of is not None else None,
            "data_age_hours": round(data_age_hours, 6) if data_age_hours is not None else None,
            "source_ids": source_ids,
        }
    used_count = sum(row["status"] == "used" for row in result.values())
    critical_items = ("realtime_fx", "economic_calendar", "overseas_markets")
    missing_critical = [name for name in critical_items if result[name]["status"] != "used"]
    analysis_gate_passed = used_count >= 5 and not missing_critical
    return {
        "items": result,
        "summary": {
            "used_count": used_count,
            "excluded_count": sum(row["status"] == "excluded" for row in result.values()),
            "unavailable_count": sum(row["status"] == "unavailable" for row in result.values()),
            "not_applicable_count": sum(row["status"] == "not_applicable" for row in result.values()),
            "available_pct": round(used_count / len(COVERAGE_ITEMS) * 100.0, 2),
            "critical_items_not_used": missing_critical,
            "analysis_gate_passed": analysis_gate_passed,
        },
    }


# ----------------------------------------


def validate_other_sources(spec: Mapping[str, Any], as_of_jst: datetime) -> dict[str, Any]:
    """
    機能:
        必須12項目外の固有DB・一次資料を、時刻と鮮度上限を持つ監査台帳として検証する。

    引数:
        spec (Mapping[str, Any]): source_idをキーとする固有情報源台帳。
        as_of_jst (datetime): 分析基準の日本時間。

    返り値:
        dict[str, Any]: 固定順に正規化した固有情報源台帳。
    """
    result: dict[str, Any] = {}
    for source_id in sorted(spec):
        if not isinstance(source_id, str) or not source_id.strip():
            raise InputValidationError("other_sources のキーは非空の source_id にしてください。")
        item = _require_mapping(spec[source_id], f"other_sources.{source_id}")
        source_reference = item.get("source_url_or_document_id")
        if not isinstance(source_reference, str) or not source_reference.strip():
            raise InputValidationError(
                f"other_sources.{source_id}.source_url_or_document_id は非空文字列にしてください。"
            )
        reason = item.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise InputValidationError(f"other_sources.{source_id}.reason は非空文字列にしてください。")
        checked_at = _parse_jst(item.get("checked_at_jst"), f"other_sources.{source_id}.checked_at_jst")
        data_as_of = _parse_jst(item.get("data_as_of_jst"), f"other_sources.{source_id}.data_as_of_jst")
        if checked_at > as_of_jst or data_as_of > as_of_jst:
            raise InputValidationError(f"other_sources.{source_id} の時刻が分析時刻より未来です。")
        if data_as_of > checked_at:
            raise InputValidationError(
                f"other_sources.{source_id}.data_as_of_jst を checked_at_jst より後にしないでください。"
            )
        checked_age_hours = (as_of_jst - checked_at).total_seconds() / 3600.0
        if checked_age_hours > COVERAGE_CHECK_MAX_AGE_HOURS:
            raise InputValidationError(f"other_sources.{source_id} の確認時刻が24時間より古いです。")
        max_data_age_hours = _bounded_float(
            item.get("max_data_age_hours"),
            f"other_sources.{source_id}.max_data_age_hours",
            0.0,
            8_760.0,
        )
        if max_data_age_hours <= 0.0:
            raise InputValidationError(f"other_sources.{source_id}.max_data_age_hours は正値にしてください。")
        data_age_hours = (as_of_jst - data_as_of).total_seconds() / 3600.0
        if data_age_hours > max_data_age_hours:
            raise InputValidationError(
                f"other_sources.{source_id} のデータ年齢が申告上限 {max_data_age_hours:.4f} 時間を超えています。"
            )
        result[source_id] = {
            "source_url_or_document_id": source_reference.strip(),
            "reason": reason.strip(),
            "checked_at_jst": _format_jst(checked_at),
            "checked_age_hours": round(checked_age_hours, 6),
            "data_as_of_jst": _format_jst(data_as_of),
            "data_age_hours": round(data_age_hours, 6),
            "max_data_age_hours": max_data_age_hours,
        }
    return result


# ----------------------------------------


def _validate_source_link(
    coverage_item: str,
    source_id: str,
    linked_at_jst: str,
    coverage_items: Mapping[str, Any],
    other_sources: Mapping[str, Any],
    as_of_jst: datetime,
    context: str,
) -> None:
    """
    機能:
        証拠・イベント・各種モデル・営業日・変動幅の情報源をcoverageまたは固有情報源台帳へ結び付ける。

    引数:
        coverage_item (str): 必須12項目の識別子またはother。
        source_id (str): 利用した情報源の識別子。
        linked_at_jst (str): 当該判断が参照したデータ対象時刻。
        coverage_items (Mapping[str, Any]): 検証済みcoverage台帳。
        other_sources (Mapping[str, Any]): 検証済み固有情報源台帳。
        as_of_jst (datetime): 分析基準の日本時間。
        context (str): エラー表示用の利用箇所。

    返り値:
        None: 整合時は値を返さず、不整合時は例外を送出する。
    """
    linked_at = _parse_jst(linked_at_jst, f"{context} の data_as_of_jst")
    if linked_at > as_of_jst:
        raise InputValidationError(f"{context} の参照時刻が分析時刻より未来です。")
    if coverage_item == "other":
        if source_id not in other_sources:
            raise InputValidationError(f"{context} の source_id={source_id} を other_sourcesへ登録してください。")
        registered_at = _parse_jst(
            other_sources[source_id]["data_as_of_jst"],
            f"other_sources.{source_id}.data_as_of_jst",
        )
        if linked_at > registered_at:
            raise InputValidationError(f"{context} の参照時刻がother_sources台帳の対象時刻より新しいです。")
        linked_age_hours = (as_of_jst - linked_at).total_seconds() / 3600.0
        if linked_age_hours > float(other_sources[source_id]["max_data_age_hours"]):
            raise InputValidationError(f"{context} の参照データがother_sources台帳の鮮度上限を超えています。")
        return

    if coverage_item not in COVERAGE_ITEMS:
        raise InputValidationError(f"{context} の coverage_item が不正です。")
    coverage_row = _require_mapping(coverage_items[coverage_item], f"coverage.{coverage_item}")
    if coverage_row.get("status") != "used":
        raise InputValidationError(f"{context} の coverage_item={coverage_item} を used と一致させてください。")
    if source_id not in coverage_row.get("source_ids", []):
        raise InputValidationError(f"{context} の source_id を coverage.{coverage_item}.source_ids に含めてください。")
    coverage_data_as_of = _parse_jst(
        coverage_row.get("data_as_of_jst"),
        f"coverage.{coverage_item}.data_as_of_jst",
    )
    if linked_at > coverage_data_as_of:
        raise InputValidationError(f"{context} の参照時刻がcoverage台帳の対象時刻より新しいです。")
    linked_age_hours = (as_of_jst - linked_at).total_seconds() / 3600.0
    if linked_age_hours > COVERAGE_USED_MAX_AGE_HOURS[coverage_item]:
        raise InputValidationError(f"{context} の参照データが coverage.{coverage_item} の鮮度上限を超えています。")


# ----------------------------------------


def _detect_distributed_sample_markers(payload: Mapping[str, Any]) -> list[str]:
    """
    機能:
        配布入力例に固有の合成識別子・URI・モデル名が実入力へ残っていないか検出する。

    引数:
        payload (Mapping[str, Any]): 検査する入力JSON全体。

    返り値:
        list[str]: 合成マーカーを検出したフィールド説明。未検出時は空配列。
    """
    candidates: list[tuple[str, Any]] = []
    probability = payload.get("probability")
    if isinstance(probability, Mapping):
        candidates.append(("probability.model_version", probability.get("model_version")))
        for provenance_name in ("base_provenance",):
            provenance = probability.get(provenance_name)
            if isinstance(provenance, Mapping):
                for field_name in ("model_id", "model_version", "model_structure_id"):
                    candidates.append(
                        (
                            f"probability.{provenance_name}.{field_name}",
                            provenance.get(field_name),
                        )
                    )
                source_links = provenance.get("source_links")
                if isinstance(source_links, Sequence) and not isinstance(
                    source_links, (str, bytes, bytearray)
                ):
                    for source_index, raw_link in enumerate(source_links):
                        if isinstance(raw_link, Mapping):
                            candidates.append(
                                (
                                    f"probability.{provenance_name}.source_links[{source_index}].source_id",
                                    raw_link.get("source_id"),
                                )
                            )
        relative_value = probability.get("relative_value")
        if isinstance(relative_value, Mapping):
            relative_model = relative_value.get("model_provenance")
            if isinstance(relative_model, Mapping):
                for field_name in ("model_id", "model_version", "model_structure_id"):
                    candidates.append(
                        (
                            f"probability.relative_value.model_provenance.{field_name}",
                            relative_model.get(field_name),
                        )
                    )
        base = probability.get("base")
        if isinstance(base, Mapping):
            scenarios = base.get("scenario_mixture")
            if isinstance(scenarios, Sequence) and not isinstance(scenarios, (str, bytes, bytearray)):
                for scenario_index, raw_scenario in enumerate(scenarios):
                    if not isinstance(raw_scenario, Mapping):
                        continue
                    conditional_basis = raw_scenario.get("conditional_probability_basis")
                    if not isinstance(conditional_basis, Mapping):
                        continue
                    for field_name in ("model_id", "model_version", "model_structure_id"):
                        candidates.append(
                            (
                                "probability.base.scenario_mixture"
                                f"[{scenario_index}].conditional_probability_basis.{field_name}",
                                conditional_basis.get(field_name),
                            )
                        )
        evidence = probability.get("evidence")
        if isinstance(evidence, Sequence) and not isinstance(evidence, (str, bytes, bytearray)):
            for index, raw_item in enumerate(evidence):
                if isinstance(raw_item, Mapping):
                    candidates.extend(
                        (
                            (f"probability.evidence[{index}].evidence_key", raw_item.get("evidence_key")),
                            (f"probability.evidence[{index}].source_id", raw_item.get("source_id")),
                        )
                    )
    nt = payload.get("nt")
    if isinstance(nt, Mapping):
        candidates.extend(
            (
                ("nt.nikkei_symbol", nt.get("nikkei_symbol")),
                ("nt.topix_symbol", nt.get("topix_symbol")),
            )
        )
    timing = payload.get("timing")
    if isinstance(timing, Mapping):
        events = timing.get("events")
        if isinstance(events, Sequence) and not isinstance(events, (str, bytes, bytearray)):
            for index, raw_event in enumerate(events):
                if isinstance(raw_event, Mapping):
                    for field_name in ("source_id", "source_url_or_document_id", "official_source"):
                        candidates.append((f"timing.events[{index}].{field_name}", raw_event.get(field_name)))
    daily = payload.get("daily_forecast")
    if isinstance(daily, Mapping):
        candidates.extend(
            (
                ("daily_forecast.calendar_source", daily.get("calendar_source")),
                ("daily_forecast.calendar_source_id", daily.get("calendar_source_id")),
            )
        )
        drift_model = daily.get("drift_model_provenance")
        if isinstance(drift_model, Mapping):
            for field_name in ("model_id", "model_version", "model_structure_id"):
                candidates.append(
                    (
                        f"daily_forecast.drift_model_provenance.{field_name}",
                        drift_model.get(field_name),
                    )
                )
        days = daily.get("days")
        if isinstance(days, Sequence) and not isinstance(days, (str, bytes, bytearray)):
            for day_index, raw_day in enumerate(days):
                if not isinstance(raw_day, Mapping):
                    continue
                for index_name in ("nikkei", "topix"):
                    index_spec = raw_day.get(index_name)
                    if not isinstance(index_spec, Mapping):
                        continue
                    candidates.append(
                        (
                            f"daily_forecast.days[{day_index}].{index_name}.expected_move_source",
                            index_spec.get("expected_move_source"),
                        )
                    )
                    source_links = index_spec.get("expected_move_source_links")
                    if isinstance(source_links, Sequence) and not isinstance(
                        source_links, (str, bytes, bytearray)
                    ):
                        for source_index, raw_link in enumerate(source_links):
                            if isinstance(raw_link, Mapping):
                                candidates.append(
                                    (
                                        "daily_forecast.days"
                                        f"[{day_index}].{index_name}.expected_move_source_links"
                                        f"[{source_index}].source_id",
                                        raw_link.get("source_id"),
                                    )
                                )
    other_sources = payload.get("other_sources")
    if isinstance(other_sources, Mapping):
        for source_id, raw_source in other_sources.items():
            candidates.append((f"other_sources.{source_id}", source_id))
            if isinstance(raw_source, Mapping):
                candidates.append(
                    (
                        f"other_sources.{source_id}.source_url_or_document_id",
                        raw_source.get("source_url_or_document_id"),
                    )
                )

    markers: list[str] = []
    for field_name, raw_value in candidates:
        if not isinstance(raw_value, str):
            continue
        normalized = raw_value.casefold()
        if "synthetic" in normalized or "合成" in raw_value:
            markers.append(field_name)
    return sorted(set(markers))


# ----------------------------------------


def _validate_evidence_attribution_references(
    rows: Sequence[Mapping[str, Any]],
    expected_block: str,
    evidence_by_key: Mapping[str, Mapping[str, Any]],
    context: str,
) -> None:
    """
    機能:
        ドリフト帰属が有効な同一分析ブロックの非中立証拠だけを参照することを検証する。

    引数:
        rows (Sequence[Mapping[str, Any]]): evidence_keyを持つ寄与行。
        expected_block (str): fundamentalまたはsupply_demand。
        evidence_by_key (Mapping[str, Mapping[str, Any]]): 正規化済み証拠監査表。
        context (str): エラー表示用の帰属箇所。

    返り値:
        None: 整合時は値を返さず、不整合時は例外を送出する。
    """
    for row in rows:
        evidence_key = str(row["evidence_key"]).casefold()
        evidence = evidence_by_key.get(evidence_key)
        if evidence is None:
            raise InputValidationError(f"{context} が未知のevidence_keyを参照しています。")
        if evidence["block"] != expected_block:
            raise InputValidationError(f"{context} が別ブロックの証拠を参照しています。")
        if evidence["effective_multiplier"] < 1e-6 or evidence["neutral_observation"]:
            raise InputValidationError(f"{context} は実効的な非中立証拠だけを参照してください。")


# ----------------------------------------


def _validate_event_attribution_references(
    rows: Sequence[Mapping[str, Any]],
    events_by_key: Mapping[str, Mapping[str, Any]],
    context: str,
    earliest_exclusive_jst: datetime | None = None,
    latest_inclusive_jst: datetime | None = None,
) -> None:
    """
    機能:
        ドリフトまたはイベント分散の帰属が検証済み重要イベントを参照することを検証する。

    引数:
        rows (Sequence[Mapping[str, Any]]): event_idを持つ寄与行。
        events_by_key (Mapping[str, Mapping[str, Any]]): 正規化済みイベント監査表。
        context (str): エラー表示用の帰属箇所。
        earliest_exclusive_jst (datetime | None): 指定時はイベント時刻がこの時刻より後であることを求める。
        latest_inclusive_jst (datetime | None): 指定時はイベント時刻がこの時刻以前であることを求める。

    返り値:
        None: 整合時は値を返さず、不整合時は例外を送出する。
    """
    for row in rows:
        event_key = str(row["event_id"]).casefold()
        event = events_by_key.get(event_key)
        if event is None:
            raise InputValidationError(f"{context} が未知のevent_idを参照しています。")
        if not event["is_material"] or not event["inside_search_horizon"]:
            raise InputValidationError(f"{context} はD5探索内の重要イベントだけを参照してください。")
        scheduled_at = _parse_jst(event["scheduled_at_jst"], f"{context} event_id={event['event_id']}")
        if earliest_exclusive_jst is not None and scheduled_at <= earliest_exclusive_jst:
            raise InputValidationError(f"{context} は分析時刻より後のイベントだけを参照してください。")
        if latest_inclusive_jst is not None and scheduled_at > latest_inclusive_jst:
            raise InputValidationError(f"{context} は評価期限以前のイベントだけを参照してください。")


# ----------------------------------------


def calculate_all(payload: Mapping[str, Any]) -> dict[str, Any]:
    """
    機能:
        入力JSON全体を検証し、時刻・4象限・NT・D1〜D5を一括計算する。

    引数:
        payload (Mapping[str, Any]): スキーマ版、分析時刻、各計算ブロック。

    返り値:
        dict[str, Any]: 同一入力と同一OS評価時刻で再現可能な計算結果と入力ハッシュ。
    """
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise InputValidationError(f"schema_version は {SCHEMA_VERSION} にしてください。")
    unknown_top_level_fields = sorted(set(payload) - TOP_LEVEL_FIELDS)
    if unknown_top_level_fields:
        raise InputValidationError(
            "未対応のトップレベル項目があります: " + ", ".join(unknown_top_level_fields)
        )
    as_of_jst = _parse_jst(payload.get("as_of_jst"), "as_of_jst")
    sample_only_raw = payload.get("sample_only")
    if not isinstance(sample_only_raw, bool):
        raise InputValidationError("sample_only は true または false を明示してください。")
    sample_only = sample_only_raw
    sample_notice = payload.get("_sample_notice")
    if sample_only:
        if not isinstance(sample_notice, str) or not sample_notice.strip():
            raise InputValidationError("sample_only=true では _sample_notice を指定してください。")
    elif sample_notice is not None:
        raise InputValidationError("_sample_notice を含む入力は sample_only=true にしてください。")
    sample_markers = _detect_distributed_sample_markers(payload)
    if not sample_only and sample_markers:
        raise InputValidationError(
            "実入力に配布サンプルの合成マーカーが残っています: " + ", ".join(sample_markers)
        )
    timing_spec = _require_mapping(payload.get("timing"), "timing")
    session_context = validate_session_context(timing_spec, as_of_jst)
    nt_spec = _require_mapping(payload.get("nt"), "nt")
    market_data_freshness = validate_market_data_freshness(
        _require_mapping(payload.get("market_data_freshness"), "market_data_freshness"),
        nt_spec,
        session_context,
        as_of_jst,
    )
    daily_spec = _require_mapping(payload.get("daily_forecast"), "daily_forecast")
    daily_forecast = calculate_daily_forecast(daily_spec, as_of_jst, session_context)
    publication_calendar_by_date = {
        row["date"]: row
        for row in market_data_freshness["jquants_publication_calendar"]
    }
    for daily_calendar_row in daily_forecast["calendar_sessions"]:
        publication_calendar_row = publication_calendar_by_date.get(daily_calendar_row["date"])
        if publication_calendar_row is not None and (
            publication_calendar_row["cash_is_trading_day"]
            != daily_calendar_row["cash_is_trading_day"]
            or publication_calendar_row["holiday_division"]
            != daily_calendar_row["holiday_division"]
        ):
            raise InputValidationError(
                "J-Quants公表営業日カレンダーをdaily_forecast.calendar_sessionsと一致させてください。"
            )
    daily_days = _require_sequence(daily_spec.get("days"), "daily_forecast.days")
    d5_input = _require_mapping(daily_days[-1], "daily_forecast.days[4]")
    d5_end_jst = _parse_jst(
        d5_input.get("target_at_jst"),
        "daily_forecast D5 target_at_jst",
    )
    timing = calculate_timing(
        timing_spec,
        as_of_jst,
        d5_end_jst,
        session_context,
        _parse_jst(
            market_data_freshness["execution_anchor_valid_until_jst"],
            "market_data_freshness.execution_anchor_valid_until_jst",
        ),
    )
    if timing["board_entry_status"] != "valid":
        raise InputValidationError("入口板が失効しています。両脚板を再取得してから再計算してください。")
    forecast_horizon = _parse_jst(timing["forecast_valid_until_jst"], "timing.forecast_valid_until_jst")
    probability = calculate_probabilities(
        _require_mapping(payload.get("probability"), "probability"),
        as_of_jst,
        _require_mapping(payload.get("calibration", {}), "calibration"),
        forecast_horizon,
    )
    current_session_kind = str(session_context["futures_session_kind"])
    session_sensitive_provenance: list[tuple[str, Mapping[str, Any]]] = [
        ("4象限baseモデル", probability["base_provenance"]),
        ("NT相対価値モデル", probability["relative_value"]["model_provenance"]),
        ("D1〜D5方向ドリフトモデル", daily_forecast["drift_model_provenance"]),
    ]
    session_sensitive_provenance.extend(
        (
            f"シナリオ '{row['scenario_id']}' の条件付き4象限モデル",
            row["conditional_probability_basis"],
        )
        for row in probability["base_scenario_audit"]
    )
    for provenance_name, provenance in session_sensitive_provenance:
        if current_session_kind not in provenance["supported_origin_sessions"]:
            raise InputValidationError(
                f"{provenance_name} は現在の{current_session_kind}を検証済み対象に含めていません。"
            )
    calibration_result = probability["calibration"]
    if (
        calibration_result["status"] == "walk_forward"
        and current_session_kind not in calibration_result["supported_origin_sessions"]
    ):
        raise InputValidationError("ウォークフォワード校正が現在の先物セッションを対象にしていません。")
    if calibration_result["status"] == "walk_forward" and (
        calibration_result["supported_origin_sessions"]
        != probability["base_provenance"]["supported_origin_sessions"]
        or calibration_result["session_path_definition_id"]
        != probability["base_provenance"]["session_path_definition_id"]
    ):
        raise InputValidationError(
            "calibrationの対応セッションとsession pathを現在のbase_provenanceへ完全一致させてください。"
        )
    nt = calculate_nt(nt_spec, probability, as_of_jst)
    last_trading_session_end = _parse_jst(
        nt["last_trading_session_end_jst"],
        "nt.last_trading_session_end_jst",
    )
    if forecast_horizon > last_trading_session_end:
        raise InputValidationError("4象限の予測有効期限を共通限月の最終取引可能時刻以前にしてください。")
    d5_target = _parse_jst(daily_forecast["nikkei"][-1]["target_at_jst"], "D5 target_at_jst")
    if d5_target > last_trading_session_end:
        raise InputValidationError("D5対象時刻まで取引可能な共通限月を選択してください。")
    if nt["oldest_snapshot_jst"] != _format_jst(
        _parse_jst(timing_spec.get("board_snapshot_jst"), "timing.board_snapshot_jst")
    ):
        raise InputValidationError("timing.board_snapshot_jst を両脚の古い方の板時刻と一致させてください。")
    if _positive_decimal(daily_spec.get("nikkei_anchor"), "daily_forecast.nikkei_anchor") != Decimal(
        nt["nikkei_mid"]
    ):
        raise InputValidationError("daily_forecast.nikkei_anchor を現在の日経先物中値と一致させてください。")
    if _positive_decimal(daily_spec.get("topix_anchor"), "daily_forecast.topix_anchor") != Decimal(nt["topix_mid"]):
        raise InputValidationError("daily_forecast.topix_anchor を現在のTOPIX先物中値と一致させてください。")
    coverage = validate_coverage(_require_mapping(payload.get("coverage"), "coverage"), as_of_jst)
    coverage_items = coverage["items"]
    other_sources = validate_other_sources(
        _require_mapping(payload.get("other_sources"), "other_sources"),
        as_of_jst,
    )
    session_source_id = timing["session_schedule_source_id"]
    session_source = other_sources.get(session_source_id)
    if session_source is None:
        raise InputValidationError("timing.session_schedule_source_id をother_sourcesへ登録してください。")
    if session_source["source_url_or_document_id"] != timing["session_schedule_source_url"]:
        raise InputValidationError("先物セッション予定のURLをother_sources登録値と一致させてください。")
    _validate_source_link(
        "other",
        session_source_id,
        timing["session_checked_at_jst"],
        {},
        other_sources,
        as_of_jst,
        "先物セッション予定",
    )
    selected_market_sources = {
        row["selected_source_id"]
        for row in market_data_freshness["source_selection_audit"]
        if row["purpose"] in ("execution_anchor", "current_price_context")
    }
    candidate_market_sources = {
        source_id
        for row in market_data_freshness["source_selection_audit"]
        if row["purpose"] in ("execution_anchor", "current_price_context")
        for source_id in row["candidate_source_ids"]
    }
    for coverage_item in PRICE_COVERAGE_ITEMS:
        coverage_row = coverage_items[coverage_item]
        if coverage_row["status"] not in ("used", "excluded"):
            continue
        coverage_data_as_of = _parse_jst(
            coverage_row["data_as_of_jst"],
            f"coverage.{coverage_item}.data_as_of_jst",
        )
        registry_observation_times: list[datetime] = []
        for source_id in coverage_row["source_ids"]:
            registry_row = market_data_freshness["source_registry"].get(source_id)
            if registry_row is None or source_id not in candidate_market_sources:
                raise InputValidationError(
                    f"coverage.{coverage_item} の価格source '{source_id}' をmarket_data_freshnessの候補へ登録してください。"
                )
            if coverage_item not in registry_row["coverage_items"]:
                raise InputValidationError(
                    f"coverage.{coverage_item} のsource '{source_id}' をsource_registry.coverage_itemsへ結び付けてください。"
                )
            if coverage_row["status"] == "used" and (
                registry_row["status"] != "valid" or source_id not in selected_market_sources
            ):
                raise InputValidationError(
                    f"coverage.{coverage_item} の利用source '{source_id}' はvalidな選択済み価格sourceにしてください。"
                )
            registry_data_as_of = _parse_jst(
                registry_row["data_as_of_jst"],
                f"価格source '{source_id}' data_as_of_jst",
            )
            registry_observation_times.append(registry_data_as_of)
        if registry_observation_times and coverage_data_as_of != max(registry_observation_times):
            raise InputValidationError(
                f"coverage.{coverage_item}.data_as_of_jstを全sourceの最新観測時刻と一致させてください。"
            )
    for update_row in market_data_freshness["jquants_dataset_updates"]:
        publication_source_id = update_row["publication_schedule_source_id"]
        if publication_source_id not in other_sources:
            raise InputValidationError(
                f"J-Quants更新 '{update_row['dataset_id']}' のpublication_schedule_source_idをother_sourcesへ登録してください。"
            )
        _validate_source_link(
            "other",
            publication_source_id,
            update_row["update_checked_at_jst"],
            {},
            other_sources,
            as_of_jst,
            f"J-Quants更新 '{update_row['dataset_id']}' の公表予定",
        )
    publication_calendar_source_id = market_data_freshness[
        "jquants_publication_calendar_source_id"
    ]
    if publication_calendar_source_id is not None:
        if publication_calendar_source_id not in other_sources:
            raise InputValidationError(
                "jquants_publication_calendar_source_idをother_sourcesへ登録してください。"
            )
        _validate_source_link(
            "other",
            publication_calendar_source_id,
            market_data_freshness["jquants_publication_calendar_checked_at_jst"],
            {},
            other_sources,
            as_of_jst,
            "J-Quants公表営業日カレンダー",
        )
    if coverage_items["jquants"]["status"] == "used":
        calendar_jquants_source_id = (
            daily_forecast["calendar_source_id"]
            if daily_forecast["calendar_coverage_item"] == "jquants"
            else None
        )
        selected_jquants_sources = {
            row["selected_source_id"]
            for row in market_data_freshness["source_selection_audit"]
            if row["purpose"] == "scheduled_history"
        }
        auditable_jquants_sources = {
            row["source_id"]
            for row in market_data_freshness["jquants_dataset_updates"]
            if (
                row["availability_status"] in ("updated", "awaiting_scheduled_update")
                and row["is_latest_available"]
            )
            or (
                row["availability_status"] == "delayed"
                and not row["is_latest_available"]
            )
        }
        invalid_jquants_sources: list[str] = []
        for source_id in coverage_items["jquants"]["source_ids"]:
            if source_id == calendar_jquants_source_id:
                if source_id in market_data_freshness["source_registry"]:
                    invalid_jquants_sources.append(f"{source_id}(取引カレンダーと定時履歴を混同)")
                continue
            registry_row = market_data_freshness["source_registry"].get(source_id)
            if (
                registry_row is None
                or registry_row["provider_family"] != "jquants"
                or registry_row["data_role"] != "scheduled_history"
                or "jquants" not in registry_row["coverage_items"]
                or registry_row["status"] != "valid"
                or source_id not in selected_jquants_sources
                or source_id not in auditable_jquants_sources
            ):
                invalid_jquants_sources.append(source_id)
                continue
        if invalid_jquants_sources:
            raise InputValidationError(
                "coverage.jquantsでusedとした定時履歴sourceをvalid台帳・選択・最新更新監査へ結合できません: "
                + ", ".join(invalid_jquants_sources)
            )
    material_events_in_search_horizon = [
        row
        for row in timing["events"]
        if row["is_material"]
        and row["inside_search_horizon"]
        and _parse_jst(
            row["window_end_jst"],
            f"イベント '{row['event_id']}' window_end_jst",
        )
        >= as_of_jst
    ]
    if not material_events_in_search_horizon and coverage_items["economic_calendar"]["status"] == "used":
        calendar_evidence = [
            row
            for row in probability["evidence_audit"]
            if row["coverage_item"] == "economic_calendar"
        ]
        if not calendar_evidence or any(
            not row["neutral_observation"]
            or any(score != 0.0 for score in row["category_scores"].values())
            for row in calendar_evidence
        ):
            raise InputValidationError(
                "D5まで重要イベントが0件の場合、economic_calendarの確認結果は全0かつneutral_observation=trueの証拠にしてください。"
            )

    daily_targets = [
        (_parse_jst(row["target_at_jst"], f"{row['label']} target_at_jst"), row["label"])
        for row in daily_forecast["nikkei"]
    ]
    assigned_event_ids: dict[str, set[str]] = {label: set() for _, label in daily_targets}
    required_material_event_ids: dict[str, set[str]] = {label: set() for _, label in daily_targets}
    for event_row in timing["events"]:
        scheduled_at = _parse_jst(event_row["scheduled_at_jst"], f"イベント '{event_row['event_id']}' scheduled_at_jst")
        assigned_label = next((label for target, label in daily_targets if scheduled_at <= target), None)
        if assigned_label is None or scheduled_at <= as_of_jst:
            continue
        normalized_event_id = event_row["event_id"].casefold()
        assigned_event_ids[assigned_label].add(normalized_event_id)
        if event_row["is_material"]:
            required_material_event_ids[assigned_label].add(normalized_event_id)
    for nikkei_day, topix_day in zip(daily_forecast["nikkei"], daily_forecast["topix"]):
        if nikkei_day["major_events"] != topix_day["major_events"]:
            raise InputValidationError(f"{nikkei_day['label']}の日経・TOPIX major_events を一致させてください。")
        provided_ids = {event_id.casefold() for event_id in nikkei_day["major_events"]}
        unknown_ids = provided_ids - required_material_event_ids[nikkei_day["label"]]
        missing_ids = required_material_event_ids[nikkei_day["label"]] - provided_ids
        if unknown_ids:
            raise InputValidationError(
                f"{nikkei_day['label']} major_events に当該予測点へ割り当てられないevent_idがあります: "
                + ", ".join(sorted(unknown_ids))
            )
        if missing_ids:
            raise InputValidationError(
                f"{nikkei_day['label']} major_events に重要event_idが不足しています: "
                + ", ".join(sorted(missing_ids))
            )

    evidence_by_key = {
        row["evidence_key"].casefold(): row
        for row in probability["evidence_audit"]
    }
    events_by_key = {
        row["event_id"].casefold(): row
        for row in timing["events"]
    }
    relative_attributions = probability["relative_value"]["attributions"]
    _validate_evidence_attribution_references(
        relative_attributions["fundamental"],
        "fundamental",
        evidence_by_key,
        "probability.relative_value.attributions.fundamental",
    )
    _validate_evidence_attribution_references(
        relative_attributions["supply_demand"],
        "supply_demand",
        evidence_by_key,
        "probability.relative_value.attributions.supply_demand",
    )
    _validate_event_attribution_references(
        relative_attributions["event"],
        events_by_key,
        "probability.relative_value.attributions.event",
        as_of_jst,
        forecast_horizon,
    )
    for index_name in ("nikkei", "topix"):
        for day_row in daily_forecast[index_name]:
            drift_attributions = day_row["drift_attributions"]
            _validate_evidence_attribution_references(
                drift_attributions["fundamental"],
                "fundamental",
                evidence_by_key,
                f"{index_name} {day_row['label']} drift_attributions.fundamental",
            )
            _validate_evidence_attribution_references(
                drift_attributions["supply_demand"],
                "supply_demand",
                evidence_by_key,
                f"{index_name} {day_row['label']} drift_attributions.supply_demand",
            )
            _validate_event_attribution_references(
                drift_attributions["event"],
                events_by_key,
                f"{index_name} {day_row['label']} drift_attributions.event",
            )
            _validate_event_attribution_references(
                [
                    {"event_id": event_id}
                    for event_id in day_row["event_variance_event_ids"]
                ],
                events_by_key,
                f"{index_name} {day_row['label']} event_variance_event_ids",
            )

    consumed_coverage_sources: set[tuple[str, str]] = set()
    consumed_coverage_source_times: dict[tuple[str, str], set[str]] = {}
    for event_row in timing["events"]:
        coverage_item = event_row["coverage_item"]
        _validate_source_link(
            coverage_item,
            event_row["source_id"],
            event_row["checked_at_jst"],
            coverage_items,
            other_sources,
            as_of_jst,
            f"イベント '{event_row['event_id']}'",
        )
        if coverage_item == "other":
            registered_reference = other_sources[event_row["source_id"]]["source_url_or_document_id"]
            if event_row["source_url_or_document_id"] != registered_reference:
                raise InputValidationError(
                    f"イベント '{event_row['event_id']}' の原典を other_sources.{event_row['source_id']} と一致させてください。"
                )
        if coverage_item != "other":
            consumed_key = (coverage_item, event_row["source_id"])
            consumed_coverage_sources.add(consumed_key)
            consumed_coverage_source_times.setdefault(consumed_key, set()).add(event_row["checked_at_jst"])
    for evidence_row in probability["evidence_audit"]:
        coverage_item = evidence_row["coverage_item"]
        _validate_source_link(
            coverage_item,
            evidence_row["source_id"],
            evidence_row["observed_at_jst"],
            coverage_items,
            other_sources,
            as_of_jst,
            f"証拠 '{evidence_row['evidence_key']}'",
        )
        if coverage_item != "other" and evidence_row["effective_multiplier"] >= 1e-6:
            consumed_key = (coverage_item, evidence_row["source_id"])
            consumed_coverage_sources.add(consumed_key)
            consumed_coverage_source_times.setdefault(consumed_key, set()).add(evidence_row["observed_at_jst"])
    for scenario_row in probability["base_scenario_audit"]:
        coverage_item = scenario_row["coverage_item"]
        _validate_source_link(
            coverage_item,
            scenario_row["source_id"],
            scenario_row["observed_at_jst"],
            coverage_items,
            other_sources,
            as_of_jst,
            f"シナリオ '{scenario_row['scenario_id']}'",
        )
        if coverage_item != "other" and scenario_row["weight"] > 0.0:
            consumed_key = (coverage_item, scenario_row["source_id"])
            consumed_coverage_sources.add(consumed_key)
            consumed_coverage_source_times.setdefault(consumed_key, set()).add(scenario_row["observed_at_jst"])
        for source_link in scenario_row["conditional_probability_basis"]["source_links"]:
            conditional_coverage_item = source_link["coverage_item"]
            _validate_source_link(
                conditional_coverage_item,
                source_link["source_id"],
                source_link["data_as_of_jst"],
                coverage_items,
                other_sources,
                as_of_jst,
                f"シナリオ '{scenario_row['scenario_id']}' の条件付き4象限モデル",
            )
            if conditional_coverage_item != "other":
                consumed_key = (conditional_coverage_item, source_link["source_id"])
                consumed_coverage_sources.add(consumed_key)
                consumed_coverage_source_times.setdefault(consumed_key, set()).add(
                    source_link["data_as_of_jst"]
                )
    for source_link in probability["base_provenance"]["source_links"]:
        coverage_item = source_link["coverage_item"]
        _validate_source_link(
            coverage_item,
            source_link["source_id"],
            source_link["data_as_of_jst"],
            coverage_items,
            other_sources,
            as_of_jst,
            "4象限baseモデル",
        )
        if coverage_item != "other":
            consumed_key = (coverage_item, source_link["source_id"])
            consumed_coverage_sources.add(consumed_key)
            consumed_coverage_source_times.setdefault(consumed_key, set()).add(source_link["data_as_of_jst"])
    relative_value = probability["relative_value"]
    for source_group_name in ("model_provenance", "spread_vol_source_links"):
        source_links = (
            relative_value[source_group_name]["source_links"]
            if source_group_name == "model_provenance"
            else relative_value[source_group_name]
        )
        for source_link in source_links:
            coverage_item = source_link["coverage_item"]
            _validate_source_link(
                coverage_item,
                source_link["source_id"],
                source_link["data_as_of_jst"],
                coverage_items,
                other_sources,
                as_of_jst,
                "NT相対価値モデル" if source_group_name == "model_provenance" else "NT相対σ",
            )
            if coverage_item != "other":
                consumed_key = (coverage_item, source_link["source_id"])
                consumed_coverage_sources.add(consumed_key)
                consumed_coverage_source_times.setdefault(consumed_key, set()).add(source_link["data_as_of_jst"])
    calendar_coverage_item = daily_forecast["calendar_coverage_item"]
    _validate_source_link(
        calendar_coverage_item,
        daily_forecast["calendar_source_id"],
        daily_forecast["calendar_data_as_of_jst"],
        coverage_items,
        other_sources,
        as_of_jst,
        "D1〜D5営業日カレンダー",
    )
    if calendar_coverage_item != "other":
        consumed_key = (calendar_coverage_item, daily_forecast["calendar_source_id"])
        consumed_coverage_sources.add(consumed_key)
        consumed_coverage_source_times.setdefault(consumed_key, set()).add(
            daily_forecast["calendar_data_as_of_jst"]
        )
    for source_link in daily_forecast["drift_model_provenance"]["source_links"]:
        coverage_item = source_link["coverage_item"]
        _validate_source_link(
            coverage_item,
            source_link["source_id"],
            source_link["data_as_of_jst"],
            coverage_items,
            other_sources,
            as_of_jst,
            "D1〜D5方向ドリフトモデル",
        )
        if coverage_item != "other":
            consumed_key = (coverage_item, source_link["source_id"])
            consumed_coverage_sources.add(consumed_key)
            consumed_coverage_source_times.setdefault(consumed_key, set()).add(source_link["data_as_of_jst"])
    for index_name in ("nikkei", "topix"):
        for day_row in daily_forecast[index_name]:
            for source_link in day_row["expected_move_source_links"]:
                coverage_item = source_link["coverage_item"]
                _validate_source_link(
                    coverage_item,
                    source_link["source_id"],
                    source_link["data_as_of_jst"],
                    coverage_items,
                    other_sources,
                    as_of_jst,
                    f"{index_name} {day_row['label']} の変動幅ソース",
                )
                if coverage_item != "other":
                    consumed_key = (coverage_item, source_link["source_id"])
                    consumed_coverage_sources.add(consumed_key)
                    consumed_coverage_source_times.setdefault(consumed_key, set()).add(
                        source_link["data_as_of_jst"]
                    )
    unconsumed_used_sources = [
        (item_name, source_id)
        for item_name in COVERAGE_ITEMS
        if coverage_items[item_name]["status"] == "used"
        for source_id in coverage_items[item_name]["source_ids"]
        if (item_name, source_id) not in consumed_coverage_sources
    ]
    if unconsumed_used_sources:
        raise InputValidationError(
            "coverageでusedとしたsourceに証拠・イベント・シナリオ・条件付きモデル・方向ドリフト・分散・営業日からの利用先がありません: "
            + ", ".join(f"{item_name}:{source_id}" for item_name, source_id in unconsumed_used_sources)
        )
    for item_name in COVERAGE_ITEMS:
        if coverage_items[item_name]["status"] != "used":
            continue
        observation_times = [
            _parse_jst(observed_at, f"coverage.{item_name} の消費時刻")
            for source_id in coverage_items[item_name]["source_ids"]
            for observed_at in consumed_coverage_source_times[(item_name, source_id)]
        ]
        coverage_data_as_of = _parse_jst(
            coverage_items[item_name]["data_as_of_jst"],
            f"coverage.{item_name}.data_as_of_jst",
        )
        if coverage_data_as_of != max(observation_times):
            raise InputValidationError(
                f"coverage.{item_name}.data_as_of_jstを全利用先の最新観測時刻と一致させてください。"
            )
    for (coverage_item, source_id), observed_times in consumed_coverage_source_times.items():
        if coverage_item not in PRICE_COVERAGE_ITEMS and coverage_item != "jquants":
            continue
        registry_row = market_data_freshness["source_registry"].get(source_id)
        if registry_row is None:
            continue
        registry_time = _parse_jst(
            registry_row["data_as_of_jst"],
            f"market_data_freshness.source_registry.{source_id}.data_as_of_jst",
        )
        mismatched_times = [
            observed_at
            for observed_at in observed_times
            if _parse_jst(observed_at, f"{coverage_item}:{source_id} の利用時刻") != registry_time
        ]
        if mismatched_times:
            raise InputValidationError(
                f"coverage.{coverage_item} のsource '{source_id}' は利用先時刻をsource_registryの観測時刻と一致させてください。"
            )
    runtime_now_jst = _runtime_current_jst()
    analysis_age_seconds = (runtime_now_jst - as_of_jst).total_seconds()
    board_entry_valid_until = _parse_jst(
        timing["board_entry_valid_until_jst"],
        "timing.board_entry_valid_until_jst",
    )
    analysis_hold_reasons: list[str] = []
    if analysis_age_seconds < -RUNTIME_FUTURE_SKEW_SECONDS:
        analysis_hold_reasons.append(
            f"分析基準時刻がOS実時計より{int(RUNTIME_FUTURE_SKEW_SECONDS)}秒超未来"
        )
    elif analysis_age_seconds > RUNTIME_AS_OF_MAX_AGE_SECONDS:
        analysis_hold_reasons.append(
            f"分析基準時刻がOS実時計から{int(RUNTIME_AS_OF_MAX_AGE_SECONDS)}秒超古い"
        )
    if runtime_now_jst >= board_entry_valid_until:
        analysis_hold_reasons.append("OS実時計で入口板の絶対有効期限を経過")
    if runtime_now_jst >= forecast_horizon:
        analysis_hold_reasons.append("OS実時計で予測の絶対有効期限を経過")
    if not coverage["summary"]["analysis_gate_passed"]:
        analysis_hold_reasons.append("市場横断データの分析ゲート未達")
    delayed_jquants_sources = sorted(
        row["source_id"]
        for row in market_data_freshness["jquants_dataset_updates"]
        if row["availability_status"] == "delayed"
        and row["source_id"]
        in {
            selection["selected_source_id"]
            for selection in market_data_freshness["source_selection_audit"]
            if selection["purpose"] == "scheduled_history"
        }
    )
    if delayed_jquants_sources:
        analysis_hold_reasons.append(
            "J-Quants定時履歴の公式公表遅延: "
            + ", ".join(delayed_jquants_sources)
        )
    if not probability["calibration"]["probability_wording_allowed"]:
        analysis_hold_reasons.append("期限一致のウォークフォワード校正ゲート未達")
    if probability["winner_tie"]:
        analysis_hold_reasons.append("4象限の生モデル首位が同率")
    selected_position = nt.get("selected_position")
    if isinstance(selected_position, Mapping) and not selected_position.get("local_position_gate_passed", False):
        analysis_hold_reasons.append(selected_position.get("warning") or "NT採用条件未達")
    if sample_only:
        analysis_hold_reasons.append("合成入力例のため分析候補外")
    evidence_completeness_pct = round(
        40.0 + 60.0 * coverage["summary"]["used_count"] / len(COVERAGE_ITEMS),
        1,
    )
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if sample_only:
        status = "sample_only"
    elif timing["status"] == "hold" or analysis_hold_reasons:
        status = "hold"
    else:
        status = "ok"
    return {
        "schema_version": SCHEMA_VERSION,
        "model_version": MODEL_VERSION,
        "status": status,
        "sample_only": sample_only,
        "analysis_hold_reasons": analysis_hold_reasons,
        "as_of_jst": _format_jst(as_of_jst),
        "timing": timing,
        "quadrants": probability,
        "nt": nt,
        "daily_forecast": daily_forecast,
        "market_data_freshness": market_data_freshness,
        "coverage": coverage,
        "coverage_consumed_sources": [
            {
                "coverage_item": item_name,
                "source_id": source_id,
                "observed_at_jst": sorted(consumed_coverage_source_times[(item_name, source_id)]),
            }
            for item_name, source_id in sorted(consumed_coverage_sources)
        ],
        "other_sources": other_sources,
        "evidence_completeness_pct": evidence_completeness_pct,
        "evidence_completeness_definition": "ファンダ・需給両ブロック40点＋必須市場横断12項目の利用率60点。確率ではない。",
        "audit": {
            "input_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "network_accessed_by_calculator": False,
            "runtime_clock_source": "OS実時計",
            "runtime_now_jst": _format_jst(runtime_now_jst),
            "analysis_age_seconds": round(analysis_age_seconds, 3),
            "runtime_as_of_max_age_seconds": RUNTIME_AS_OF_MAX_AGE_SECONDS,
            "runtime_future_skew_seconds": RUNTIME_FUTURE_SKEW_SECONDS,
            "validation_scope": "入力形式・OS実時計に対する現在性・時刻関係・数理不変条件",
        },
    }


# ----------------------------------------


def _read_payload(input_path: str | None) -> Mapping[str, Any]:
    """
    機能:
        指定ファイルまたは標準入力からJSONペイロードを読み込む。

    引数:
        input_path (str | None): 入力ファイルパス。未指定時は標準入力を使う。

    返り値:
        Mapping[str, Any]: 解析済みJSONオブジェクト。
    """
    try:
        if input_path:
            content = Path(input_path).read_text(encoding="utf-8")
        else:
            stdin_buffer = getattr(sys.stdin, "buffer", None)
            if stdin_buffer is None:
                content = sys.stdin.read()
            else:
                content = stdin_buffer.read().decode("utf-8-sig")
        parsed = json.loads(content)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InputValidationError(f"JSON入力を読み込めません: {exc}") from exc
    return _require_mapping(parsed, "入力JSON")


# ----------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """
    機能:
        コマンドライン引数を解析し、計算結果を標準出力へJSONで返す。

    引数:
        argv (Sequence[str] | None): コマンドライン引数。未指定時は実行環境の引数を使う。

    返り値:
        int: 正常終了は0、入力不正は2。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")

    # ----------------------------------------

    parser = argparse.ArgumentParser(description="日経225・TOPIX・NT予測の純計算を検証します。")
    parser.add_argument("--input", help="UTF-8 JSON入力ファイル。省略時は標準入力を使います。")
    parser.add_argument("--pretty", action="store_true", help="出力JSONをインデントします。")
    args = parser.parse_args(argv)
    try:
        result = calculate_all(_read_payload(args.input))
    except InputValidationError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    indent = 2 if args.pretty else None
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=indent))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
