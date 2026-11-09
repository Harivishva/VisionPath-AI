"""Iteration 3: FACS velocity, multilingual normalization, waiting-room monitor, FHIR + EHR sync."""
from __future__ import annotations

import numpy as np
import pytest

from app.config import Settings
from app.services import clinical_terms, facs, waiting_room
from app.services.facs import FacsState
from app.services.fhir_service import validate_bundle

# ---------------------------------------------------------------- FACS


def face(brow_scale=1.0, nose_scale=1.0, lip_l=(0.0, 0.0, 0.0), lip_r=(0.0, 0.0, 0.0)) -> np.ndarray:
    """478 synthetic 3D landmarks: eyes 100 px apart, brows/nose/lips at known spots."""
    p = np.zeros((478, 3))
    p[facs.EYE_OUTER_L], p[facs.EYE_OUTER_R] = (100, 100, 0), (200, 100, 0)
    p[facs.CANTHUS_L], p[facs.CANTHUS_R] = (125, 100, 0), (175, 100, 0)
    p[facs.BROW_L] = (125, 100 - 20 * brow_scale, 0)
    p[facs.BROW_R] = (175, 100 - 20 * brow_scale, 0)
    p[facs.NOSE_L] = (140, 160, 0)
    p[facs.NOSE_R] = (140 + 20 * nose_scale, 160, 0)
    p[facs.LIP_L] = (120 + lip_l[0], 200 + lip_l[1], lip_l[2])
    p[facs.LIP_R] = (180 + lip_r[0], 200 + lip_r[1], lip_r[2])
    return p


def run(state: FacsState, frames: list[np.ndarray], fps: float = 30.0, t0: float = 0.0) -> float:
    t = t0
    for f in frames:
        state.add(t, f)
        t += 1.0 / fps
    return t


def test_facs_calibrates_then_reports_zero_for_a_still_face():
    st = FacsState()
    assert st.current()["state"] == "not_measured"
    run(st, [face()] * 5)
    assert st.current()["state"] == "calibrating"
    run(st, [face()] * 60, t0=1.0)
    c = st.current()
    assert c["state"] == "pending_review"
    assert c["au04_distress"] == 0 and c["au09_pain"] == 0
    assert c["au12_14_palsy_velocity"] == pytest.approx(0, abs=1e-6)
    assert c["motion_class"] == "static" and c["experimental"] is True


def test_facs_brow_lowering_and_nose_change_raise_scores_with_velocity():
    st = FacsState()
    t = run(st, [face()] * 40)
    frames = [face(brow_scale=1 - 0.2 * k / 30, nose_scale=1 + 0.15 * k / 30) for k in range(1, 31)]
    run(st, frames, t0=t)
    c = st.current()
    assert c["au04_distress"] > 0.5
    assert c["au09_pain"] > 0.5
    assert c["au04_velocity"] < 0  # brow-canthus distance is shrinking
    assert c["au09_velocity"] > 0


def test_facs_static_asymmetry_is_not_palsy_velocity_but_one_sided_motion_is():
    # Structural asymmetry from frame one (left lip corner permanently higher): no motion, no velocity.
    st = FacsState()
    run(st, [face(lip_l=(0, -8, 0))] * 80)
    c = st.current()
    assert c["au12_14_palsy_velocity"] == pytest.approx(0, abs=1e-6) and c["motion_class"] == "static"

    # Symmetric smile (corners move outward mirror-wise): lips active, asymmetry ~0.
    sm = FacsState()
    run(sm, [face()] * 30)
    run(sm, [face(lip_l=(-k * 0.3, -k * 0.2, 0), lip_r=(k * 0.3, -k * 0.2, 0)) for k in range(30)], t0=1.0)
    s = sm.current()
    assert s["motion_class"] == "active" and s["au12_14_palsy_velocity"] < 0.03

    # One-sided droop motion: only the left corner moves -> a clear asymmetric velocity.
    od = FacsState()
    run(od, [face()] * 30)
    run(od, [face(lip_l=(0, k * 0.5, 0)) for k in range(30)], t0=1.0)
    o = od.current()
    assert o["au12_14_palsy_velocity"] > 0.1
    assert o["au12_14_palsy_velocity"] > 5 * s["au12_14_palsy_velocity"]


