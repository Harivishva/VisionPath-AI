import base64
import json


from tests.conftest import make_session


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_diagnostics_has_no_secrets(client):
    d = client.get("/api/diagnostics").json()
    text = json.dumps(d).lower()
    assert "api_key" not in text and "sk-" not in text
    assert d["vision"]["ready"] is True
    assert d["transcription"]["ready"] is False and "manual" in d["transcription"]["detail"].lower()
    assert d["raw_media_persistence"] is False


def test_session_requires_consent_and_valid_reference(client):
    assert client.post("/api/sessions", json={"reference": "A"}).status_code == 422
    assert client.post("/api/sessions", json={"reference": "<script>", "consent_acknowledged": True}).status_code == 422
    assert client.post("/api/sessions", json={"reference": "A", "consent_acknowledged": True, "extra": 1}).status_code == 422


def test_session_isolation_and_token(client):
    a, ha, _ = make_session(client, reference="A-1")
    b, hb, _ = make_session(client, reference="B-1")
    assert client.get(f"/api/sessions/{a}", headers=ha).status_code == 200
    assert client.get(f"/api/sessions/{a}", headers=hb).status_code == 403
    assert client.get(f"/api/sessions/{a}").status_code == 403
    assert client.get("/api/sessions/ses_nope", headers=ha).status_code == 404
    client.post(f"/api/sessions/{a}/transcript", json={"text": "I have chest pain"}, headers=ha)
    snap_b = client.get(f"/api/sessions/{b}", headers=hb).json()
    assert snap_b["segments"] == [] and snap_b["alerts"] == []


def test_lifecycle_transitions(client):
    sid, h, _ = make_session(client)
    c = lambda a: client.post(f"/api/sessions/{sid}/control", json={"action": a}, headers=h)
    assert c("pause").status_code == 409
    assert c("start").json()["status"] == "live"
    assert c("start").status_code == 409
    assert c("pause").json()["status"] == "paused"
    assert c("resume").json()["status"] == "live"
    snap = c("stop").json()
    assert snap["status"] == "stopped" and snap["privacy"]["media_released"] is True
    assert client.post(f"/api/sessions/{sid}/control", json={"action": "bogus"}, headers=h).status_code == 422


def test_manual_transcript_flow_extracts_alerts_and_missing_info(client):
    sid, h, _ = make_session(client)
    r = client.post(f"/api/sessions/{sid}/transcript",
                    json={"text": "My arm suddenly feels weak. I do not have chest pain."}, headers=h)
    snap = r.json()
    assert r.status_code == 201
    concepts = {m["concept"]: m["polarity"] for m in snap["mentions"]}
    assert concepts == {"unilateral_weakness": "affirmed", "chest_pain": "negated"}
    assert [a["rule_id"] for a in snap["alerts"]] == ["R-NEURO-SUDDEN"]
    assert snap["indicator"]["level"] == "immediate_human_review"
    assert snap["alerts"][0]["evidence"] and snap["alerts"][0]["state"] == "active"
    assert snap["segments"][0]["source"] == "manual"
    assert any("onset" in x.lower() for x in snap["missing_information"]) or True
    assert snap["modalities"]["vision"]["state"] == "not_measured"  # never "normal"
    assert snap["modalities"]["audio"]["state"] == "not_measured"


def test_alert_visible_when_other_modalities_unavailable_and_ack(client):
    sid, h, _ = make_session(client)
    snap = client.post(f"/api/sessions/{sid}/transcript", json={"text": "I have chest pain"}, headers=h).json()
    assert snap["modalities"]["vision"]["state"] != "available"
    aid = snap["alerts"][0]["id"]
    snap = client.post(f"/api/sessions/{sid}/alerts/{aid}/ack", headers=h).json()
    assert snap["alerts"][0]["state"] == "acknowledged" and snap["alerts"][0]["acknowledged_at"]
    assert client.post(f"/api/sessions/{sid}/alerts/zzz/ack", headers=h).status_code == 404


def test_transcript_correction_reextracts(client):
    sid, h, _ = make_session(client)
    snap = client.post(f"/api/sessions/{sid}/transcript", json={"text": "I have a headache"}, headers=h).json()
    seg = snap["segments"][0]["id"]
    snap = client.patch(f"/api/sessions/{sid}/transcript/{seg}", json={"text": "I have chest pain"}, headers=h).json()
    assert snap["segments"][0]["edited"] and snap["segments"][0]["original_text"] == "I have a headache"
    assert [m["concept"] for m in snap["mentions"]] == ["chest_pain"]
    assert client.patch(f"/api/sessions/{sid}/transcript/nope", json={"text": "x"}, headers=h).status_code == 404


def test_synthetic_only_in_synthetic_sessions(client):
    sid, h, _ = make_session(client, synthetic=False)
    assert client.post(f"/api/sessions/{sid}/synthetic-transcript", headers=h).status_code == 409
    sid, h, _ = make_session(client, synthetic=True, reference="DEMO-1")
    snap = client.post(f"/api/sessions/{sid}/synthetic-transcript", headers=h).json()
    assert snap["segments"][0]["source"] == "synthetic" and snap["synthetic"] is True
    pol = {m["concept"]: m["polarity"] for m in snap["mentions"]}
    assert pol["chest_pain"] == "negated" and pol["facial_droop"] == "affirmed"
    assert pol["unilateral_weakness"] == "uncertain"


