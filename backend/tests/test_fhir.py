import copy

from app.schemas.models import StaffObservation, Urgency
from app.services import fhir_service as fs
from app.services.session_manager import SessionManager
from app.schemas.models import SessionCreate
from app.config import Settings
from app.services.safety_rules import RuleEngine


def make(synthetic=False, text="I have chest pain since this morning. No shortness of breath."):
    sm = SessionManager(Settings())
    s = sm.create(SessionCreate(reference="T-1", synthetic=synthetic, consent_acknowledged=True))
    seg, new = s.add_segment(text, "manual")
    s.reevaluate(RuleEngine())
    return s


def confirm_all(s):
    for m in s.mentions:
        m.status = "confirmed"
    s.reevaluate(RuleEngine())


def test_valid_bundle_generated_and_validated():
    s = make()
    confirm_all(s)
    s.review.finalized = True
    bundle, notes = fs.build_bundle(s)
    v = fs.validate_bundle(bundle)
    assert v["valid"], v["errors"]
    counts = v["resource_counts"]
    assert counts["Patient"] == 1 and counts["Encounter"] == 1 and counts["Flag"] >= 1
    assert counts["Observation"] >= 3
    assert v["references_checked"] > 0
    assert "clinical validity" in v["disclaimer"]


def test_pending_mentions_excluded_from_export():
    s = make()
    bundle, notes = fs.build_bundle(s)
    texts = [e["resource"].get("code", {}).get("text") for e in bundle["entry"]]
    assert "Chest pain / discomfort" not in texts
    assert any("unconfirmed" in n for n in notes) and any("not finalized" in n for n in notes)


def test_negated_symptom_exported_as_absent_and_unknown_not_invented():
    s = make()
    confirm_all(s)
    bundle, _ = fs.build_bundle(s)
    sym = {e["resource"]["code"]["text"]: e["resource"] for e in bundle["entry"]
           if e["resource"]["resourceType"] == "Observation" and "valueCodeableConcept" in e["resource"]}
    assert sym["Chest pain / discomfort"]["valueCodeableConcept"]["text"] == "Present"
    assert sym["Difficulty breathing"]["valueCodeableConcept"]["text"] == "Absent"


def test_unavailable_measurements_use_data_absent_reason_not_values():
    s = make()
    confirm_all(s)
    bundle, _ = fs.build_bundle(s)
    exp = [e["resource"] for e in bundle["entry"]
           if e["resource"]["resourceType"] == "Observation"
           and "Experimental" in e["resource"]["code"]["text"]]
    assert exp and all("dataAbsentReason" in r and "valueQuantity" not in r and "valueInteger" not in r for r in exp)
    assert all(r["status"] == "preliminary" for r in exp)


def test_no_vital_signs_or_vital_category_ever_generated():
    s = make()
    confirm_all(s)
    s.observations.append(StaffObservation(text="pale", concerning=True, urgency=Urgency.PROMPT))
    bundle, _ = fs.build_bundle(s)
    assert "vital-signs" not in str(bundle)


def test_synthetic_sessions_tagged_as_test_data():
    s = make(synthetic=True)
    bundle, _ = fs.build_bundle(s)
    assert "HTEST" in str(bundle["meta"]) and "HTEST" in str(bundle["entry"][0]["resource"]["meta"])


def test_invalid_reference_detected():
    s = make()
    confirm_all(s)
    bundle, _ = fs.build_bundle(s)
    bad = copy.deepcopy(bundle)
    for e in bad["entry"]:
        if e["resource"]["resourceType"] == "Observation":
            e["resource"]["subject"] = {"reference": "urn:uuid:does-not-exist"}
            break
    v = fs.validate_bundle(bad)
    assert not v["valid"] and any("does not resolve" in e["message"] for e in v["errors"])


def test_invalid_structure_and_codes_detected():
    s = make()
    confirm_all(s)
    bundle, _ = fs.build_bundle(s)
    bad = copy.deepcopy(bundle)
    bad["type"] = "bogus"
    bad["entry"][1]["resource"]["status"] = "not-a-status"
    bad["entry"][2]["resource"].pop("code", None)
    v = fs.validate_bundle(bad)
    msgs = " ".join(e["message"] for e in v["errors"])
    assert not v["valid"] and "value set" in msgs and len(v["errors"]) >= 3


def test_known_invalid_non_bundle():
    assert fs.validate_bundle({"resourceType": "Patient"})["valid"] is False
    assert fs.validate_bundle({"resourceType": "Bundle", "type": "collection",
                               "entry": [{"fullUrl": "urn:uuid:1", "resource": {"resourceType": "Observation"}}]})["valid"] is False


def test_known_valid_minimal_example():
    b = {"resourceType": "Bundle", "type": "collection", "entry": [
        {"fullUrl": "urn:uuid:11111111-1111-1111-1111-111111111111",
         "resource": {"resourceType": "Patient", "id": "p1"}},
        {"fullUrl": "urn:uuid:22222222-2222-2222-2222-222222222222",
         "resource": {"resourceType": "Observation", "status": "final", "code": {"text": "x"},
                      "subject": {"reference": "urn:uuid:11111111-1111-1111-1111-111111111111"}}}]}
    assert fs.validate_bundle(b)["valid"], fs.validate_bundle(b)["errors"]
