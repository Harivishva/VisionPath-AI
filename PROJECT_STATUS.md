# VisionPath AI — Project status

**Phase:** 7 of 7 complete (environment → architecture → backend → frontend → integration → test/repair → run & demo prep).
**Last verified:** 2026-10-29, Windows 11, Python 3.13.5, Node 24.15, Microsoft Edge (Playwright channel), real laptop webcam + microphone.

## Completed features (all exercised by tests or a live run)

- [x] Next.js 16 / React 19 / Tailwind 4 UI ("Quiet Clinical Precision"): setup, intake workspace, summary, FHIR export, diagnostics; every state in the brief has a designed treatment
- [x] Explicit camera/microphone permission after *Start capture*; Start / Pause / Resume / Stop; tracks released on pause, stop, delete, page hide; device-ended handling; permission-denied / no-device messages; manual intake always usable
- [x] Live frames → FastAPI WebSocket (bounded drop-oldest queues) → OpenCV + **MediaPipe FaceLandmarker**: face presence, head pose, quality gates, smoothed experimental geometry, landmark overlay on the feed, measured fps/latency
- [x] Live PCM16 audio → RMS/dBFS, ZCR, spectral features (librosa), reliability-gated F0, clipping/low-level flags, adaptive VAD, utterance segmentation
- [x] Local **faster-whisper** STT (small.en) + optional OpenAI-compatible STT; VAD/confidence hallucination guard; editable transcript with preserved original; manual entry; honest "unavailable" states
- [x] Negation/uncertainty-aware symptom extraction; staff confirm / reject / edit; onset kept as stated (uncertain onsets are never converted to definite times)
- [x] Configurable transparent safety-rule engine (10 rules) with evidence, acknowledgement, withdrawal-on-rejection, rule enable/severity config; prototype urgency label explicitly "not ESI"
- [x] Distinct missing-data states throughout; unavailable modalities never rendered as normal
- [x] Optional LLM draft (schema-validated, timeout + bounded retry, hallucinated-symptom rejection, cannot touch alerts) with deterministic fallback
- [x] FHIR R4-compatible bundle (Patient, Encounter, Observation, Flag, DocumentReference) with structural + value-set + reference validation; copy/download; export gated on valid + finalized
- [x] Privacy: no media persistence, in-memory sessions with **idle-TTL expiry (120 min)**, per-session tokens, CORS allow-list, log redaction, delete-session wipe
- [x] Scripts (`setup`, `start-*`, `run-tests`), pinned requirements + lockfile, `.env.example`, README with demo script and troubleshooting

## Test results (final run)

| Suite | Result |
|---|---|
| Backend `ruff` (F, E9, B) | all checks passed |
| Backend `pytest` | **93 passed** (3 consecutive stable runs) |
| Frontend `tsc --noEmit` | clean |
| Frontend `vitest` | **30 passed** |
| Frontend `next build` (production) | succeeds |
| Playwright `fake-devices` (Edge + Chromium synthetic camera/mic) | **8 passed**; the synthetic-camera pipeline test used 1 retry on the last run (see "Known flake") |
| Playwright `real-devices` (actual webcam + mic) | **1 passed** (run twice) |
| Layout (1366×768, 1440×900, 1024×700) | no horizontal overflow, no page-level scroll, primary controls in viewport at the two target sizes |

Backend coverage includes: startup/health, session CRUD & isolation (tokens), validation, lifecycle, WebSocket flow (bad token, frames, audio, invalid/oversized payloads, pause, disconnect, isolation, periodic status), vision on synthetic/degraded frames, geometry maths & roll invariance, audio features/VAD/clipping/segmentation, STT provider failure/retry/credential errors, hallucination guard (incl. real Whisper on silence/noise), symptom negation/uncertainty, every rule trigger and non-trigger, rule configuration, alert ack/withdrawal, LLM schema/timeouts/hallucination rejection, LLM cannot remove alerts, FHIR valid/invalid/bad-reference/bad-enum, privacy/log redaction/media release, session expiry.

## Verified with actual devices and models (this machine)

