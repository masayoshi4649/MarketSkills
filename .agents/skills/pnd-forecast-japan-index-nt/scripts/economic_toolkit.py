#!/usr/bin/env python3
"""経済学教科書に対応する取得済み数値の純計算を行う。"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any


TOOLKIT_SCHEMA_VERSION = "1.0.0"


class EconomicInputError(ValueError):
    """経済計算の入力契約違反を表す。"""


# ----------------------------------------


def _finite_float(value: Any, name: str) -> float:
    """
    機能:
        入力値を有限の浮動小数点数へ変換する。

    引数:
        value (Any): 変換対象の値。
        name (str): エラー表示用の項目名。

    返り値:
        float: 有限性を確認した数値。
    """
    if isinstance(value, bool):
        raise EconomicInputError(f"{name} は数値で指定してください。")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise EconomicInputError(f"{name} は数値で指定してください。") from exc
    if not math.isfinite(result):
        raise EconomicInputError(f"{name} は有限値で指定してください。")
    return result


# ----------------------------------------


def _positive_float(value: Any, name: str) -> float:
    """
    機能:
        入力値を正の有限浮動小数点数として検証する。

    引数:
        value (Any): 検証対象の値。
        name (str): エラー表示用の項目名。

    返り値:
        float: 0より大きい有限値。
    """
    result = _finite_float(value, name)
    if result <= 0.0:
        raise EconomicInputError(f"{name} は0より大きくしてください。")
    return result


# ----------------------------------------


def _nonnegative_float(value: Any, name: str) -> float:
    """
    機能:
        入力値を非負の有限浮動小数点数として検証する。

    引数:
        value (Any): 検証対象の値。
        name (str): エラー表示用の項目名。

    返り値:
        float: 0以上の有限値。
    """
    result = _finite_float(value, name)
    if result < 0.0:
        raise EconomicInputError(f"{name} は0以上にしてください。")
    return result


# ----------------------------------------


def _numeric_sequence(value: Any, name: str, minimum_length: int = 1) -> list[float]:
    """
    機能:
        JSON配列を有限数値の配列へ正規化する。

    引数:
        value (Any): 数値配列として検証する値。
        name (str): エラー表示用の項目名。
        minimum_length (int): 必要な最小要素数。

    返り値:
        list[float]: 有限数値へ変換した配列。
    """
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise EconomicInputError(f"{name} は配列で指定してください。")
    result = [_finite_float(item, f"{name}[{index}]") for index, item in enumerate(value)]
    if len(result) < minimum_length:
        raise EconomicInputError(f"{name} は{minimum_length}要素以上にしてください。")
    return result


# ----------------------------------------


def _normalized_weights(value: Any, length: int, name: str = "weights") -> list[float]:
    """
    機能:
        省略可能な正のウェイト列を合計1へ正規化する。

    引数:
        value (Any): ウェイト配列。Noneなら等ウェイトを使う。
        length (int): 必要なウェイト数。
        name (str): エラー表示用の項目名。

    返り値:
        list[float]: 合計1の正規化済みウェイト。
    """
    if value is None:
        return [1.0 / length] * length
    weights = _numeric_sequence(value, name, length)
    if len(weights) != length:
        raise EconomicInputError(f"{name} の要素数を対象値と一致させてください。")
    if any(weight <= 0.0 for weight in weights):
        raise EconomicInputError(f"{name} はすべて0より大きくしてください。")
    total = sum(weights)
    return [weight / total for weight in weights]


# ----------------------------------------


def arc_elasticity(parameters: Mapping[str, Any]) -> dict[str, Any]:
    """
    機能:
        価格と数量の2点から中点法の弧弾力性を計算する。

    引数:
        parameters (Mapping[str, Any]): price_initial、price_final、quantity_initial、quantity_final。

    返り値:
        dict[str, Any]: 弾力性、絶対値、弾力性区分。
    """
    price_initial = _positive_float(parameters.get("price_initial"), "price_initial")
    price_final = _positive_float(parameters.get("price_final"), "price_final")
    quantity_initial = _nonnegative_float(parameters.get("quantity_initial"), "quantity_initial")
    quantity_final = _nonnegative_float(parameters.get("quantity_final"), "quantity_final")
    average_quantity = (quantity_initial + quantity_final) / 2.0
    if average_quantity == 0.0:
        raise EconomicInputError("平均数量が0のため弾力性を計算できません。")
    price_change_rate = (price_final - price_initial) / ((price_initial + price_final) / 2.0)
    if math.isclose(price_change_rate, 0.0, rel_tol=0.0, abs_tol=1e-15):
        raise EconomicInputError("価格変化が0のため弾力性を計算できません。")
    quantity_change_rate = (quantity_final - quantity_initial) / average_quantity
    elasticity = quantity_change_rate / price_change_rate
    absolute_elasticity = abs(elasticity)
    if math.isclose(absolute_elasticity, 1.0, rel_tol=0.0, abs_tol=1e-12):
        classification = "単位弾力的"
    elif absolute_elasticity > 1.0:
        classification = "弾力的"
    else:
        classification = "非弾力的"
    return {
        "elasticity": elasticity,
        "absolute_elasticity": absolute_elasticity,
        "classification": classification,
        "method": "midpoint_arc_elasticity",
    }


# ----------------------------------------


def tax_incidence(parameters: Mapping[str, Any]) -> dict[str, Any]:
    """
    機能:
        需要と供給の局所弾力性から単位税の消費者・生産者負担を計算する。

    引数:
        parameters (Mapping[str, Any]): demand_elasticity_abs、supply_elasticity、tax_per_unit。

    返り値:
        dict[str, Any]: 両者の負担比率と単位当たり負担額。
    """
    demand = _positive_float(parameters.get("demand_elasticity_abs"), "demand_elasticity_abs")
    supply = _positive_float(parameters.get("supply_elasticity"), "supply_elasticity")
    tax = _nonnegative_float(parameters.get("tax_per_unit", 0.0), "tax_per_unit")
    consumer_share = supply / (demand + supply)
    producer_share = demand / (demand + supply)
    return {
        "consumer_share": consumer_share,
        "producer_share": producer_share,
        "consumer_burden_per_unit": tax * consumer_share,
        "producer_burden_per_unit": tax * producer_share,
        "assumption": "局所的な競争均衡と一定弾力性",
    }


# ----------------------------------------


def fisher_real_rate(parameters: Mapping[str, Any]) -> dict[str, Any]:
    """
    機能:
        名目金利と期待インフレ率から厳密なFisher実質金利を計算する。

    引数:
        parameters (Mapping[str, Any]): nominal_rate_pct、expected_inflation_pct。

    返り値:
        dict[str, Any]: 厳密値と一次近似の実質金利率。
    """
    nominal_pct = _finite_float(parameters.get("nominal_rate_pct"), "nominal_rate_pct")
    inflation_pct = _finite_float(parameters.get("expected_inflation_pct"), "expected_inflation_pct")
    if inflation_pct <= -100.0:
        raise EconomicInputError("expected_inflation_pct は-100%より大きくしてください。")
    exact_pct = ((1.0 + nominal_pct / 100.0) / (1.0 + inflation_pct / 100.0) - 1.0) * 100.0
    return {
        "exact_real_rate_pct": exact_pct,
        "linear_approximation_pct": nominal_pct - inflation_pct,
    }


# ----------------------------------------


def taylor_rule_gap(parameters: Mapping[str, Any]) -> dict[str, Any]:
    """
    機能:
        指定係数のTaylor型ルール値と現行政策金利との差を計算する。

    引数:
        parameters (Mapping[str, Any]): 中立実質金利、物価、目標、需給ギャップ、係数、現行金利。

    返り値:
        dict[str, Any]: ルール金利、現行金利との差、各寄与。
    """
    neutral_real = _finite_float(parameters.get("neutral_real_rate_pct"), "neutral_real_rate_pct")
    inflation = _finite_float(parameters.get("inflation_pct"), "inflation_pct")
    target = _finite_float(parameters.get("inflation_target_pct"), "inflation_target_pct")
    output_gap = _finite_float(parameters.get("output_gap_pct"), "output_gap_pct")
    inflation_weight = _finite_float(parameters.get("inflation_weight", 0.5), "inflation_weight")
    output_weight = _finite_float(parameters.get("output_gap_weight", 0.5), "output_gap_weight")
    current_rate = _finite_float(parameters.get("current_policy_rate_pct"), "current_policy_rate_pct")
    inflation_gap_contribution = inflation_weight * (inflation - target)
    output_gap_contribution = output_weight * output_gap
    rule_rate = neutral_real + inflation + inflation_gap_contribution + output_gap_contribution
    return {
        "rule_rate_pct": rule_rate,
        "gap_to_current_pct_points": rule_rate - current_rate,
        "neutral_plus_inflation_pct": neutral_real + inflation,
        "inflation_gap_contribution_pct_points": inflation_gap_contribution,
        "output_gap_contribution_pct_points": output_gap_contribution,
        "warning": "記述的ルールであり中央銀行の反応関数推定値ではない",
    }


# ----------------------------------------


def prospect_value(parameters: Mapping[str, Any]) -> dict[str, Any]:
    """
    機能:
        参照点からの利得・損失へ累乗型のプロスペクト価値関数を適用する。

    引数:
        parameters (Mapping[str, Any]): outcome、reference、gain_alpha、loss_beta、loss_aversion。

    返り値:
        dict[str, Any]: 参照点差、価値、利得・損失領域。
    """
    outcome = _finite_float(parameters.get("outcome"), "outcome")
    reference = _finite_float(parameters.get("reference"), "reference")
    gain_alpha = _positive_float(parameters.get("gain_alpha", 0.88), "gain_alpha")
    loss_beta = _positive_float(parameters.get("loss_beta", 0.88), "loss_beta")
    loss_aversion = _positive_float(parameters.get("loss_aversion", 2.25), "loss_aversion")
    difference = outcome - reference
    if difference >= 0.0:
        value = difference**gain_alpha
        domain = "gain"
    else:
        value = -loss_aversion * ((-difference) ** loss_beta)
        domain = "loss"
    return {
        "reference_difference": difference,
        "prospect_value": value,
        "domain": domain,
        "warning": "価値関数の出力は市場確率・期待収益ではない",
    }


# ----------------------------------------


def gini_coefficient(parameters: Mapping[str, Any]) -> dict[str, Any]:
    """
    機能:
        非負値と任意ウェイトからLorenz曲線の台形積分によるGini係数を計算する。

    引数:
        parameters (Mapping[str, Any]): valuesと省略可能なweights。

    返り値:
        dict[str, Any]: Gini係数、加重平均、観測数。
    """
    values = _numeric_sequence(parameters.get("values"), "values", 1)
    if any(value < 0.0 for value in values):
        raise EconomicInputError("values は非負にしてください。")
    weights = _normalized_weights(parameters.get("weights"), len(values))
    weighted_mean = sum(value * weight for value, weight in zip(values, weights))
    if weighted_mean <= 0.0:
        raise EconomicInputError("加重平均が0の分布ではGini係数を定義できません。")
    ordered = sorted(zip(values, weights), key=lambda pair: pair[0])
    cumulative_population = 0.0
    cumulative_income = 0.0
    lorenz_area_twice = 0.0
    for value, weight in ordered:
        next_population = cumulative_population + weight
        next_income = cumulative_income + value * weight / weighted_mean
        lorenz_area_twice += (cumulative_income + next_income) * (next_population - cumulative_population)
        cumulative_population = next_population
        cumulative_income = next_income
    return {
        "gini": max(0.0, min(1.0, 1.0 - lorenz_area_twice)),
        "weighted_mean": weighted_mean,
        "observation_count": len(values),
    }


# ----------------------------------------


def _payoff_matrix(value: Any, name: str) -> list[list[float]]:
    """
    機能:
        2×2利得行列を有限数値へ正規化する。

    引数:
        value (Any): 2行2列の配列。
        name (str): エラー表示用の項目名。

    返り値:
        list[list[float]]: 2×2の数値行列。
    """
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != 2:
        raise EconomicInputError(f"{name} は2×2配列にしてください。")
    rows = [_numeric_sequence(row, f"{name}[{index}]", 2) for index, row in enumerate(value)]
    if any(len(row) != 2 for row in rows):
        raise EconomicInputError(f"{name} は2×2配列にしてください。")
    return rows


# ----------------------------------------


def mixed_nash_2x2(parameters: Mapping[str, Any]) -> dict[str, Any]:
    """
    機能:
        非退化2人2戦略ゲームの完全混合Nash均衡候補を計算する。

    引数:
        parameters (Mapping[str, Any]): row_payoffsとcolumn_payoffsの2×2行列。

    返り値:
        dict[str, Any]: 各プレイヤーの第1戦略確率と均衡期待利得。
    """
    row = _payoff_matrix(parameters.get("row_payoffs"), "row_payoffs")
    column = _payoff_matrix(parameters.get("column_payoffs"), "column_payoffs")
    row_denominator = row[0][0] - row[0][1] - row[1][0] + row[1][1]
    column_denominator = column[0][0] - column[0][1] - column[1][0] + column[1][1]
    if math.isclose(row_denominator, 0.0, rel_tol=0.0, abs_tol=1e-15):
        raise EconomicInputError("行プレイヤーの無差別条件が退化しています。")
    if math.isclose(column_denominator, 0.0, rel_tol=0.0, abs_tol=1e-15):
        raise EconomicInputError("列プレイヤーの無差別条件が退化しています。")
    column_first_probability = (row[1][1] - row[0][1]) / row_denominator
    row_first_probability = (column[1][1] - column[1][0]) / column_denominator
    if not 0.0 < row_first_probability < 1.0 or not 0.0 < column_first_probability < 1.0:
        raise EconomicInputError("完全混合均衡が単位区間の内部にありません。純粋戦略を別途確認してください。")
    row_payoff = (
        column_first_probability * row[0][0]
        + (1.0 - column_first_probability) * row[0][1]
    )
    column_payoff = (
        row_first_probability * column[0][0]
        + (1.0 - row_first_probability) * column[1][0]
    )
    return {
        "row_first_strategy_probability": row_first_probability,
        "column_first_strategy_probability": column_first_probability,
        "row_expected_payoff": row_payoff,
        "column_expected_payoff": column_payoff,
        "warning": "利得と戦略集合を固定した一回ゲームの候補",
    }


# ----------------------------------------


def present_value(parameters: Mapping[str, Any]) -> dict[str, Any]:
    """
    機能:
        等間隔キャッシュフロー列を指定割引率で現在価値へ変換する。

    引数:
        parameters (Mapping[str, Any]): cash_flows、discount_rate_pct、first_period。

    返り値:
        dict[str, Any]: 現在価値合計と期間別現在価値。
    """
    cash_flows = _numeric_sequence(parameters.get("cash_flows"), "cash_flows", 1)
    discount_pct = _finite_float(parameters.get("discount_rate_pct"), "discount_rate_pct")
    if discount_pct <= -100.0:
        raise EconomicInputError("discount_rate_pct は-100%より大きくしてください。")
    first_period_raw = parameters.get("first_period", 0)
    if isinstance(first_period_raw, bool) or not isinstance(first_period_raw, int) or first_period_raw < 0:
        raise EconomicInputError("first_period は0以上の整数にしてください。")
    rate = discount_pct / 100.0
    contributions = [
        cash_flow / ((1.0 + rate) ** (first_period_raw + index))
        for index, cash_flow in enumerate(cash_flows)
    ]
    return {
        "present_value": sum(contributions),
        "period_present_values": contributions,
        "first_period": first_period_raw,
    }


# ----------------------------------------


def covered_interest_parity(parameters: Mapping[str, Any]) -> dict[str, Any]:
    """
    機能:
        単利近似のカバー付き金利平価から理論フォワードを計算する。

    引数:
        parameters (Mapping[str, Any]): spot、domestic_rate_pct、foreign_rate_pct、tenor_years、observed_forward。

    返り値:
        dict[str, Any]: 理論フォワード、フォワードポイント、任意の観測乖離。
    """
    spot = _positive_float(parameters.get("spot"), "spot")
    domestic_pct = _finite_float(parameters.get("domestic_rate_pct"), "domestic_rate_pct")
    foreign_pct = _finite_float(parameters.get("foreign_rate_pct"), "foreign_rate_pct")
    tenor = _positive_float(parameters.get("tenor_years"), "tenor_years")
    domestic_factor = 1.0 + domestic_pct / 100.0 * tenor
    foreign_factor = 1.0 + foreign_pct / 100.0 * tenor
    if domestic_factor <= 0.0 or foreign_factor <= 0.0:
        raise EconomicInputError("金利と期間から得る単利係数を正にしてください。")
    theoretical_forward = spot * domestic_factor / foreign_factor
    result: dict[str, Any] = {
        "theoretical_forward": theoretical_forward,
        "forward_points": theoretical_forward - spot,
        "method": "simple_interest_covered_interest_parity",
    }
    if parameters.get("observed_forward") is not None:
        observed = _positive_float(parameters.get("observed_forward"), "observed_forward")
        result["observed_forward"] = observed
        result["observed_minus_theoretical"] = observed - theoretical_forward
        result["deviation_bps_of_spot"] = (observed - theoretical_forward) / spot * 10_000.0
    return result


# ----------------------------------------


def mincer_earnings(parameters: Mapping[str, Any]) -> dict[str, Any]:
    """
    機能:
        Mincer型賃金式から条件付き対数賃金と水準指数を計算する。

    引数:
        parameters (Mapping[str, Any]): 切片、就学年数、経験年数、各係数。

    返り値:
        dict[str, Any]: 予測対数賃金、水準指数、各寄与。
    """
    intercept = _finite_float(parameters.get("intercept"), "intercept")
    schooling = _nonnegative_float(parameters.get("schooling_years"), "schooling_years")
    experience = _nonnegative_float(parameters.get("experience_years"), "experience_years")
    schooling_coefficient = _finite_float(
        parameters.get("schooling_coefficient"), "schooling_coefficient"
    )
    experience_coefficient = _finite_float(
        parameters.get("experience_coefficient"), "experience_coefficient"
    )
    experience_squared_coefficient = _finite_float(
        parameters.get("experience_squared_coefficient"), "experience_squared_coefficient"
    )
    schooling_contribution = schooling_coefficient * schooling
    experience_contribution = experience_coefficient * experience
    experience_squared_contribution = experience_squared_coefficient * experience**2
    log_earnings = (
        intercept
        + schooling_contribution
        + experience_contribution
        + experience_squared_contribution
    )
    try:
        earnings_index = math.exp(log_earnings)
    except OverflowError as exc:
        raise EconomicInputError("係数から得る賃金水準が計算範囲を超えました。") from exc
    if not math.isfinite(earnings_index):
        raise EconomicInputError("係数から得る賃金水準が有限値ではありません。")
    return {
        "predicted_log_earnings": log_earnings,
        "predicted_earnings_index": earnings_index,
        "schooling_contribution": schooling_contribution,
        "experience_contribution": experience_contribution,
        "experience_squared_contribution": experience_squared_contribution,
        "warning": "係数は識別済み推定結果を別途入力し、因果効果と自動的に解釈しない",
    }


# ----------------------------------------


def social_npv(parameters: Mapping[str, Any]) -> dict[str, Any]:
    """
    機能:
        便益、私的費用、外部費用の時系列から社会的現在価値を計算する。

    引数:
        parameters (Mapping[str, Any]): benefits、private_costs、external_costs、割引率、first_period。

    返り値:
        dict[str, Any]: 各現在価値と社会的純現在価値。
    """
    benefits = _numeric_sequence(parameters.get("benefits"), "benefits", 1)
    private_costs = _numeric_sequence(parameters.get("private_costs"), "private_costs", 1)
    external_costs = _numeric_sequence(parameters.get("external_costs"), "external_costs", 1)
    if not len(benefits) == len(private_costs) == len(external_costs):
        raise EconomicInputError("benefits、private_costs、external_costsの要素数を一致させてください。")
    common = {
        "discount_rate_pct": parameters.get("discount_rate_pct"),
        "first_period": parameters.get("first_period", 0),
    }
    benefit_pv = present_value({"cash_flows": benefits, **common})["present_value"]
    private_cost_pv = present_value({"cash_flows": private_costs, **common})["present_value"]
    external_cost_pv = present_value({"cash_flows": external_costs, **common})["present_value"]
    return {
        "benefit_present_value": benefit_pv,
        "private_cost_present_value": private_cost_pv,
        "external_cost_present_value": external_cost_pv,
        "social_net_present_value": benefit_pv - private_cost_pv - external_cost_pv,
    }


# ----------------------------------------


def poverty_gap(parameters: Mapping[str, Any]) -> dict[str, Any]:
    """
    機能:
        所得、貧困線、任意ウェイトから貧困率と平均貧困ギャップを計算する。

    引数:
        parameters (Mapping[str, Any]): incomes、poverty_line、任意のweights。

    返り値:
        dict[str, Any]: 貧困率、全体平均・貧困層内平均の不足率。
    """
    incomes = _numeric_sequence(parameters.get("incomes"), "incomes", 1)
    if any(income < 0.0 for income in incomes):
        raise EconomicInputError("incomes は非負にしてください。")
    poverty_line = _positive_float(parameters.get("poverty_line"), "poverty_line")
    weights = _normalized_weights(parameters.get("weights"), len(incomes))
    gaps = [max(poverty_line - income, 0.0) / poverty_line for income in incomes]
    poor_weights = [weight for income, weight in zip(incomes, weights) if income < poverty_line]
    headcount = sum(poor_weights)
    population_gap = sum(weight * gap for weight, gap in zip(weights, gaps))
    poor_only_gap = population_gap / headcount if headcount > 0.0 else 0.0
    return {
        "poverty_headcount_ratio": headcount,
        "poverty_gap_index": population_gap,
        "average_gap_among_poor": poor_only_gap,
    }


# ----------------------------------------


def incremental_cost_effectiveness(parameters: Mapping[str, Any]) -> dict[str, Any]:
    """
    機能:
        新規介入と比較対象の増分費用効果比を計算する。

    引数:
        parameters (Mapping[str, Any]): 両案の費用と効果。

    返り値:
        dict[str, Any]: 増分費用、増分効果、ICER、費用効果平面の象限。
    """
    cost_new = _finite_float(parameters.get("cost_new"), "cost_new")
    cost_comparator = _finite_float(parameters.get("cost_comparator"), "cost_comparator")
    effect_new = _finite_float(parameters.get("effect_new"), "effect_new")
    effect_comparator = _finite_float(parameters.get("effect_comparator"), "effect_comparator")
    incremental_cost = cost_new - cost_comparator
    incremental_effect = effect_new - effect_comparator
    if math.isclose(incremental_effect, 0.0, rel_tol=0.0, abs_tol=1e-15):
        raise EconomicInputError("増分効果が0のためICERを定義できません。")
    if incremental_cost >= 0.0 and incremental_effect > 0.0:
        quadrant = "more_costly_more_effective"
    elif incremental_cost < 0.0 and incremental_effect > 0.0:
        quadrant = "dominant"
    elif incremental_cost >= 0.0 and incremental_effect < 0.0:
        quadrant = "dominated"
    else:
        quadrant = "less_costly_less_effective"
    return {
        "incremental_cost": incremental_cost,
        "incremental_effect": incremental_effect,
        "icer": incremental_cost / incremental_effect,
        "cost_effectiveness_plane": quadrant,
        "warning": "採用判断には閾値、予算影響、公平性、不確実性を別途確認する",
    }


CALCULATORS: dict[str, Callable[[Mapping[str, Any]], dict[str, Any]]] = {
    "arc_elasticity": arc_elasticity,
    "tax_incidence": tax_incidence,
    "fisher_real_rate": fisher_real_rate,
    "taylor_rule_gap": taylor_rule_gap,
    "prospect_value": prospect_value,
    "gini_coefficient": gini_coefficient,
    "mixed_nash_2x2": mixed_nash_2x2,
    "present_value": present_value,
    "covered_interest_parity": covered_interest_parity,
    "mincer_earnings": mincer_earnings,
    "social_npv": social_npv,
    "poverty_gap": poverty_gap,
    "incremental_cost_effectiveness": incremental_cost_effectiveness,
}


# ----------------------------------------


def calculate_model(model: str, parameters: Mapping[str, Any]) -> dict[str, Any]:
    """
    機能:
        計算名に対応する経済モデルを選び、単一計算を実行する。

    引数:
        model (str): CALCULATORSへ登録した計算名。
        parameters (Mapping[str, Any]): 選択モデルの入力値。

    返り値:
        dict[str, Any]: モデル名と計算結果。
    """
    if model not in CALCULATORS:
        raise EconomicInputError(f"未対応のmodelです: {model}")
    if not isinstance(parameters, Mapping):
        raise EconomicInputError("parameters はJSONオブジェクトにしてください。")
    return {"model": model, "result": CALCULATORS[model](parameters)}


# ----------------------------------------


def calculate_batch(payload: Mapping[str, Any]) -> dict[str, Any]:
    """
    機能:
        JSONペイロード内の複数経済計算を入力順に一括実行する。

    引数:
        payload (Mapping[str, Any]): schema_versionとcalculationsを持つ入力。

    返り値:
        dict[str, Any]: スキーマ版、ネット未使用監査、計算結果配列。
    """
    if not isinstance(payload, Mapping):
        raise EconomicInputError("入力全体はJSONオブジェクトにしてください。")
    allowed_fields = {"schema_version", "calculations"}
    unknown_fields = sorted(set(payload) - allowed_fields)
    if unknown_fields:
        raise EconomicInputError("未対応のトップレベル項目があります: " + ", ".join(unknown_fields))
    if payload.get("schema_version") != TOOLKIT_SCHEMA_VERSION:
        raise EconomicInputError(f"schema_version は {TOOLKIT_SCHEMA_VERSION} にしてください。")
    calculations = payload.get("calculations")
    if isinstance(calculations, (str, bytes)) or not isinstance(calculations, Sequence):
        raise EconomicInputError("calculations は配列にしてください。")
    if not calculations:
        raise EconomicInputError("calculations は1件以上にしてください。")
    seen_ids: set[str] = set()
    results: list[dict[str, Any]] = []
    for index, raw_calculation in enumerate(calculations):
        if not isinstance(raw_calculation, Mapping):
            raise EconomicInputError(f"calculations[{index}] はJSONオブジェクトにしてください。")
        calculation_id = raw_calculation.get("id")
        if not isinstance(calculation_id, str) or not calculation_id.strip():
            raise EconomicInputError(f"calculations[{index}].id は非空文字列にしてください。")
        normalized_id = calculation_id.strip().casefold()
        if normalized_id in seen_ids:
            raise EconomicInputError("calculations.id は正規化後も重複させないでください。")
        seen_ids.add(normalized_id)
        model = raw_calculation.get("model")
        if not isinstance(model, str) or not model.strip():
            raise EconomicInputError(f"calculations[{index}].model は非空文字列にしてください。")
        calculated = calculate_model(model.strip(), raw_calculation.get("parameters"))
        results.append({"id": calculation_id.strip(), **calculated})
    return {
        "schema_version": TOOLKIT_SCHEMA_VERSION,
        "network_accessed_by_calculator": False,
        "calculation_count": len(results),
        "calculations": results,
    }


# ----------------------------------------


def _read_payload(input_path: str | None) -> Mapping[str, Any]:
    """
    機能:
        UTF-8の指定ファイルまたは標準入力からJSONを読み込む。

    引数:
        input_path (str | None): 入力ファイルパス。Noneなら標準入力。

    返り値:
        Mapping[str, Any]: 解析済みJSONオブジェクト。
    """
    try:
        if input_path is not None:
            text = Path(input_path).read_text(encoding="utf-8-sig")
        else:
            text = sys.stdin.buffer.read().decode("utf-8-sig")
        payload = json.loads(text)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EconomicInputError(f"入力JSONを読めません: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise EconomicInputError("入力全体はJSONオブジェクトにしてください。")
    return payload


# ----------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """
    機能:
        経済計算ツールのCLI引数を処理し、JSON結果を標準出力へ書き出す。

    引数:
        argv (Sequence[str] | None): コマンドライン引数。Noneなら実プロセス引数。

    返り値:
        int: 成功時0、入力エラー時2。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")

    # ----------------------------------------

    parser = argparse.ArgumentParser(description="経済学教科書に対応する純計算ツール")
    parser.add_argument("--input", help="UTF-8 JSON入力ファイル。省略時は標準入力")
    parser.add_argument("--pretty", action="store_true", help="JSONをインデント付きで出力")
    parser.add_argument("--list-models", action="store_true", help="利用可能な計算名を表示")
    args = parser.parse_args(argv)
    try:
        if args.list_models:
            result: Mapping[str, Any] = {
                "schema_version": TOOLKIT_SCHEMA_VERSION,
                "models": sorted(CALCULATORS),
            }
        else:
            result = calculate_batch(_read_payload(args.input))
        json.dump(
            result,
            sys.stdout,
            ensure_ascii=False,
            indent=2 if args.pretty else None,
            separators=None if args.pretty else (",", ":"),
        )
        sys.stdout.write("\n")
        return 0
    except EconomicInputError as exc:
        json.dump(
            {"status": "error", "error": str(exc)},
            sys.stdout,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        sys.stdout.write("\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