def test_facs_buffer_is_three_seconds_and_gaps_restart_it():
    st = FacsState()
    run(st, [face()] * 200)
    assert st.current()["buffer_frames"] == facs.BUFFER_FRAMES
    st.add(100.0, face())  # >1 s later
    assert st.current()["buffer_frames"] == 1
    assert st.base_brow is not None  # baseline survives the gap
    st.reset()
    assert st.current()["state"] == "not_measured"


# ---------------------------------------------------------------- multilingual


@pytest.mark.parametrize("text,tag", [
    ("It feels like my chest is heavy like a big rock", "Substernal Chest Pressure"),
    ("Nenju barama irukku", "Substernal Chest Pressure"),
    ("seene mein dard hai", "Chest Pain"),
    ("moochu thinaral irukku", "Acute Dyspnea"),
    ("I can barely catch my breath", "Acute Dyspnea"),
    ("saans lene mein takleef", "Acute Dyspnea"),
    ("thalai suthudhu", "Dizziness / Presyncope"),
])
def test_normalization_maps_colloquial_phrases(text, tag):
    assert tag in clinical_terms.normalize(text)


def test_normalization_code_switched_example_and_no_invention():
    out = clinical_terms.normalize("Nenju barama irukku... I can barely catch my breath")
    assert out == ["Substernal Chest Pressure", "Acute Dyspnea"]
    assert clinical_terms.normalize("The weather is lovely and I am fine") == []
    assert clinical_terms.normalize("") == []


def test_language_detection_flags_code_switching():
    langs = clinical_terms.detect_languages("Nenju barama irukku... I can barely catch my breath", "en")
    assert langs == ["ta", "en"]
    assert clinical_terms.detect_languages("I have a headache and some nausea", "en") == ["en"]
    assert clinical_terms.detect_languages("நெஞ்சு வலி") == ["ta"]
    assert "hi" in clinical_terms.detect_languages("मुझे सीने में दर्द है")
    assert clinical_terms.detect_languages("") == []


def test_session_aggregates_languages_and_symptoms_and_unified_exposes_them(client, new_session):
    sid, h, _ = new_session()
    r = client.post(f"/api/sessions/{sid}/transcript",
                    json={"text": "Nenju barama irukku... I can barely catch my breath"}, headers=h)
    assert r.status_code == 201
    u = client.get(f"/api/sessions/{sid}/unified", headers=h).json()
    sp = u["extracted_metrics"]["speech"]
    assert sp["detected_languages"] == ["ta", "en"]
    assert sp["normalized_clinical_symptoms"] == ["Substernal Chest Pressure", "Acute Dyspnea"]
    assert sp["raw_transcript"].startswith("Nenju barama")
    assert sp["dysarthria_confidence"] is None
    assert u["patient_session_id"] == sid
    # Editing the segment recomputes the tags (nothing stale is kept).
    seg_id = client.get(f"/api/sessions/{sid}", headers=h).json()["segments"][0]["id"]
    client.patch(f"/api/sessions/{sid}/transcript/{seg_id}", json={"text": "I am fine"}, headers=h)
    u2 = client.get(f"/api/sessions/{sid}/unified", headers=h).json()
    assert u2["extracted_metrics"]["speech"]["normalized_clinical_symptoms"] == []


def test_stt_language_defaults_to_auto_and_model_is_english_only_by_default():
    s = Settings()
    assert s.stt_language == "auto"
    assert s.local_whisper_model.endswith(".en")  # multilingual needs an explicit, downloaded model


# ---------------------------------------------------------------- waiting room

CFG = Settings(waiting_baseline_samples=5, waiting_min_samples=5, waiting_eval_interval_s=300,
               waiting_window_s=300)


def feed(m, t0, n, hr=None, au09=None, au04=0.1, conf=0.9):
    for i in range(n):
        waiting_room.ingest(m, t0 + i, {"hr_bpm": hr, "hr_confidence": conf if hr else None,
                                        "au04_distress": au04 if au09 is not None else None,
                                        "au09_pain": au09}, CFG)
    return t0 + n


