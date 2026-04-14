"""probability.py — CDF and bucket probability estimation."""

import math

from weatherbet.market.parser import in_bucket


def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bucket_prob(forecast, t_low, t_high, sigma=None):
    """Probability that the actual temp falls in [t_low, t_high].

    Edge buckets (-999 / 999) always use normal CDF.
    Regular buckets use in_bucket (0/1) unless sigma is None or 0.
    """
    s = 2.0 if sigma is None else float(sigma)
    if s <= 0:
        if t_low == -999:
            return 1.0 if float(forecast) <= t_high else 0.0
        if t_high == 999:
            return 1.0 if float(forecast) >= t_low else 0.0
        return 1.0 if in_bucket(forecast, t_low, t_high) else 0.0
    if t_low == -999:
        return norm_cdf((t_high - float(forecast)) / s)
    if t_high == 999:
        return 1.0 - norm_cdf((t_low - float(forecast)) / s)
    return 1.0 if in_bucket(forecast, t_low, t_high) else 0.0
