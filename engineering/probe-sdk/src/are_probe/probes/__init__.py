"""Probe families shipped by the internal probe SDK."""

from .refusal_robustness import (
    grade_ops_refusal_accuracy,
    grade_refusal_robustness,
)
from .bias_fairness import (
    grade_counterfactual_stability,
    grade_demographic_parity,
    grade_equal_opportunity,
    grade_eval_representation,
    grade_sentiment_skew,
)

FAMILY_GRADERS = {
    "refusal_robustness": grade_refusal_robustness,
    "ops_refusal_accuracy": grade_ops_refusal_accuracy,
    "bias_demographic_parity": grade_demographic_parity,
    "bias_equal_opportunity": grade_equal_opportunity,
    "bias_counterfactual_stability": grade_counterfactual_stability,
    "bias_eval_representation": grade_eval_representation,
    "bias_sentiment_skew": grade_sentiment_skew,
}