def test_baseline_then_no_alert_when_stable():
    m = waiting_room.MonitorState()
    waiting_room.start(m, 0.0)
    t = feed(m, 0.0, 10, hr=80.0, au09=0.1)
    assert m.base_hr == 80.0 and m.base_au09 == 0.1
    t = feed(m, t, 290, hr=84.0, au09=0.12)
    assert waiting_room.evaluate(m, 301.0, CFG, "iso") is None
    st = waiting_room.state(m, 301.0, CFG)
    assert st["is_monitored"] and not st["acuity_escalation_alert"]
    assert st["baseline_hr_delta"] == "+4 BPM"
    assert m.last_eval["status"] == "ok"


def test_heart_rate_rise_over_25_bpm_alerts_only_at_the_evaluation_interval_and_latches():
    m = waiting_room.MonitorState()
    waiting_room.start(m, 0.0)
    feed(m, 0.0, 10, hr=80.0)
    feed(m, 10.0, 290, hr=115.0)
    assert waiting_room.evaluate(m, 120.0, CFG, "iso") is None  # not due yet (5-minute cadence)
    alert = waiting_room.evaluate(m, 301.0, CFG, "iso")
    assert alert and alert["re_triage_alert"] and "Heart rate rose" in alert["reasons"][0]
    assert waiting_room.state(m, 301.0, CFG)["acuity_escalation_alert"] is True
    assert waiting_room.evaluate(m, 602.0, CFG, "iso2") is None  # latched: not re-raised
    waiting_room.acknowledge(m)
    assert waiting_room.state(m, 602.0, CFG)["acuity_escalation_alert"] is False


def test_pain_score_rise_over_35_percent_alerts_and_exactly_25_bpm_does_not():
    m = waiting_room.MonitorState()
    waiting_room.start(m, 0.0)
    feed(m, 0.0, 10, hr=80.0, au09=0.4)
    feed(m, 10.0, 290, hr=105.0, au09=0.4 * 1.2)  # +25 BPM exactly, +20 % pain: neither rule fires
    assert waiting_room.evaluate(m, 301.0, CFG, "iso") is None
    feed(m, 310.0, 290, hr=105.0, au09=0.4 * 1.5)
    a = waiting_room.evaluate(m, 602.0, CFG, "iso")
    assert a and any("AU09" in r for r in a["reasons"])


def test_unreliable_pulse_never_becomes_a_baseline_and_sparse_data_is_not_an_alert():
    m = waiting_room.MonitorState()
    waiting_room.start(m, 0.0)
    feed(m, 0.0, 10, hr=150.0, conf=0.2)
    assert m.base_hr is None
    assert waiting_room.evaluate(m, 301.0, CFG, "iso") is None
    assert m.last_eval["status"] in ("insufficient_data", "no_baseline")
    assert waiting_room.state(m, 301.0, CFG)["acuity_escalation_alert"] is False


def test_samples_are_throttled_to_about_one_hz_and_off_means_off():
    m = waiting_room.MonitorState()
    assert waiting_room.ingest(m, 0.0, {"hr_bpm": 80, "hr_confidence": 1.0}, CFG) is False  # not enabled
    waiting_room.start(m, 0.0)
    assert waiting_room.ingest(m, 1.0, {"hr_bpm": 80, "hr_confidence": 1.0}, CFG) is True
    assert waiting_room.ingest(m, 1.2, {"hr_bpm": 80, "hr_confidence": 1.0}, CFG) is False
    assert waiting_room.ingest(m, 2.0, {"hr_bpm": 80, "hr_confidence": 1.0}, CFG) is True
    waiting_room.stop(m)
    assert waiting_room.state(m, 3.0, CFG)["phase"] == "off"


