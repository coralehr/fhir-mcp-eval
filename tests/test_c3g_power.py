import unittest

import c3g_power


class C3GPowerTests(unittest.TestCase):
    def test_registered_iid_examples_recompute(self) -> None:
        self.assertEqual(
            c3g_power.analytic_screen(
                discordance_upper=0.20,
                contrast_icc=0,
                mean_cluster_size=1,
                cluster_size_cv=0,
            )["n_iid"],
            267,
        )
        self.assertEqual(
            c3g_power.analytic_screen(
                discordance_upper=0.30,
                contrast_icc=0,
                mean_cluster_size=1,
                cluster_size_cv=0,
            )["n_iid"],
            405,
        )
        self.assertEqual(
            c3g_power.analytic_screen(
                discordance_upper=0.50,
                contrast_icc=0,
                mean_cluster_size=1,
                cluster_size_cv=0,
            )["n_iid"],
            681,
        )

    def test_cluster_inflation_uses_nonnegative_icc(self) -> None:
        base = c3g_power.analytic_screen(
            discordance_upper=0.30,
            contrast_icc=-0.2,
            mean_cluster_size=3,
            cluster_size_cv=0.5,
        )
        inflated = c3g_power.analytic_screen(
            discordance_upper=0.30,
            contrast_icc=0.2,
            mean_cluster_size=3,
            cluster_size_cv=0.5,
        )
        self.assertEqual(base["design_effect"], 1.0)
        self.assertGreater(inflated["n_required"], inflated["n_iid"])

    def test_invalid_inputs_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            c3g_power.analytic_screen(
                discordance_upper=0.01,
                contrast_icc=0,
                mean_cluster_size=1,
                cluster_size_cv=0,
            )


if __name__ == "__main__":
    unittest.main()
