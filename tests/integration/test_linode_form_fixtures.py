import hashlib
import json
from pathlib import Path

import pytest

from santhau_studio.collectors.cgttrg import public_spec
from santhau_studio.collectors.deep import parse_detail
from santhau_studio.collectors.public_deep import identity_problem

ROOT = Path(__file__).parents[1] / "fixtures/hsmt-linode-20261106"
CASES = json.loads((ROOT / "cases.json").read_text(encoding="utf-8"))


def test_saved_fixture_manifest():
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    assert (
        hashlib.sha256((ROOT / "cases.json").read_bytes()).hexdigest() == manifest["cases_sha256"]
    )
    assert sum(c["source_snapshot_count"] for c in CASES) == 137


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["case"])
@pytest.mark.parametrize("reverse", [False, True])
def test_saved_linode_form_shapes(case, reverse):
    payload = {**case["payload"]}
    if reverse:
        payload["bidoInvBiddingDTO"] = list(reversed(payload["bidoInvBiddingDTO"]))
    expected = case["expected"]
    problem = identity_problem(payload, case["record"], "HSMT", case["expected_id"])
    assert problem == expected["identity_problem"]
    if problem:
        return  # projection must be quarantined before parsing unrelated notice data
    parsed = parse_detail(public_spec("TBMT", "HSMT"), payload)
    assert not parsed.problems
    assert parsed.capability == expected["capability"]
    assert len(parsed.documents) == expected["documents"]
    assert len(parsed.lots) == expected["lots"]
    assert (
        sum(len(lot.goods) for lot in parsed.lots) + len(parsed.unallotted_goods)
        == expected["goods"]
    )