def test_alert_streams_as_acuity_escalation_event_and_appears_in_unified(client, new_session):
    sid, h, _ = new_session()
    st = client.app.state.vp
    s = st.sessions.get(sid, h["X-Session-Token"])
    client.post(f"/api/sessions/{sid}/control", json={"action": "start"}, headers=h)
    r = client.post(f"/api/sessions/{sid}/waiting-room/start", headers=h)
    assert r.status_code == 200 and r.json()["is_monitored"] is True
    q: list = []
    import asyncio
    qq: asyncio.Queue = asyncio.Queue(maxsize=100)
    s.subscribers.add(qq)
    cfg = st.settings
    cfg.waiting_baseline_samples, cfg.waiting_min_samples = 5, 5
    for i in range(10):
        s.note_metric("hr_bpm", 70.0)
        s.note_metric("hr_confidence", 0.9)
        st.waiting_tick(s, now=1000.0 + i)
    s.monitor.registered_at = 0.0
    for i in range(320):  # the first pass after registered_at=0 evaluates at t=1010; the next is due 300 s later
        s.note_metric("hr_bpm", 110.0)
        s.note_metric("hr_confidence", 0.9)
        st.waiting_tick(s, now=1010.0 + i)
    while not qq.empty():
        q.append(qq.get_nowait())
    assert any(e["type"] == "Acuity_Escalation_Alert" and e["payload"]["re_triage_alert"] for e in q)
    u = client.get(f"/api/sessions/{sid}/unified", headers=h).json()
    w = u["waiting_room_monitoring"]
    assert w["is_monitored"] and w["acuity_escalation_alert"] is True
    assert "baseline_hr_delta" in w  # value needs the real clock; covered by the unit tests above
    client.post(f"/api/sessions/{sid}/waiting-room/ack", headers=h)
    assert client.get(f"/api/sessions/{sid}/waiting-room", headers=h).json()["acuity_escalation_alert"] is False


def test_waiting_room_needs_a_live_session(client, new_session):
    sid, h, _ = new_session()
    assert client.post(f"/api/sessions/{sid}/waiting-room/start", headers=h).status_code == 409


# ---------------------------------------------------------------- unified schema + FHIR + EHR


def test_unified_contains_all_master_schema_keys(client, new_session):
    sid, h, _ = new_session()
    u = client.get(f"/api/sessions/{sid}/unified", headers=h).json()
    for k in ("timestamp", "patient_session_id", "extracted_metrics", "guardrail_status",
              "waiting_room_monitoring", "xai_attributions", "fhir_bundle_ready"):
        assert k in u
    v = u["extracted_metrics"]["vision"]
    assert set(v["facs_action_units"]) == {"au04_distress", "au09_pain", "au12_14_palsy_velocity"}
    assert all(x is None for x in v["facs_action_units"].values())  # missing is null, never 0
    assert "pitch_instability_hz" in u["extracted_metrics"]["audio_dsp"]
    assert u["fhir_bundle_ready"] is False
    assert u["waiting_room_monitoring"]["is_monitored"] is False


def _bundle_with_signals(client, new_session):
    sid, h, _ = new_session(synthetic=True)
    s = client.app.state.vp.sessions.get(sid, h["X-Session-Token"])
    for k, v in (("hr_bpm", 134.0), ("hr_confidence", 0.88), ("asymmetry_pct", 11.2),
                 ("gasping_detected", True), ("speech_to_breath_ratio", 0.32), ("breath_events", 3),
                 ("vocal_jitter", 0.045)):
        s.note_metric(k, v)
    client.post(f"/api/sessions/{sid}/synthetic-transcript", headers=h)
    client.app.state.vp.refresh_guardrail(s, force=True)
    r = client.post(f"/api/sessions/{sid}/fhir", headers=h).json()
    return sid, h, r


def test_fhir_bundle_has_loinc_heart_rate_asymmetry_gasping_and_esi_encounter(client, new_session):
    sid, h, r = _bundle_with_signals(client, new_session)
    assert r["validation"]["valid"], r["validation"]["errors"]
    entries = [e["resource"] for e in r["bundle"]["entry"]]
    hr = [x for x in entries if x["resourceType"] == "Observation"
          and x.get("code", {}).get("coding", [{}])[0].get("code") == "8867-4"]
    assert len(hr) == 1 and hr[0]["valueQuantity"]["value"] == 134.0 and hr[0]["status"] == "preliminary"
    texts = [x.get("code", {}).get("text", "") for x in entries if x["resourceType"] == "Observation"]
    assert any("facial asymmetry" in t for t in texts) and any("gasping" in t for t in texts)
    enc = next(x for x in entries if x["resourceType"] == "Encounter")
    assert enc["priority"]["coding"][0]["code"] == "2"
    assert "Not an official ESI assessment" in enc["priority"]["text"]
    assert "hard override" in enc["extension"][0]["valueString"]
    pat = next(x for x in entries if x["resourceType"] == "Patient")
    assert any(i["value"] == sid for i in pat["identifier"])