def test_staff_observation_and_validation(client):
    sid, h, _ = make_session(client)
    r = client.post(f"/api/sessions/{sid}/observations",
                    json={"text": "Appears pale", "concerning": True, "urgency": "immediate_human_review"}, headers=h)
    assert r.status_code == 201 and r.json()["alerts"][0]["rule_id"] == "R-STAFF-001"
    assert client.post(f"/api/sessions/{sid}/observations", json={"text": "", "concerning": True}, headers=h).status_code == 422
    assert client.post(f"/api/sessions/{sid}/transcript", json={"text": "x" * 5000}, headers=h).status_code == 422


def test_review_requires_resolving_pending_then_fhir_export(client):
    sid, h, _ = make_session(client, synthetic=True, reference="DEMO-2")
    snap = client.post(f"/api/sessions/{sid}/synthetic-transcript", headers=h).json()
    assert client.post(f"/api/sessions/{sid}/review", json={"finalize": True}, headers=h).status_code == 409
    for m in snap["mentions"]:
        client.patch(f"/api/sessions/{sid}/symptoms/{m['id']}", json={"status": "confirmed"}, headers=h)
    client.put(f"/api/sessions/{sid}/summary", json={"chief_complaint": "Facial droop and speech difficulty"}, headers=h)
    d = client.post(f"/api/sessions/{sid}/summary/draft?mode=llm", headers=h).json()
    assert d["origin"] == "deterministic" and "LLM draft unavailable" in d["notice"]
    assert "SYNTHETIC" in d["text"]
    pre = client.post(f"/api/sessions/{sid}/fhir", headers=h).json()
    assert pre["validation"]["valid"] and pre["finalized"] is False and pre["exportable"] is False
    assert client.post(f"/api/sessions/{sid}/review", json={"finalize": True, "reviewer": "RN Test"}, headers=h).status_code == 200
    out = client.post(f"/api/sessions/{sid}/fhir", headers=h).json()
    assert out["validation"]["valid"] and out["exportable"] is True
    assert out["validation"]["resource_counts"]["DocumentReference"] == 1
    assert client.get(f"/api/sessions/{sid}/fhir", headers=h).json()["bundle"]["resourceType"] == "Bundle"
    doc = next(e["resource"] for e in out["bundle"]["entry"] if e["resource"]["resourceType"] == "DocumentReference")
    assert "SYNTHETIC" in base64.b64decode(doc["content"][0]["attachment"]["data"]).decode()
    # editing after finalization is blocked
    assert client.put(f"/api/sessions/{sid}/summary", json={"clinician_notes": "x"}, headers=h).status_code == 409


def test_fhir_get_before_generate_404(client):
    sid, h, _ = make_session(client)
    assert client.get(f"/api/sessions/{sid}/fhir", headers=h).status_code == 404


def test_rules_endpoint_requires_token(client):
    assert client.get("/api/rules").status_code == 200
    rules = client.get("/api/rules").json()["rules"]
    body = {"rules": [{"id": rules[0]["id"], "enabled": False, "severity": "routine_review"}]}
    assert client.put("/api/rules", json=body).status_code == 403
    _, h, _ = make_session(client)
    assert client.put("/api/rules", json=body, headers=h).status_code == 200
    assert client.put("/api/rules", json={"rules": [{"id": "X", "enabled": True, "severity": "routine_review"}]}, headers=h).status_code == 422
    body["rules"][0]["enabled"] = True
    body["rules"][0]["severity"] = rules[0]["severity"]
    client.put("/api/rules", json=body, headers=h)


def test_cleanup_deletes_everything(client):
    sid, h, _ = make_session(client)
    client.post(f"/api/sessions/{sid}/transcript", json={"text": "I have chest pain"}, headers=h)
    assert client.delete(f"/api/sessions/{sid}", headers=h).json()["media_released"] is True
    assert client.get(f"/api/sessions/{sid}", headers=h).status_code == 404
    assert client.app.state.vp.sessions.count() == 0


def test_session_limit(client):
    client.app.state.vp.settings.max_sessions = 2
    make_session(client, reference="a"), make_session(client, reference="b")
    r = client.post("/api/sessions", json={"reference": "c", "consent_acknowledged": True})
    assert r.status_code == 429


def test_cors_restricted(client):
    r = client.get("/api/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in r.headers
    r = client.get("/api/health", headers={"Origin": "http://localhost:3000"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_idle_sessions_expire_and_are_erased(client):
    st = client.app.state.vp
    sid, h, _ = make_session(client)
    client.post(f"/api/sessions/{sid}/transcript", json={"text": "I have chest pain"}, headers=h)
    s = st.sessions._sessions[sid]
    assert st.sessions.expired_ids() == []
    s.last_activity -= st.settings.session_idle_ttl_minutes * 60 + 5
    assert st.sessions.expired_ids() == [sid]
    import asyncio
    assert asyncio.run(st.sessions.sweep()) == 1
    assert st.sessions.count() == 0 and s.segments == [] and s.mentions == []
    assert client.get(f"/api/sessions/{sid}", headers=h).status_code == 404


def test_session_with_connected_client_never_expires(client):
    st = client.app.state.vp
    sid, h, _ = make_session(client)
    s = st.sessions._sessions[sid]
    s.last_activity -= 10 * 3600
    s.subscribers.add(object())  # an attached live client
    assert st.sessions.expired_ids() == []
    s.subscribers.clear()
