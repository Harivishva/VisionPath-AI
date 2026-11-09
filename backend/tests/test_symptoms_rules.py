from app.schemas.models import StaffObservation, Urgency
from app.services import symptom_extractor as sx
from app.services.safety_rules import RuleEngine, merge_alerts, overall_indicator
from app.schemas.models import RuleConfig, TranscriptSegment


def ex(text):
    return sx.extract("seg1", text)


def one(text, concept):
    ms = [m for m in ex(text) if m.concept == concept]
    assert ms, f"{concept} not extracted from: {text}"
    return ms[0]


def test_affirmed_symptom():
    assert one("I have chest pain", "chest_pain").polarity == "affirmed"


def test_negation():
    assert one("I do not have any chest pain.", "chest_pain").polarity == "negated"
    assert one("No shortness of breath.", "dyspnea").polarity == "negated"
    assert one("She denies chest pain", "chest_pain").polarity == "negated"


def test_negation_scope_breaks_at_but():
    ms = ex("No chest pain but my arm feels weak")
    pol = {m.concept: m.polarity for m in ms}
    assert pol["chest_pain"] == "negated"
    assert pol["unilateral_weakness"] == "affirmed"


def test_uncertainty_is_not_negation_and_not_definite():
    m = one("I am not sure whether the weakness in my arm started this morning.", "unilateral_weakness")
    assert m.polarity == "uncertain"
    assert m.onset is not None and m.onset.certain is False and "this morning" in m.onset.text


def test_certain_onset_preserved():
    m = one("My chest pain started this morning.", "chest_pain")
    assert m.polarity == "affirmed" and m.onset and m.onset.certain


def test_duration_onset():
    m = one("I have had a headache for two hours", "headache")
    assert m.onset and "two hours" in m.onset.text


def test_sudden_flag_and_reporter():
    m = one("My husband suddenly has trouble speaking, he is slurring his words", "slurred_speech")
    assert m.sudden and m.reporter == "observer"


def test_negated_has_no_sudden_or_onset():
    m = one("There is no sudden chest pain", "chest_pain")
    assert not m.sudden and m.onset is None


def test_no_invention_on_unrelated_text():
    assert ex("The weather is nice and I walked here") == []


def engine_eval(text="", obs=None, engine=None):
    ms = ex(text) if text else []
    seg = TranscriptSegment(id="seg1", text=text or "x", original_text=text or "x", source="manual")
    return (engine or RuleEngine()).evaluate(ms, obs or [], [seg]), ms


def rule_ids(alerts):
    return {a.rule_id for a in alerts}


def test_sudden_neuro_triggers_immediate():
    alerts, _ = engine_eval("Suddenly my face is drooping on one side")
    a = [x for x in alerts if x.rule_id == "R-NEURO-SUDDEN"]
    assert a and a[0].severity == Urgency.IMMEDIATE
    assert a[0].evidence and a[0].evidence[0].source == "symptom_mention"
    assert a[0].recommended_action and a[0].explanation


def test_neuro_without_sudden_is_prompt():
    alerts, _ = engine_eval("My arm feels weak")
    assert rule_ids(alerts) == {"R-NEURO-REPORTED"}
    assert alerts[0].severity == Urgency.PROMPT


def test_combo():
    alerts, _ = engine_eval("My face is drooping and I am slurring my words")
    assert "R-NEURO-COMBO" in rule_ids(alerts)


def test_negated_symptom_does_not_trigger():
    alerts, _ = engine_eval("I do not have chest pain. No shortness of breath.")
    assert alerts == []


def test_uncertain_symptom_still_surfaced():
    alerts, _ = engine_eval("I am not sure, maybe I have chest pain")
    assert "R-UNCERTAIN-001" in rule_ids(alerts)
    assert all(a.pending_review for a in alerts)


def test_chest_breath_syncope_bleeding():
    ids = rule_ids(engine_eval("I have chest pain and I can't breathe")[0])
    assert {"R-CHEST-001", "R-BREATH-001"} <= ids
    assert "R-CONSC-001" in rule_ids(engine_eval("He passed out earlier")[0])
    assert "R-BLEED-SEVERE" in rule_ids(engine_eval("She is bleeding heavily")[0])
    assert rule_ids(engine_eval("a small cut is bleeding")[0]) == {"R-BLEED-001"}


def test_staff_observation_rule():
    o = StaffObservation(text="Patient appears pale and diaphoretic", concerning=True, urgency=Urgency.IMMEDIATE)
    alerts, _ = engine_eval(obs=[o])
    assert alerts[0].rule_id == "R-STAFF-001" and alerts[0].severity == Urgency.IMMEDIATE
    o2 = StaffObservation(text="walks normally", concerning=False, urgency=Urgency.ROUTINE)
    assert engine_eval(obs=[o2])[0] == []


def test_rejected_mention_withdraws_but_stays_visible():
    eng = RuleEngine()
    alerts, ms = engine_eval("I have chest pain", engine=eng)
    ms[0].status = "rejected"
    now = eng.evaluate(ms, [], [])
    merged = merge_alerts(alerts, now)
    assert merged and merged[0].state == "withdrawn" and merged[0].withdrawn_reason


def test_ack_state_preserved_on_reevaluation():
    eng = RuleEngine()
    alerts, ms = engine_eval("I have chest pain", engine=eng)
    alerts[0].state = "acknowledged"
    merged = merge_alerts(alerts, eng.evaluate(ms, [], []))
    assert merged[0].state == "acknowledged"


def test_rules_configurable():
    eng = RuleEngine()
    eng.update([RuleConfig(id="R-CHEST-001", enabled=False, severity=Urgency.IMMEDIATE)])
    assert engine_eval("I have chest pain", engine=eng)[0] == []
    eng.update([RuleConfig(id="R-CHEST-001", enabled=True, severity=Urgency.PROMPT)])
    assert engine_eval("I have chest pain", engine=eng)[0][0].severity == Urgency.PROMPT


def test_indicator_states_are_distinct_and_not_esi():
    assert overall_indicator([], False)["level"] == "insufficient_information"
    assert overall_indicator([], True)["level"] == "routine_review"
    alerts, _ = engine_eval("I have chest pain")
    ind = overall_indicator(alerts, True)
    assert ind["level"] == "immediate_human_review" and "ESI" in ind["disclaimer"]
    assert "score" not in ind


def test_speech_difficulty_phrasings():
    for t in ["I suddenly have trouble getting my words out", "She can't get her words out",
              "He has difficulty speaking", "my speech is garbled"]:
        assert one(t, "slurred_speech").polarity == "affirmed", t
    assert one("I have no trouble speaking", "slurred_speech").polarity == "negated"


def test_synthetic_statement_extracts_all_four_concepts():
    from app.api.routes import SYNTHETIC_STATEMENT
    pol = {m.concept: m.polarity for m in ex(SYNTHETIC_STATEMENT)}
    assert pol == {"facial_droop": "affirmed", "slurred_speech": "affirmed", "chest_pain": "negated",
                   "unilateral_weakness": "uncertain"}