def test_ehr_sync_returns_201_for_a_valid_bundle_and_is_labelled_simulated(client, new_session):
    _, h, r = _bundle_with_signals(client, new_session)
    resp = client.post("/api/ehr/sync", json=r["bundle"], headers=h)
    assert resp.status_code == 201
    d = resp.json()
    assert d["simulated"] is True and d["status"] == "created" and d["transmission_id"].startswith("ehr_")
    assert "no external system" in d["target"]
    assert d["resource_counts"]["Encounter"] == 1


def test_ehr_sync_rejects_invalid_or_unauthenticated(client, new_session):
    _, h, r = _bundle_with_signals(client, new_session)
    assert client.post("/api/ehr/sync", json=r["bundle"]).status_code == 403
    bad = {"resourceType": "Bundle", "type": "not-a-type", "entry": [
        {"fullUrl": "urn:uuid:1", "resource": {"resourceType": "Observation", "status": "bogus"}}]}
    resp = client.post("/api/ehr/sync", json=bad, headers=h)
    assert resp.status_code == 422 and resp.json()["detail"]["errors"]
    assert client.post("/api/ehr/sync", json={"hello": "world"}, headers=h).status_code == 422
    assert not validate_bundle({"resourceType": "Patient"})["valid"]


# ---------------------------------------------------------------- follow-up: everyday phrasing + visual findings


@pytest.mark.parametrize("text,concept", [
    ("My chest hurts", "chest_pain"), ("there is a pressure in my chest", "chest_pain"),
    ("my head is hurting a lot", "headache"), ("I feel like I'm going to pass out", "syncope"),
    ("my heart is beating really fast", "palpitations"), ("it hurts when I breathe", "dyspnea"),
    ("I feel sick", "nausea"), ("I cannot move my left leg", "unilateral_weakness"),
    ("my tongue feels thick and my words are not clear", "slurred_speech"),
    ("I keep coughing", "cough"), ("my leg is swollen", "swelling"), ("I fell down the stairs", "injury"),
])
def test_everyday_phrasings_are_extracted(text, concept):
    from app.services.symptom_extractor import extract
    got = [(m.concept, m.polarity) for m in extract("s", text)]
    assert (concept, "affirmed") in got


def test_negated_everyday_phrasings_stay_negated():
    from app.services.symptom_extractor import extract
    assert [(m.concept, m.polarity) for m in extract("s", "I do not have chest pain")] == [("chest_pain", "negated")]
    assert [(m.concept, m.polarity) for m in extract("s", "I have no pain")] == [("pain_general", "negated")]


def test_visual_findings_follow_fresh_metrics_and_missing_data_gives_none():
    from app.services import vision_findings
    s = Settings()
    assert vision_findings.derive({}, None, s) == []
    m = {"asymmetry_pct": 7.5, "au04_distress": 0.8, "au09_pain": 0.2, "au12_14_palsy_velocity": 0.2,
         "hr_bpm": 140.0, "hr_confidence": 0.9}
    f = {"state": "pending_review", "motion_class": "active"}
    got = {x["signal"]: x for x in vision_findings.derive(m, f, s)}
    assert got["asymmetry"]["level"] == "notice" and "Possible" in got["asymmetry"]["label"]
    assert set(got) == {"asymmetry", "au04", "au12_14", "hr"} and got["hr"]["level"] == "alert"
    assert vision_findings.derive({**m, "asymmetry_pct": 12.0}, f, s)[0]["level"] == "alert"
    assert "hr" not in {x["signal"] for x in vision_findings.derive({**m, "hr_confidence": 0.2}, f, s)}
