"""Check catalogue (ARCHITECTURE section 9.1). Importing this package fills REGISTRY."""

from copilot.validation.checks import (  # noqa: F401
    v_comp_01,
    v_comp_02,
    v_def_01,
    v_dual_01,
    v_mat_01,
    v_pk_01,
    v_prior_01,
    v_rec_01,
    v_rerun_01,
    v_sch_01,
    v_sql_01,
    v_time_01,
    v_var_01,
)
from copilot.validation.checks.base import REGISTRY, CheckSpec

__all__ = ["REGISTRY", "CheckSpec"]
