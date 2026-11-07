"""The backfill ceiling is OUR config, not the source's rate limit."""
import math

from santhau_studio.storage.collection_scope import DEFAULT_POLICY

# Measured on the real Data Root (public-data-census.log / public-data-gap-causes.log)
DAY_FAMILY_WINDOWS = 10626
REQUESTS_PER_WINDOW = 35.4


def test_default_policy_no_longer_forces_dozens_of_passes():
    needed = DAY_FAMILY_WINDOWS * REQUESTS_PER_WINDOW
    runs = math.ceil(needed / DEFAULT_POLICY["max_total_requests"])
    assert runs <= 13, f"default ceiling still needs {runs} passes"
    assert DEFAULT_POLICY["quota_per_day"] >= 200
    assert DEFAULT_POLICY["reconcile_enabled"] is True


def test_ceiling_is_still_inside_our_own_bounds():
    # configure() validates every key against BOUNDS; a default above them would
    # make a fresh warehouse impossible to set up.
    assert DEFAULT_POLICY["max_total_requests"] <= 30000
    assert DEFAULT_POLICY["max_total_seconds"] <= 86400
