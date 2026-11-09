"""Iteration 2: STT noise pipeline, rPPG, acoustic DSP, guardrails, unified document, XAI shares."""
import numpy as np

from app.config import Settings
from app.services import audio_dsp, audio_preprocess, guardrails
from app.services.rppg import RppgState, estimate_rate, pos_pulse
from app.services.unified import build_unified, evaluate_guardrail
from tests.conftest import make_session

SR = 16000
S = Settings(transcription_provider="none", llm_provider="none", stt_denoise=True)
rng = np.random.default_rng(7)


def voiced(secs, f0=150.0, amp=0.12):
    t = np.arange(int(secs * SR)) / SR
    return sum(amp / k * np.sin(2 * np.pi * f0 * k * t) for k in (1, 2, 3, 4)).astype(np.float32)


def noise(secs, amp):
    return (rng.standard_normal(int(secs * SR)) * amp).astype(np.float32)


# ---------------- STT noise pipeline ----------------
def test_too_quiet_is_flagged_and_boosted_not_dropped():
    x = voiced(1.0, amp=0.01) + noise(1.0, 0.0005)  # RMS ~0.0085: quiet but real speech
    out, st = audio_preprocess.prepare(x, SR, S)
    assert st["audio_quality"] == "too_quiet" and st["skip_stt"] is False
    assert st["gain_db"] > 6
    assert audio_preprocess.rms(out) > 0.02  # boosted before the speech model sees it


def test_near_silence_is_flagged_and_not_transcribed():
    x = voiced(1.0, amp=0.001) + noise(1.0, 0.0002)
    _, st = audio_preprocess.prepare(x, SR, S)
    assert st["audio_quality"] == "too_quiet" and st["skip_stt"] is True


