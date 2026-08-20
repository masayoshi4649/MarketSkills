#!/usr/bin/env python3
"""index_nt_calculatorの主要不変条件を検証する。"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import subprocess
import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from index_nt_calculator import (
    InputValidationError,
    _derive_daily_jquants_expected_state,
    _derive_weekly_jquants_arrival_state,
    _validate_weekly_publication_schedule,
    bivariate_normal_cdf,
    calculate_all,
)


def build_payload() -> dict:
    """
    機能:
        テストで共用する合成入力を作成する。

    引数:
        なし。

    返り値:
        dict: 4象限、時刻、NT、D1〜D5を含む入力JSON相当辞書。
    """
    days = []
    dates = ("2026-08-21", "2026-08-24", "2026-08-25", "2026-08-26", "2026-08-27")
    for index, target_date in enumerate(dates, start=1):
        days.append(
            {
                "label": f"D{index}",
                "target_at_jst": f"{target_date}T15:30:00+09:00",
                "major_events": ["test-event"] if index == 1 else [],
                "nikkei": {
                    "fundamental_drift_bps": 0,
                    "supply_demand_drift_bps": 0,
                    "event_drift_bps": 0,
                    "drift_attributions": {
                        "fundamental": [],
                        "supply_demand": [],
                        "event": [],
                    },
                    "incremental_base_sigma_pct": 1.0,
                    "incremental_event_sigma_pct": 0.1 if index == 1 else 0,
                    "event_variance_event_ids": ["test-event"] if index == 1 else [],
                    "expected_move_source": "試験用固定増分分散",
                    "expected_move_source_links": [
                        {
                            "coverage_item": "other",
                            "source_id": "fixture-daily-volatility",
                            "data_as_of_jst": "2026-08-20T09:55:00+09:00",
                        }
                    ],
                },
                "topix": {
                    "fundamental_drift_bps": 0,
                    "supply_demand_drift_bps": 0,
                    "event_drift_bps": 0,
                    "drift_attributions": {
                        "fundamental": [],
                        "supply_demand": [],
                        "event": [],
                    },
                    "incremental_base_sigma_pct": 0.8,
                    "incremental_event_sigma_pct": 0.1 if index == 1 else 0,
                    "event_variance_event_ids": ["test-event"] if index == 1 else [],
                    "expected_move_source": "試験用固定増分分散",
                    "expected_move_source_links": [
                        {
                            "coverage_item": "other",
                            "source_id": "fixture-daily-volatility",
                            "data_as_of_jst": "2026-08-20T09:55:00+09:00",
                        }
                    ],
                },
            }
        )

    # ----------------------------------------

    publication_calendar = []
    publication_calendar_start = date(2026, 7, 27)
    for offset in range(49):
        calendar_date = publication_calendar_start + timedelta(days=offset)
        is_mountain_day = calendar_date == date(2026, 8, 11)
        cash_is_trading_day = calendar_date.weekday() < 5 and not is_mountain_day
        publication_calendar.append(
            {
                "date": calendar_date.isoformat(),
                "cash_is_trading_day": cash_is_trading_day,
                "holiday_division": (
                    "1" if cash_is_trading_day else "3" if is_mountain_day else "0"
                ),
            }
        )
    return {
        "schema_version": "6.0.0",
        "sample_only": False,
        "as_of_jst": "2026-08-20T10:00:00+09:00",
        "timing": {
            "cash_market_state": "open",
            "futures_session_kind": "day_session",
            "futures_session_phase": "regular",
            "exchange_trading_day": "2026-08-20",
            "holiday_division": "1",
            "cash_market_holiday_division": "1",
            "is_derivatives_holiday_trading": False,
            "session_calendar": [
                {
                    "date": "2026-08-20",
                    "cash_is_trading_day": True,
                    "derivatives_session_open": True,
                    "holiday_division": "1",
                }
            ],
            "session_start_jst": "2026-08-20T08:45:00+09:00",
            "continuous_end_jst": "2026-08-20T15:40:00+09:00",
            "session_end_jst": "2026-08-20T15:45:00+09:00",
            "next_session_start_jst": "2026-08-20T17:00:00+09:00",
            "session_schedule_source_id": "fixture-jpx-session-schedule",
            "session_schedule_source_url": "fixture://jpx/session-schedule",
            "session_checked_at_jst": "2026-08-20T09:50:00+09:00",
            "board_snapshot_jst": "2026-08-20T09:59:05+09:00",
            "board_ttl_minutes": 30,
            "provider_expires_jst": "2026-08-20T10:29:00+09:00",
            "model_refresh_jst": "2026-08-20T15:40:00+09:00",
            "materiality_threshold": 0.35,
            "events": [
                {
                    "event_id": "test-event",
                    "title": "試験用重要イベント",
                    "coverage_item": "other",
                    "source_id": "fixture-calendar",
                    "source_url_or_document_id": "fixture://calendar/test-event",
                    "source_timezone": "Asia/Tokyo",
                    "reference_period": "試験期間",
                    "scheduled_at_source": "2026-08-20T12:30:00+09:00",
                    "scheduled_at_jst": "2026-08-20T12:30:00+09:00",
                    "previous_value": "not_applicable",
                    "official_source": "fixture://calendar/test-event",
                    "checked_at_jst": "2026-08-20T09:50:00+09:00",
                    "release_status": "scheduled",
                    "consensus_status": "not_applicable",
                    "window_start_jst": "2026-08-20T12:30:00+09:00",
                    "window_end_jst": "2026-08-20T12:45:00+09:00",
                    "safety_buffer_minutes": 15,
                    "occurrence_probability": 1.0,
                    "impact_score": 4,
                    "relevance": 1.0,
                    "source_quality": 1.0,
                }
            ],
        },
        "probability": {
            "model_version": "fixture-model-v1",
            "horizon_end_jst": "2026-08-20T12:15:00+09:00",
            "base_provenance": {
                "base_method": "joint_normal",
                "model_id": "fixture-joint-normal-v1",
                "model_version": "fixture-model-v1",
                "calibration_version": "fixture-calibration-v1",
                "method": "weighted_bivariate_state_model",
                "model_structure_id": "fixture-joint-normal-evidence-v1",
                "model_artifact_sha256": "98b2ced0327fbb1d7d72b52cbca4c49ecee788ddd42eed6ad584cd035faeed6b",
                "training_data_cutoff_jst": "2026-08-19T23:59:00+09:00",
                "horizon_definition": "forecast_valid_until_midpoint_direction",
                "supported_origin_sessions": ["day_session", "night_session"],
                "session_path_definition_id": "current_continuous_session_no_recess_crossing_v1",
                "horizon_seconds": 8100,
                "prediction_output_sha256": "e1012aeeda9dd78025b1e070fac230b0a27f37474039dd8b90a14845780c10e0",
                "validation_passed": False,
                "source_links": [
                    {
                        "coverage_item": "other",
                        "source_id": "fixture-base-model",
                        "data_as_of_jst": "2026-08-20T09:58:00+09:00",
                    }
                ],
            },
            "base": {
                "joint_normal": {
                    "nikkei_mean_pct": 0,
                    "topix_mean_pct": 0,
                    "nikkei_vol_pct": 1.0,
                    "topix_vol_pct": 0.8,
                    "correlation": 0,
                }
            },
            "dominant_category": "nk_up_topix_up",
            "relative_value": {
                "fundamental_spread_mean_bps": 0,
                "supply_demand_spread_mean_bps": 0,
                "event_spread_mean_bps": 0,
                "spread_vol_pct": 0.5,
                "round_trip_cost_pct": 0.05,
                "attributions": {
                    "fundamental": [],
                    "supply_demand": [],
                    "event": [],
                },
                "model_provenance": {
                    "model_id": "fixture-relative-value-v1",
                    "model_version": "fixture-relative-value-v1",
                    "calibration_version": "fixture-relative-calibration-v1",
                    "method": "weighted_spread_state_model",
                    "model_structure_id": "fixture-relative-structure-v1",
                    "model_artifact_sha256": "d15096a18f5f6abbb6ddf5680de678e8c80536839d6f7b8514edd016907f42b4",
                    "training_data_cutoff_jst": "2026-08-19T23:59:00+09:00",
                    "horizon_definition": "forecast_valid_until_nt_spread_return",
                    "supported_origin_sessions": ["day_session", "night_session"],
                    "session_path_definition_id": "current_continuous_session_no_recess_crossing_v1",
                    "horizon_seconds": 8100,
                    "validation_passed": False,
                    "source_links": [
                        {
                            "coverage_item": "other",
                            "source_id": "fixture-relative-model",
                            "data_as_of_jst": "2026-08-20T09:58:00+09:00",
                        }
                    ],
                },
                "spread_vol_source_links": [
                    {
                        "coverage_item": "other",
                        "source_id": "fixture-daily-volatility",
                        "data_as_of_jst": "2026-08-20T09:55:00+09:00",
                    }
                ],
            },
            "evidence": [
                {
                    "evidence_key": "fundamental-neutral",
                    "block": "fundamental",
                    "coverage_item": "other",
                    "source_id": "fixture-fundamental",
                    "fact": "試験用の中立ファンダメンタルズ観測",
                    "inference": "4象限へ方向差を付けない",
                    "counterevidence": "試験入力のため実市場反証なし",
                    "category_scores": {
                        "nk_up_topix_up": 0,
                        "nk_up_topix_down": 0,
                        "nk_down_topix_up": 0,
                        "nk_down_topix_down": 0,
                    },
                    "quality": 1,
                    "independence": 1,
                    "observed_at_jst": "2026-08-20T09:55:00+09:00",
                    "half_life_hours": 24,
                    "neutral_observation": True,
                },
                {
                    "evidence_key": "supply-neutral",
                    "block": "supply_demand",
                    "coverage_item": "other",
                    "source_id": "fixture-supply",
                    "fact": "試験用の中立需給観測",
                    "inference": "4象限へ方向差を付けない",
                    "counterevidence": "試験入力のため実市場反証なし",
                    "category_scores": {
                        "nk_up_topix_up": 0,
                        "nk_up_topix_down": 0,
                        "nk_down_topix_up": 0,
                        "nk_down_topix_down": 0,
                    },
                    "quality": 1,
                    "independence": 1,
                    "observed_at_jst": "2026-08-20T09:59:00+09:00",
                    "half_life_hours": 2,
                    "neutral_observation": True,
                },
            ],
        },
        "calibration": {
            "status": "uncalibrated",
            "effective_sample_count": 0,
        },
        "nt": {
            "nikkei_symbol": "FIXTURE-NK225M-202609",
            "nikkei_board_source_id": "fixture-kabus-nikkei",
            "nikkei_board_product": "micro",
            "topix_symbol": "FIXTURE-TOPIXM-202609",
            "topix_board_source_id": "fixture-kabus-topix",
            "nikkei_contract_month": "2026-09",
            "topix_contract_month": "2026-09",
            "nikkei_last_trading_day": "2026-09-10",
            "topix_last_trading_day": "2026-09-10",
            "last_trading_session_end_jst": "2026-09-10T15:45:00+09:00",
            "nikkei_snapshot_jst": "2026-08-20T09:59:05+09:00",
            "topix_snapshot_jst": "2026-08-20T09:59:05+09:00",
            "max_snapshot_skew_seconds": 15,
            "nikkei_bid": "39995",
            "nikkei_ask": "40005",
            "nikkei_bid_quantity": 100,
            "nikkei_ask_quantity": 100,
            "topix_bid": "2499.75",
            "topix_ask": "2500.25",
            "topix_bid_quantity": 100,
            "topix_ask_quantity": 100,
            "strategy": "nt_long",
            "selected_position": {
                "nikkei_product": "micro",
                "nikkei_quantity": 6,
                "topix_mini_quantity": 1,
            },
        },
        "daily_forecast": {
            "calendar_verified": True,
            "target_definition_id": "tse_cash_close_15_30",
            "target_venue": "JPX_TSE",
            "target_phase": "cash_close",
            "d1_origin_rule": "day_next_cash_trading_day_or_night_exchange_trading_day",
            "drift_model_provenance": {
                "model_id": "fixture-daily-drift-v1",
                "model_version": "fixture-daily-drift-v1",
                "calibration_version": "fixture-daily-drift-calibration-v1",
                "method": "walk_forward_incremental_drift",
                "model_structure_id": "fixture-daily-drift-structure-v1",
                "model_artifact_sha256": "e46c8c9161bb7c4148f6751c4f8dac51a734a79b9f32d0009825f493ac4356bb",
                "training_data_cutoff_jst": "2026-08-19T23:59:00+09:00",
                "horizon_definition": "next_trading_day_incremental_log_return",
                "supported_origin_sessions": ["day_session", "night_session"],
                "session_path_definition_id": "current_continuous_session_no_recess_crossing_v1",
                "validation_passed": False,
                "source_links": [
                    {
                        "coverage_item": "other",
                        "source_id": "fixture-drift-model",
                        "data_as_of_jst": "2026-08-20T09:58:00+09:00",
                    }
                ],
            },
            "calendar_source": "試験用営業日カレンダー",
            "calendar_coverage_item": "other",
            "calendar_source_id": "fixture-trading-calendar",
            "calendar_data_as_of_jst": "2026-08-20T09:50:00+09:00",
            "calendar_fetched_at_jst": "2026-08-20T09:50:00+09:00",
            "calendar_sessions": [
                {"date": "2026-08-21", "cash_is_trading_day": True, "derivatives_session_open": True, "holiday_division": "1"},
                {"date": "2026-08-22", "cash_is_trading_day": False, "derivatives_session_open": False, "holiday_division": "0"},
                {"date": "2026-08-23", "cash_is_trading_day": False, "derivatives_session_open": False, "holiday_division": "0"},
                {"date": "2026-08-24", "cash_is_trading_day": True, "derivatives_session_open": True, "holiday_division": "1"},
                {"date": "2026-08-25", "cash_is_trading_day": True, "derivatives_session_open": True, "holiday_division": "1"},
                {"date": "2026-08-26", "cash_is_trading_day": True, "derivatives_session_open": True, "holiday_division": "1"},
                {"date": "2026-08-27", "cash_is_trading_day": True, "derivatives_session_open": True, "holiday_division": "1"},
            ],
            "verified_trading_dates": list(dates),
            "nikkei_anchor": "40000",
            "topix_anchor": "2500",
            "nikkei_tick": "5",
            "topix_tick": "0.25",
            "days": days,
        },
        "market_data_freshness": {
            "jquants_publication_calendar_source_id": "fixture-jquants-publication-calendar",
            "jquants_publication_calendar_checked_at_jst": "2026-08-20T09:59:00+09:00",
            "jquants_publication_calendar": publication_calendar,
            "source_registry": {
                "fixture-kabus-nikkei": {
                    "provider_family": "kabus",
                    "data_role": "execution_anchor",
                    "series_key": "nikkei-202609-bid-ask",
                    "instrument_type": "nikkei225_futures",
                    "symbol": "FIXTURE-NK225M-202609",
                    "contract_month": "2026-09",
                    "quote_type": "bid_ask",
                    "status": "valid",
                    "status_reason": "試験用Kabus日経板",
                    "checked_at_jst": "2026-08-20T09:59:10+09:00",
                    "data_as_of_jst": "2026-08-20T09:59:05+09:00",
                    "fetched_at_jst": "2026-08-20T09:59:10+09:00",
                    "max_age_seconds": 120,
                    "kabus_market_code": 2,
                },
                "fixture-kabus-topix": {
                    "provider_family": "kabus",
                    "data_role": "execution_anchor",
                    "series_key": "topix-202609-bid-ask",
                    "instrument_type": "topix_futures",
                    "symbol": "FIXTURE-TOPIXM-202609",
                    "contract_month": "2026-09",
                    "quote_type": "bid_ask",
                    "status": "valid",
                    "status_reason": "試験用Kabus TOPIX板",
                    "checked_at_jst": "2026-08-20T09:59:10+09:00",
                    "data_as_of_jst": "2026-08-20T09:59:05+09:00",
                    "fetched_at_jst": "2026-08-20T09:59:10+09:00",
                    "max_age_seconds": 120,
                    "kabus_market_code": 2,
                },
                "fixture-225225-nikkei": {
                    "provider_family": "225225",
                    "data_role": "near_realtime_context",
                    "series_key": "nikkei-202609-bid-ask",
                    "instrument_type": "nikkei225_futures",
                    "symbol": "FIXTURE-NK225M-202609",
                    "contract_month": "2026-09",
                    "quote_type": "bid_ask",
                    "status": "valid",
                    "status_reason": "試験用225225.jp系補助値",
                    "checked_at_jst": "2026-08-20T09:59:00+09:00",
                    "data_as_of_jst": "2026-08-20T09:58:00+09:00",
                    "fetched_at_jst": "2026-08-20T09:58:05+09:00",
                    "max_age_seconds": 300,
                },
                "fixture-225225-topix": {
                    "provider_family": "225225",
                    "data_role": "near_realtime_context",
                    "series_key": "topix-202609-bid-ask",
                    "instrument_type": "topix_futures",
                    "symbol": "FIXTURE-TOPIXM-202609",
                    "contract_month": "2026-09",
                    "quote_type": "bid_ask",
                    "status": "valid",
                    "status_reason": "試験用225225.jp系補助値",
                    "checked_at_jst": "2026-08-20T09:59:00+09:00",
                    "data_as_of_jst": "2026-08-20T09:58:00+09:00",
                    "fetched_at_jst": "2026-08-20T09:58:05+09:00",
                    "max_age_seconds": 300,
                },
                "fixture-jquants-daily": {
                    "provider_family": "jquants",
                    "data_role": "scheduled_history",
                    "coverage_items": ["jquants"],
                    "dataset_id": "daily-prices",
                    "series_key": "jquants-daily-prices",
                    "instrument_type": "cash_index_history",
                    "symbol": "TOPIX/NK225-history",
                    "contract_month": "not_applicable",
                    "quote_type": "daily_ohlc",
                    "status": "valid",
                    "status_reason": "16:30頃の次回更新前に利用できる前営業日分",
                    "checked_at_jst": "2026-08-20T09:59:00+09:00",
                    "data_as_of_jst": "2026-08-19T16:30:00+09:00",
                    "fetched_at_jst": "2026-08-20T09:59:00+09:00",
                    "max_age_seconds": 86400,
                },
            },
            "source_selection_audit": [
                {
                    "selection_key": "nikkei_execution_anchor",
                    "purpose": "execution_anchor",
                    "series_key": "nikkei-202609-bid-ask",
                    "selected_source_id": "fixture-kabus-nikkei",
                    "candidate_source_ids": ["fixture-kabus-nikkei", "fixture-225225-nikkei"],
                    "selection_reason": "有効な最上位tierのKabus板を選択",
                },
                {
                    "selection_key": "topix_execution_anchor",
                    "purpose": "execution_anchor",
                    "series_key": "topix-202609-bid-ask",
                    "selected_source_id": "fixture-kabus-topix",
                    "candidate_source_ids": ["fixture-kabus-topix", "fixture-225225-topix"],
                    "selection_reason": "有効な最上位tierのKabus板を選択",
                },
                {
                    "selection_key": "jquants_daily_history",
                    "purpose": "scheduled_history",
                    "series_key": "jquants-daily-prices",
                    "selected_source_id": "fixture-jquants-daily",
                    "candidate_source_ids": ["fixture-jquants-daily"],
                    "selection_reason": "定時更新済みの最新利用可能履歴を選択",
                },
            ],
            "jquants_dataset_updates": [
                {
                    "dataset_id": "daily-prices",
                    "source_id": "fixture-jquants-daily",
                    "schedule_profile_id": "daily_prices_1630",
                    "frequency": "daily",
                    "official_update_time_description": "日次16:30頃・時刻非保証",
                    "latest_expected_update_jst": "2026-08-20T16:30:00+09:00",
                    "update_checked_at_jst": "2026-08-20T09:59:00+09:00",
                    "latest_available_published_at_jst": "2026-08-19T16:30:00+09:00",
                    "latest_available_reference_date": "2026-08-19",
                    "expected_latest_reference_date": "2026-08-19",
                    "availability_status": "awaiting_scheduled_update",
                    "is_latest_available": True,
                    "publication_schedule_source_id": "fixture-jquants-update-schedule",
                }
            ],
        },
        "coverage": {
            item: {
                "status": "unavailable",
                "reason": "合成単体テストでは外部データ取得を行わない",
                "checked_at_jst": "2026-08-20T09:58:00+09:00",
            }
            for item in (
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
        },
        "other_sources": {
            "fixture-jquants-publication-calendar": {
                "source_url_or_document_id": "fixture://jquants/publication-calendar",
                "reason": "単体試験用のJ-Quants公表営業日カレンダー",
                "checked_at_jst": "2026-08-20T09:59:00+09:00",
                "data_as_of_jst": "2026-08-20T09:59:00+09:00",
                "max_data_age_hours": 24,
            },
            "fixture-jquants-update-schedule": {
                "source_url_or_document_id": "fixture://jquants/update-schedule",
                "reason": "単体試験用のJ-Quants定時更新予定",
                "checked_at_jst": "2026-08-20T09:59:00+09:00",
                "data_as_of_jst": "2026-08-20T09:59:00+09:00",
                "max_data_age_hours": 24,
            },
            "fixture-jpx-session-schedule": {
                "source_url_or_document_id": "fixture://jpx/session-schedule",
                "reason": "単体試験用のJPX先物セッション予定",
                "checked_at_jst": "2026-08-20T09:50:00+09:00",
                "data_as_of_jst": "2026-08-20T09:50:00+09:00",
                "max_data_age_hours": 24,
            },
            "fixture-calendar": {
                "source_url_or_document_id": "fixture://calendar/test-event",
                "reason": "単体試験用のイベント予定",
                "checked_at_jst": "2026-08-20T09:50:00+09:00",
                "data_as_of_jst": "2026-08-20T09:50:00+09:00",
                "max_data_age_hours": 24,
            },
            "fixture-base-model": {
                "source_url_or_document_id": "fixture://model/base-probability",
                "reason": "単体試験用の4象限baseモデル",
                "checked_at_jst": "2026-08-20T09:58:00+09:00",
                "data_as_of_jst": "2026-08-20T09:58:00+09:00",
                "max_data_age_hours": 24,
            },
            "fixture-drift-model": {
                "source_url_or_document_id": "fixture://model/daily-drift",
                "reason": "単体試験用の日次方向ドリフトモデル",
                "checked_at_jst": "2026-08-20T09:58:00+09:00",
                "data_as_of_jst": "2026-08-20T09:58:00+09:00",
                "max_data_age_hours": 24,
            },
            "fixture-daily-volatility": {
                "source_url_or_document_id": "fixture://model/daily-volatility",
                "reason": "単体試験用の増分分散",
                "checked_at_jst": "2026-08-20T09:58:00+09:00",
                "data_as_of_jst": "2026-08-20T09:55:00+09:00",
                "max_data_age_hours": 24,
            },
            "fixture-fundamental": {
                "source_url_or_document_id": "fixture://evidence/fundamental",
                "reason": "単体試験用のファンダメンタルズ証拠",
                "checked_at_jst": "2026-08-20T09:58:00+09:00",
                "data_as_of_jst": "2026-08-20T09:55:00+09:00",
                "max_data_age_hours": 24,
            },
            "fixture-supply": {
                "source_url_or_document_id": "fixture://evidence/supply",
                "reason": "単体試験用の需給証拠",
                "checked_at_jst": "2026-08-20T09:59:00+09:00",
                "data_as_of_jst": "2026-08-20T09:59:00+09:00",
                "max_data_age_hours": 24,
            },
            "fixture-scenario": {
                "source_url_or_document_id": "fixture://scenario/weights",
                "reason": "単体試験用のシナリオ重み",
                "checked_at_jst": "2026-08-20T09:58:00+09:00",
                "data_as_of_jst": "2026-08-20T09:58:00+09:00",
                "max_data_age_hours": 24,
            },
            "fixture-scenario-conditional": {
                "source_url_or_document_id": "fixture://model/scenario-conditional",
                "reason": "単体試験用のシナリオ条件付き4象限モデル",
                "checked_at_jst": "2026-08-20T09:58:00+09:00",
                "data_as_of_jst": "2026-08-20T09:58:00+09:00",
                "max_data_age_hours": 24,
            },
            "fixture-relative-model": {
                "source_url_or_document_id": "fixture://model/relative-value",
                "reason": "単体試験用のNT相対価値モデル",
                "checked_at_jst": "2026-08-20T09:58:00+09:00",
                "data_as_of_jst": "2026-08-20T09:58:00+09:00",
                "max_data_age_hours": 24,
            },
            "fixture-trading-calendar": {
                "source_url_or_document_id": "fixture://calendar/trading-days",
                "reason": "単体試験用の営業日列",
                "checked_at_jst": "2026-08-20T09:50:00+09:00",
                "data_as_of_jst": "2026-08-20T09:50:00+09:00",
                "max_data_age_hours": 24,
            },
        },
    }


# ----------------------------------------


def configure_night_session_payload(
    payload: dict,
    as_of_jst: str,
    futures_session_phase: str = "regular",
    kabus_market_code: int = 24,
) -> None:
    """
    機能:
        2026年8月20日開始のナイトセッション用に時刻・板鮮度・更新監査を同期する。

    引数:
        payload (dict): build_payloadで作成した入力辞書。
        as_of_jst (str): ナイトセッション内外を試すISO 8601形式の分析基準時刻。
        futures_session_phase (str): regularまたはpre_closingの申告値。
        kabus_market_code (int): 両脚Kabus板へ設定する市場コード。

    返り値:
        None: 入力辞書を更新し、値は返さない。
    """
    as_of = datetime.fromisoformat(as_of_jst)
    session_start = datetime.fromisoformat("2026-08-20T17:00:00+09:00")
    continuous_end = datetime.fromisoformat("2026-08-21T05:55:00+09:00")
    session_end = datetime.fromisoformat("2026-08-21T06:00:00+09:00")
    next_session_start = datetime.fromisoformat("2026-08-21T08:45:00+09:00")
    board_snapshot = as_of - timedelta(seconds=5)
    horizon_end = min(as_of + timedelta(hours=1), continuous_end)
    if horizon_end <= as_of:
        horizon_end = as_of + timedelta(minutes=1)

    payload["as_of_jst"] = as_of.isoformat()
    timing = payload["timing"]
    timing.update(
        {
            "cash_market_state": "closed",
            "futures_session_kind": "night_session",
            "futures_session_phase": futures_session_phase,
            "exchange_trading_day": "2026-08-21",
            "holiday_division": "1",
            "cash_market_holiday_division": "1",
            "is_derivatives_holiday_trading": False,
            "session_calendar": [
                {
                    "date": "2026-08-20",
                    "cash_is_trading_day": True,
                    "derivatives_session_open": True,
                    "holiday_division": "1",
                },
                {
                    "date": "2026-08-21",
                    "cash_is_trading_day": True,
                    "derivatives_session_open": True,
                    "holiday_division": "1",
                },
            ],
            "session_start_jst": session_start.isoformat(),
            "continuous_end_jst": continuous_end.isoformat(),
            "session_end_jst": session_end.isoformat(),
            "next_session_start_jst": next_session_start.isoformat(),
            "session_checked_at_jst": (as_of - timedelta(minutes=1)).isoformat(),
            "board_snapshot_jst": board_snapshot.isoformat(),
            "provider_expires_jst": (as_of + timedelta(minutes=1)).isoformat(),
            "model_refresh_jst": horizon_end.isoformat(),
            "events": [],
        }
    )

    # ----------------------------------------

    payload["probability"]["horizon_end_jst"] = horizon_end.isoformat()
    horizon_seconds = int((horizon_end - as_of).total_seconds())
    payload["probability"]["base_provenance"]["horizon_seconds"] = horizon_seconds
    payload["probability"]["relative_value"]["model_provenance"]["horizon_seconds"] = horizon_seconds
    for day in payload["daily_forecast"]["days"]:
        day["major_events"] = []
        for index_name in ("nikkei", "topix"):
            day[index_name]["event_drift_bps"] = 0
            day[index_name]["drift_attributions"]["event"] = []
            day[index_name]["incremental_event_sigma_pct"] = 0
            day[index_name]["event_variance_event_ids"] = []

    # ----------------------------------------

    payload["nt"]["nikkei_snapshot_jst"] = board_snapshot.isoformat()
    payload["nt"]["topix_snapshot_jst"] = board_snapshot.isoformat()
    registry = payload["market_data_freshness"]["source_registry"]
    for source_id in ("fixture-kabus-nikkei", "fixture-kabus-topix"):
        registry[source_id].update(
            {
                "data_as_of_jst": board_snapshot.isoformat(),
                "fetched_at_jst": (as_of - timedelta(seconds=4)).isoformat(),
                "checked_at_jst": (as_of - timedelta(seconds=3)).isoformat(),
                "kabus_market_code": kabus_market_code,
            }
        )
    for source_id in ("fixture-225225-nikkei", "fixture-225225-topix"):
        registry[source_id].update(
            {
                "data_as_of_jst": (as_of - timedelta(seconds=60)).isoformat(),
                "fetched_at_jst": (as_of - timedelta(seconds=55)).isoformat(),
                "checked_at_jst": (as_of - timedelta(seconds=50)).isoformat(),
            }
        )
    expected_update = datetime.combine(as_of.date(), datetime.min.time(), as_of.tzinfo).replace(
        hour=16,
        minute=30,
    )
    if as_of >= expected_update:
        latest_published_at = expected_update
        latest_reference_date = as_of.date()
        availability_status = "updated"
    else:
        latest_published_at = datetime.fromisoformat("2026-08-20T16:30:00+09:00")
        latest_reference_date = date.fromisoformat("2026-08-20")
        availability_status = "awaiting_scheduled_update"
    update_checked_at = latest_published_at + timedelta(minutes=2)
    registry["fixture-jquants-daily"].update(
        {
            "data_as_of_jst": latest_published_at.isoformat(),
            "fetched_at_jst": (latest_published_at + timedelta(minutes=1)).isoformat(),
            "checked_at_jst": update_checked_at.isoformat(),
        }
    )

    # ----------------------------------------

    update = payload["market_data_freshness"]["jquants_dataset_updates"][0]
    update.update(
        {
            "latest_expected_update_jst": expected_update.isoformat(),
            "update_checked_at_jst": update_checked_at.isoformat(),
            "latest_available_published_at_jst": latest_published_at.isoformat(),
            "latest_available_reference_date": latest_reference_date.isoformat(),
            "expected_latest_reference_date": latest_reference_date.isoformat(),
            "availability_status": availability_status,
            "is_latest_available": True,
        }
    )
    session_source = payload["other_sources"]["fixture-jpx-session-schedule"]
    session_source["checked_at_jst"] = timing["session_checked_at_jst"]
    session_source["data_as_of_jst"] = timing["session_checked_at_jst"]
    update_source = payload["other_sources"]["fixture-jquants-update-schedule"]
    update_source["checked_at_jst"] = update_checked_at.isoformat()
    update_source["data_as_of_jst"] = update_checked_at.isoformat()


# ----------------------------------------


def add_weekly_investor_type_update(
    payload: dict,
    *,
    latest_expected_update_jst: str = "2026-08-20T18:00:00+09:00",
    latest_available_published_at_jst: str = "2026-08-13T18:00:00+09:00",
    latest_available_reference_date: str = "2026-08-07",
    expected_latest_reference_date: str = "2026-08-07",
    availability_status: str = "awaiting_scheduled_update",
    is_latest_available: bool = True,
    official_schedule_window_end_date: str = "2026-09-13",
    official_publication_schedule: list[dict] | None = None,
    extend_publication_calendar: bool = True,
) -> None:
    """
    機能:
        投資部門別週次datasetの台帳・選択・公式予定をテスト入力へ追加する。

    引数:
        payload (dict): build_payloadまたはナイト設定済みの入力辞書。
        latest_expected_update_jst (str): 支配的な更新予定時刻。
        latest_available_published_at_jst (str): 実際に取得できた最新公表時刻。
        latest_available_reference_date (str): 実際に取得できた最新対象日。
        expected_latest_reference_date (str): 状態機械が期待する対象日。
        availability_status (str): awaiting、delayed、updated等の可用性状態。
        is_latest_available (bool): 期待対象へ到達済みかの申告値。
        official_schedule_window_end_date (str): 公式予定から導出した確認窓終端日。
        official_publication_schedule (list[dict] | None): 6対象週の公式公表予定。省略時は標準列を使う。
        extend_publication_calendar (bool): 最大公表日まで共有カレンダーを延長するか。

    返り値:
        None: 入力辞書を更新し、値は返さない。
    """
    schedule = official_publication_schedule or [
        {"release_at_jst": "2026-08-06T18:00:00+09:00", "reference_date": "2026-07-31"},
        {"release_at_jst": "2026-08-13T18:00:00+09:00", "reference_date": "2026-08-07"},
        {"release_at_jst": "2026-08-20T18:00:00+09:00", "reference_date": "2026-08-14"},
        {"release_at_jst": "2026-08-27T18:00:00+09:00", "reference_date": "2026-08-21"},
        {"release_at_jst": "2026-09-03T18:00:00+09:00", "reference_date": "2026-08-28"},
        {"release_at_jst": "2026-09-10T18:00:00+09:00", "reference_date": "2026-09-04"},
    ]
    as_of = datetime.fromisoformat(payload["as_of_jst"])
    latest_published_at = datetime.fromisoformat(latest_available_published_at_jst)
    source_id = "fixture-jquants-investor-type"
    freshness = payload["market_data_freshness"]
    freshness["source_registry"][source_id] = {
        "provider_family": "jquants",
        "data_role": "scheduled_history",
        "coverage_items": ["jquants"],
        "dataset_id": "investor-type",
        "series_key": "jquants-investor-type-weekly",
        "instrument_type": "investor_type_flow",
        "symbol": "TSE-INVESTOR-TYPE",
        "contract_month": "not_applicable",
        "quote_type": "weekly_aggregate",
        "status": "valid",
        "status_reason": "公式予定と実到着対象日を分離した週次データ",
        "checked_at_jst": as_of.isoformat(),
        "data_as_of_jst": latest_published_at.isoformat(),
        "fetched_at_jst": latest_published_at.isoformat(),
        "max_age_seconds": 31_536_000,
    }
    freshness["source_selection_audit"].append(
        {
            "selection_key": "jquants_investor_type_history",
            "purpose": "scheduled_history",
            "series_key": "jquants-investor-type-weekly",
            "selected_source_id": source_id,
            "candidate_source_ids": [source_id],
            "selection_reason": "公式予定と実到着を監査した最新週次データを選択",
        }
    )
    freshness["jquants_dataset_updates"].append(
        {
            "dataset_id": "investor-type",
            "source_id": source_id,
            "schedule_profile_id": "investor_type_weekly_1800",
            "frequency": "weekly",
            "official_update_time_description": "週次18:00頃・公式特例日を優先",
            "latest_expected_update_jst": latest_expected_update_jst,
            "update_checked_at_jst": as_of.isoformat(),
            "latest_available_published_at_jst": latest_available_published_at_jst,
            "latest_available_reference_date": latest_available_reference_date,
            "expected_latest_reference_date": expected_latest_reference_date,
            "availability_status": availability_status,
            "is_latest_available": is_latest_available,
            "publication_schedule_source_id": "fixture-jquants-update-schedule",
            "official_schedule_complete": True,
            "official_schedule_window_start_date": "2026-08-03",
            "official_schedule_window_end_date": official_schedule_window_end_date,
            "official_publication_schedule": schedule,
        }
    )
    update_source = payload["other_sources"]["fixture-jquants-update-schedule"]
    update_source["checked_at_jst"] = as_of.isoformat()
    update_source["data_as_of_jst"] = as_of.isoformat()

    # ----------------------------------------

    if not extend_publication_calendar:
        return
    week_monday = as_of.date() - timedelta(days=as_of.weekday())
    base_end = week_monday + timedelta(days=27)
    maximum_release_date = max(
        datetime.fromisoformat(row["release_at_jst"]).date()
        for row in schedule
    )
    required_end = max(base_end, maximum_release_date)
    publication_calendar = freshness["jquants_publication_calendar"]
    next_date = date.fromisoformat(publication_calendar[-1]["date"]) + timedelta(days=1)
    while next_date <= required_end:
        cash_is_trading_day = next_date.weekday() < 5
        publication_calendar.append(
            {
                "date": next_date.isoformat(),
                "cash_is_trading_day": cash_is_trading_day,
                "holiday_division": "1" if cash_is_trading_day else "0",
            }
        )
        next_date += timedelta(days=1)


# ----------------------------------------


def build_valid_calibration() -> dict:
    """
    機能:
        呼称ゲートを通過する期限一致ウォークフォワード校正入力を作成する。

    引数:
        なし。

    返り値:
        dict: 標本数・象限件数・損失・信頼度区間を含む校正入力。
    """
    return {
        "status": "walk_forward",
        "trained_through": "2026-08-19",
        "effective_sample_count": 240,
        "model_version": "fixture-model-v1",
        "calibration_version": "fixture-calibration-v1",
        "method": "rolling_origin",
        "horizon_definition": "forecast_valid_until_midpoint_direction",
        "supported_origin_sessions": ["day_session", "night_session"],
        "session_path_definition_id": "current_continuous_session_no_recess_crossing_v1",
        "horizon_seconds": 8100,
        "base_method": "joint_normal",
        "model_id": "fixture-joint-normal-v1",
        "model_structure_id": "fixture-joint-normal-evidence-v1",
        "base_model_artifact_sha256": "98b2ced0327fbb1d7d72b52cbca4c49ecee788ddd42eed6ad584cd035faeed6b",
        "validation_passed": True,
        "quadrant_observation_counts": {
            "nk_up_topix_up": 60,
            "nk_up_topix_down": 60,
            "nk_down_topix_up": 60,
            "nk_down_topix_down": 60,
        },
        "metrics": {
            "multiclass_brier": 0.65,
            "log_loss": 1.25,
            "top_class_accuracy": 0.43666666666666665,
            "reliability_max_abs_error": 0.03,
        },
        "reliability_bins": [
            {"count": 80, "mean_predicted_probability": 0.30, "realized_frequency": 0.29},
            {"count": 80, "mean_predicted_probability": 0.45, "realized_frequency": 0.43},
            {"count": 80, "mean_predicted_probability": 0.62, "realized_frequency": 0.59},
        ],
    }


# ----------------------------------------


def configure_scenario_model(payload: dict) -> dict:
    """
    機能:
        4象限baseをシナリオ混合へ切り替え、条件付き分布モデルの共通来歴を返す。

    引数:
        payload (dict): build_payloadまたはbuild_trade_ready_payloadで作成した入力。

    返り値:
        dict: 各シナリオへ複製して指定する条件付き確率モデル来歴。
    """
    provenance = payload["probability"]["base_provenance"]
    provenance.update(
        {
            "base_method": "scenario_mixture",
            "model_id": "fixture-scenario-conditional-v1",
            "method": "weighted_conditional_scenario_model",
            "model_structure_id": "fixture-scenario-mixture-v1",
            "model_artifact_sha256": "3a4a6e17cbf184dfb2e1e4c2954a588008681656bb8a80c7a92ebe1229ab4525",
            "validation_passed": True,
            "source_links": [
                {
                    "coverage_item": "other",
                    "source_id": "fixture-scenario-conditional",
                    "data_as_of_jst": "2026-08-20T09:58:00+09:00",
                }
            ],
        }
    )
    calibration = payload.get("calibration")
    if isinstance(calibration, dict) and calibration.get("status") == "walk_forward":
        calibration.update(
            {
                "base_method": provenance["base_method"],
                "model_id": provenance["model_id"],
                "model_structure_id": provenance["model_structure_id"],
                "base_model_artifact_sha256": provenance["model_artifact_sha256"],
            }
        )
    return {
        "model_id": provenance["model_id"],
        "model_version": provenance["model_version"],
        "calibration_version": provenance["calibration_version"],
        "method": provenance["method"],
        "model_structure_id": provenance["model_structure_id"],
        "model_artifact_sha256": provenance["model_artifact_sha256"],
        "training_data_cutoff_jst": provenance["training_data_cutoff_jst"],
        "horizon_definition": provenance["horizon_definition"],
        "supported_origin_sessions": copy.deepcopy(provenance["supported_origin_sessions"]),
        "session_path_definition_id": provenance["session_path_definition_id"],
        "horizon_seconds": provenance["horizon_seconds"],
        "validation_passed": True,
        "source_links": copy.deepcopy(provenance["source_links"]),
    }


# ----------------------------------------


def seal_probability_base(payload: dict) -> None:
    """
    機能:
        現在のprobability.baseを正規化し、来歴へ予測出力SHA-256を設定する。

    引数:
        payload (dict): probability.baseとbase_provenanceを含むテスト入力。

    返り値:
        None: 入力辞書へハッシュを設定し、値は返さない。
    """
    canonical = json.dumps(
        payload["probability"]["base"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    payload["probability"]["base_provenance"]["prediction_output_sha256"] = digest


# ----------------------------------------


def set_valid_scenario_base(payload: dict) -> None:
    """
    機能:
        排他的・網羅的な2分岐と共通条件付きモデル来歴をテスト入力へ設定する。

    引数:
        payload (dict): 4象限baseを置換するテスト入力。

    返り値:
        None: 入力辞書を更新し、値は返さない。
    """
    conditional_basis = configure_scenario_model(payload)
    common = {
        "weight_source": "試験用シナリオ重み",
        "coverage_item": "other",
        "source_id": "fixture-scenario",
        "observed_at_jst": "2026-08-20T09:58:00+09:00",
        "resolution_verified": True,
        "variance_double_count_checked": True,
        "mutually_exclusive_verified": True,
        "exhaustive_verified": True,
        "partition_definition_id": "fixture-validated-partition-v1",
    }
    payload["probability"]["base"] = {
        "scenario_mixture": [
            {
                **common,
                "scenario_id": "shock",
                "weight": 0.5,
                "conditional_probability_basis": copy.deepcopy(conditional_basis),
                "conditional_probabilities": {
                    "nk_up_topix_up": 0.7,
                    "nk_up_topix_down": 0.1,
                    "nk_down_topix_up": 0.1,
                    "nk_down_topix_down": 0.1,
                },
            },
            {
                **common,
                "scenario_id": "no-shock",
                "weight": 0.5,
                "conditional_probability_basis": copy.deepcopy(conditional_basis),
                "conditional_probabilities": {
                    "nk_up_topix_up": 0.2,
                    "nk_up_topix_down": 0.2,
                    "nk_down_topix_up": 0.2,
                    "nk_down_topix_down": 0.4,
                },
            },
        ]
    }
    seal_probability_base(payload)


# ----------------------------------------


def build_trade_ready_payload() -> dict:
    """
    機能:
        校正・coverage消費・非同率・NT相対価値を満たす正常系入力を作成する。

    引数:
        なし。

    返り値:
        dict: 全体statusがokとなる正常系入力。
    """
    payload = build_payload()
    payload["calibration"] = build_valid_calibration()
    payload["probability"]["base_provenance"]["validation_passed"] = True
    fundamental = payload["probability"]["evidence"][0]
    fundamental.update(
        {
            "coverage_item": "realtime_fx",
            "source_id": "fixture-fx",
            "category_scores": {
                "nk_up_topix_up": 2,
                "nk_up_topix_down": 1,
                "nk_down_topix_up": -1,
                "nk_down_topix_down": -2,
            },
            "neutral_observation": False,
            "observed_at_jst": "2026-08-20T09:59:00+09:00",
        }
    )
    supply = payload["probability"]["evidence"][1]
    supply.update(
        {
            "coverage_item": "overseas_markets",
            "source_id": "fixture-overseas",
            "category_scores": {
                "nk_up_topix_up": 2,
                "nk_up_topix_down": 0,
                "nk_down_topix_up": 0,
                "nk_down_topix_down": -2,
            },
            "neutral_observation": False,
        }
    )
    payload["probability"]["relative_value"].update(
        {
            "fundamental_spread_mean_bps": 8,
            "supply_demand_spread_mean_bps": 4,
            "attributions": {
                "fundamental": [
                    {"evidence_key": "fundamental-neutral", "contribution_bps": 8}
                ],
                "supply_demand": [
                    {"evidence_key": "supply-neutral", "contribution_bps": 4}
                ],
                "event": [],
            },
        }
    )
    payload["probability"]["relative_value"]["model_provenance"]["validation_passed"] = True
    payload["daily_forecast"]["drift_model_provenance"]["validation_passed"] = True
    for day in payload["daily_forecast"]["days"]:
        day["nikkei"].update(
            {
                "fundamental_drift_bps": 5,
                "supply_demand_drift_bps": 3,
                "drift_attributions": {
                    "fundamental": [
                        {"evidence_key": "fundamental-neutral", "contribution_bps": 5}
                    ],
                    "supply_demand": [
                        {"evidence_key": "supply-neutral", "contribution_bps": 3}
                    ],
                    "event": [],
                },
            }
        )
        day["topix"].update(
            {
                "fundamental_drift_bps": 3,
                "supply_demand_drift_bps": 2,
                "drift_attributions": {
                    "fundamental": [
                        {"evidence_key": "fundamental-neutral", "contribution_bps": 3}
                    ],
                    "supply_demand": [
                        {"evidence_key": "supply-neutral", "contribution_bps": 2}
                    ],
                    "event": [],
                },
            }
        )
    payload["probability"]["evidence"].append(
        {
            "evidence_key": "fixture-news-neutral",
            "block": "fundamental",
            "coverage_item": "news",
            "source_id": "fixture-news",
            "fact": "直近ニュースを確認済み",
            "inference": "追加方向差なし",
            "counterevidence": "新規材料が出れば無効",
            "category_scores": {
                "nk_up_topix_up": 0,
                "nk_up_topix_down": 0,
                "nk_down_topix_up": 0,
                "nk_down_topix_down": 0,
            },
            "quality": 1,
            "independence": 1,
            "observed_at_jst": "2026-08-20T09:59:00+09:00",
            "half_life_hours": 24,
            "neutral_observation": True,
        }
    )
    event = payload["timing"]["events"][0]
    event["coverage_item"] = "economic_calendar"
    event["source_id"] = "fixture-economic-calendar"
    for day in payload["daily_forecast"]["days"]:
        for index_name in ("nikkei", "topix"):
            day[index_name]["expected_move_source_links"] = [
                {
                    "coverage_item": "options",
                    "source_id": "fixture-options",
                    "data_as_of_jst": "2026-08-20T09:59:00+09:00",
                }
            ]
    used_items = {
        "news": ("fixture-news", "2026-08-20T09:59:00+09:00"),
        "realtime_fx": ("fixture-fx", "2026-08-20T09:59:00+09:00"),
        "economic_calendar": ("fixture-economic-calendar", "2026-08-20T09:50:00+09:00"),
        "overseas_markets": ("fixture-overseas", "2026-08-20T09:59:00+09:00"),
        "options": ("fixture-options", "2026-08-20T09:59:00+09:00"),
    }
    for item_name, (source_id, data_as_of) in used_items.items():
        payload["coverage"][item_name] = {
            "status": "used",
            "reason": "正常系試験で実際に証拠または分散へ利用",
            "checked_at_jst": "2026-08-20T09:59:00+09:00",
            "data_as_of_jst": data_as_of,
            "source_ids": [source_id],
        }
    market_registry = payload["market_data_freshness"]["source_registry"]
    market_registry.update(
        {
            "fixture-fx": {
                "provider_family": "other_market",
                "data_role": "near_realtime_context",
                "coverage_items": ["realtime_fx"],
                "series_key": "usd-jpy-spot",
                "instrument_type": "fx_spot",
                "symbol": "USDJPY",
                "contract_month": "not_applicable",
                "quote_type": "midpoint",
                "status": "valid",
                "status_reason": "正常系試験用の直近為替",
                "checked_at_jst": "2026-08-20T09:59:00+09:00",
                "data_as_of_jst": "2026-08-20T09:59:00+09:00",
                "fetched_at_jst": "2026-08-20T09:59:00+09:00",
                "max_age_seconds": 300,
            },
            "fixture-overseas": {
                "provider_family": "225225",
                "data_role": "near_realtime_context",
                "coverage_items": ["overseas_markets"],
                "series_key": "overseas-index-context",
                "instrument_type": "overseas_index_futures",
                "symbol": "OVERSEAS-FIXTURE",
                "contract_month": "front",
                "quote_type": "context_price",
                "status": "valid",
                "status_reason": "正常系試験用の225225.jp系海外市場値",
                "checked_at_jst": "2026-08-20T09:59:00+09:00",
                "data_as_of_jst": "2026-08-20T09:59:00+09:00",
                "fetched_at_jst": "2026-08-20T09:59:00+09:00",
                "max_age_seconds": 300,
            },
            "fixture-options": {
                "provider_family": "other_market",
                "data_role": "near_realtime_context",
                "coverage_items": ["options"],
                "series_key": "nikkei-options-surface",
                "instrument_type": "index_options_surface",
                "symbol": "NK225-OPTIONS",
                "contract_month": "front",
                "quote_type": "implied_volatility_surface",
                "status": "valid",
                "status_reason": "正常系試験用の直近OP面",
                "checked_at_jst": "2026-08-20T09:59:00+09:00",
                "data_as_of_jst": "2026-08-20T09:59:00+09:00",
                "fetched_at_jst": "2026-08-20T09:59:00+09:00",
                "max_age_seconds": 300,
            },
        }
    )
    payload["market_data_freshness"]["source_selection_audit"].extend(
        [
            {
                "selection_key": "usd_jpy_current_context",
                "purpose": "current_price_context",
                "series_key": "usd-jpy-spot",
                "selected_source_id": "fixture-fx",
                "candidate_source_ids": ["fixture-fx"],
                "selection_reason": "取得できた最新の為替観測を選択",
            },
            {
                "selection_key": "overseas_current_context",
                "purpose": "current_price_context",
                "series_key": "overseas-index-context",
                "selected_source_id": "fixture-overseas",
                "candidate_source_ids": ["fixture-overseas"],
                "selection_reason": "225225.jp系の最新海外市場観測を選択",
            },
            {
                "selection_key": "nikkei_options_current_context",
                "purpose": "current_price_context",
                "series_key": "nikkei-options-surface",
                "selected_source_id": "fixture-options",
                "candidate_source_ids": ["fixture-options"],
                "selection_reason": "取得できた最新のOP面を選択",
            },
        ]
    )
    return payload


# ----------------------------------------


def add_secondary_fx_price_source(
    payload: dict,
    evidence_observed_at_jst: str = "2026-08-20T09:58:30+09:00",
) -> None:
    """
    機能:
        複数価格sourceのcoverage集約時刻を検証するため、EUR/JPY価格と利用証拠を追加する。

    引数:
        payload (dict): build_trade_ready_payloadで作成した入力辞書。
        evidence_observed_at_jst (str): 追加証拠が利用した価格観測時刻。

    返り値:
        None: 入力辞書を更新し、値は返さない。
    """
    source_id = "fixture-fx-secondary"
    source_observed_at_jst = "2026-08-20T09:58:30+09:00"
    payload["market_data_freshness"]["source_registry"][source_id] = {
        "provider_family": "225225",
        "data_role": "near_realtime_context",
        "coverage_items": ["realtime_fx"],
        "series_key": "eur-jpy-spot",
        "instrument_type": "fx_spot",
        "symbol": "EURJPY",
        "contract_month": "not_applicable",
        "quote_type": "midpoint",
        "status": "valid",
        "status_reason": "複数価格source集約試験用のEUR/JPY",
        "checked_at_jst": source_observed_at_jst,
        "data_as_of_jst": source_observed_at_jst,
        "fetched_at_jst": source_observed_at_jst,
        "max_age_seconds": 300,
    }
    payload["market_data_freshness"]["source_selection_audit"].append(
        {
            "selection_key": "eur_jpy_current_context",
            "purpose": "current_price_context",
            "series_key": "eur-jpy-spot",
            "selected_source_id": source_id,
            "candidate_source_ids": [source_id],
            "selection_reason": "225225.jp系の最新EUR/JPY観測を選択",
        }
    )
    payload["coverage"]["realtime_fx"]["source_ids"].append(source_id)
    payload["probability"]["evidence"].append(
        {
            "evidence_key": "fixture-secondary-fx-neutral",
            "block": "fundamental",
            "coverage_item": "realtime_fx",
            "source_id": source_id,
            "fact": "EUR/JPYの追加価格系列を確認済み",
            "inference": "追加方向差なし",
            "counterevidence": "価格観測時刻が変われば再評価",
            "category_scores": {
                "nk_up_topix_up": 0,
                "nk_up_topix_down": 0,
                "nk_down_topix_up": 0,
                "nk_down_topix_down": 0,
            },
            "quality": 1,
            "independence": 1,
            "observed_at_jst": evidence_observed_at_jst,
            "half_life_hours": 1,
            "neutral_observation": True,
        }
    )


# ----------------------------------------


class IndexNtCalculatorTest(unittest.TestCase):
    """純計算ツールの主要な数理・表示不変条件を検証する。"""

    def setUp(self) -> None:
        """
        機能:
            各テストのOS実時計を分析基準時刻から60秒後へ固定する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 固定時計を登録し、値は返さない。
        """
        self._runtime_clock_patcher = patch(
            "index_nt_calculator._runtime_current_jst",
            return_value=datetime.fromisoformat("2026-08-20T10:01:00+09:00"),
        )
        self._runtime_clock_patcher.start()
        self.addCleanup(self._runtime_clock_patcher.stop)

    # ----------------------------------------

    def test_night_session_at_1701_is_valid(self) -> None:
        """
        機能:
            17:01のナイトセッションを通常の新規入口として受理することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        configure_night_session_payload(payload, "2026-08-20T17:01:00+09:00")
        result = calculate_all(payload)
        self.assertEqual(result["timing"]["futures_session_kind"], "night_session")
        self.assertEqual(result["timing"]["futures_session_phase"], "regular")
        self.assertEqual(result["timing"]["board_entry_status"], "valid")

    # ----------------------------------------

    def test_same_night_after_midnight_keeps_exchange_trading_day_as_d1(self) -> None:
        """
        機能:
            翌00:01でも開始日前夜と同一ナイトとして扱い、D1をexchange_trading_dayから始めることを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        configure_night_session_payload(payload, "2026-08-21T00:01:00+09:00")
        result = calculate_all(payload)
        self.assertEqual(result["timing"]["session_start_jst"], "2026/08/20 17:00:00 (JST)")
        self.assertEqual(result["timing"]["exchange_trading_day"], "2026-08-21")
        self.assertEqual(result["daily_forecast"]["calendar_origin_date"], "2026-08-21")
        self.assertEqual(result["daily_forecast"]["verified_trading_dates"][0], "2026-08-21")
        self.assertEqual(
            result["daily_forecast"]["nikkei"][0]["target_at_jst"],
            "2026/08/21 15:30:00 (JST)",
        )

    # ----------------------------------------

    def test_night_regular_phase_is_valid_until_055459(self) -> None:
        """
        機能:
            翌05:54:59まではナイトのregular板と1秒の予測期限を受理することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        configure_night_session_payload(payload, "2026-08-21T05:54:59+09:00")
        result = calculate_all(payload)
        self.assertEqual(result["timing"]["board_entry_status"], "valid")
        self.assertEqual(result["timing"]["forecast_valid_until_jst"], "2026/08/21 05:55:00 (JST)")
        self.assertEqual(result["quadrants"]["base_provenance"]["horizon_seconds"], 1)

    # ----------------------------------------

    def test_night_pre_closing_at_0555_is_rejected_for_new_entry(self) -> None:
        """
        機能:
            翌05:55のpre_closing板を通常の新規入口アンカーとして拒否することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        configure_night_session_payload(
            payload,
            "2026-08-21T05:55:00+09:00",
            futures_session_phase="pre_closing",
        )
        with self.assertRaisesRegex(InputValidationError, "プレクロージング中の板"):
            calculate_all(payload)

    # ----------------------------------------

    def test_night_session_end_at_0600_is_rejected(self) -> None:
        """
        機能:
            翌06:00ちょうどを終了済みナイトセッション内として申告できないことを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        configure_night_session_payload(
            payload,
            "2026-08-21T06:00:00+09:00",
            futures_session_phase="pre_closing",
        )
        with self.assertRaisesRegex(InputValidationError, "開始以上・終了未満"):
            calculate_all(payload)

    # ----------------------------------------

    def test_day_session_end_cannot_be_self_extended(self) -> None:
        """
        機能:
            日中セッション終了を15:45より後へ自己申告して入口期限を延長できないことを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        payload["timing"]["session_end_jst"] = "2026-08-20T16:00:00+09:00"
        with self.assertRaisesRegex(InputValidationError, "日中先物は08:45開始"):
            calculate_all(payload)

    # ----------------------------------------

    def test_night_kabus_market_code_contract(self) -> None:
        """
        機能:
            ナイト板のKabus市場コード24を受理し、日中専用23を拒否することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        valid_payload = build_payload()
        configure_night_session_payload(
            valid_payload,
            "2026-08-20T17:01:00+09:00",
            kabus_market_code=24,
        )
        valid_result = calculate_all(valid_payload)
        for source_id in ("fixture-kabus-nikkei", "fixture-kabus-topix"):
            self.assertEqual(
                valid_result["market_data_freshness"]["source_registry"][source_id]["kabus_market_code"],
                24,
            )

        invalid_payload = build_payload()
        configure_night_session_payload(
            invalid_payload,
            "2026-08-20T17:01:00+09:00",
            kabus_market_code=23,
        )
        with self.assertRaisesRegex(InputValidationError, "kabus_market_codeを現在セッション"):
            calculate_all(invalid_payload)

    # ----------------------------------------

    def test_valid_kabus_source_cannot_be_skipped_for_225225(self) -> None:
        """
        機能:
            同一系列に有効なKabus板があるとき225225.jp系を選ぶ優先順位迂回を拒否することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        selection = payload["market_data_freshness"]["source_selection_audit"][0]
        selection["selected_source_id"] = "fixture-225225-nikkei"
        with self.assertRaisesRegex(InputValidationError, "有効な上位tierを飛ばして"):
            calculate_all(payload)

    # ----------------------------------------

    def test_jquants_cannot_be_selected_as_execution_anchor(self) -> None:
        """
        機能:
            他候補を無効化してもJ-Quants定時履歴を現在板execution_anchorへ昇格できないことを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        registry = payload["market_data_freshness"]["source_registry"]
        registry["fixture-kabus-nikkei"]["status"] = "unavailable"
        registry["fixture-225225-nikkei"]["status"] = "invalid"
        registry["fixture-jquants-daily"].update(
            {
                "series_key": "nikkei-202609-bid-ask",
                "instrument_type": "nikkei225_futures",
                "symbol": "FIXTURE-NK225M-202609",
                "contract_month": "2026-09",
                "data_as_of_jst": "2026-08-20T09:59:05+09:00",
                "fetched_at_jst": "2026-08-20T09:59:10+09:00",
                "checked_at_jst": "2026-08-20T09:59:10+09:00",
            }
        )
        selection = payload["market_data_freshness"]["source_selection_audit"][0]
        selection["selected_source_id"] = "fixture-jquants-daily"
        selection["candidate_source_ids"] = [
            "fixture-kabus-nikkei",
            "fixture-225225-nikkei",
            "fixture-jquants-daily",
        ]
        payload["nt"]["nikkei_board_source_id"] = "fixture-jquants-daily"
        with self.assertRaisesRegex(InputValidationError, "execution_anchorはKabus"):
            calculate_all(payload)

    # ----------------------------------------

    def test_provider_max_age_and_ttl_are_enforced(self) -> None:
        """
        機能:
            Kabus・225225.jp系のprovider上限とstatus=validのTTL超過をそれぞれ拒否することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        cases = (
            ("kabus_upper_bound", "fixture-kabus-nikkei", "max_age_seconds", 121, "kabus上限120秒"),
            ("225225_upper_bound", "fixture-225225-nikkei", "max_age_seconds", 301, "225225上限300秒"),
            ("valid_ttl_exceeded", "fixture-kabus-nikkei", "data_as_of_jst", "2026-08-20T09:57:00+09:00", "TTL超過"),
        )
        for case_name, source_id, field_name, value, error_pattern in cases:
            with self.subTest(case_name=case_name):
                payload = build_payload()
                payload["market_data_freshness"]["source_registry"][source_id][field_name] = value
                with self.assertRaisesRegex(InputValidationError, error_pattern):
                    calculate_all(payload)

    # ----------------------------------------

    def test_used_jquants_requires_latest_update_audit(self) -> None:
        """
        機能:
            J-Quantsを営業日根拠へ使用しながら公表予定・最新対象日の更新監査を欠落させる入力を拒否する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        payload["coverage"]["jquants"] = {
            "status": "used",
            "reason": "試験用の日次価格履歴を使用",
            "checked_at_jst": "2026-08-20T09:59:00+09:00",
            "data_as_of_jst": "2026-08-19T16:30:00+09:00",
            "source_ids": ["fixture-jquants-daily"],
        }
        payload["probability"]["evidence"].append(
            {
                "evidence_key": "jquants-history-neutral",
                "block": "fundamental",
                "coverage_item": "jquants",
                "source_id": "fixture-jquants-daily",
                "fact": "J-Quants日次価格履歴を確認済み",
                "inference": "追加方向差なし",
                "counterevidence": "次回更新で再評価",
                "category_scores": {
                    "nk_up_topix_up": 0,
                    "nk_up_topix_down": 0,
                    "nk_down_topix_up": 0,
                    "nk_down_topix_down": 0,
                },
                "quality": 1,
                "independence": 1,
                "observed_at_jst": "2026-08-19T16:30:00+09:00",
                "half_life_hours": 168,
                "neutral_observation": True,
            }
        )
        payload["market_data_freshness"]["jquants_dataset_updates"] = []
        with self.assertRaisesRegex(InputValidationError, "dataset更新監査"):
            calculate_all(payload)

    # ----------------------------------------

    def test_derivatives_holiday_session_accepts_closed_cash_market(self) -> None:
        """
        機能:
            holiday_division=3の日中先物を現物休場・先物開場として受理し、D1を対応営業日から始めることを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        payload["timing"].update(
            {
                "cash_market_state": "holiday_closed",
                "exchange_trading_day": "2026-08-21",
                "holiday_division": "3",
                "cash_market_holiday_division": "3",
                "is_derivatives_holiday_trading": True,
                "session_calendar": [
                    {
                        "date": "2026-08-20",
                        "cash_is_trading_day": False,
                        "derivatives_session_open": True,
                        "holiday_division": "3",
                    },
                    {
                        "date": "2026-08-21",
                        "cash_is_trading_day": True,
                        "derivatives_session_open": True,
                        "holiday_division": "1",
                    },
                ],
            }
        )
        payload["market_data_freshness"]["jquants_dataset_updates"][0][
            "latest_expected_update_jst"
        ] = "2026-08-21T16:30:00+09:00"
        publication_row = next(
            row
            for row in payload["market_data_freshness"]["jquants_publication_calendar"]
            if row["date"] == "2026-08-20"
        )
        publication_row.update(
            {
                "cash_is_trading_day": False,
                "holiday_division": "3",
            }
        )
        result = calculate_all(payload)
        self.assertEqual(result["timing"]["cash_market_state"], "holiday_closed")
        self.assertEqual(result["timing"]["holiday_division"], "3")
        self.assertTrue(result["timing"]["is_derivatives_holiday_trading"])
        self.assertEqual(result["daily_forecast"]["calendar_origin_date"], "2026-08-21")

    # ----------------------------------------

    def test_daily_targets_require_tse_cash_close_at_1530(self) -> None:
        """
        機能:
            D1の対象時刻を15:30以外へずらして現物終値予測を自己延長できないことを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        payload["daily_forecast"]["days"][0]["target_at_jst"] = "2026-08-21T15:31:00+09:00"
        with self.assertRaisesRegex(InputValidationError, "15:30 JST"):
            calculate_all(payload)

    # ----------------------------------------

    def test_night_session_keeps_contract_last_trading_end_at_1545(self) -> None:
        """
        機能:
            ナイト起点でも選択限月の最終取引可能時刻を最終取引日15:45のまま維持することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        configure_night_session_payload(payload, "2026-08-20T17:01:00+09:00")
        result = calculate_all(payload)
        self.assertEqual(result["nt"]["last_trading_day"], "2026-09-10")
        self.assertEqual(
            result["nt"]["last_trading_session_end_jst"],
            "2026/09/10 15:45:00 (JST)",
        )

    # ----------------------------------------

    def test_saturday_night_session_start_is_rejected(self) -> None:
        """
        機能:
            土曜日17:00を指数先物ナイトセッション開始として申告する入力を拒否することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        payload["as_of_jst"] = "2026-08-22T17:01:00+09:00"
        payload["timing"].update(
            {
                "cash_market_state": "closed",
                "futures_session_kind": "night_session",
                "futures_session_phase": "regular",
                "exchange_trading_day": "2026-08-24",
                "session_start_jst": "2026-08-22T17:00:00+09:00",
                "continuous_end_jst": "2026-08-23T05:55:00+09:00",
                "session_end_jst": "2026-08-23T06:00:00+09:00",
                "next_session_start_jst": "2026-08-24T08:45:00+09:00",
            }
        )
        with self.assertRaisesRegex(InputValidationError, "土日に開始"):
            calculate_all(payload)

    # ----------------------------------------

    def test_normal_night_exchange_trading_day_cannot_be_postponed(self) -> None:
        """
        機能:
            通常ナイトのJPX取引日を直後の日中セッションより後へ先送りする入力を拒否することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        configure_night_session_payload(payload, "2026-08-20T17:01:00+09:00")
        payload["timing"]["exchange_trading_day"] = "2026-08-24"
        with self.assertRaisesRegex(InputValidationError, "session_calendar"):
            calculate_all(payload)

    # ----------------------------------------

    def test_night_d1_origin_exchange_trading_day_cannot_be_skipped(self) -> None:
        """
        機能:
            ナイトのexchange_trading_dayを休場扱いにしてD1を後日へ飛ばす入力を拒否することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        configure_night_session_payload(payload, "2026-08-20T17:01:00+09:00")
        payload["daily_forecast"]["calendar_sessions"][0].update(
            {
                "cash_is_trading_day": False,
                "derivatives_session_open": False,
                "holiday_division": "0",
            }
        )
        with self.assertRaisesRegex(InputValidationError, "calendar_sessions先頭"):
            calculate_all(payload)

    # ----------------------------------------

    def test_cash_market_holiday_requires_holiday_trading_flag(self) -> None:
        """
        機能:
            分析日がholiday_division=3なのに先物祝日取引フラグを偽とする入力を拒否することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        configure_night_session_payload(payload, "2026-08-21T00:01:00+09:00")
        payload["timing"].update(
            {
                "cash_market_state": "holiday_closed",
                "cash_market_holiday_division": "3",
                "is_derivatives_holiday_trading": False,
            }
        )
        with self.assertRaisesRegex(InputValidationError, "as_of_jst当日がholiday_division=3"):
            calculate_all(payload)

    # ----------------------------------------

    def test_jquants_dataset_profile_and_registry_mismatch_is_rejected(self) -> None:
        """
        機能:
            J-Quants更新行のdataset、固定profile、registry datasetが一致しない入力を拒否することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        mismatch_cases = (
            ("profile", "dataset_idとschedule_profile_id"),
            ("registry", "dataset_idをsource_registry"),
        )
        for mismatch_kind, error_pattern in mismatch_cases:
            with self.subTest(mismatch_kind=mismatch_kind):
                payload = build_payload()
                if mismatch_kind == "profile":
                    payload["market_data_freshness"]["jquants_dataset_updates"][0][
                        "schedule_profile_id"
                    ] = "issue_open_interest_2115"
                else:
                    payload["market_data_freshness"]["source_registry"][
                        "fixture-jquants-daily"
                    ]["dataset_id"] = "investor-type"
                with self.assertRaisesRegex(InputValidationError, error_pattern):
                    calculate_all(payload)

    # ----------------------------------------

    def test_jquants_weekly_publication_time_and_reference_date_are_separate(self) -> None:
        """
        機能:
            週次J-Quantsの公表日時と統計対象日を別項目として保持する正常入力を受理することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        source_id = "fixture-jquants-investor-type"
        payload["market_data_freshness"]["source_registry"][source_id] = {
            "provider_family": "jquants",
            "data_role": "scheduled_history",
            "coverage_items": ["jquants"],
            "dataset_id": "investor-type",
            "series_key": "jquants-investor-type-weekly",
            "instrument_type": "investor_type_flow",
            "symbol": "TSE-INVESTOR-TYPE",
            "contract_month": "not_applicable",
            "quote_type": "weekly_aggregate",
            "status": "valid",
            "status_reason": "第4営業日18:00頃に公表された前週対象データ",
            "checked_at_jst": "2026-08-20T09:59:00+09:00",
            "data_as_of_jst": "2026-08-13T18:00:00+09:00",
            "fetched_at_jst": "2026-08-20T09:58:30+09:00",
            "max_age_seconds": 604800,
        }
        payload["market_data_freshness"]["source_selection_audit"].append(
            {
                "selection_key": "jquants_investor_type_history",
                "purpose": "scheduled_history",
                "series_key": "jquants-investor-type-weekly",
                "selected_source_id": source_id,
                "candidate_source_ids": [source_id],
                "selection_reason": "確認済みの最新週次投資部門別を選択",
            }
        )
        payload["market_data_freshness"]["jquants_dataset_updates"].append(
            {
                "dataset_id": "investor-type",
                "source_id": source_id,
                "schedule_profile_id": "investor_type_weekly_1800",
                "frequency": "weekly",
                "official_update_time_description": "週次・第4営業日18:00頃・時刻非保証",
                "latest_expected_update_jst": "2026-08-20T18:00:00+09:00",
                "update_checked_at_jst": "2026-08-20T09:59:00+09:00",
                "latest_available_published_at_jst": "2026-08-13T18:00:00+09:00",
                "latest_available_reference_date": "2026-08-07",
                "expected_latest_reference_date": "2026-08-07",
                "availability_status": "awaiting_scheduled_update",
                "is_latest_available": True,
                "publication_schedule_source_id": "fixture-jquants-update-schedule",
                "official_schedule_complete": True,
                "official_schedule_window_start_date": "2026-08-03",
                "official_schedule_window_end_date": "2026-09-13",
                "official_publication_schedule": [
                    {"release_at_jst": "2026-08-06T18:00:00+09:00", "reference_date": "2026-07-31"},
                    {"release_at_jst": "2026-08-13T18:00:00+09:00", "reference_date": "2026-08-07"},
                    {"release_at_jst": "2026-08-20T18:00:00+09:00", "reference_date": "2026-08-14"},
                    {"release_at_jst": "2026-08-27T18:00:00+09:00", "reference_date": "2026-08-21"},
                    {"release_at_jst": "2026-09-03T18:00:00+09:00", "reference_date": "2026-08-28"},
                    {"release_at_jst": "2026-09-10T18:00:00+09:00", "reference_date": "2026-09-04"},
                ],
            }
        )
        result = calculate_all(payload)
        weekly_update = next(
            row
            for row in result["market_data_freshness"]["jquants_dataset_updates"]
            if row["dataset_id"] == "investor-type"
        )
        self.assertEqual(
            weekly_update["latest_available_published_at_jst"],
            "2026/08/13 18:00:00 (JST)",
        )
        self.assertEqual(weekly_update["latest_available_reference_date"], "2026-08-07")
        self.assertEqual(
            weekly_update["official_publication_schedule"][2]["release_at_jst"],
            "2026/08/20 18:00:00 (JST)",
        )
        omitted_schedule_payload = copy.deepcopy(payload)
        del omitted_schedule_payload["market_data_freshness"]["jquants_dataset_updates"][-1][
            "official_publication_schedule"
        ][1]
        with self.assertRaisesRegex(InputValidationError, "6対象週"):
            calculate_all(omitted_schedule_payload)
        payload["market_data_freshness"]["jquants_dataset_updates"][-1][
            "latest_expected_update_jst"
        ] = "2026-08-22T18:00:00+09:00"
        with self.assertRaisesRegex(InputValidationError, "公式公表予定列"):
            calculate_all(payload)

    # ----------------------------------------

    def test_jquants_weekly_official_schedule_supports_golden_week_exception(self) -> None:
        """
        機能:
            公式日程にあるゴールデンウィーク後の週次特例公表を第4営業日固定にせず導出する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        calendar_by_date = {}
        calendar_start = date(2026, 4, 20)
        closed_weekdays = {
            date(2026, 4, 29),
            date(2026, 5, 4),
            date(2026, 5, 5),
            date(2026, 5, 6),
        }
        for offset in range(49):
            calendar_date = calendar_start + timedelta(days=offset)
            cash_is_trading_day = (
                calendar_date.weekday() < 5 and calendar_date not in closed_weekdays
            )
            calendar_by_date[calendar_date] = {
                "date": calendar_date.isoformat(),
                "cash_is_trading_day": cash_is_trading_day,
                "holiday_division": "1" if cash_is_trading_day else "0",
            }
        update = {
            "official_schedule_complete": True,
            "official_schedule_window_start_date": "2026-04-27",
            "official_schedule_window_end_date": "2026-06-07",
            "official_publication_schedule": [
                {"release_at_jst": "2026-04-30T18:00:00+09:00", "reference_date": "2026-04-24"},
                {"release_at_jst": "2026-05-12T18:00:00+09:00", "reference_date": "2026-05-01"},
                {"release_at_jst": "2026-05-14T18:00:00+09:00", "reference_date": "2026-05-08"},
                {"release_at_jst": "2026-05-21T18:00:00+09:00", "reference_date": "2026-05-15"},
                {"release_at_jst": "2026-05-28T18:00:00+09:00", "reference_date": "2026-05-22"},
                {"release_at_jst": "2026-06-04T18:00:00+09:00", "reference_date": "2026-05-29"},
            ],
        }
        schedule_context = _validate_weekly_publication_schedule(
            update,
            "investor-type",
            datetime.fromisoformat("2026-05-11T10:00:00+09:00"),
            calendar_by_date,
            date(2026, 4, 24),
            datetime.fromisoformat("2026-04-30T18:00:00+09:00"),
        )
        self.assertEqual(
            schedule_context["expected_update"],
            datetime.fromisoformat("2026-05-12T18:00:00+09:00"),
        )
        self.assertEqual(schedule_context["expected_reference"], date(2026, 4, 24))

        early_calendar_by_date = {}
        early_calendar_start = date(2026, 4, 6)
        for offset in range(49):
            calendar_date = early_calendar_start + timedelta(days=offset)
            cash_is_trading_day = (
                calendar_date.weekday() < 5 and calendar_date not in closed_weekdays
            )
            early_calendar_by_date[calendar_date] = {
                "date": calendar_date.isoformat(),
                "cash_is_trading_day": cash_is_trading_day,
                "holiday_division": "1" if cash_is_trading_day else "0",
            }
        early_update = {
            "official_schedule_complete": True,
            "official_schedule_window_start_date": "2026-04-13",
            "official_schedule_window_end_date": "2026-05-24",
            "official_publication_schedule": [
                {"release_at_jst": "2026-04-16T18:00:00+09:00", "reference_date": "2026-04-10"},
                {"release_at_jst": "2026-04-23T18:00:00+09:00", "reference_date": "2026-04-17"},
                {"release_at_jst": "2026-04-30T18:00:00+09:00", "reference_date": "2026-04-24"},
                {"release_at_jst": "2026-05-12T18:00:00+09:00", "reference_date": "2026-05-01"},
                {"release_at_jst": "2026-05-14T18:00:00+09:00", "reference_date": "2026-05-08"},
                {"release_at_jst": "2026-05-21T18:00:00+09:00", "reference_date": "2026-05-15"},
            ],
        }
        early_schedule_context = _validate_weekly_publication_schedule(
            early_update,
            "investor-type",
            datetime.fromisoformat("2026-05-01T10:00:00+09:00"),
            early_calendar_by_date,
            date(2026, 4, 24),
            datetime.fromisoformat("2026-04-30T18:00:00+09:00"),
        )
        self.assertEqual(
            early_schedule_context["expected_update"],
            datetime.fromisoformat("2026-05-12T18:00:00+09:00"),
        )
        self.assertEqual(
            early_schedule_context["expected_reference"],
            date(2026, 4, 24),
        )

        april_thirteenth_calendar_by_date = {}
        april_thirteenth_calendar_start = date(2026, 3, 23)
        april_thirteenth_calendar_end = date(2026, 5, 12)
        calendar_date = april_thirteenth_calendar_start
        while calendar_date <= april_thirteenth_calendar_end:
            cash_is_trading_day = (
                calendar_date.weekday() < 5 and calendar_date not in closed_weekdays
            )
            april_thirteenth_calendar_by_date[calendar_date] = {
                "date": calendar_date.isoformat(),
                "cash_is_trading_day": cash_is_trading_day,
                "holiday_division": "1" if cash_is_trading_day else "0",
            }
            calendar_date += timedelta(days=1)
        april_thirteenth_update = {
            "official_schedule_complete": True,
            "official_schedule_window_start_date": "2026-03-30",
            "official_schedule_window_end_date": "2026-05-12",
            "official_publication_schedule": [
                {"release_at_jst": "2026-04-02T18:00:00+09:00", "reference_date": "2026-03-27"},
                {"release_at_jst": "2026-04-09T18:00:00+09:00", "reference_date": "2026-04-03"},
                {"release_at_jst": "2026-04-16T18:00:00+09:00", "reference_date": "2026-04-10"},
                {"release_at_jst": "2026-04-23T18:00:00+09:00", "reference_date": "2026-04-17"},
                {"release_at_jst": "2026-04-30T18:00:00+09:00", "reference_date": "2026-04-24"},
                {"release_at_jst": "2026-05-12T18:00:00+09:00", "reference_date": "2026-05-01"},
            ],
        }
        april_thirteenth_context = _validate_weekly_publication_schedule(
            april_thirteenth_update,
            "investor-type",
            datetime.fromisoformat("2026-04-13T10:00:00+09:00"),
            april_thirteenth_calendar_by_date,
            date(2026, 4, 3),
            datetime.fromisoformat("2026-04-09T18:00:00+09:00"),
        )
        self.assertEqual(
            april_thirteenth_context["schedule"][-1]["release_at_jst"],
            "2026/05/12 18:00:00 (JST)",
        )
        self.assertEqual(len(april_thirteenth_calendar_by_date), 51)

        over_limit_update = copy.deepcopy(april_thirteenth_update)
        over_limit_update["official_schedule_window_end_date"] = "2026-06-22"
        over_limit_update["official_publication_schedule"][-1][
            "release_at_jst"
        ] = "2026-06-22T18:00:00+09:00"
        with self.assertRaisesRegex(InputValidationError, "最大90暦日"):
            _validate_weekly_publication_schedule(
                over_limit_update,
                "investor-type",
                datetime.fromisoformat("2026-04-13T10:00:00+09:00"),
                april_thirteenth_calendar_by_date,
                date(2026, 4, 3),
                datetime.fromisoformat("2026-04-09T18:00:00+09:00"),
            )

        for case_name, schedule_index, reference_date in (
            ("too_old", 0, "1900-01-01"),
            ("non_trading", 1, "2026-05-02"),
            ("reverse", 1, "2026-04-17"),
        ):
            with self.subTest(case_name=case_name):
                invalid_update = copy.deepcopy(update)
                invalid_update["official_publication_schedule"][schedule_index][
                    "reference_date"
                ] = reference_date
                with self.assertRaises(InputValidationError):
                    _validate_weekly_publication_schedule(
                        invalid_update,
                        "investor-type",
                        datetime.fromisoformat("2026-05-11T10:00:00+09:00"),
                        calendar_by_date,
                        date(2026, 4, 24),
                        datetime.fromisoformat("2026-04-30T18:00:00+09:00"),
                    )
        wrong_time_update = copy.deepcopy(update)
        wrong_time_update["official_publication_schedule"][1][
            "release_at_jst"
        ] = "2026-05-12T15:30:00+09:00"
        with self.assertRaisesRegex(InputValidationError, "更新目安18:00"):
            _validate_weekly_publication_schedule(
                wrong_time_update,
                "investor-type",
                datetime.fromisoformat("2026-05-11T10:00:00+09:00"),
                calendar_by_date,
                date(2026, 4, 24),
                datetime.fromisoformat("2026-04-30T18:00:00+09:00"),
            )

    # ----------------------------------------

    def test_jquants_weekly_schedule_extends_shared_calendar_without_padding(self) -> None:
        """
        機能:
            6件目の特例公表日まで共有カレンダーを動的延長し、余分日と不連続を拒否する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        delayed_last_release_schedule = [
            {"release_at_jst": "2026-08-06T18:00:00+09:00", "reference_date": "2026-07-31"},
            {"release_at_jst": "2026-08-13T18:00:00+09:00", "reference_date": "2026-08-07"},
            {"release_at_jst": "2026-08-20T18:00:00+09:00", "reference_date": "2026-08-14"},
            {"release_at_jst": "2026-08-27T18:00:00+09:00", "reference_date": "2026-08-21"},
            {"release_at_jst": "2026-09-03T18:00:00+09:00", "reference_date": "2026-08-28"},
            {"release_at_jst": "2026-09-17T18:00:00+09:00", "reference_date": "2026-09-04"},
        ]
        payload = build_payload()
        add_weekly_investor_type_update(
            payload,
            official_schedule_window_end_date="2026-09-17",
            official_publication_schedule=delayed_last_release_schedule,
        )
        result = calculate_all(payload)
        publication_calendar = result["market_data_freshness"][
            "jquants_publication_calendar"
        ]
        self.assertEqual(len(publication_calendar), 53)
        self.assertEqual(publication_calendar[-1]["date"], "2026-09-17")
        weekly_update = result["market_data_freshness"]["jquants_dataset_updates"][1]
        self.assertEqual(
            weekly_update["official_schedule_window_end_date"],
            "2026-09-17",
        )

        extra_calendar_day_payload = copy.deepcopy(payload)
        extra_calendar_day_payload["market_data_freshness"][
            "jquants_publication_calendar"
        ].append(
            {
                "date": "2026-09-18",
                "cash_is_trading_day": True,
                "holiday_division": "1",
            }
        )
        with self.assertRaisesRegex(InputValidationError, "過不足なく"):
            calculate_all(extra_calendar_day_payload)

        discontinuous_calendar_payload = copy.deepcopy(payload)
        discontinuous_calendar_payload["market_data_freshness"][
            "jquants_publication_calendar"
        ][-2]["date"] = "2026-09-18"
        with self.assertRaisesRegex(InputValidationError, "連続"):
            calculate_all(discontinuous_calendar_payload)

        padded_window_payload = copy.deepcopy(payload)
        padded_window_payload["market_data_freshness"]["jquants_dataset_updates"][
            -1
        ]["official_schedule_window_end_date"] = "2026-09-18"
        with self.assertRaisesRegex(InputValidationError, "最大公表日"):
            calculate_all(padded_window_payload)

        daily_only_extra_day_payload = build_payload()
        daily_only_extra_day_payload["market_data_freshness"][
            "jquants_publication_calendar"
        ].append(
            {
                "date": "2026-09-14",
                "cash_is_trading_day": True,
                "holiday_division": "1",
            }
        )
        with self.assertRaisesRegex(InputValidationError, "過不足なく"):
            calculate_all(daily_only_extra_day_payload)

    # ----------------------------------------

    def test_jquants_weekly_state_follows_due_schedule_and_actual_arrival(self) -> None:
        """
        機能:
            週次データを予定前awaiting、予定到来後未着delayed、到着後次回awaitingへ遷移させる。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        cases = (
            {
                "name": "before_release",
                "as_of": "2026-08-20T17:59:00+09:00",
                "latest_expected": "2026-08-20T18:00:00+09:00",
                "latest_published": "2026-08-13T18:00:00+09:00",
                "latest_reference": "2026-08-07",
                "expected_reference": "2026-08-07",
                "status": "awaiting_scheduled_update",
                "is_latest": True,
                "last_due": "2026/08/13 18:00:00 (JST)",
                "last_due_reference": "2026-08-07",
                "next_scheduled": "2026/08/20 18:00:00 (JST)",
                "next_scheduled_reference": "2026-08-14",
                "early_arrival": False,
            },
            {
                "name": "at_release_not_arrived",
                "as_of": "2026-08-20T18:00:00+09:00",
                "latest_expected": "2026-08-20T18:00:00+09:00",
                "latest_published": "2026-08-13T18:00:00+09:00",
                "latest_reference": "2026-08-07",
                "expected_reference": "2026-08-14",
                "status": "delayed",
                "is_latest": False,
                "last_due": "2026/08/20 18:00:00 (JST)",
                "last_due_reference": "2026-08-14",
                "next_scheduled": "2026/08/27 18:00:00 (JST)",
                "next_scheduled_reference": "2026-08-21",
                "early_arrival": False,
            },
            {
                "name": "after_release_not_arrived",
                "as_of": "2026-08-20T18:01:00+09:00",
                "latest_expected": "2026-08-20T18:00:00+09:00",
                "latest_published": "2026-08-13T18:00:00+09:00",
                "latest_reference": "2026-08-07",
                "expected_reference": "2026-08-14",
                "status": "delayed",
                "is_latest": False,
                "last_due": "2026/08/20 18:00:00 (JST)",
                "last_due_reference": "2026-08-14",
                "next_scheduled": "2026/08/27 18:00:00 (JST)",
                "next_scheduled_reference": "2026-08-21",
                "early_arrival": False,
            },
            {
                "name": "after_release_arrived",
                "as_of": "2026-08-20T18:01:00+09:00",
                "latest_expected": "2026-08-27T18:00:00+09:00",
                "latest_published": "2026-08-20T18:00:00+09:00",
                "latest_reference": "2026-08-14",
                "expected_reference": "2026-08-14",
                "status": "awaiting_scheduled_update",
                "is_latest": True,
                "last_due": "2026/08/20 18:00:00 (JST)",
                "last_due_reference": "2026-08-14",
                "next_scheduled": "2026/08/27 18:00:00 (JST)",
                "next_scheduled_reference": "2026-08-21",
                "early_arrival": False,
            },
            {
                "name": "same_day_early_arrival",
                "as_of": "2026-08-20T17:59:00+09:00",
                "latest_expected": "2026-08-27T18:00:00+09:00",
                "latest_published": "2026-08-20T17:58:00+09:00",
                "latest_reference": "2026-08-14",
                "expected_reference": "2026-08-14",
                "status": "awaiting_scheduled_update",
                "is_latest": True,
                "last_due": "2026/08/13 18:00:00 (JST)",
                "last_due_reference": "2026-08-07",
                "next_scheduled": "2026/08/27 18:00:00 (JST)",
                "next_scheduled_reference": "2026-08-21",
                "early_arrival": True,
            },
        )
        for case in cases:
            with self.subTest(case=case["name"]):
                payload = build_payload()
                configure_night_session_payload(payload, case["as_of"])
                add_weekly_investor_type_update(
                    payload,
                    latest_expected_update_jst=case["latest_expected"],
                    latest_available_published_at_jst=case["latest_published"],
                    latest_available_reference_date=case["latest_reference"],
                    expected_latest_reference_date=case["expected_reference"],
                    availability_status=case["status"],
                    is_latest_available=case["is_latest"],
                )
                result = calculate_all(payload)
                weekly_update = result["market_data_freshness"][
                    "jquants_dataset_updates"
                ][1]
                self.assertEqual(weekly_update["availability_status"], case["status"])
                self.assertEqual(
                    weekly_update["latest_expected_update_jst"],
                    datetime.fromisoformat(case["latest_expected"]).strftime(
                        "%Y/%m/%d %H:%M:%S (JST)"
                    ),
                )
                self.assertEqual(
                    weekly_update["expected_latest_reference_date"],
                    case["expected_reference"],
                )
                self.assertEqual(weekly_update["last_due_update_jst"], case["last_due"])
                self.assertEqual(
                    weekly_update["last_due_reference_date"],
                    case["last_due_reference"],
                )
                self.assertEqual(
                    weekly_update["next_scheduled_update_jst"],
                    case["next_scheduled"],
                )
                self.assertEqual(
                    weekly_update["next_scheduled_reference_date"],
                    case["next_scheduled_reference"],
                )
                self.assertEqual(
                    weekly_update["early_arrival_observed"],
                    case["early_arrival"],
                )
                if case["early_arrival"]:
                    self.assertEqual(
                        weekly_update["observed_schedule_update_jst"],
                        "2026/08/20 18:00:00 (JST)",
                    )
                    self.assertEqual(
                        weekly_update["observed_schedule_reference_date"],
                        "2026-08-14",
                    )
                if case["status"] == "delayed":
                    self.assertEqual(result["status"], "hold")
                    self.assertTrue(
                        any(
                            "J-Quants定時履歴の公式公表遅延" in reason
                            for reason in result["analysis_hold_reasons"]
                        )
                    )

        previous_day_arrival_payload = build_payload()
        configure_night_session_payload(
            previous_day_arrival_payload,
            "2026-08-20T17:59:00+09:00",
        )
        add_weekly_investor_type_update(
            previous_day_arrival_payload,
            latest_expected_update_jst="2026-08-27T18:00:00+09:00",
            latest_available_published_at_jst="2026-08-19T17:58:00+09:00",
            latest_available_reference_date="2026-08-14",
            expected_latest_reference_date="2026-08-14",
            availability_status="awaiting_scheduled_update",
            is_latest_available=True,
        )
        with self.assertRaisesRegex(
            InputValidationError,
            "公式公表日が実際の最新公表日より後",
        ):
            calculate_all(previous_day_arrival_payload)

    # ----------------------------------------

    def test_jquants_publication_cannot_predate_its_reference_or_official_release(self) -> None:
        """
        機能:
            日次・週次の公表日が対象日より前の入力と、週次公式公表日前の実到着申告を拒否する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        daily_payload = build_payload()
        daily_update = daily_payload["market_data_freshness"][
            "jquants_dataset_updates"
        ][0]
        daily_update["latest_available_published_at_jst"] = (
            "2026-08-18T16:30:00+09:00"
        )
        daily_payload["market_data_freshness"]["source_registry"][
            "fixture-jquants-daily"
        ]["data_as_of_jst"] = "2026-08-18T16:30:00+09:00"
        daily_payload["market_data_freshness"]["source_registry"][
            "fixture-jquants-daily"
        ]["max_age_seconds"] = 31_536_000
        with self.assertRaisesRegex(InputValidationError, "最新公表日を最新取得対象日以後"):
            calculate_all(daily_payload)

        weekly_before_reference_payload = build_payload()
        add_weekly_investor_type_update(
            weekly_before_reference_payload,
            latest_available_published_at_jst="2026-08-06T18:00:00+09:00",
        )
        with self.assertRaisesRegex(InputValidationError, "最新公表日を最新取得対象日以後"):
            calculate_all(weekly_before_reference_payload)

        weekly_before_official_release_payload = build_payload()
        add_weekly_investor_type_update(
            weekly_before_official_release_payload,
            latest_available_published_at_jst="2026-08-08T18:00:00+09:00",
        )
        with self.assertRaisesRegex(InputValidationError, "公式公表日が実際の最新公表日より後"):
            calculate_all(weekly_before_official_release_payload)

    # ----------------------------------------

    def test_jquants_weekly_last_schedule_early_arrival_remains_updated(self) -> None:
        """
        機能:
            週次6件目の同日早着を5件目へ巻き戻さずupdatedとし、前日早着を拒否する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        release_rows = (
            (datetime.fromisoformat("2026-01-08T18:00:00+09:00"), date(2026, 1, 2)),
            (datetime.fromisoformat("2026-01-15T18:00:00+09:00"), date(2026, 1, 9)),
            (datetime.fromisoformat("2026-01-22T18:00:00+09:00"), date(2026, 1, 16)),
            (datetime.fromisoformat("2026-01-29T18:00:00+09:00"), date(2026, 1, 23)),
            (datetime.fromisoformat("2026-02-05T18:00:00+09:00"), date(2026, 1, 30)),
            (datetime.fromisoformat("2026-02-12T18:00:00+09:00"), date(2026, 2, 6)),
        )
        context = _derive_weekly_jquants_arrival_state(
            "investor-type",
            release_rows,
            datetime.fromisoformat("2026-02-12T17:59:00+09:00"),
            date(2026, 2, 6),
            datetime.fromisoformat("2026-02-12T17:58:00+09:00"),
            True,
        )
        self.assertEqual(context["expected_availability_status"], "updated")
        self.assertEqual(
            context["expected_update"],
            datetime.fromisoformat("2026-02-12T18:00:00+09:00"),
        )
        self.assertEqual(context["expected_reference"], date(2026, 2, 6))
        self.assertIsNone(context["next_scheduled_update"])
        self.assertTrue(context["early_arrival_observed"])
        self.assertEqual(
            context["observed_schedule_update"],
            datetime.fromisoformat("2026-02-12T18:00:00+09:00"),
        )
        self.assertEqual(
            context["observed_schedule_reference"],
            date(2026, 2, 6),
        )

        with self.assertRaisesRegex(
            InputValidationError,
            "公式公表日が実際の最新公表日より後",
        ):
            _derive_weekly_jquants_arrival_state(
                "investor-type",
                release_rows,
                datetime.fromisoformat("2026-02-12T17:59:00+09:00"),
                date(2026, 2, 6),
                datetime.fromisoformat("2026-02-11T17:58:00+09:00"),
                True,
            )

    # ----------------------------------------

    def test_jquants_daily_same_day_early_arrival_rolls_to_next_cash_day(self) -> None:
        """
        機能:
            日次更新が目安時刻より同日早着した場合だけ、支配予定を次の現物営業日へ進める。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        cash_dates = (
            date(2026, 8, 19),
            date(2026, 8, 20),
            date(2026, 8, 21),
            date(2026, 8, 24),
        )
        early_context = _derive_daily_jquants_expected_state(
            "daily-prices",
            datetime.fromisoformat("2026-08-20T16:29:00+09:00"),
            cash_dates,
            16,
            30,
            date(2026, 8, 20),
            datetime.fromisoformat("2026-08-20T16:29:00+09:00"),
        )
        self.assertTrue(early_context["early_arrival_observed"])
        self.assertEqual(
            early_context["expected_update"],
            datetime.fromisoformat("2026-08-21T16:30:00+09:00"),
        )
        self.assertEqual(early_context["expected_reference"], date(2026, 8, 20))

        previous_day_context = _derive_daily_jquants_expected_state(
            "daily-prices",
            datetime.fromisoformat("2026-08-20T16:29:00+09:00"),
            cash_dates,
            16,
            30,
            date(2026, 8, 20),
            datetime.fromisoformat("2026-08-19T16:29:00+09:00"),
        )
        self.assertFalse(previous_day_context["early_arrival_observed"])
        self.assertEqual(
            previous_day_context["expected_update"],
            datetime.fromisoformat("2026-08-20T16:30:00+09:00"),
        )

        awaiting_payload = build_payload()
        daily_update = awaiting_payload["market_data_freshness"][
            "jquants_dataset_updates"
        ][0]
        daily_update.update(
            {
                "latest_expected_update_jst": "2026-08-21T16:30:00+09:00",
                "latest_available_published_at_jst": "2026-08-20T09:59:00+09:00",
                "latest_available_reference_date": "2026-08-20",
                "expected_latest_reference_date": "2026-08-20",
                "availability_status": "awaiting_scheduled_update",
                "is_latest_available": True,
            }
        )
        awaiting_payload["market_data_freshness"]["source_registry"][
            "fixture-jquants-daily"
        ]["data_as_of_jst"] = "2026-08-20T09:59:00+09:00"
        awaiting_result = calculate_all(awaiting_payload)
        normalized_daily_update = awaiting_result["market_data_freshness"][
            "jquants_dataset_updates"
        ][0]
        self.assertEqual(
            normalized_daily_update["availability_status"],
            "awaiting_scheduled_update",
        )
        self.assertTrue(normalized_daily_update["early_arrival_observed"])
        self.assertEqual(
            normalized_daily_update["latest_expected_update_jst"],
            "2026/08/21 16:30:00 (JST)",
        )
        self.assertEqual(
            normalized_daily_update["observed_schedule_update_jst"],
            "2026/08/20 16:30:00 (JST)",
        )
        self.assertEqual(
            normalized_daily_update["observed_schedule_reference_date"],
            "2026-08-20",
        )

        invalid_updated_payload = copy.deepcopy(awaiting_payload)
        invalid_updated_payload["market_data_freshness"][
            "jquants_dataset_updates"
        ][0]["availability_status"] = "updated"
        with self.assertRaisesRegex(InputValidationError, "同日早着後.*awaiting"):
            calculate_all(invalid_updated_payload)

    # ----------------------------------------

    def test_stale_weekly_backup_can_keep_an_older_reference_but_cannot_be_used(self) -> None:
        """
        機能:
            同一datasetの選択済み現行sourceと非選択stale代替を監査し、代替のcoverage利用を拒否する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        add_weekly_investor_type_update(payload)
        freshness = payload["market_data_freshness"]
        backup_source_id = "fixture-jquants-investor-type-stale"
        freshness["source_registry"][backup_source_id] = {
            "provider_family": "jquants",
            "data_role": "scheduled_history",
            "coverage_items": ["jquants"],
            "dataset_id": "investor-type",
            "series_key": "jquants-investor-type-weekly",
            "instrument_type": "investor_type_flow",
            "symbol": "TSE-INVESTOR-TYPE",
            "contract_month": "not_applicable",
            "quote_type": "weekly_aggregate",
            "status": "stale",
            "status_reason": "現行sourceより古い非選択代替",
            "checked_at_jst": "2026-08-20T09:59:00+09:00",
            "data_as_of_jst": "2026-08-13T17:00:00+09:00",
            "fetched_at_jst": "2026-08-13T17:00:00+09:00",
            "max_age_seconds": 3_600,
        }
        weekly_selection = next(
            row
            for row in freshness["source_selection_audit"]
            if row["selection_key"] == "jquants_investor_type_history"
        )
        weekly_selection["candidate_source_ids"].append(backup_source_id)
        backup_update = copy.deepcopy(freshness["jquants_dataset_updates"][-1])
        backup_update.update(
            {
                "source_id": backup_source_id,
                "latest_expected_update_jst": "2026-08-13T18:00:00+09:00",
                "update_checked_at_jst": "2026-08-20T09:59:00+09:00",
                "latest_available_published_at_jst": "2026-08-13T17:00:00+09:00",
                "latest_available_reference_date": "2026-07-10",
                "expected_latest_reference_date": "2026-08-07",
                "availability_status": "delayed",
                "is_latest_available": False,
            }
        )
        freshness["jquants_dataset_updates"].append(backup_update)
        result = calculate_all(payload)
        stale_update = next(
            row
            for row in result["market_data_freshness"]["jquants_dataset_updates"]
            if row["source_id"] == backup_source_id
        )
        self.assertEqual(stale_update["latest_available_reference_date"], "2026-07-10")
        self.assertEqual(stale_update["availability_status"], "delayed")
        current_update = next(
            row
            for row in result["market_data_freshness"]["jquants_dataset_updates"]
            if row["source_id"] == "fixture-jquants-investor-type"
        )
        self.assertNotEqual(
            current_update["latest_available_reference_date"],
            stale_update["latest_available_reference_date"],
        )
        for field_name in (
            "schedule_profile_id",
            "frequency",
            "publication_schedule_source_id",
            "official_schedule_window_start_date",
            "official_schedule_window_end_date",
            "official_publication_schedule",
        ):
            self.assertEqual(current_update[field_name], stale_update[field_name])
        self.assertFalse(
            any(
                backup_source_id in reason
                for reason in result["analysis_hold_reasons"]
            )
        )

        stale_coverage_payload = copy.deepcopy(payload)
        stale_coverage_payload["coverage"]["jquants"] = {
            "status": "used",
            "reason": "非選択stale代替の利用を試行",
            "checked_at_jst": "2026-08-20T09:59:00+09:00",
            "data_as_of_jst": "2026-08-13T17:00:00+09:00",
            "source_ids": [backup_source_id],
        }
        with self.assertRaisesRegex(InputValidationError, "valid台帳"):
            calculate_all(stale_coverage_payload)

        inconsistent_schedule_payload = copy.deepcopy(payload)
        inconsistent_schedule_payload["market_data_freshness"][
            "jquants_dataset_updates"
        ][-1]["official_publication_schedule"][0][
            "release_at_jst"
        ] = "2026-08-07T18:00:00+09:00"
        with self.assertRaisesRegex(InputValidationError, "dataset-global公式契約"):
            calculate_all(inconsistent_schedule_payload)

    # ----------------------------------------

    def test_jquants_future_reference_date_is_rejected(self) -> None:
        """
        機能:
            J-Quantsの最新対象日または期待対象日を分析日より未来にする入力を拒否することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        update = payload["market_data_freshness"]["jquants_dataset_updates"][0]
        update["latest_available_reference_date"] = "2026-08-21"
        update["expected_latest_reference_date"] = "2026-08-21"
        with self.assertRaisesRegex(InputValidationError, "対象日を分析日より未来"):
            calculate_all(payload)

    # ----------------------------------------

    def test_jquants_expected_reference_date_is_derived_from_publication_calendar(self) -> None:
        """
        機能:
            古い対象日を最新と自己申告しても共有公表営業日カレンダー由来の対象日と不一致なら拒否する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        update = payload["market_data_freshness"]["jquants_dataset_updates"][0]
        update["latest_available_reference_date"] = "1900-01-01"
        update["expected_latest_reference_date"] = "1900-01-01"
        update["is_latest_available"] = True
        with self.assertRaisesRegex(InputValidationError, "共有カレンダー由来の対象日"):
            calculate_all(payload)

    # ----------------------------------------

    def test_selected_jquants_unavailable_or_not_latest_is_rejected(self) -> None:
        """
        機能:
            選択済みJ-Quants履歴が取得不能または最新対象日未到達の場合に拒否することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        for invalid_state in ("unavailable", "not_latest"):
            with self.subTest(invalid_state=invalid_state):
                payload = build_payload()
                update = payload["market_data_freshness"]["jquants_dataset_updates"][0]
                if invalid_state == "unavailable":
                    update["availability_status"] = "unavailable"
                else:
                    update["latest_available_reference_date"] = "2026-08-18"
                    update["is_latest_available"] = False
                with self.assertRaisesRegex(InputValidationError, "selected scheduled_history"):
                    calculate_all(payload)

    # ----------------------------------------

    def test_price_coverage_usage_time_must_match_registry_observation(self) -> None:
        """
        機能:
            価格coverageの個別利用時刻が同じsourceのregistry観測時刻と異なる入力を拒否することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_trade_ready_payload()
        add_secondary_fx_price_source(
            payload,
            evidence_observed_at_jst="2026-08-20T09:58:00+09:00",
        )
        with self.assertRaisesRegex(InputValidationError, "利用先時刻をsource_registry"):
            calculate_all(payload)

    # ----------------------------------------

    def test_multiple_price_sources_allow_coverage_latest_time_aggregation(self) -> None:
        """
        機能:
            複数の価格sourceを使うcoverageでは個別時刻を維持し、集約時刻に最大値を許容することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_trade_ready_payload()
        add_secondary_fx_price_source(payload)
        result = calculate_all(payload)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(
            result["coverage"]["items"]["realtime_fx"]["data_as_of_jst"],
            "2026/08/20 09:59:00 (JST)",
        )
        self.assertEqual(
            result["market_data_freshness"]["source_registry"]["fixture-fx-secondary"][
                "data_as_of_jst"
            ],
            "2026/08/20 09:58:30 (JST)",
        )

    # ----------------------------------------

    def test_forced_share_is_over_half_and_sums_to_one(self) -> None:
        """
        機能:
            生確率が25%ずつでも四択比較値の一意な最大が50%を超え、合計100%になることを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        result = calculate_all(build_payload())
        raw = result["quadrants"]["raw_model_probability_bp"]
        forced = result["quadrants"]["forced_decision_share_bp"]
        self.assertEqual(set(raw.values()), {2500})
        self.assertEqual(sum(forced.values()), 10_000)
        self.assertEqual(forced["nk_up_topix_up"], 5_010)
        self.assertGreater(max(forced.values()), 5_000)
        self.assertTrue(result["quadrants"]["winner_tie"])
        self.assertEqual(result["status"], "hold")
        self.assertIn("4象限の生モデル首位が同率", result["analysis_hold_reasons"])

    # ----------------------------------------

    def test_bivariate_normal_same_direction_probability(self) -> None:
        """
        機能:
            平均ゼロの二変量正規分布で既知の同方向確率へ一致することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        rho = 0.5
        expected = 0.25 + math.asin(rho) / (2.0 * math.pi)
        actual = bivariate_normal_cdf(0.0, 0.0, rho)
        self.assertAlmostEqual(actual, expected, places=6)

    # ----------------------------------------

    def test_nt_equivalence_uses_current_nt_ratio(self) -> None:
        """
        機能:
            NT倍率16でTOPIX mini 1枚が日経mini 0.625枚、micro 6.25枚になることを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        nt = calculate_all(build_payload())["nt"]
        self.assertEqual(nt["nt_mid"], "16.000000")
        self.assertEqual(nt["topix_mini_1_to_nikkei_mini_exact"], "0.625000")
        self.assertEqual(nt["topix_mini_1_to_nikkei_mini_1dp"], "0.6")
        self.assertEqual(nt["topix_mini_1_to_nikkei_micro_exact"], "6.250000")
        self.assertEqual(nt["topix_mini_1_to_nikkei_micro_1dp"], "6.3")
        self.assertFalse(nt["smallest_hedge_candidates"]["mini"]["execution_board_verified"])
        self.assertTrue(nt["smallest_hedge_candidates"]["micro"]["execution_board_verified"])

    # ----------------------------------------

    def test_selected_product_must_match_verified_nikkei_board(self) -> None:
        """
        機能:
            micro板しか検証していない入力でmini採用案を実板案として扱わないことを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        payload["nt"]["selected_position"]["nikkei_product"] = "mini"
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_last_trading_day_must_match_contract_month(self) -> None:
        """
        機能:
            限月と異なる年月の取引最終日を指定したNT入力を拒否することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        payload["nt"]["nikkei_last_trading_day"] = "2026-12-10"
        payload["nt"]["topix_last_trading_day"] = "2026-12-10"
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_hedge_search_audits_floor_round_and_ceil(self) -> None:
        """
        機能:
            最小整数ヘッジ探索が理論枚数のfloor・四捨五入・ceilを重複排除しつつ比較することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        candidate = calculate_all(build_payload())["nt"]["smallest_hedge_candidates"]["mini"]
        methods = {
            method
            for row in candidate["rounding_candidates_for_selected_topix"]
            for method in row["rounding_methods"]
        }
        self.assertEqual(methods, {"floor", "round_half_up", "ceil"})

    # ----------------------------------------

    def test_relative_value_probabilities_include_cost_band(self) -> None:
        """
        機能:
            NTロング・ショート・コスト帯内の確率が合計100%となり、両分析寄与が残ることを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        relative = calculate_all(build_trade_ready_payload())["quadrants"]["relative_value"]
        self.assertEqual(sum(relative["probability_bp"].values()), 10_000)
        self.assertEqual(relative["fundamental_spread_mean_bps"], 8.0)
        self.assertEqual(relative["supply_demand_spread_mean_bps"], 4.0)
        self.assertGreater(
            relative["probability_bp"]["nt_long_after_cost"],
            relative["probability_bp"]["nt_short_after_cost"],
        )

    # ----------------------------------------

    def test_verified_scenario_mixture_builds_joint_base_probability(self) -> None:
        """
        機能:
            Polymarket等を含む検証済みシナリオ重みと条件付き4象限分布から共同事前確率を作ることを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        conditional_basis = configure_scenario_model(payload)
        payload["probability"]["base"] = {
            "scenario_mixture": [
                {
                    "scenario_id": "shock",
                    "weight": 0.6,
                    "weight_source": "試験用予測市場",
                    "coverage_item": "other",
                    "source_id": "fixture-scenario",
                    "observed_at_jst": "2026-08-20T09:58:00+09:00",
                    "resolution_verified": True,
                    "variance_double_count_checked": True,
                    "mutually_exclusive_verified": True,
                    "exhaustive_verified": True,
                    "partition_definition_id": "fixture-shock-partition-v1",
                    "conditional_probability_basis": copy.deepcopy(conditional_basis),
                    "conditional_probabilities": {
                        "nk_up_topix_up": 0.7,
                        "nk_up_topix_down": 0.1,
                        "nk_down_topix_up": 0.1,
                        "nk_down_topix_down": 0.1,
                    },
                },
                {
                    "scenario_id": "no-shock",
                    "weight": 0.4,
                    "weight_source": "試験用予測市場の補集合",
                    "coverage_item": "other",
                    "source_id": "fixture-scenario",
                    "observed_at_jst": "2026-08-20T09:58:00+09:00",
                    "resolution_verified": True,
                    "variance_double_count_checked": True,
                    "mutually_exclusive_verified": True,
                    "exhaustive_verified": True,
                    "partition_definition_id": "fixture-shock-partition-v1",
                    "conditional_probability_basis": copy.deepcopy(conditional_basis),
                    "conditional_probabilities": {
                        "nk_up_topix_up": 0.1,
                        "nk_up_topix_down": 0.1,
                        "nk_down_topix_up": 0.1,
                        "nk_down_topix_down": 0.7,
                    },
                },
            ]
        }
        seal_probability_base(payload)
        result = calculate_all(payload)["quadrants"]
        self.assertEqual(result["base_method"], "scenario_mixture")
        self.assertEqual(result["base_probability_bp"]["nk_up_topix_up"], 4_600)
        self.assertEqual(sum(result["base_probability_bp"].values()), 10_000)

    # ----------------------------------------

    def test_residual_direction_flips_between_nt_long_and_short(self) -> None:
        """
        機能:
            同じ6対1枚でもNTロングとNTショートで端数残存方向が反転することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        long_result = calculate_all(build_payload())["nt"]["selected_position"]
        short_payload = build_payload()
        short_payload["nt"]["strategy"] = "nt_short"
        short_result = calculate_all(short_payload)["nt"]["selected_position"]
        self.assertEqual(long_result["residual_label"], "TOPIX売り超過")
        self.assertLess(int(long_result["signed_residual_yen"]), 0)
        self.assertEqual(short_result["residual_label"], "TOPIX買い超過")
        self.assertGreater(int(short_result["signed_residual_yen"]), 0)

    # ----------------------------------------

    def test_daily_forecast_has_five_ordered_bands(self) -> None:
        """
        機能:
            日経・TOPIXのD1〜D5が5行あり、各予測区間が中心値を包含することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        daily = calculate_all(build_payload())["daily_forecast"]
        for index_name in ("nikkei", "topix"):
            rows = daily[index_name]
            self.assertEqual([row["label"] for row in rows], ["D1", "D2", "D3", "D4", "D5"])
            for row in rows:
                center = float(row["center_price_p50"])
                lower68, upper68 = map(float, row["range68"])
                lower90, upper90 = map(float, row["range90"])
                self.assertLessEqual(lower90, lower68)
                self.assertLessEqual(lower68, center)
                self.assertLessEqual(center, upper68)
                self.assertLessEqual(upper68, upper90)

    # ----------------------------------------

    def test_timing_keeps_board_and_forecast_deadlines_separate(self) -> None:
        """
        機能:
            入口板期限、イベント前の予測期限、次の変動窓が別時刻になることを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        timing = calculate_all(build_payload())["timing"]
        self.assertEqual(timing["board_entry_valid_until_jst"], "2026/08/20 10:01:05 (JST)")
        self.assertEqual(timing["forecast_valid_until_jst"], "2026/08/20 12:15:00 (JST)")
        self.assertEqual(timing["next_material_move_window"]["from_jst"], "2026/08/20 12:30:00 (JST)")
        self.assertEqual(
            timing["next_material_move_window"]["scheduled_at_source_jst"],
            "2026/08/20 12:30:00 (JST)",
        )
        self.assertEqual(timing["next_material_move_window"]["source_utc_offset"], "+09:00")

    # ----------------------------------------

    def test_all_jst_output_uses_required_display_format(self) -> None:
        """
        機能:
            純計算器が返す全JST日時をyyyy/MM/dd HH:mm:ss (JST)へ統一することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        result = calculate_all(build_payload())
        stack: list[object] = [result]
        checked_count = 0
        while stack:
            current = stack.pop()
            if isinstance(current, dict):
                for key, value in current.items():
                    if key.endswith("_jst") and value is not None:
                        self.assertIsInstance(value, str)
                        self.assertRegex(
                            value,
                            r"^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2} \(JST\)$",
                            msg=f"{key} の日時書式が不正です: {value}",
                        )
                        checked_count += 1
                    else:
                        stack.append(value)
            elif isinstance(current, list):
                stack.extend(current)
        self.assertGreater(checked_count, 20)

    # ----------------------------------------

    def test_expired_board_is_rejected(self) -> None:
        """
        機能:
            入口板が分析時刻より前に失効している場合、確率やNTを計算せず再取得を要求することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        payload["timing"]["board_snapshot_jst"] = "2026-08-20T09:00:00+09:00"
        payload["nt"]["nikkei_snapshot_jst"] = "2026-08-20T09:00:00+09:00"
        payload["nt"]["topix_snapshot_jst"] = "2026-08-20T09:00:05+09:00"
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_probability_horizon_must_match_forecast_deadline(self) -> None:
        """
        機能:
            4象限の評価期限が時刻計算で確定した予測有効期限と異なる入力を拒否することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        payload["probability"]["horizon_end_jst"] = "2026-08-20T12:16:00+09:00"
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_all_material_event_deadlines_are_compared(self) -> None:
        """
        機能:
            後発イベントでも長い安全余裕により期限が先行する場合、その最小期限を採用することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        payload["timing"]["events"].append(
            {
                "event_id": "later-long-buffer",
                "title": "後発だが安全余裕が長いイベント",
                "coverage_item": "other",
                "source_id": "fixture-calendar",
                "source_url_or_document_id": "fixture://calendar/test-event",
                "source_timezone": "Asia/Tokyo",
                "reference_period": "試験期間",
                "scheduled_at_source": "2026-08-20T14:00:00+09:00",
                "scheduled_at_jst": "2026-08-20T14:00:00+09:00",
                "previous_value": "not_applicable",
                "official_source": "fixture://calendar/later-event",
                "checked_at_jst": "2026-08-20T09:50:00+09:00",
                "release_status": "scheduled",
                "consensus_status": "not_applicable",
                "window_start_jst": "2026-08-20T14:00:00+09:00",
                "window_end_jst": "2026-08-20T14:10:00+09:00",
                "safety_buffer_minutes": 120,
                "occurrence_probability": 1.0,
                "impact_score": 4,
                "relevance": 1.0,
                "source_quality": 1.0,
            }
        )
        payload["probability"]["horizon_end_jst"] = "2026-08-20T12:00:00+09:00"
        payload["probability"]["base_provenance"]["horizon_seconds"] = 7200
        payload["probability"]["relative_value"]["model_provenance"]["horizon_seconds"] = 7200
        payload["daily_forecast"]["days"][0]["major_events"].append("later-long-buffer")
        result = calculate_all(payload)
        self.assertEqual(result["timing"]["forecast_valid_until_jst"], "2026/08/20 12:00:00 (JST)")
        self.assertEqual(
            result["timing"]["next_material_move_window"]["event_id"],
            "test-event",
        )

    # ----------------------------------------

    def test_daily_forecast_rejects_past_target(self) -> None:
        """
        機能:
            D1が分析時刻以前の場合、過去値を将来予測として受け入れないことを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        payload["daily_forecast"]["verified_trading_dates"][0] = "2026-08-20"
        payload["daily_forecast"]["days"][0]["target_at_jst"] = "2026-08-20T09:30:00+09:00"
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_duplicate_evidence_key_is_rejected(self) -> None:
        """
        機能:
            同じ材料をファンダメンタルズと需給へ二重計上できないことを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        payload["probability"]["evidence"][1]["evidence_key"] = "fundamental-neutral"
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_zero_effective_evidence_does_not_satisfy_block(self) -> None:
        """
        機能:
            品質0のダミー証拠でファンダメンタルズ必須ブロックを満たせないことを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        payload["probability"]["evidence"][0]["quality"] = 0
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_tie_without_explicit_dominant_category_is_rejected(self) -> None:
        """
        機能:
            完全同率時に根拠のない固定順で最大象限を選ばないことを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        del payload["probability"]["dominant_category"]
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_strong_evidence_does_not_need_dominance_projection(self) -> None:
        """
        機能:
            生モデル確率自体が50%超の場合に四択比較値を改変しないことを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        for item in payload["probability"]["evidence"]:
            item["category_scores"] = {
                "nk_up_topix_up": 3,
                "nk_up_topix_down": -3,
                "nk_down_topix_up": -3,
                "nk_down_topix_down": -3,
            }
            item["neutral_observation"] = False
        result = calculate_all(payload)["quadrants"]
        self.assertGreater(result["raw_model_probability_bp"]["nk_up_topix_up"], 5_000)
        self.assertFalse(result["constraint_applied"])
        self.assertEqual(result["raw_model_probability_bp"], result["forced_decision_share_bp"])

    # ----------------------------------------

    def test_future_calibration_date_is_rejected(self) -> None:
        """
        機能:
            分析日以降を学習終了日にした未来参照を拒否することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        payload["calibration"] = {
            "status": "walk_forward",
            "trained_through": "2026-08-20",
            "effective_sample_count": 200,
        }
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_fractional_calibration_sample_count_is_rejected(self) -> None:
        """
        機能:
            校正標本数の小数を黙って整数へ切り捨てず入力不備として拒否することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        payload["calibration"]["effective_sample_count"] = 1.9
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_complete_walk_forward_calibration_allows_direction_wording(self) -> None:
        """
        機能:
            十分な標本・象限件数・損失・信頼度区間を持つ校正だけが方向的中率相当の呼称を許可することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        payload["calibration"] = {
            "status": "walk_forward",
            "trained_through": "2026-08-19",
            "effective_sample_count": 240,
            "model_version": "fixture-model-v1",
            "calibration_version": "fixture-calibration-v1",
            "method": "rolling_origin",
            "horizon_definition": "forecast_valid_until_midpoint_direction",
            "supported_origin_sessions": ["day_session", "night_session"],
            "session_path_definition_id": "current_continuous_session_no_recess_crossing_v1",
            "horizon_seconds": 8100,
            "base_method": "joint_normal",
            "model_id": "fixture-joint-normal-v1",
            "model_structure_id": "fixture-joint-normal-evidence-v1",
            "base_model_artifact_sha256": "98b2ced0327fbb1d7d72b52cbca4c49ecee788ddd42eed6ad584cd035faeed6b",
            "validation_passed": True,
            "quadrant_observation_counts": {
                "nk_up_topix_up": 60,
                "nk_up_topix_down": 60,
                "nk_down_topix_up": 60,
                "nk_down_topix_down": 60,
            },
            "metrics": {
                "multiclass_brier": 0.65,
                "log_loss": 1.25,
                "top_class_accuracy": 0.43666666666666665,
                "reliability_max_abs_error": 0.03,
            },
            "reliability_bins": [
                {"count": 80, "mean_predicted_probability": 0.30, "realized_frequency": 0.29},
                {"count": 80, "mean_predicted_probability": 0.45, "realized_frequency": 0.43},
                {"count": 80, "mean_predicted_probability": 0.62, "realized_frequency": 0.59},
            ],
        }
        payload["probability"]["base_provenance"]["validation_passed"] = True
        calibration = calculate_all(payload)["quadrants"]["calibration"]
        self.assertTrue(calibration["probability_wording_allowed"])
        self.assertTrue(calibration["win_rate_wording_allowed"])
        self.assertIn("取引損益の勝率ではない", calibration["wording_definition"])
        self.assertEqual(calibration["wording_block_reasons"], [])

    # ----------------------------------------

    def test_different_contract_months_are_rejected(self) -> None:
        """
        機能:
            日経とTOPIXで異なる限月を混ぜたNT倍率計算を拒否することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        payload["nt"]["topix_contract_month"] = "2026-12"
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_missing_cross_asset_coverage_is_rejected(self) -> None:
        """
        機能:
            必須市場横断項目の確認結果が欠けた入力を拒否することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        del payload["coverage"]["gold"]
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_stale_used_realtime_fx_is_rejected(self) -> None:
        """
        機能:
            リアルタイム利用と申告した為替の対象時刻が鮮度上限を超える場合に拒否することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        payload["coverage"]["realtime_fx"] = {
            "status": "used",
            "reason": "試験用鮮度テスト",
            "checked_at_jst": "2026-08-20T09:58:00+09:00",
            "data_as_of_jst": "2026-08-20T09:00:00+09:00",
            "source_ids": ["fixture-fx"],
        }
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_evidence_source_must_match_used_coverage(self) -> None:
        """
        機能:
            取得不能とした情報源を証拠台帳だけで利用済みに見せる矛盾を拒否することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        payload["probability"]["evidence"][0]["coverage_item"] = "news"
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_boolean_is_not_accepted_as_numeric_volatility(self) -> None:
        """
        機能:
            Python上で数値に見える真偽値を相対ボラティリティとして受理しないことを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        payload["probability"]["relative_value"]["spread_vol_pct"] = True
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_notional_gate_failure_forces_hold(self) -> None:
        """
        機能:
            採用NT枚数の名目差が5%を超える場合、採用不可かつ全体保留になることを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        payload["nt"]["selected_position"] = {
            "nikkei_product": "micro",
            "nikkei_quantity": 1,
            "topix_mini_quantity": 1,
        }
        result = calculate_all(payload)
        self.assertEqual(result["status"], "hold")
        self.assertFalse(result["nt"]["selected_position"]["local_position_gate_passed"])
        self.assertIn("名目差5%超", result["analysis_hold_reasons"])

    # ----------------------------------------

    def test_three_to_five_percent_mismatch_is_warning_but_position_gate_passes(self) -> None:
        """
        機能:
            名目差3%超5%以内の採用案を警告付き許容として明示することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        selected = calculate_all(build_trade_ready_payload())["nt"]["selected_position"]
        self.assertTrue(selected["local_position_gate_passed"])
        self.assertFalse(selected["passes_preferred_notional_gate"])
        self.assertEqual(selected["warning"], "名目差3%超5%以内")

    # ----------------------------------------

    def test_insufficient_entry_board_quantity_forces_hold(self) -> None:
        """
        機能:
            採用枚数が入口側の表示板数量を超える場合、採用不可かつ全体保留になることを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        payload["nt"]["nikkei_ask_quantity"] = 1
        result = calculate_all(payload)
        self.assertEqual(result["status"], "hold")
        self.assertFalse(result["nt"]["selected_position"]["liquidity_sufficient"])
        self.assertIn("板数量不足", result["analysis_hold_reasons"])

    # ----------------------------------------

    def test_same_input_produces_same_hash_and_output(self) -> None:
        """
        機能:
            同じ入力と固定したOS評価時刻に対する計算結果と監査ハッシュが決定論的であることを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        first = calculate_all(copy.deepcopy(payload))
        second = calculate_all(copy.deepcopy(payload))
        self.assertEqual(first, second)

    # ----------------------------------------

    def test_distributed_sample_input_is_executable(self) -> None:
        """
        機能:
            配布する完全入力例が現在の入力契約を満たし、そのまま純計算できることを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        sample_path = Path(__file__).resolve().parent.parent / "assets" / "計算入力例.json"
        payload = json.loads(sample_path.read_text(encoding="utf-8"))
        result = calculate_all(payload)
        self.assertEqual(result["schema_version"], "6.0.0")
        self.assertEqual(result["model_version"], "5.0.0")
        self.assertEqual(result["status"], "sample_only")
        self.assertTrue(result["sample_only"])

    # ----------------------------------------

    def test_trade_ready_payload_is_ok(self) -> None:
        """
        機能:
            校正・coverage消費・非同率・NT相対価値の全ゲート通過時だけokになることを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        result = calculate_all(build_trade_ready_payload())
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["analysis_hold_reasons"], [])
        self.assertTrue(result["nt"]["selected_position"]["local_position_gate_passed"])

    # ----------------------------------------

    def test_os_runtime_rejects_stale_analysis_candidate(self) -> None:
        """
        機能:
            OS実時計から120秒を超えて古い分析を候補採用不可へ落とすことを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        with patch(
            "index_nt_calculator._runtime_current_jst",
            return_value=datetime.fromisoformat("2026-08-20T10:03:00+09:00"),
        ):
            result = calculate_all(build_trade_ready_payload())
        self.assertEqual(result["status"], "hold")
        self.assertTrue(result["nt"]["selected_position"]["local_position_gate_passed"])
        self.assertIn("分析基準時刻がOS実時計から120秒超古い", result["analysis_hold_reasons"])
        self.assertEqual(result["audit"]["runtime_clock_source"], "OS実時計")
        self.assertEqual(result["audit"]["analysis_age_seconds"], 180.0)

    # ----------------------------------------

    def test_os_runtime_rejects_future_analysis_candidate(self) -> None:
        """
        機能:
            OS実時計より30秒を超えて未来の分析基準時刻を候補採用不可へ落とすことを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        with patch(
            "index_nt_calculator._runtime_current_jst",
            return_value=datetime.fromisoformat("2026-08-20T09:59:00+09:00"),
        ):
            result = calculate_all(build_trade_ready_payload())
        self.assertEqual(result["status"], "hold")
        self.assertIn("分析基準時刻がOS実時計より30秒超未来", result["analysis_hold_reasons"])

    # ----------------------------------------

    def test_os_runtime_rejects_expired_board_and_forecast(self) -> None:
        """
        機能:
            OS実時計が入口板期限と予測期限を過ぎた分析を候補採用不可へ落とすことを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        with patch(
            "index_nt_calculator._runtime_current_jst",
            return_value=datetime.fromisoformat("2026-08-20T12:16:00+09:00"),
        ):
            result = calculate_all(build_trade_ready_payload())
        self.assertEqual(result["status"], "hold")
        self.assertIn("OS実時計で入口板の絶対有効期限を経過", result["analysis_hold_reasons"])
        self.assertIn("OS実時計で予測の絶対有効期限を経過", result["analysis_hold_reasons"])

    # ----------------------------------------

    def test_calculator_audit_records_validation_inputs(self) -> None:
        """
        機能:
            純計算器の監査情報へ入力ハッシュ、OS実時計、検証範囲を記録することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        result = calculate_all(build_trade_ready_payload())
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["analysis_hold_reasons"], [])
        self.assertEqual(result["audit"]["runtime_clock_source"], "OS実時計")
        self.assertEqual(len(result["audit"]["input_sha256"]), 64)
        self.assertIn("数理不変条件", result["audit"]["validation_scope"])

    # ----------------------------------------

    def test_unknown_top_level_field_is_rejected(self) -> None:
        """
        機能:
            現行スキーマにないトップレベル項目を黙って無視せず拒否することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_trade_ready_payload()
        payload["unsupported_field"] = {}
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_uncalibrated_trade_ready_input_forces_hold(self) -> None:
        """
        機能:
            他条件が全て正常でも期限一致校正がなければ売買判断をholdにすることを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_trade_ready_payload()
        payload["calibration"] = {"status": "uncalibrated", "effective_sample_count": 0}
        result = calculate_all(payload)
        self.assertEqual(result["status"], "hold")
        self.assertIn("期限一致のウォークフォワード校正ゲート未達", result["analysis_hold_reasons"])

    # ----------------------------------------

    def test_nt_long_against_short_signal_fails_position_gate(self) -> None:
        """
        機能:
            NTショート側の相対価値が優位なのにNTロングを採用する経路を拒否することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_trade_ready_payload()
        relative = payload["probability"]["relative_value"]
        relative["fundamental_spread_mean_bps"] = -100
        relative["supply_demand_spread_mean_bps"] = -100
        relative["attributions"]["fundamental"][0]["contribution_bps"] = -100
        relative["attributions"]["supply_demand"][0]["contribution_bps"] = -100
        result = calculate_all(payload)
        selected = result["nt"]["selected_position"]
        self.assertEqual(result["status"], "hold")
        self.assertFalse(selected["relative_value_gate"])
        self.assertFalse(selected["local_position_gate_passed"])
        self.assertIn("NT相対価値ゲート未達", result["analysis_hold_reasons"])

    # ----------------------------------------

    def test_nt_inside_cost_band_fails_position_gate(self) -> None:
        """
        機能:
            相対ドリフトが往復コスト帯内であるNT案を採用不可にすることを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_trade_ready_payload()
        relative = payload["probability"]["relative_value"]
        relative["fundamental_spread_mean_bps"] = 0
        relative["supply_demand_spread_mean_bps"] = 0
        relative["attributions"]["fundamental"] = []
        relative["attributions"]["supply_demand"] = []
        relative["round_trip_cost_pct"] = 0.5
        selected = calculate_all(payload)["nt"]["selected_position"]
        self.assertFalse(selected["relative_drift_gate"])
        self.assertFalse(selected["relative_probability_gate"])
        self.assertFalse(selected["local_position_gate_passed"])

    # ----------------------------------------

    def test_scenario_source_must_match_used_coverage(self) -> None:
        """
        機能:
            Polymarket由来シナリオをcoverage未利用のまま事前分布へ混ぜる経路を拒否することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        conditional_basis = configure_scenario_model(payload)
        scenario = {
            "weight_source": "試験用予測市場",
            "coverage_item": "polymarket",
            "source_id": "fixture-polymarket",
            "observed_at_jst": "2026-08-20T09:58:00+09:00",
            "resolution_verified": True,
            "variance_double_count_checked": True,
            "mutually_exclusive_verified": True,
            "exhaustive_verified": True,
            "partition_definition_id": "fixture-polymarket-partition-v1",
            "conditional_probability_basis": conditional_basis,
        }
        payload["probability"]["base"] = {
            "scenario_mixture": [
                {
                    **scenario,
                    "scenario_id": "event",
                    "weight": 0.5,
                    "conditional_probabilities": {
                        "nk_up_topix_up": 0.7,
                        "nk_up_topix_down": 0.1,
                        "nk_down_topix_up": 0.1,
                        "nk_down_topix_down": 0.1,
                    },
                },
                {
                    **scenario,
                    "scenario_id": "no-event",
                    "weight": 0.5,
                    "conditional_probabilities": {
                        "nk_up_topix_up": 0.1,
                        "nk_up_topix_down": 0.1,
                        "nk_down_topix_up": 0.1,
                        "nk_down_topix_down": 0.7,
                    },
                },
            ]
        }
        seal_probability_base(payload)
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_removed_sample_flags_do_not_enable_synthetic_template(self) -> None:
        """
        機能:
            配布例のsample標識だけを外しても合成識別子の検出により実入力へ昇格できないことを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        sample_path = Path(__file__).resolve().parent.parent / "assets" / "計算入力例.json"
        payload = json.loads(sample_path.read_text(encoding="utf-8"))
        payload["sample_only"] = False
        del payload["_sample_notice"]
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_stale_trading_calendar_is_rejected(self) -> None:
        """
        機能:
            古い営業日カレンダー取得時刻を新しいcoverage申告で隠す経路を拒否することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        payload["daily_forecast"]["calendar_fetched_at_jst"] = "2020-01-01T00:00:00+09:00"
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_stale_other_source_link_is_rejected(self) -> None:
        """
        機能:
            固有台帳の現在時刻だけを更新して古い証拠を利用する経路を拒否することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        payload["probability"]["evidence"][0]["observed_at_jst"] = "2026-08-18T09:55:00+09:00"
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_nt_strategy_requires_selected_position(self) -> None:
        """
        機能:
            NT方向を指定しながら採用枚数と端数監査を省略する入力を拒否することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        del payload["nt"]["selected_position"]
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_used_coverage_without_consumer_is_rejected(self) -> None:
        """
        機能:
            coverageをusedと自己申告しながら証拠・イベント・分散へ使わない入力を拒否することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        payload["coverage"]["oil"] = {
            "status": "used",
            "reason": "利用先のない誤申告を再現",
            "checked_at_jst": "2026-08-20T09:59:00+09:00",
            "data_as_of_jst": "2026-08-20T09:59:00+09:00",
            "source_ids": ["fixture-oil"],
        }
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_each_used_coverage_source_requires_consumer(self) -> None:
        """
        機能:
            消費済みcoverage項目へ未使用source_idを混ぜて網羅性を水増しする経路を拒否することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_trade_ready_payload()
        payload["coverage"]["realtime_fx"]["source_ids"].append("fixture-unused-fx")
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_block_only_probabilities_are_separate_distributions(self) -> None:
        """
        機能:
            ファンダメンタルズ単独と需給単独の4象限分布を別々に100%で出すことを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        payload["probability"]["evidence"][0]["category_scores"] = {
            "nk_up_topix_up": 3,
            "nk_up_topix_down": 1,
            "nk_down_topix_up": -1,
            "nk_down_topix_down": -3,
        }
        payload["probability"]["evidence"][0]["neutral_observation"] = False
        payload["probability"]["evidence"][1]["category_scores"] = {
            "nk_up_topix_up": -3,
            "nk_up_topix_down": -1,
            "nk_down_topix_up": 1,
            "nk_down_topix_down": 3,
        }
        payload["probability"]["evidence"][1]["neutral_observation"] = False
        blocks = calculate_all(payload)["quadrants"]["evidence_block_only_probability_bp"]
        self.assertEqual(sum(blocks["fundamental"].values()), 10_000)
        self.assertEqual(sum(blocks["supply_demand"].values()), 10_000)
        self.assertNotEqual(blocks["fundamental"], blocks["supply_demand"])

    # ----------------------------------------

    def test_source_timezone_must_match_original_offset(self) -> None:
        """
        機能:
            発表元timezoneと矛盾する原時刻offsetでイベント時刻を偽装できないことを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        payload["timing"]["events"][0]["source_timezone"] = "America/New_York"
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_berlin_timezone_fallback_accepts_valid_summer_offset(self) -> None:
        """
        機能:
            OSにtzdataがなくても正しいEurope/Berlin夏時間offsetを検証できることを確認する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        event = payload["timing"]["events"][0]
        event["source_timezone"] = "Europe/Berlin"
        event["scheduled_at_source"] = "2026-08-20T05:30:00+02:00"
        result = calculate_all(payload)
        self.assertEqual(result["timing"]["events"][0]["source_timezone"], "Europe/Berlin")

    # ----------------------------------------

    def test_stale_event_consensus_is_rejected(self) -> None:
        """
        機能:
            24時間を超えて古いイベントコンセンサスを方向判断へ利用できないことを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        event = payload["timing"]["events"][0]
        event["consensus_status"] = "used"
        event["consensus_source"] = "fixture://consensus"
        event["consensus_checked_at_jst"] = "2026-08-18T09:59:00+09:00"
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_material_event_must_appear_in_assigned_daily_path(self) -> None:
        """
        機能:
            D1〜D5内の重要イベントIDを該当日のmajor_eventsから欠落できないことを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        later_event = copy.deepcopy(payload["timing"]["events"][0])
        later_event.update(
            {
                "event_id": "d3-event",
                "title": "D3重要イベント",
                "scheduled_at_source": "2026-08-25T10:00:00+09:00",
                "scheduled_at_jst": "2026-08-25T10:00:00+09:00",
                "window_start_jst": "2026-08-25T10:00:00+09:00",
                "window_end_jst": "2026-08-25T10:15:00+09:00",
            }
        )
        payload["timing"]["events"].append(later_event)
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_normalized_identifiers_cannot_be_double_counted(self) -> None:
        """
        機能:
            前後空白だけが異なる証拠IDとイベントIDを重複投入できないことを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        evidence_payload = build_payload()
        evidence_payload["probability"]["evidence"][1]["evidence_key"] = " fundamental-neutral "
        with self.assertRaises(InputValidationError):
            calculate_all(evidence_payload)

        event_payload = build_payload()
        duplicate_event = copy.deepcopy(event_payload["timing"]["events"][0])
        duplicate_event["event_id"] = " test-event "
        event_payload["timing"]["events"].append(duplicate_event)
        with self.assertRaises(InputValidationError):
            calculate_all(event_payload)

    # ----------------------------------------

    def test_declared_cost_cannot_be_below_observed_board_spreads(self) -> None:
        """
        機能:
            両脚の観測スプレッドを下回る往復コスト申告を拒否することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        payload["nt"]["nikkei_bid"] = "39000"
        payload["nt"]["nikkei_ask"] = "41000"
        payload["nt"]["topix_bid"] = "2400"
        payload["nt"]["topix_ask"] = "2600"
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_scheduled_event_occurrence_probability_is_fixed_to_one(self) -> None:
        """
        機能:
            予定済みイベントを発生確率0として期限計算から消せないことを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        payload["timing"]["events"][0]["occurrence_probability"] = 0
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_conditional_event_requires_audited_occurrence_basis(self) -> None:
        """
        機能:
            条件付きイベントへ根拠なしの任意発生確率を入力できないことを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        event = payload["timing"]["events"][0]
        event["release_status"] = "conditional"
        event["occurrence_probability"] = 0.5
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_official_conditional_event_uses_binary_occurrence_state(self) -> None:
        """
        機能:
            公式条件の確認済み状態を根拠種別付きの0または1として受理することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        event = payload["timing"]["events"][0]
        event["release_status"] = "conditional"
        event["occurrence_basis_type"] = "official_condition"
        event["occurrence_probability"] = 1
        result = calculate_all(payload)
        self.assertEqual(result["timing"]["events"][0]["occurrence_basis_type"], "official_condition")

    # ----------------------------------------

    def test_materiality_threshold_is_not_user_tunable(self) -> None:
        """
        機能:
            重要イベント閾値を入力ごとに引き上げて期限を延長できないことを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        payload["timing"]["materiality_threshold"] = 1
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_coverage_data_cannot_postdate_its_check(self) -> None:
        """
        機能:
            coverageの確認時刻より未来のデータ対象時刻を申告できないことを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_trade_ready_payload()
        payload["coverage"]["realtime_fx"]["checked_at_jst"] = "2026-08-20T09:58:00+09:00"
        payload["coverage"]["realtime_fx"]["data_as_of_jst"] = "2026-08-20T09:59:00+09:00"
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_implausible_last_trading_day_is_rejected(self) -> None:
        """
        機能:
            限月年月だけが一致する架空の取引最終日を受け入れないことを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        payload["nt"]["nikkei_last_trading_day"] = "2026-09-30"
        payload["nt"]["topix_last_trading_day"] = "2026-09-30"
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_forecast_cannot_extend_beyond_last_trading_day_without_nt_strategy(self) -> None:
        """
        機能:
            NT戦略なしでも4象限期限を共通限月の取引最終日より後へ延長できないことを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        payload["timing"]["events"][0]["impact_score"] = 0
        payload["daily_forecast"]["days"][0]["major_events"] = []
        later_dates = ("2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11")
        payload["daily_forecast"]["verified_trading_dates"] = list(later_dates)
        for day, target_date in zip(payload["daily_forecast"]["days"], later_dates):
            day["target_at_jst"] = f"{target_date}T15:30:00+09:00"
        payload["timing"]["model_refresh_jst"] = "2026-09-11T10:00:00+09:00"
        payload["probability"]["horizon_end_jst"] = "2026-09-11T10:00:00+09:00"
        payload["nt"]["strategy"] = None
        payload["nt"]["selected_position"] = None
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_d5_must_not_outlive_selected_contract(self) -> None:
        """
        機能:
            取引最終日後のD5を同じ先物アンカーで予測する入力を拒否することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        later_dates = ("2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11")
        payload["daily_forecast"]["verified_trading_dates"] = list(later_dates)
        for day, target_date in zip(payload["daily_forecast"]["days"], later_dates):
            day["target_at_jst"] = f"{target_date}T15:30:00+09:00"
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_last_trading_day_uses_official_session_end_time(self) -> None:
        """
        機能:
            最終取引日の深夜まで期限を延ばさず公式の最終取引可能時刻で拒否することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        payload["timing"]["events"][0]["impact_score"] = 0
        payload["daily_forecast"]["days"][0]["major_events"] = []
        later_dates = ("2026-09-04", "2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10")
        payload["daily_forecast"]["verified_trading_dates"] = list(later_dates)
        for day, target_date in zip(payload["daily_forecast"]["days"], later_dates):
            day["target_at_jst"] = f"{target_date}T15:30:00+09:00"
        payload["daily_forecast"]["days"][-1]["target_at_jst"] = "2026-09-10T23:59:00+09:00"
        payload["timing"]["model_refresh_jst"] = "2026-09-10T23:45:00+09:00"
        payload["probability"]["horizon_end_jst"] = "2026-09-10T23:45:00+09:00"
        payload["nt"]["strategy"] = None
        payload["nt"]["selected_position"] = None
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_last_trading_session_end_cannot_be_self_extended(self) -> None:
        """
        機能:
            共通限月の最終取引可能時刻を公式時刻より後へ自己申告できないことを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        payload["nt"]["last_trading_session_end_jst"] = "2026-09-10T23:59:00+09:00"
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_forecast_deadline_is_capped_by_current_continuous_session(self) -> None:
        """
        機能:
            モデル再推定が現在ザラバより後でも休止時間へ予測期限を延ばさないことを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        payload["timing"]["events"][0]["impact_score"] = 0
        payload["daily_forecast"]["days"][0]["major_events"] = []
        for index_name in ("nikkei", "topix"):
            payload["daily_forecast"]["days"][0][index_name]["incremental_event_sigma_pct"] = 0
            payload["daily_forecast"]["days"][0][index_name]["event_variance_event_ids"] = []
        payload["timing"]["model_refresh_jst"] = "2026-08-28T09:00:00+09:00"
        payload["probability"]["horizon_end_jst"] = "2026-08-20T15:40:00+09:00"
        payload["probability"]["base_provenance"]["horizon_seconds"] = 20400
        payload["probability"]["relative_value"]["model_provenance"]["horizon_seconds"] = 20400
        result = calculate_all(payload)
        self.assertEqual(result["timing"]["forecast_valid_until_jst"], "2026/08/20 15:40:00 (JST)")
        self.assertEqual(result["timing"]["deadline_reasons"], ["現在先物セッションのザラバ終了"])

    # ----------------------------------------

    def test_calibration_bins_require_positive_counts(self) -> None:
        """
        機能:
            標本0のダミー行で信頼度帯3区間要件を満たせないことを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_trade_ready_payload()
        payload["calibration"]["reliability_bins"] = [
            {"count": 240, "mean_predicted_probability": 0.30, "realized_frequency": 0.30},
            {"count": 0, "mean_predicted_probability": 0.45, "realized_frequency": 0.45},
            {"count": 0, "mean_predicted_probability": 0.62, "realized_frequency": 0.62},
        ]
        payload["calibration"]["metrics"]["reliability_max_abs_error"] = 0
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_calibration_bins_require_strict_probability_order(self) -> None:
        """
        機能:
            同じ平均予測確率の行を複製して信頼度帯を水増しできないことを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_trade_ready_payload()
        payload["calibration"]["reliability_bins"] = [
            {"count": 80, "mean_predicted_probability": 0.50, "realized_frequency": 0.50},
            {"count": 80, "mean_predicted_probability": 0.50, "realized_frequency": 0.50},
            {"count": 80, "mean_predicted_probability": 0.62, "realized_frequency": 0.62},
        ]
        payload["calibration"]["metrics"]["reliability_max_abs_error"] = 0
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_calibration_horizon_seconds_must_match_current_deadline(self) -> None:
        """
        機能:
            別の保有時間で得た校正成績を現在の予測期限へ流用できないことを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_trade_ready_payload()
        payload["calibration"]["horizon_seconds"] = 3600
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_calibration_accuracy_must_match_weighted_bin_realization(self) -> None:
        """
        機能:
            確率帯の件数加重実現率と矛盾するトップ分類的中率を拒否することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_trade_ready_payload()
        payload["calibration"]["metrics"]["top_class_accuracy"] = 0.9
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_scenario_mixture_rejects_zero_weight_branch(self) -> None:
        """
        機能:
            重み0の残余分岐で複数シナリオ要件を形だけ満たせないことを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        conditional_basis = configure_scenario_model(payload)
        common = {
            "weight_source": "試験用シナリオ",
            "coverage_item": "other",
            "source_id": "fixture-scenario",
            "observed_at_jst": "2026-08-20T09:58:00+09:00",
            "resolution_verified": True,
            "variance_double_count_checked": True,
            "mutually_exclusive_verified": True,
            "exhaustive_verified": True,
            "partition_definition_id": "fixture-zero-weight-partition-v1",
            "conditional_probability_basis": conditional_basis,
        }
        payload["probability"]["base"] = {
            "scenario_mixture": [
                {
                    **common,
                    "scenario_id": "active",
                    "weight": 1,
                    "conditional_probabilities": {
                        "nk_up_topix_up": 0.4,
                        "nk_up_topix_down": 0.3,
                        "nk_down_topix_up": 0.2,
                        "nk_down_topix_down": 0.1,
                    },
                },
                {
                    **common,
                    "scenario_id": "zero-residual",
                    "weight": 0,
                    "conditional_probabilities": {
                        "nk_up_topix_up": 0.1,
                        "nk_up_topix_down": 0.2,
                        "nk_down_topix_up": 0.3,
                        "nk_down_topix_down": 0.4,
                    },
                },
            ]
        }
        seal_probability_base(payload)
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_no_material_event_requires_neutral_calendar_evidence(self) -> None:
        """
        機能:
            非重要候補だけを残して経済カレンダー由来の方向スコアを捏造できないことを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_trade_ready_payload()
        payload["timing"]["events"][0]["impact_score"] = 0
        payload["daily_forecast"]["days"][0]["major_events"] = []
        payload["probability"]["horizon_end_jst"] = payload["timing"]["model_refresh_jst"]
        payload["probability"]["evidence"].append(
            {
                "evidence_key": "calendar-directional",
                "block": "fundamental",
                "coverage_item": "economic_calendar",
                "source_id": "fixture-economic-calendar",
                "fact": "重要予定なしという確認結果",
                "inference": "不正な方向付け",
                "counterevidence": "重要予定がないため方向差は付けられない",
                "category_scores": {
                    "nk_up_topix_up": 3,
                    "nk_up_topix_down": 3,
                    "nk_down_topix_up": -3,
                    "nk_down_topix_down": -3,
                },
                "quality": 1,
                "independence": 1,
                "observed_at_jst": "2026-08-20T09:50:00+09:00",
                "half_life_hours": 24,
                "neutral_observation": False,
            }
        )
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_neutral_observation_must_have_zero_scores(self) -> None:
        """
        機能:
            neutral_observation=trueと非ゼロ方向スコアの矛盾を拒否することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        payload["probability"]["evidence"][0]["category_scores"]["nk_up_topix_up"] = 1
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_constant_nonzero_evidence_scores_are_rejected(self) -> None:
        """
        機能:
            中心化後に全0となる定数スコアで必須分析ブロックを水増しできないことを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        payload["probability"]["evidence"][0]["category_scores"] = {
            "nk_up_topix_up": 1,
            "nk_up_topix_down": 1,
            "nk_down_topix_up": 1,
            "nk_down_topix_down": 1,
        }
        payload["probability"]["evidence"][0]["neutral_observation"] = False
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_zero_multiplier_evidence_does_not_consume_used_source(self) -> None:
        """
        機能:
            品質0の証拠で必須coverage sourceを利用済みに見せる経路を拒否することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_trade_ready_payload()
        fx_evidence = next(
            row
            for row in payload["probability"]["evidence"]
            if row["coverage_item"] == "realtime_fx"
        )
        fx_evidence["quality"] = 0
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_other_event_reference_must_match_registry(self) -> None:
        """
        機能:
            otherイベントの原典識別子を登録台帳と食い違わせられないことを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        payload["timing"]["events"][0]["source_url_or_document_id"] = "fixture://mismatch"
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_asymmetric_quantiles_require_positive_variance_consistency(self) -> None:
        """
        機能:
            正の累積σに対して全分位0のゼロ幅予測帯を指定できないことを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        payload["daily_forecast"]["days"][0]["nikkei"]["cumulative_quantile_log_return_pct"] = {
            "p05": 0,
            "p16": 0,
            "p84": 0,
            "p95": 0,
        }
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_cumulative_quantile_width_cannot_shrink(self) -> None:
        """
        機能:
            正の増分分散を足しながら翌日の累積予測帯だけを縮小できないことを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        payload["daily_forecast"]["days"][0]["nikkei"]["cumulative_quantile_log_return_pct"] = {
            "p05": -3.0,
            "p16": -1.8,
            "p84": 1.8,
            "p95": 3.0,
        }
        payload["daily_forecast"]["days"][1]["nikkei"]["cumulative_quantile_log_return_pct"] = {
            "p05": -2.34,
            "p16": -1.42,
            "p84": 1.42,
            "p95": 2.34,
        }
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_calendar_data_cannot_postdate_fetch(self) -> None:
        """
        機能:
            営業日データ対象時刻を取得時刻より後へ置く時系列逆転を拒否することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        payload["daily_forecast"]["calendar_data_as_of_jst"] = "2026-08-20T09:59:00+09:00"
        payload["daily_forecast"]["calendar_fetched_at_jst"] = "2026-08-20T09:50:00+09:00"
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_other_source_data_cannot_postdate_check(self) -> None:
        """
        機能:
            other台帳のデータ対象時刻を確認時刻より後へ置く時系列逆転を拒否することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        source = payload["other_sources"]["fixture-trading-calendar"]
        source["data_as_of_jst"] = "2026-08-20T09:59:00+09:00"
        source["checked_at_jst"] = "2026-08-20T09:50:00+09:00"
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_verified_dates_must_be_derived_from_complete_calendar_slice(self) -> None:
        """
        機能:
            中間営業日を省略してD1を数週間後へ飛ばす入力を拒否することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        later_dates = ("2026-09-04", "2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10")
        payload["daily_forecast"]["verified_trading_dates"] = list(later_dates)
        for day, target_date in zip(payload["daily_forecast"]["days"], later_dates):
            day["target_at_jst"] = f"{target_date}T15:30:00+09:00"
        first_date = date(2026, 8, 21)
        final_date = date(2026, 9, 10)
        open_dates = {date.fromisoformat(value) for value in later_dates}
        payload["daily_forecast"]["calendar_sessions"] = [
            {
                "date": (first_date + timedelta(days=offset)).isoformat(),
                "is_trading_day": first_date + timedelta(days=offset) in open_dates,
            }
            for offset in range((final_date - first_date).days + 1)
        ]
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_daily_calendar_derivatives_state_must_match_holiday_division(self) -> None:
        """
        機能:
            D1～D5営業日列で通常営業日の先物休場や土日の先物開場を自己申告できないことを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        for case_name, row_index, derivatives_open in (
            ("weekday_closed", 0, False),
            ("weekend_open", 1, True),
        ):
            with self.subTest(case_name=case_name):
                payload = build_payload()
                payload["daily_forecast"]["calendar_sessions"][row_index][
                    "derivatives_session_open"
                ] = derivatives_open
                with self.assertRaisesRegex(InputValidationError, "derivatives_session_open"):
                    calculate_all(payload)

    # ----------------------------------------

    def test_publication_and_daily_calendars_must_agree_on_overlapping_dates(self) -> None:
        """
        機能:
            J-Quants公表営業日列とD1～D5営業日列に相反する公式営業日状態を併存させない。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        publication_row = next(
            row
            for row in payload["market_data_freshness"]["jquants_publication_calendar"]
            if row["date"] == "2026-08-24"
        )
        publication_row.update(
            {
                "cash_is_trading_day": False,
                "holiday_division": "3",
            }
        )
        with self.assertRaisesRegex(InputValidationError, "daily_forecast.calendar_sessions"):
            calculate_all(payload)

    # ----------------------------------------

    def test_daily_ticks_must_match_supported_contracts(self) -> None:
        """
        機能:
            巨大な任意呼値で中心値と予測帯を0へ丸める入力を拒否することを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        nikkei_payload = build_payload()
        nikkei_payload["daily_forecast"]["nikkei_tick"] = "100000"
        with self.assertRaises(InputValidationError):
            calculate_all(nikkei_payload)

        topix_payload = build_payload()
        topix_payload["daily_forecast"]["topix_tick"] = "10000"
        with self.assertRaises(InputValidationError):
            calculate_all(topix_payload)

    # ----------------------------------------

    def test_eventless_day_rejects_event_drift_and_variance(self) -> None:
        """
        機能:
            major_eventsが空の日へイベント方向またはイベント分散を注入する経路を拒否する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        for case_name in ("drift", "variance"):
            with self.subTest(case_name=case_name):
                payload = build_payload()
                nikkei = payload["daily_forecast"]["days"][1]["nikkei"]
                if case_name == "drift":
                    nikkei["event_drift_bps"] = 1
                    nikkei["drift_attributions"]["event"] = [
                        {"event_id": "test-event", "contribution_bps": 1}
                    ]
                else:
                    nikkei["incremental_event_sigma_pct"] = 0.1
                    nikkei["event_variance_event_ids"] = ["test-event"]
                with self.assertRaises(InputValidationError):
                    calculate_all(payload)

    # ----------------------------------------

    def test_daily_drift_requires_exact_effective_attribution(self) -> None:
        """
        機能:
            日別方向ドリフトの帰属欠落、別ブロック参照、bp不一致、未検証モデルを拒否する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        for case_name in ("missing", "wrong_block", "mismatch", "unvalidated_model"):
            with self.subTest(case_name=case_name):
                payload = build_trade_ready_payload()
                nikkei = payload["daily_forecast"]["days"][0]["nikkei"]
                if case_name == "missing":
                    nikkei["drift_attributions"]["fundamental"] = []
                elif case_name == "wrong_block":
                    nikkei["drift_attributions"]["fundamental"][0]["evidence_key"] = "supply-neutral"
                elif case_name == "mismatch":
                    nikkei["drift_attributions"]["fundamental"][0]["contribution_bps"] = 4
                else:
                    payload["daily_forecast"]["drift_model_provenance"]["validation_passed"] = False
                with self.assertRaises(InputValidationError):
                    calculate_all(payload)

    # ----------------------------------------

    def test_relative_value_requires_attribution_model_and_variance_source(self) -> None:
        """
        機能:
            NT相対平均の帰属と検証済みモデル、および相対σの算出元を必須化する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        for case_name in ("missing", "mismatch", "missing_sigma_source", "unvalidated_model"):
            with self.subTest(case_name=case_name):
                payload = build_trade_ready_payload()
                relative = payload["probability"]["relative_value"]
                if case_name == "missing":
                    relative["attributions"]["fundamental"] = []
                elif case_name == "mismatch":
                    relative["attributions"]["fundamental"][0]["contribution_bps"] = 7
                elif case_name == "missing_sigma_source":
                    relative["spread_vol_source_links"] = []
                else:
                    relative["model_provenance"]["validation_passed"] = False
                with self.assertRaises(InputValidationError):
                    calculate_all(payload)

    # ----------------------------------------

    def test_scenario_conditionals_require_matching_model_basis(self) -> None:
        """
        機能:
            シナリオ条件付き4象限分布の来歴欠落、期限・構造不一致、未封印改変を拒否する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        for case_name in ("missing", "horizon", "future_cutoff", "structure", "unsealed_values"):
            with self.subTest(case_name=case_name):
                payload = build_trade_ready_payload()
                set_valid_scenario_base(payload)
                scenarios = payload["probability"]["base"]["scenario_mixture"]
                if case_name == "missing":
                    del scenarios[0]["conditional_probability_basis"]
                elif case_name == "horizon":
                    scenarios[0]["conditional_probability_basis"]["horizon_seconds"] = 8101
                elif case_name == "future_cutoff":
                    scenarios[0]["conditional_probability_basis"][
                        "training_data_cutoff_jst"
                    ] = "2026-08-20T10:01:00+09:00"
                elif case_name == "structure":
                    scenarios[0]["conditional_probability_basis"]["model_structure_id"] = "mismatch"
                else:
                    for scenario in scenarios:
                        scenario["conditional_probabilities"] = {
                            "nk_up_topix_up": 1,
                            "nk_up_topix_down": 0,
                            "nk_down_topix_up": 0,
                            "nk_down_topix_down": 0,
                        }
                with self.assertRaises(InputValidationError):
                    calculate_all(payload)

    # ----------------------------------------

    def test_calibrated_base_is_bound_to_model_artifact_and_output_hash(self) -> None:
        """
        機能:
            校正済みbaseの方式・構造・モデル成果物と未封印の予測出力差替えを拒否する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        for case_name in ("missing_provenance", "changed_parameters", "method", "structure"):
            with self.subTest(case_name=case_name):
                payload = build_trade_ready_payload()
                if case_name == "missing_provenance":
                    del payload["probability"]["base_provenance"]
                elif case_name == "changed_parameters":
                    base = payload["probability"]["base"]["joint_normal"]
                    base["nikkei_mean_pct"] = 100
                    base["topix_mean_pct"] = 100
                    base["nikkei_vol_pct"] = 0.000001
                    base["topix_vol_pct"] = 0.000001
                elif case_name == "method":
                    payload["calibration"]["base_method"] = "historical_counts"
                else:
                    payload["calibration"]["model_structure_id"] = "mismatch"
                with self.assertRaises(InputValidationError):
                    calculate_all(payload)

    # ----------------------------------------

    def test_dynamic_base_output_can_be_resealed_under_same_model_artifact(self) -> None:
        """
        機能:
            同じ検証済みモデルの新しい動的予測は出力SHAを再計算すれば校正を再利用できることを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_trade_ready_payload()
        payload["probability"]["base"]["joint_normal"]["nikkei_mean_pct"] = 0.1
        seal_probability_base(payload)
        result = calculate_all(payload)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["quadrants"]["calibration"]["probability_wording_allowed"])

    # ----------------------------------------

    def test_relative_event_after_probability_horizon_is_rejected(self) -> None:
        """
        機能:
            4象限・NT評価期限より後のイベントを期限内NT相対平均へ帰属できないことを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_trade_ready_payload()
        payload["probability"]["relative_value"]["event_spread_mean_bps"] = 100
        payload["probability"]["relative_value"]["attributions"]["event"] = [
            {"event_id": "test-event", "contribution_bps": 100}
        ]
        with self.assertRaises(InputValidationError):
            calculate_all(payload)

    # ----------------------------------------

    def test_utf8_standard_input_cli_is_supported(self) -> None:
        """
        機能:
            Windowsの既定コードページに依存せずUTF-8標準入力をCLIが処理できることを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        script_path = Path(__file__).with_name("index_nt_calculator.py")
        input_bytes = json.dumps(build_payload(), ensure_ascii=False).encode("utf-8")
        completed = subprocess.run(
            [sys.executable, "-B", str(script_path)],
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", errors="replace"))
        result = json.loads(completed.stdout.decode("utf-8"))
        self.assertEqual(result["status"], "hold")
        self.assertTrue(result["analysis_hold_reasons"])

    # ----------------------------------------

    def test_holiday_trading_flag_requires_a_division_three_session_chain(self) -> None:
        """
        機能:
            通常ナイトへ祝日取引flagだけを付けてJPX取引日を先送りする入力を拒否する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        configure_night_session_payload(payload, "2026-08-20T17:01:00+09:00")
        payload["timing"]["is_derivatives_holiday_trading"] = True
        with self.assertRaisesRegex(InputValidationError, "HolidayDivision=3有無"):
            calculate_all(payload)

    # ----------------------------------------

    def test_night_cash_calendar_division_must_match_as_of_date(self) -> None:
        """
        機能:
            ナイト開始日内の現物休日区分をsession_calendarと異なる値へ自己申告できないことを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        configure_night_session_payload(payload, "2026-08-20T17:01:00+09:00")
        payload["timing"].update(
            {
                "cash_market_state": "holiday_closed",
                "cash_market_holiday_division": "0",
            }
        )
        with self.assertRaisesRegex(InputValidationError, "as_of_jst暦日"):
            calculate_all(payload)

    # ----------------------------------------

    def test_jquants_daily_expected_update_uses_current_cash_business_date(self) -> None:
        """
        機能:
            現物営業日のJ-Quants日次更新予定を過去日へずらして旧データを最新扱いできないことを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        update = payload["market_data_freshness"]["jquants_dataset_updates"][0]
        update["latest_expected_update_jst"] = "2026-08-19T16:30:00+09:00"
        update["availability_status"] = "updated"
        with self.assertRaisesRegex(InputValidationError, "共有公表営業日カレンダー.*導出値"):
            calculate_all(payload)

    # ----------------------------------------

    def test_unavailable_jquants_candidate_does_not_require_a_fake_update_row(self) -> None:
        """
        機能:
            未取得のJ-Quants候補を理由付きで残しつつ、有効な選択sourceだけを更新監査できることを検証する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = build_payload()
        payload["market_data_freshness"]["source_registry"]["fixture-jquants-daily-backup"] = {
            "provider_family": "jquants",
            "data_role": "scheduled_history",
            "coverage_items": ["jquants"],
            "dataset_id": "daily-prices",
            "series_key": "jquants-daily-prices",
            "instrument_type": "cash_index_history",
            "symbol": "TOPIX/NK225-history-backup",
            "contract_month": "not_applicable",
            "quote_type": "daily_ohlc",
            "status": "unavailable",
            "status_reason": "代替経路を確認したが取得不能",
            "checked_at_jst": "2026-08-20T09:59:00+09:00",
        }
        selection = next(
            row
            for row in payload["market_data_freshness"]["source_selection_audit"]
            if row["selection_key"] == "jquants_daily_history"
        )
        selection["candidate_source_ids"].append("fixture-jquants-daily-backup")
        result = calculate_all(payload)
        self.assertEqual(result["market_data_freshness"]["source_registry"][
            "fixture-jquants-daily-backup"
        ]["status"], "unavailable")

    # ----------------------------------------

    def test_market_source_semantics_and_same_tier_freshness_are_enforced(self) -> None:
        """
        機能:
            価格sourceのcoverage用途偽装と、同一tier内で古い候補を選ぶ入力を拒否する。

        引数:
            self (IndexNtCalculatorTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        semantic_payload = build_trade_ready_payload()
        semantic_payload["market_data_freshness"]["source_registry"]["fixture-fx"][
            "coverage_items"
        ] = ["options"]
        with self.assertRaisesRegex(InputValidationError, "source_registry.coverage_items"):
            calculate_all(semantic_payload)

        freshness_payload = build_trade_ready_payload()
        fresher_source = copy.deepcopy(
            freshness_payload["market_data_freshness"]["source_registry"]["fixture-fx"]
        )
        fresher_source.update(
            {
                "symbol": "USDJPY-SECONDARY",
                "data_as_of_jst": "2026-08-20T09:59:01+09:00",
                "fetched_at_jst": "2026-08-20T09:59:01+09:00",
                "checked_at_jst": "2026-08-20T09:59:01+09:00",
            }
        )
        freshness_payload["market_data_freshness"]["source_registry"][
            "fixture-fx-fresher"
        ] = fresher_source
        selection = next(
            row
            for row in freshness_payload["market_data_freshness"]["source_selection_audit"]
            if row["selection_key"] == "usd_jpy_current_context"
        )
        selection["candidate_source_ids"].append("fixture-fx-fresher")
        with self.assertRaisesRegex(InputValidationError, "同一tier内で最も新しい観測"):
            calculate_all(freshness_payload)


if __name__ == "__main__":
    unittest.main()
