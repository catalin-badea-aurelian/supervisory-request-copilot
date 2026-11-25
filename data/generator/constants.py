"""Fixed parameters of the synthetic SPB world. Test-support code: not part of the package."""

from __future__ import annotations

from datetime import date, datetime

SEED = 42
GENERATOR_VERSION = "1.0.0"

START = date(2024, 10, 1)
END = date(2025, 9, 30)
DEFAULT_AS_OF = date(2025, 10, 15)
# Business events at or after this instant do not exist in the synthetic world.
EVENT_CUTOFF = datetime(2025, 10, 15, 0, 0, 0)

N_MERCHANTS = 2000
N_TRANSACTIONS = 1_500_000

COUNTRIES = ["DE", "FR", "ES", "IT", "NL", "BE", "LU", "IE"]
COUNTRY_WEIGHT = [0.24, 0.20, 0.14, 0.14, 0.10, 0.07, 0.05, 0.06]
# multiplier on chargeback propensity per merchant country
COUNTRY_CB_MULT = {
    "DE": 0.85,
    "FR": 1.0,
    "ES": 1.1,
    "IT": 1.05,
    "NL": 0.85,
    "BE": 0.9,
    "LU": 0.8,
    "IE": 1.0,
}

# mcc_group: (share of merchants, mean ticket EUR, chargeback mult, fraud mult)
MCC = {
    "grocery": (0.14, 28.0, 0.4, 0.5),
    "restaurants": (0.12, 32.0, 0.6, 0.5),
    "travel": (0.08, 240.0, 1.8, 1.5),
    "fashion": (0.12, 70.0, 1.1, 1.2),
    "electronics": (0.08, 190.0, 1.3, 2.0),
    "digital_services": (0.10, 22.0, 1.6, 2.5),
    "utilities": (0.07, 85.0, 0.3, 0.3),
    "health": (0.08, 55.0, 0.5, 0.4),
    "entertainment": (0.09, 40.0, 1.0, 1.0),
    "other": (0.12, 60.0, 1.0, 0.8),
}
MCC_GROUPS = list(MCC)

# Monthly seasonality, Oct-2024 .. Sep-2025
SEASONALITY = [0.95, 1.0, 1.25, 0.90, 0.88, 1.0, 1.02, 1.05, 1.08, 1.12, 1.10, 1.0]

MERCHANT_SIGMA = 0.5  # lognormal sigma of merchant size (in value terms)
WAVE_SIZE_MULT = 2.0  # merchants onboarded in the 2025-09 wave are larger than average
TICKET_SIGMA = 0.9
WALLET_SHARE = 0.30
OTHER_CONSUMER_COUNTRIES = ["GB", "US", "CH", "PT", "AT", "PL", "SE", "DK"]

# status probabilities (final state): refunded, authorised-only, rest settled
P_REFUNDED = 0.025
P_AUTHORISED = 0.03

# Chargebacks: a country-month pool is sampled (weights = amount**CB_AMOUNT_EXPONENT x
# multipliers, so larger tickets are more likely) until the chargeback VALUE reaches the target
# rate x multiplier-weighted pool value. Hitting a value target keeps baseline indicators
# stable even in the smallest country.
CB_BASE_VALUE_RATE = 0.0035
CB_AMOUNT_EXPONENT = 0.7
CB_MONTH_NOISE = 0.06
# R1: genuine risk event, Italy / travel / June 2025
R1_COUNTRY, R1_MCC, R1_MONTH, R1_MULT = "IT", "travel", date(2025, 6, 1), 18.0

FRAUD_BASE_VALUE_RATE = 0.00050
FRAUD_AMOUNT_EXPONENT = 0.0

# Defect parameters (test-only knowledge)
D2_WAVE_SHARE = 0.05  # share of all merchants onboarded in 2025-09
D2_NULL_FRACTION = 0.40  # fraction of the wave with a missing country (~2% of all merchants)
D3_LATE_FROM = date(2025, 9, 19)  # partitions from this day are loaded late
D3_LATE_LOADED_AT = datetime(2025, 10, 20, 3, 0, 0)
GROUP_BUILD_LAG_DAYS = 3
D3_GROUP_BUILT_AT = datetime(2025, 10, 3, 6, 0, 0)
D4_FROM_MONTH = date(2025, 9, 1)
D1_MONTH, D1_COUNTRY = date(2025, 8, 1), "ES"