- Real webcam (640×480) → real MediaPipe pipeline: 33–34 frames processed per 7 s at ~5 fps, 12–16 ms server latency, 0 dropped. The camera was aimed at a wall, so the app correctly reported **"No face detected — not a normal finding"** (no fabricated values).
- Real microphone (3 input devices present) → real audio pipeline: ~25 chunks, 0 dropped, noise floor adaptive.
- Real MediaPipe face tracking on a **real face photograph** (MediaPipe's public sample portrait, not stored): detected, head pose ≈ upright, geometry computed, overlay (oval/eyes/lips) visually aligned; darkened and blurred variants rejected as `low_quality` with reasons; mirroring flips the geometry sign as expected.
- Real local Whisper on Windows-TTS speech: transcript correct, negation/sudden-onset extraction correct; real model returns no text for silence/noise after the guard.
- Browser pane inside this desktop app **blocks** camera/mic; permission-denial UI was verified there. Device capture was verified through Playwright/Edge instead.

## Known limitations (please read before presenting)

- **Live face tracking on a human in front of the live webcam was not observed**: the camera never saw a full face during automated runs. Face tracking is verified on a still photo and the live pipeline is verified end-to-end for "no face". Try it yourself in front of the camera before the demo.
- **Live human speech through the real microphone → transcript was not verified end-to-end.** During the first real-device run, ~0.5 s of very-low-level audio produced the transcript "Thank you so much." (probably a Whisper hallucination, possibly real speech). A VAD + confidence guard was added and the re-run produced no transcript, but this stays a risk of any Whisper-based system: every machine transcript is shown as pending review.
- OpenAI-compatible STT and LLM paths are tested only against mocked HTTP; no real provider was called.
- FHIR validation is local (`fhir.resources` R4B models, value-set and reference checks). It was **not** run through the official HL7 validator, and the experimental vision/audio observations use local text/codes, not standard terminology. SNOMED codes are used only for 10 unambiguous symptoms; everything else is text.
- Symptom extraction is a limited English lexicon (≈17 concepts), not clinically validated; Whisper `small.en` is English-only and imperfect. Rules fire on reported text/staff input only; vision/audio never trigger or suppress alerts.
- Geometric asymmetry values are experimental descriptors, not probabilities or measurements; no skin-colour/pallor analysis was implemented (deliberately).
- Not verified: Chrome/Firefox/Safari, mobile, screen readers, automated contrast audit, weaker/older laptops, `setup.ps1` on a clean machine (start/test scripts were run; setup was reproduced by hand), non-Windows OS.
- Sessions are in memory only (restart = data gone by design); no user accounts, no TLS; intended for local, single-operator use. Not production grade, not HIPAA/regulatory compliant.
- No ESLint config (Next 16 removed `next lint`); lint = `ruff` (backend) + `tsc --strict` (frontend).

## Known flake

Edge's *synthetic* camera (`--use-fake-device-for-media-stream`) sporadically ends its own video track (≈2 of 6 probe runs; logged with no `stop()` call from the app, no page-hide). The app reports it correctly ("Camera disconnected") and stops uploading frames; this has a deterministic test. The one e2e test that needs several seconds of synthetic camera is configured with retries. The real camera did not show this behaviour in two runs.

## Remaining failures

None known in the automated suites.

## Commands executed (main ones)

`python -m venv`, `pip install -r backend/requirements-dev.txt`, `python backend/scripts/download_models.py --whisper`, `npm install`, `npx tsc --noEmit`, `npx vitest run`, `npx next build`, `python -m pytest`, `python -m ruff check`, `npx playwright test --project=fake-devices`, `E2E_REAL_DEVICES=1 npx playwright test --project=real-devices`, `.\run-tests.ps1`, `.\start-backend.ps1`, `.\start-frontend.ps1`.

## Next actions (suggested)

1. Rehearse the demo with a person in front of the camera and speaking (README "3-minute demonstration"); tune `VP_MAX_HEAD_*`/lighting if the quality gate is too strict for your setup; consider `base.en` Whisper for better accuracy.
2. Run the generated bundle through the official HL7 FHIR validator (java) and, if needed, map the experimental signals to agreed local codes.
3. Add a clinician-reviewed extraction lexicon and rule set; add TLS/auth before any non-local use.


## Iteration 2 (2026-11-04)

Added: STT noise pipeline (RMS/SNR/too_quiet/noisereduce, `temperature=0`, clinical prompt), rPPG (POS) vitals estimate, acoustic DSP (ratio/F0 variation/breath bursts/MFCC), deterministic guardrail matrix with forced prototype priority, XAI panel + overlays, unified JSON endpoint and WS event.

**Verified:** backend `pytest` 118 passed (incl. POS recovering known 55-135 BPM from synthetic signals, rejecting pure noise, gasp/ratio on synthetic audio, every guardrail threshold and non-trigger, stale-data handling, too_quiet over the real WebSocket path); `ruff` clean; `tsc` clean; `vitest` 34 passed; production build OK; Playwright fake-devices 8 passed at 1366x768 / 1440x900 / 1024x700.

**NOT verified:** rPPG accuracy on a real human face or against a pulse oximeter (only synthetic signals); acoustic gasp detection on real breathing sounds; noise reduction effect on real ER noise and on Whisper accuracy; the live webcam at 15 fps for sustained periods; real-device e2e was not re-run after this iteration.

**Caveats:** webcam rPPG is sensitive to light, motion, skin tone, camera auto-exposure and JPEG compression; the heart rate is withheld below a signal-strength threshold. WebRTC VAD not added. Level badge is a prototype ESI-style priority, not official ESI.

## Iteration 3 (2026-11-05)

Added: FACS-style AU velocity proxies, Tamil/Hindi/English symptom normalization and language labels, waiting-room re-triage monitor (event + banner + endpoints), FHIR additions (LOINC 8867-4 HR, asymmetry, gasping, Encounter priority), simulated `POST /api/ehr/sync`, extended unified JSON.

Verified: backend pytest 149 passed (26 new in `tests/test_iteration3.py`: synthetic-landmark AU tests incl. static-vs-active asymmetry, lexicon, monitor thresholds/cadence/latching, event stream, FHIR content, EHR sync 201/422/403), ruff clean, tsc clean, vitest 36 passed, `next build` OK, Playwright fake-devices 7 passed + 1 flaky pass on retry (capture pipeline, first run after a server restart).

NOT verified: AU scores and lip-velocity asymmetry on real faces (only synthetic landmarks tested; thresholds are unvalidated); Whisper language auto-detection (needs the multilingual model, not downloaded); the Tamil/Hindi lexicon (not reviewed by a native speaker or clinician); the waiting monitor over real 5-minute sessions (tested with an injected clock); EHR sync is a simulation; official HL7 validator not run.