def test_noisy_speech_is_denoised_and_snr_reported():
    x = np.concatenate([noise(0.5, 0.02), voiced(1.5, amp=0.2) + noise(1.5, 0.02), noise(0.5, 0.02)])
    out, st = audio_preprocess.prepare(x, SR, S)
    assert st["noise_filter_applied"] is True
    assert st["snr_db"] is not None and st["snr_db"] > 3
    assert len(out) == len(x)
    # the noise-only lead-in is attenuated
    assert np.sqrt(np.mean(out[: SR // 2] ** 2)) < np.sqrt(np.mean(x[: SR // 2] ** 2))


def test_clean_loud_audio_skips_gating():
    x = np.concatenate([noise(0.5, 0.0002), voiced(1.5, amp=0.3), noise(0.5, 0.0002)])
    _, st = audio_preprocess.prepare(x, SR, S)
    assert st["audio_quality"] == "good"
    assert st["noise_filter_applied"] is False


def test_denoise_failure_falls_back_to_raw(monkeypatch):
    monkeypatch.setattr(audio_preprocess, "denoise", lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
    x = np.concatenate([noise(0.5, 0.02), voiced(1.5, amp=0.2) + noise(1.5, 0.02)])
    out, st = audio_preprocess.prepare(x, SR, S)
    assert st["noise_filter_applied"] is False and st["noise_filter_error"] == "RuntimeError"
    assert out is x


# ---------------- rPPG ----------------
def synth_rgb(bpm, secs=6.0, fps=30.0, noise_amp=0.0):
    t = np.arange(0, secs, 1 / fps)
    pulse = np.sin(2 * np.pi * bpm / 60 * t)
    base = np.array([150.0, 110.0, 90.0])
    # green carries the strongest pulsatile component, as in real skin
    rgb = base[None, :] * (1 + 0.004 * pulse[:, None] * np.array([0.3, 1.0, 0.5])[None, :])
    rgb += rng.standard_normal(rgb.shape) * noise_amp
    return t, rgb


def test_pos_recovers_known_heart_rate():
    for bpm in (55, 72, 100, 135):
        t, rgb = synth_rgb(bpm)
        est = estimate_rate(t, rgb, 0.35)
        assert est["hr"] is not None and abs(est["hr"] - bpm) < 4, (bpm, est)
        assert est["confidence"] >= 0.5


def test_rppg_rejects_pure_noise():
    t = np.arange(0, 6.0, 1 / 30)
    rgb = np.array([150.0, 110.0, 90.0])[None, :] + rng.standard_normal((len(t), 3)) * 0.8
    est = estimate_rate(t, rgb, 0.6)
    assert est["hr"] is None  # never invents a rate from noise


def test_rppg_state_collecting_then_low_fps_then_ok():
    st = RppgState(window_s=5.0, min_fps=10.0)
    t, rgb = synth_rgb(80, secs=2.0)
    for ti, r in zip(t, rgb, strict=True):
        st.add(ti, r)
    assert st.update()["state"] == "not_measured"
    st = RppgState(window_s=5.0, min_fps=10.0)
    t, rgb = synth_rgb(80, secs=6.0, fps=6.0)  # too slow for a pulse
    for ti, r in zip(t, rgb, strict=True):
        st.add(ti, r)
    assert st.update()["state"] == "unavailable"
    st = RppgState(window_s=5.0, min_fps=10.0)
    t, rgb = synth_rgb(80, secs=6.0, fps=15.0)
    for ti, r in zip(t, rgb, strict=True):
        st.add(ti, r)
    est = st.update()
    assert est["state"] == "pending_review" and abs(est["heart_rate_bpm"] - 80) < 5


def test_rppg_gap_resets_buffer():
    st = RppgState()
    t, rgb = synth_rgb(80, secs=3.0, fps=15.0)
    for ti, r in zip(t, rgb, strict=True):
        st.add(ti, r)
    st.add(t[-1] + 5.0, rgb[-1])
    assert len(st.t) == 1


def test_pos_pulse_shape():
    t, rgb = synth_rgb(70, secs=4.0)
    assert pos_pulse(rgb, 30.0).shape == (len(t),)


# ---------------- acoustic DSP ----------------
def gappy_audio(gap_s, burst, n=4):
    parts = [noise(0.3, 0.001)]
    for _ in range(n):
        parts.append(voiced(1.0))
        gap = noise(gap_s, 0.001)
        if burst:
            a = int(0.4 * SR)
            start = int(0.3 * SR)
            gap[start:start + a] += noise(0.4, 0.03)  # breath-like broadband burst
        parts.append(gap)
    parts.append(voiced(1.0))
    return np.concatenate(parts)


def test_dsp_gasp_and_low_ratio():
    d = audio_dsp.analyze(gappy_audio(2.2, burst=True), SR, S)
    assert d["state"] == "pending_review"
    assert d["gasping_detected"] is True
    assert d["speech_to_breath_ratio"] < 0.4
    assert len(d["mfcc_mean"]) == 13 and d["stft_energy_db"]["low_0_500hz"] is not None


def test_dsp_pauses_without_bursts_are_not_gasping():
    d = audio_dsp.analyze(gappy_audio(1.6, burst=False), SR, S)
    assert d["gasping_detected"] is False  # ordinary silence is not an emergency


def test_dsp_continuous_speech_has_high_ratio_and_jitter_measured():
    x = np.concatenate([noise(0.3, 0.001), voiced(6.0), noise(0.3, 0.001)])
    d = audio_dsp.analyze(x, SR, S, noise_floor_dbfs=-60.0)
    assert d["speech_to_breath_ratio"] > 0.9
    assert d["vocal_jitter"] is not None and d["vocal_jitter"] < 0.02  # steady pitch


def test_dsp_insufficient_speech_is_not_a_finding():
    d = audio_dsp.analyze(noise(8.0, 0.001), SR, S)
    assert d["gasping_detected"] is None and d["speech_to_breath_ratio"] is None
    assert d["state"] in ("low_quality", "not_measured")


# ---------------- guardrails ----------------
BASE = {"asymmetry_pct": None, "hr_bpm": None, "hr_confidence": None,
        "gasping_detected": None, "speech_to_breath_ratio": None}


def g(**kw):
    return guardrails.evaluate({**BASE, **kw}, S)


def test_rule1_tachycardia_and_bradycardia():
    r = g(hr_bpm=134, hr_confidence=0.88)
    assert r["hard_override_triggered"] and r["forced_esi_score"] == 2
    assert "TACHYCARDIA" in r["triggered_rule"]
    assert g(hr_bpm=35, hr_confidence=0.9)["triggered_rules"][0]["name"] == "BRADYCARDIA"
    assert not g(hr_bpm=130, hr_confidence=0.9)["hard_override_triggered"]  # strict >
    assert not g(hr_bpm=72, hr_confidence=0.9)["hard_override_triggered"]


def test_rule1_ignores_unreliable_heart_rate():
    assert not g(hr_bpm=170, hr_confidence=0.3)["hard_override_triggered"]
    assert not g(hr_bpm=170, hr_confidence=None)["hard_override_triggered"]


def test_rule2_asymmetry():
    assert g(asymmetry_pct=10.1)["forced_esi_score"] == 2
    assert not g(asymmetry_pct=10.0)["hard_override_triggered"]
    assert not g(asymmetry_pct=None)["hard_override_triggered"]  # unavailable != normal, != abnormal


def test_rule3_requires_both_gasping_and_low_ratio():
    assert g(gasping_detected=True, speech_to_breath_ratio=0.32)["hard_override_triggered"]
    assert not g(gasping_detected=True, speech_to_breath_ratio=0.5)["hard_override_triggered"]
    assert not g(gasping_detected=False, speech_to_breath_ratio=0.1)["hard_override_triggered"]
    assert not g(gasping_detected=True, speech_to_breath_ratio=None)["hard_override_triggered"]


def test_combined_rule_name_matches_schema_style():
    r = g(hr_bpm=134, hr_confidence=0.88, gasping_detected=True, speech_to_breath_ratio=0.32)
    assert r["triggered_rule"] == "ESI_LEVEL_2_TACHYCARDIA_AND_GASPING_OVERRIDE"
    assert r["forced_esi_score"] == 2 and len(r["triggered_rules"]) == 2


def test_gasp_level_configurable_to_1():
    r = guardrails.evaluate({**BASE, "gasping_detected": True, "speech_to_breath_ratio": 0.2},
                            Settings(guard_gasp_level=1))
    assert r["forced_esi_score"] == 1


def test_priority_override_beats_text_rule():
    over = g(asymmetry_pct=12)
    assert guardrails.priority(over, {"level": "routine_review"}) == {"level": 2, "source": "guardrail"}
    none = g()
    assert guardrails.priority(none, {"level": "prompt_clinical_review"})["level"] == 3
    assert guardrails.priority(none, {"level": "routine_review"})["level"] is None


def test_attributions_sum_to_100_and_none_when_idle():
    m = {**BASE, "asymmetry_pct": 11.2, "hr_bpm": 134, "hr_confidence": 0.9,
         "gasping_detected": True, "speech_to_breath_ratio": 0.32}
    a = guardrails.attributions(m, {"level": "immediate_human_review"}, S)
    total = sum(a[k] for k in ("asymmetry_weight_pct", "heart_rate_weight_pct",
                               "gasping_weight_pct", "transcript_weight_pct"))
    assert total == 100
    idle = guardrails.attributions(BASE, {"level": "routine_review"}, S)
    assert idle["asymmetry_weight_pct"] is None


# ---------------- unified document / API ----------------
def test_unified_endpoint_matches_schema_and_missing_is_null(client):
    sid, h, _ = make_session(client)
    r = client.get(f"/api/sessions/{sid}/unified", headers=h)
    assert r.status_code == 200
    u = r.json()
    assert set(u) >= {"timestamp", "audio_status", "extracted_metrics", "guardrail_status",
                      "xai_attributions", "priority"}
    assert u["audio_status"]["audio_quality"] == "not_measured"
    v = u["extracted_metrics"]["vision"]
    assert v["facial_asymmetry_pct"] is None and v["rppg_heart_rate_bpm"] is None
    assert u["extracted_metrics"]["speech"]["dysarthria_confidence"] is None
    assert u["guardrail_status"]["hard_override_triggered"] is False
    assert client.get(f"/api/sessions/{sid}/unified").status_code in (401, 403)


def test_stale_metrics_do_not_drive_override(client):
    sid, h, _ = make_session(client)
    st = client.app.state.vp
    s = st.sessions.get(sid, h["X-Session-Token"])
    s.note_metric("asymmetry_pct", 15.0)
    assert evaluate_guardrail(s, st.settings)["hard_override_triggered"] is True
    s.metrics["asymmetry_pct"] = (15.0, s.metrics["asymmetry_pct"][1] - 60)  # 60 s old
    assert evaluate_guardrail(s, st.settings)["hard_override_triggered"] is False
    assert build_unified(s, st.settings)["extracted_metrics"]["vision"]["facial_asymmetry_pct"] is None


def test_override_does_not_depend_on_text_or_llm(client):
    sid, h, _ = make_session(client)
    st = client.app.state.vp
    s = st.sessions.get(sid, h["X-Session-Token"])
    s.note_metric("hr_bpm", 140.0)
    s.note_metric("hr_confidence", 0.9)
    u = client.get(f"/api/sessions/{sid}/unified", headers=h).json()
    assert u["guardrail_status"]["forced_esi_score"] == 2 and u["priority"]["source"] == "guardrail"


# ---------------- hallucination guards ----------------
def test_broadband_noise_is_never_sent_to_stt():
    for amp in (0.003, 0.01, 0.03, 0.1):
        x = np.concatenate([noise(0.5, 0.001), noise(2.0, amp), noise(0.5, 0.001)])
        _, st = audio_preprocess.prepare(x, SR, S)
        assert st["skip_stt"] is True, (amp, st)
        assert st["audio_quality"] in ("no_speech", "too_quiet")


def test_pitched_speech_like_audio_is_sent_to_stt():
    x = np.concatenate([noise(0.4, 0.001), voiced(1.5, amp=0.15), noise(0.6, 0.001)])
    _, st = audio_preprocess.prepare(x, SR, S)
    assert st["skip_stt"] is False and st["voiced_fraction"] > 0.2


def test_defaults_do_not_prime_or_filter():
    d = Settings()
    assert d.stt_initial_prompt == "" and d.stt_denoise is False


def test_real_whisper_returns_nothing_for_noise_and_silence():
    from app.services.transcription_service import TranscriptionService

    svc = TranscriptionService(Settings(transcription_provider="local"))
    for sig in (noise(3.0, 0.002), noise(3.0, 0.02), np.zeros(SR * 3, np.float32)):
        assert svc._local(sig.astype(np.float32), SR).strip() == ""
