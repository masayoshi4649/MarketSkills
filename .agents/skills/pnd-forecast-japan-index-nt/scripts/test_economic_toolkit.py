#!/usr/bin/env python3
"""economic_toolkitの主要式と入力不変条件を検証する。"""

from __future__ import annotations

import json
import math
import subprocess
import sys
import unittest
from pathlib import Path

from economic_toolkit import EconomicInputError, calculate_batch, calculate_model


class EconomicToolkitTest(unittest.TestCase):
    """経済学補助計算の正常系と拒否条件を検証する。"""

    def test_arc_elasticity_uses_midpoint_method(self) -> None:
        """
        機能:
            価格上昇と数量減少から中点法の負の需要弾力性を得ることを検証する。

        引数:
            self (EconomicToolkitTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        result = calculate_model(
            "arc_elasticity",
            {
                "price_initial": 100,
                "price_final": 110,
                "quantity_initial": 1000,
                "quantity_final": 900,
            },
        )["result"]
        self.assertAlmostEqual(result["elasticity"], -1.105263157894737)
        self.assertEqual(result["classification"], "弾力的")

    # ----------------------------------------

    def test_tax_incidence_shares_sum_to_one(self) -> None:
        """
        機能:
            税負担比率が弾力性に従い合計1になることを検証する。

        引数:
            self (EconomicToolkitTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        result = calculate_model(
            "tax_incidence",
            {"demand_elasticity_abs": 0.5, "supply_elasticity": 1.5, "tax_per_unit": 20},
        )["result"]
        self.assertAlmostEqual(result["consumer_share"], 0.75)
        self.assertAlmostEqual(result["producer_share"], 0.25)
        self.assertAlmostEqual(result["consumer_burden_per_unit"], 15.0)

    # ----------------------------------------

    def test_fisher_and_taylor_calculations(self) -> None:
        """
        機能:
            Fisher実質金利とTaylor型ルール差を既知値で検証する。

        引数:
            self (EconomicToolkitTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        fisher = calculate_model(
            "fisher_real_rate",
            {"nominal_rate_pct": 5, "expected_inflation_pct": 2},
        )["result"]
        self.assertAlmostEqual(fisher["exact_real_rate_pct"], 2.941176470588225)
        taylor = calculate_model(
            "taylor_rule_gap",
            {
                "neutral_real_rate_pct": 1,
                "inflation_pct": 3,
                "inflation_target_pct": 2,
                "output_gap_pct": -1,
                "current_policy_rate_pct": 2,
            },
        )["result"]
        self.assertAlmostEqual(taylor["rule_rate_pct"], 4.0)
        self.assertAlmostEqual(taylor["gap_to_current_pct_points"], 2.0)

    # ----------------------------------------

    def test_prospect_value_distinguishes_loss_domain(self) -> None:
        """
        機能:
            同じ絶対差でも損失回避係数により損失側価値が大きくなることを検証する。

        引数:
            self (EconomicToolkitTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        gain = calculate_model("prospect_value", {"outcome": 110, "reference": 100})["result"]
        loss = calculate_model("prospect_value", {"outcome": 90, "reference": 100})["result"]
        self.assertEqual(gain["domain"], "gain")
        self.assertEqual(loss["domain"], "loss")
        self.assertGreater(abs(loss["prospect_value"]), gain["prospect_value"])

    # ----------------------------------------

    def test_weighted_gini_known_cases(self) -> None:
        """
        機能:
            完全平等と二人の極端分布についてGini係数の既知値を検証する。

        引数:
            self (EconomicToolkitTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        equal = calculate_model("gini_coefficient", {"values": [10, 10, 10]})["result"]
        unequal = calculate_model("gini_coefficient", {"values": [0, 10]})["result"]
        self.assertAlmostEqual(equal["gini"], 0.0)
        self.assertAlmostEqual(unequal["gini"], 0.5)

    # ----------------------------------------

    def test_mixed_nash_matching_pennies(self) -> None:
        """
        機能:
            マッチング・ペニーの完全混合均衡が双方50%になることを検証する。

        引数:
            self (EconomicToolkitTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        result = calculate_model(
            "mixed_nash_2x2",
            {
                "row_payoffs": [[1, -1], [-1, 1]],
                "column_payoffs": [[-1, 1], [1, -1]],
            },
        )["result"]
        self.assertAlmostEqual(result["row_first_strategy_probability"], 0.5)
        self.assertAlmostEqual(result["column_first_strategy_probability"], 0.5)
        self.assertAlmostEqual(result["row_expected_payoff"], 0.0)

    # ----------------------------------------

    def test_present_value_and_social_npv(self) -> None:
        """
        機能:
            私的現在価値と外部費用を含む社会的現在価値を検証する。

        引数:
            self (EconomicToolkitTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        present = calculate_model(
            "present_value",
            {"cash_flows": [100, 100], "discount_rate_pct": 10, "first_period": 1},
        )["result"]
        self.assertAlmostEqual(present["present_value"], 173.55371900826447)
        social = calculate_model(
            "social_npv",
            {
                "benefits": [0, 120],
                "private_costs": [100, 0],
                "external_costs": [10, 10],
                "discount_rate_pct": 0,
            },
        )["result"]
        self.assertAlmostEqual(social["social_net_present_value"], 0.0)

    # ----------------------------------------

    def test_covered_interest_parity_with_observed_forward(self) -> None:
        """
        機能:
            国内外金利差から理論フォワードと観測乖離を計算することを検証する。

        引数:
            self (EconomicToolkitTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        result = calculate_model(
            "covered_interest_parity",
            {
                "spot": 150,
                "domestic_rate_pct": 1,
                "foreign_rate_pct": 5,
                "tenor_years": 1,
                "observed_forward": 145,
            },
        )["result"]
        self.assertAlmostEqual(result["theoretical_forward"], 144.28571428571428)
        self.assertAlmostEqual(result["observed_minus_theoretical"], 0.7142857142857224)

    # ----------------------------------------

    def test_mincer_earnings_returns_contributions(self) -> None:
        """
        機能:
            Mincer型賃金式の各寄与と対数賃金合計を検証する。

        引数:
            self (EconomicToolkitTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        result = calculate_model(
            "mincer_earnings",
            {
                "intercept": 1,
                "schooling_years": 16,
                "experience_years": 10,
                "schooling_coefficient": 0.08,
                "experience_coefficient": 0.04,
                "experience_squared_coefficient": -0.001,
            },
        )["result"]
        self.assertAlmostEqual(result["predicted_log_earnings"], 2.58)
        self.assertAlmostEqual(result["schooling_contribution"], 1.28)
        self.assertTrue(math.isfinite(result["predicted_earnings_index"]))

    # ----------------------------------------

    def test_poverty_gap_and_icer(self) -> None:
        """
        機能:
            貧困指標と増分費用効果比を既知値で検証する。

        引数:
            self (EconomicToolkitTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        poverty = calculate_model(
            "poverty_gap",
            {"incomes": [50, 100, 150], "poverty_line": 100},
        )["result"]
        self.assertAlmostEqual(poverty["poverty_headcount_ratio"], 1.0 / 3.0)
        self.assertAlmostEqual(poverty["poverty_gap_index"], 1.0 / 6.0)
        icer = calculate_model(
            "incremental_cost_effectiveness",
            {"cost_new": 120, "cost_comparator": 100, "effect_new": 1.5, "effect_comparator": 1.0},
        )["result"]
        self.assertAlmostEqual(icer["icer"], 40.0)
        self.assertEqual(icer["cost_effectiveness_plane"], "more_costly_more_effective")

    # ----------------------------------------

    def test_batch_preserves_ids_and_rejects_duplicates(self) -> None:
        """
        機能:
            一括計算が入力順とIDを保持し、正規化後の重複IDを拒否することを検証する。

        引数:
            self (EconomicToolkitTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        payload = {
            "schema_version": "1.0.0",
            "calculations": [
                {
                    "id": "real-rate",
                    "model": "fisher_real_rate",
                    "parameters": {"nominal_rate_pct": 3, "expected_inflation_pct": 1},
                },
                {
                    "id": "pv",
                    "model": "present_value",
                    "parameters": {"cash_flows": [100], "discount_rate_pct": 0},
                },
            ],
        }
        result = calculate_batch(payload)
        self.assertEqual(result["calculation_count"], 2)
        self.assertEqual([row["id"] for row in result["calculations"]], ["real-rate", "pv"])
        payload["calculations"][1]["id"] = " REAL-RATE "
        with self.assertRaises(EconomicInputError):
            calculate_batch(payload)

    # ----------------------------------------

    def test_degenerate_and_invalid_inputs_are_rejected(self) -> None:
        """
        機能:
            ゼロ分母、負の分布値、退化ゲームなどの不定計算を拒否することを検証する。

        引数:
            self (EconomicToolkitTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        invalid_cases = [
            (
                "arc_elasticity",
                {"price_initial": 100, "price_final": 100, "quantity_initial": 10, "quantity_final": 9},
            ),
            ("gini_coefficient", {"values": [-1, 1]}),
            (
                "mixed_nash_2x2",
                {"row_payoffs": [[1, 1], [1, 1]], "column_payoffs": [[1, 1], [1, 1]]},
            ),
            (
                "incremental_cost_effectiveness",
                {"cost_new": 1, "cost_comparator": 0, "effect_new": 1, "effect_comparator": 1},
            ),
        ]
        for model, parameters in invalid_cases:
            with self.subTest(model=model), self.assertRaises(EconomicInputError):
                calculate_model(model, parameters)

    # ----------------------------------------

    def test_cli_lists_models_and_accepts_utf8_stdin(self) -> None:
        """
        機能:
            CLIがモデル一覧とUTF-8標準入力の日本語IDを正常処理することを検証する。

        引数:
            self (EconomicToolkitTest): unittestのテストインスタンス。

        返り値:
            None: 検証成功時は値を返さない。
        """
        script = Path(__file__).resolve().parent / "economic_toolkit.py"
        listed = subprocess.run(
            [sys.executable, "-B", str(script), "--list-models"],
            capture_output=True,
            check=False,
        )
        self.assertEqual(listed.returncode, 0)
        self.assertIn("covered_interest_parity", json.loads(listed.stdout.decode("utf-8"))["models"])
        payload = {
            "schema_version": "1.0.0",
            "calculations": [
                {
                    "id": "実質金利",
                    "model": "fisher_real_rate",
                    "parameters": {"nominal_rate_pct": 1, "expected_inflation_pct": 2},
                }
            ],
        }
        completed = subprocess.run(
            [sys.executable, "-B", str(script)],
            input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0)
        output = json.loads(completed.stdout.decode("utf-8"))
        self.assertEqual(output["calculations"][0]["id"], "実質金利")


if __name__ == "__main__":
    unittest.main()
