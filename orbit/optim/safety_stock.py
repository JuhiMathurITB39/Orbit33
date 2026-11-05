"""Safety stock and reorder point with explicit validity conditions.

Setting. Daily demand has mean mu_d and standard deviation sigma_d; replenishment lead time L has mean L_mean and
standard deviation L_sd (days); the review period is R days (R = 0 for continuous review). Protection period
P = L + R.

Moments of demand over the protection period (compound-sum identities; they require that daily demands are
uncorrelated across days with common mean/variance, and that demand is independent of lead time):

    E[D_P]   = mu_d * (L_mean + R)
    Var[D_P] = (L_mean + R) * sigma_d**2  +  mu_d**2 * L_sd**2

Classical safety stock:  SS = z_alpha * sqrt(Var[D_P]),  z_alpha = Phi^{-1}(alpha)  (alpha = cycle service level).

That normal-quantile formula is only valid when D_P is well approximated by a Normal, i.e. mean large enough and
coefficient of variation small enough that P(D_P < 0) is negligible. We therefore apply it only when
    E[D_P] >= MIN_MEAN_FOR_NORMAL  and  CV = sd/mean <= MAX_CV_FOR_NORMAL
Otherwise we use the quantile of a moment-matched Gamma distribution (non-negative, right-skewed), which is the
safer approximation for slow, intermittent items. The method used is returned with every result.
"""

from __future__ import annotations

import numpy as np
from scipy import stats

MIN_MEAN_FOR_NORMAL = 15.0
MAX_CV_FOR_NORMAL = 0.5


def period_moments(mu_d, sigma_d, L_mean, L_sd, R):
    mu_d, sigma_d, L_mean, L_sd = (np.asarray(x, float) for x in (mu_d, sigma_d, L_mean, L_sd))
    P = L_mean + R
    mean = mu_d * P
    var = P * sigma_d ** 2 + (mu_d ** 2) * (L_sd ** 2)
    var = np.maximum(var, mean)  # count data cannot be less dispersed than Poisson (variance floor = mean)
    return mean, var


def gamma_params(mean: np.ndarray, var: np.ndarray):
    mean = np.maximum(np.asarray(mean, float), 1e-9)
    var = np.maximum(np.asarray(var, float), 1e-9)
    return mean ** 2 / var, var / mean  # shape k, scale theta


def safety_stock(mu_d, sigma_d, L_mean, L_sd, R, service_level) -> dict:
    """Vectorised. Returns dict(ss, z, mean_P, sd_P, method, normal_valid)."""
    alpha = float(service_level)
    if not (0.5 < alpha < 0.9999):
        raise ValueError("service_level must be in (0.5, 0.9999)")
    mean, var = period_moments(mu_d, sigma_d, L_mean, L_sd, R)
    sd = np.sqrt(var)
    cv = sd / np.maximum(mean, 1e-9)
    valid = (mean >= MIN_MEAN_FOR_NORMAL) & (cv <= MAX_CV_FOR_NORMAL)
    z = stats.norm.ppf(alpha)
    ss_norm = z * sd
    k, th = gamma_params(mean, var)
    ss_gamma = np.maximum(stats.gamma.ppf(alpha, a=k, scale=th) - mean, 0.0)
    ss = np.where(valid, ss_norm, ss_gamma)
    ss = np.where(mean < 1e-9, 0.0, ss)
    return dict(ss=ss, z=float(z), mean_P=mean, sd_P=sd, normal_valid=valid,
                method=np.where(valid, "normal", "gamma"))


def reorder_point(mu_d, sigma_d, L_mean, L_sd, service_level) -> dict:
    """Continuous-review reorder point ROP = E[D_L] + SS_L (R = 0): reorder when the inventory position reaches ROP."""
    ss = safety_stock(mu_d, sigma_d, L_mean, L_sd, 0.0, service_level)
    mean_L = np.asarray(mu_d, float) * np.asarray(L_mean, float)
    return dict(rop=mean_L + ss["ss"], demand_during_lead=mean_L, ss=ss["ss"], method=ss["method"])
