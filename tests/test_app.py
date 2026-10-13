"""The demo app runs the real modules: outcomes, registry, audit chain and tamper detection are not canned."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.live import FIXTURES_DIR, LiveEngine
from app.server import create_app

ROOT = Path(__file__).resolve().parent.parent
BUYER = {"X-Tessera-Role": "buyer:P-0002"}
LENDER = {"X-Tessera-Role": "lender:L1"}
REVIEWER = {"X-Tessera-Role": "reviewer:REV-1"}


@pytest.fixture(scope="module")
def engine() -> LiveEngine:
    e = LiveEngine()
    e.load_fixtures()
    return e


@pytest.fixture(scope="module")
def client(engine: LiveEngine) -> TestClient:
    return TestClient(create_app(demo_mode=True, engine=engine))


def test_every_fixture_matches_its_expected_outcome(engine: LiveEngine) -> None:
    assert len(engine.order) == 25
    for cid in engine.order:
        expected = json.loads((FIXTURES_DIR / cid / "expected.json").read_text())["expected_outcome"]
        assert engine.states[cid].decision.outcome.value == expected, cid


def test_acceptance_needs_conditional_approval(client: TestClient) -> None:
    r = client.post("/requests/ring_real_firms/accept", headers=BUYER, json={"decision": "accept"})
    assert r.status_code == 409 and "conditional approval" in r.json()["detail"]


def test_clean_trade_finances_only_after_signed_acceptance_and_confirm(client: TestClient) -> None:
    early = client.post("/reservations/confirm", headers=LENDER, json={"request_id": "clean_services", "lender_id": "L1"})
    assert early.status_code == 409  # no acceptance yet
    ok = client.post("/requests/clean_goods/accept", headers=BUYER, json={"decision": "accept"})
    assert ok.status_code == 200 and ok.json()["state"] == "buyer_accepted"
    wrong = client.post("/reservations/confirm", headers={"X-Tessera-Role": "lender:L2"}, json={"request_id": "clean_goods", "lender_id": "L2"})
    assert wrong.status_code == 409 and "owned by lender" in wrong.json()["detail"]
    done = client.post("/reservations/confirm", headers=LENDER, json={"request_id": "clean_goods", "lender_id": "L1"})
    assert done.status_code == 200
    assert done.json()["state"] == "disbursed" and done.json()["reservation"]["state"] == "financed"
    # Discount arithmetic: 85% of 10,00,000 at 12% for 60 days = 1_676_712 paise.
    assert done.json()["card"]["pricing"]["discount_paise"] == 1_676_712


def test_roles_are_enforced(client: TestClient) -> None:
    r = client.post("/overrides", headers=BUYER, json={"case_id": "bad_arith", "reason": "x" * 5})
    assert r.status_code == 403


def test_hard_fail_override_needs_two_reviewers_then_can_still_finance(client: TestClient) -> None:
    body = {"case_id": "mm_qty", "new_outcome": "conditionally_approved", "reviewer_id": "REV-1", "reason": "qty explained in writing"}
    assert client.post("/overrides", headers=REVIEWER, json=body).status_code == 409
    same = client.post("/overrides", headers=REVIEWER, json={**body, "second_reviewer_id": "REV-1"})
    assert same.status_code == 409  # must be distinct
    ok = client.post("/overrides", headers=REVIEWER, json={**body, "second_reviewer_id": "REV-2"})
    assert ok.status_code == 200 and ok.json()["decision"]["outcome"] == "conditionally_approved"
    assert client.post("/requests/mm_qty/accept", headers=BUYER, json={}).status_code == 200
    fin = client.post("/reservations/confirm", headers=LENDER, json={"request_id": "mm_qty", "lender_id": "L1"})
    assert fin.status_code == 200 and fin.json()["state"] == "disbursed"


def test_no_override_can_lift_the_capacity_cap(client: TestClient) -> None:
    body = {"case_id": "above_outstanding", "new_outcome": "conditionally_approved", "reviewer_id": "REV-1",
            "second_reviewer_id": "REV-2", "reason": "reviewed with both officers"}
    r = client.post("/overrides", headers=REVIEWER, json=body)
    assert r.status_code == 409 and "registry" in r.json()["detail"].lower()
    assert client.get("/requests/above_outstanding").json()["decision"]["outcome"] == "reject"


def test_concurrent_reservations_have_exactly_one_winner(client: TestClient) -> None:
    for _ in range(25):
        res = client.post("/reservations/concurrent-demo", headers=LENDER, json={"case_id": "concurrent_reservation"}).json()
        assert res["active_reservation_count"] == 1
        assert [o["won"] for o in res["outcomes"]].count(True) == 1


def test_tamper_is_detected_and_localised_and_outage_is_not_tampering(client: TestClient) -> None:
    assert client.get("/audit/verify/ring_real_firms").json()["integrity"] == "verified"
    target = client.get("/requests/ring_real_firms").json()["audit_proof"]["encrypted_blobs"][0]["doc_id"]
    other = client.get("/requests/ring_real_firms").json()["audit_proof"]["encrypted_blobs"][1]["doc_id"]
    assert client.post("/demo/tamper", json={"target": target}).status_code == 200
    bad = client.get(f"/audit/verify/{target}").json()
    assert bad["integrity"] == "failed" and bad["failed_file"] == target
    assert client.get(f"/audit/verify/{other}").json()["integrity"] == "verified"
    client.post("/demo/tamper", json={"target": target, "reset": True})
    assert client.get(f"/audit/verify/{target}").json()["integrity"] == "verified"

    client.post("/demo/network-off", json={"offline": True})
    off = client.get("/audit/verify/ring_real_firms").json()
    assert off["integrity"] == "verified" and off["anchoring"] == "unavailable"
    client.post("/demo/network-off", json={"online": True})
    assert client.get("/audit/verify/ring_real_firms").json()["anchoring"] == "anchored"


def test_anchor_is_labelled_simulated_when_no_testnet(client: TestClient) -> None:
    assert client.get("/audit/verify/clean_goods").json()["anchor_mode"] == "simulated"
    assert "simulated" in client.get("/health").json()["anchor"]


def test_selective_disclosure_verifies_one_field(client: TestClient) -> None:
    sd = client.get("/audit/verify/clean_goods").json()["selective_disclosure"]
    assert sd["verified"] is True and sd["disclosed_field"] == "grand_total_paise"
    assert "grand_total_paise" not in sd["hidden_fields"] and sd["hidden_fields"]


def test_demo_routes_do_not_exist_without_the_flag(engine: LiveEngine) -> None:
    prod = TestClient(create_app(demo_mode=False, engine=engine))
    assert prod.post("/demo/tamper", json={}).status_code in (404, 405)
    assert prod.post("/demo/network-off", json={}).status_code in (404, 405)


def test_upload_runs_real_extraction_and_rejects_non_pdf(client: TestClient) -> None:
    bad = client.post("/requests", headers={"X-Tessera-Role": "seller:P-0001"}, json={"case_id": "clean_goods", "file_magic": "NOPE"})
    assert bad.status_code == 400
    pdf = (ROOT / "fixtures" / "clean_goods" / "invoice.pdf").read_bytes()
    r = client.post("/requests", headers={"X-Tessera-Role": "seller:P-0001"}, data={"case_id": "clean_goods"}, files={"file": ("inv.pdf", pdf, "application/pdf")})
    assert r.status_code == 200
    card = r.json()
    assert card["source"] == "upload" and card["extraction"]["provenance"]
    assert any(c["check"].startswith("extract.") for c in card["decision"]["checks"])
    fake = client.post("/requests", headers={"X-Tessera-Role": "seller:P-0001"}, files={"file": ("x.pdf", b"not a pdf", "application/pdf")})
    assert fake.status_code == 400


def test_breakit_runs_the_real_network_check(client: TestClient) -> None:
    ring = client.post("/breakit", json={"ewb_speed_kmh": 410, "retained_pct": 0.7, "links": 2, "total_days": 2}).json()
    calm = client.post("/breakit", json={"ewb_speed_kmh": 44, "retained_pct": 18.4, "links": 0, "total_days": 40}).json()
    assert ring["tier"] == "high" and ring["ring_graph"]["nodes"]
    assert calm["score"] < ring["score"]
    assert any("₹" in line for line in ring["evidence"])  # evidence sentences are built from numbers


def test_no_fabricated_chain_claims_in_the_ui() -> None:
    for f in (ROOT / "app" / "static").glob("*"):
        if f.suffix in {".js", ".html"} and f.name != "data.js":
            text = f.read_text(encoding="utf-8")
            assert "Polygon Amoy" not in text and "block #" not in text, f.name
            assert "innerHTML" not in text, f.name


def test_queue_sorts_by_tier_then_exposure(client: TestClient) -> None:
    items = client.get("/queue?all_cases=true").json()["all_prioritized"]
    keys = [(-i["tier_rank"], -i["exposure_paise"], i["request_id"]) for i in items]
    assert keys == sorted(keys)
