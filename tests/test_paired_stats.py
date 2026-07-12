import math
import unittest

import paired_stats as ps


class McNemarTests(unittest.TestCase):
    def test_no_discordant_pairs_is_p1(self):
        self.assertEqual(ps.exact_mcnemar_p(0, 0), 1.0)

    def test_symmetric_discordance_is_high_p(self):
        self.assertGreater(ps.exact_mcnemar_p(5, 5), 0.9)

    def test_known_value(self):
        # b=8, c=2 -> two-sided exact p = 2 * sum_{i<=2} C(10,i)/2^10 = 2*56/1024
        self.assertTrue(math.isclose(ps.exact_mcnemar_p(8, 2), 2 * 56 / 1024))

    def test_extreme_discordance_is_significant(self):
        self.assertLess(ps.exact_mcnemar_p(20, 1), 0.001)

    def test_symmetry(self):
        self.assertEqual(ps.exact_mcnemar_p(7, 3), ps.exact_mcnemar_p(3, 7))


class BootstrapTests(unittest.TestCase):
    def _pairs(self, n_clusters=30, per=4, a_rate=0.7, b_rate=0.5):
        import random

        rng = random.Random(1)
        pairs = []
        for c in range(n_clusters):
            for _ in range(per):
                pairs.append((f"p{c}", int(rng.random() < a_rate), int(rng.random() < b_rate)))
        return pairs

    def test_point_estimate_matches_simple_diff(self):
        pairs = self._pairs()
        out = ps.cluster_bootstrap_ci(pairs, n_boot=200)
        n = len(pairs)
        expect = (sum(a for _, a, _ in pairs) - sum(b for _, _, b in pairs)) / n
        self.assertTrue(math.isclose(out["point_diff"], expect))

    def test_ci_brackets_point_and_is_reproducible(self):
        pairs = self._pairs()
        out1 = ps.cluster_bootstrap_ci(pairs, n_boot=500, seed=7)
        out2 = ps.cluster_bootstrap_ci(pairs, n_boot=500, seed=7)
        self.assertEqual(out1, out2)
        self.assertLessEqual(out1["ci_low"], out1["point_diff"])
        self.assertGreaterEqual(out1["ci_high"], out1["point_diff"])

    def test_real_effect_ci_excludes_zero(self):
        pairs = self._pairs(n_clusters=80, per=5, a_rate=0.8, b_rate=0.4)
        out = ps.cluster_bootstrap_ci(pairs, n_boot=1000)
        self.assertGreater(out["ci_low"], 0.0)

    def test_summary_shape(self):
        pairs = self._pairs()
        s = ps.paired_summary(pairs, n_boot=200)
        self.assertEqual(s["n"], len(pairs))
        self.assertIn("mcnemar_p", s)
        self.assertIn("cluster_bootstrap", s)


if __name__ == "__main__":
    unittest.main()
