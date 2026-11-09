# VisionPath AI — multimodal emergency-intake decision-support prototype

VisionPath AI uses a laptop webcam and microphone to extract **observable signals**, **transcribe speech**, organise **patient-reported symptoms**, surface **transparent safety-rule triggers** and produce a **reviewed intake summary** and **FHIR R4-compatible JSON**.

> **This is a decision-support prototype, not a diagnostic device.** It never diagnoses stroke, hypoxia, cyanosis, bleeding, respiratory failure or anything else. It does not measure oxygen saturation, blood pressure, temperature or blood loss. Its urgency labels are workflow aids, **not Emergency Severity Index (ESI) levels**. A trained clinician remains responsible for assessment and prioritisation. Use **synthetic or explicitly consented data only**. No HIPAA, Indian-regulatory or clinical-safety compliance is claimed.

## What it does

| Area | Implementation (all real, no faked live values) |
|---|---|
| Camera | Browser `getUserMedia` → ~5 fps, 480 px JPEG frames over WebSocket → OpenCV decode → **MediaPipe FaceLandmarker** (478 landmarks, head pose from the facial transformation matrix) |
| Vision signals | Face present/absent, quality gates (brightness, sharpness, face size/visibility, head yaw/pitch/roll), smoothed **experimental** left/right geometry (mouth-corner, brow, eye-opening offsets in inter-ocular units). Never a probability, never diagnostic. Model confidence is **not** shown because the model does not provide one |
| Microphone | `AudioWorklet` → 16 kHz PCM16 chunks → RMS/dBFS, zero-crossing rate, spectral centroid/rolloff/flatness (librosa), autocorrelation F0 (withheld unless periodic), clipping & low-level detection, adaptive-threshold voice-activity detection, utterance segmentation |
| Speech-to-text | **Local faster-whisper** (default, audio stays on the device) or any OpenAI-compatible API; both optional. VAD + confidence filtering stops hallucinated text on silence. Manual transcript entry always works. Transcripts are editable; the original is preserved |
| Symptom extraction | Deterministic lexicon with **negation** ("no chest pain"), **uncertainty** ("not sure whether…" is never turned into a definite onset), onset phrases, observer vs patient reporter, sudden-onset wording. Staff confirm / reject / edit every item |
| Safety rules | Configurable rule engine (`R-NEURO-SUDDEN`, `R-CHEST-001`, …). Every alert lists rule id, exact evidence quote, source, timestamp, severity, explanation, recommended action, acknowledgement state. Rules run synchronously on text so **no camera, model or LLM failure can delay an alert** |
| Missing data | `not_measured` / `not_detected` / `unavailable` / `low_quality` / `pending_review` are distinct states everywhere (UI, summary, FHIR `dataAbsentReason`) |
| LLM (optional) | Provider-independent, JSON-schema validated, timeouts + bounded retries, rejects drafts that mention symptoms not in the input. **Cannot** create, change or suppress alerts. Deterministic summary is the fallback |
| FHIR | `Bundle(collection)` with `Patient` (pseudonymous), `Encounter`, `Observation` (patient-reported / staff / experimental signals with `dataAbsentReason` when unmeasured), `Flag` (rule alerts), `DocumentReference` (reviewed summary). Validated locally with `fhir.resources` R4B models + value-set and reference-consistency checks. Synthetic sessions are tagged `HTEST` |
| Privacy | No media written to disk; session data lives in server memory only; token-protected sessions; CORS allow-list; bounded queues; log redaction; delete-session wipes everything |

## Quick start (Windows PowerShell)

Requirements: Python 3.11+ (tested on 3.13), Node.js 20+ (tested on 24), a webcam/microphone for live capture.

```powershell
cd visionpath
.\setup.ps1          # venv + pip deps + MediaPipe model + local Whisper model + npm install + build
.\start-all.ps1      # starts API (:8000) and web app (:3000) and opens the browser
```

Very long project paths can break Windows DLL loading. If `setup.ps1` fails with "filename or extension is too long", set a short venv location first: `$env:VP_VENV="C:\vpenv"` (the start/test scripts honour it).

Manual start: `.\start-backend.ps1` and `.\start-frontend.ps1` in two terminals. API docs: <http://localhost:8000/docs>.

### Configuration

Copy `backend/.env.example` to `backend/.env`. Everything is optional.

| Variable | Purpose |
|---|---|
| `VP_TRANSCRIPTION_PROVIDER` | `auto` (default: local Whisper if installed) · `local` · `openai` · `none` |
| `VP_LOCAL_WHISPER_MODEL` | `small.en` (default, ~480 MB) · `base.en` · `tiny.en` (fastest, least accurate) · `medium.en` |
| `VP_TRANSCRIPTION_BASE_URL/API_KEY/MODEL` | OpenAI-compatible transcription. **Audio is sent to this endpoint** |
| `VP_LLM_PROVIDER/BASE_URL/API_KEY/MODEL` | Optional summary drafting. **Confirmed intake text is sent to this endpoint** |
| `VP_CORS_ORIGINS` | Allowed web origins |
| `VP_MAX_HEAD_YAW/PITCH/ROLL_DEG`, `VP_MIN_SHARPNESS`, `VP_MIN_BRIGHTNESS` | Vision quality gates |

Frontend: `frontend/.env.example` (`NEXT_PUBLIC_API_URL`, default `http://localhost:8000`).

The core pipeline (camera CV, audio features, manual symptoms, rules, FHIR) needs **no paid API and no key**. Models: the MediaPipe face model (~3.7 MB) is fetched by `backend/scripts/download_models.py`; Whisper downloads from Hugging Face on first use (or `download_models.py --whisper`).

## Tests

```powershell
.\run-tests.ps1                         # ruff, pytest, tsc, vitest, production build
.\run-tests.ps1 -E2E                    # + Playwright with Chromium synthetic camera/mic (servers must be running)
.\run-tests.ps1 -E2E -RealDevices       # + the REAL webcam/microphone test (turns your camera on for ~15 s)
```

See `PROJECT_STATUS.md` for current results and exactly what is and is not verified.

## 3-minute demonstration

Real devices:
1. Open <http://localhost:3000> → check the readiness panel (backend, face model, speech-to-text).
2. Keep **Synthetic demonstration session** ticked, tick consent, **Create intake session**.
3. **Start capture** → allow camera + microphone. The live feed, face outline and measured fps/latency appear; cover the lens or leave the frame to show "Visual analysis unavailable — not a normal finding" and the low-light/blur "Insufficient signal" state.
4. Say: *"My face feels like it is drooping on one side and I suddenly have trouble speaking. I do not have chest pain."* The transcript appears (local Whisper); symptoms show *affirmed / affirmed / negated*.
5. The **Immediate human review** indicator and `R-NEURO-SUDDEN` alert show the exact evidence. No chest-pain alert fires (negated). **Acknowledge** it.
6. **Confirm** each symptom → **Summary** tab: confirm chief complaint, *Generate draft*, edit, **Finalize review**.
7. **FHIR export** → *Generate & validate* → download the JSON. → **Stop session** (devices released) → *End & delete session*.

Fallback if speech-to-text or the microphone is unavailable: on the Intake tab press **Load synthetic statement** (clearly labelled `SYNTHETIC DEMO`, only available in synthetic sessions) or type the sentence above into *Add statement manually*, then continue from step 5.

## Architecture

```
backend/app
  main.py, config.py, state.py
  api/routes.py          REST     api/ws.py   live WebSocket (video | audio | transcription workers, bounded queues)
  schemas/models.py      strict Pydantic models and enums
  services/
    vision_processor.py        OpenCV + MediaPipe, quality gating, smoothing
    audio_processor.py         features, VAD, utterance segmentation
    transcription_service.py   local Whisper / OpenAI-compatible, hallucination guard
    symptom_extractor.py       negation & uncertainty aware extraction
    safety_rules.py            configurable rule engine, alert merging, urgency label
    multimodal_aggregator.py   honest per-modality state + snapshot
    clinical_summary.py        deterministic sections + validated optional LLM draft
    fhir_service.py            bundle builder + validator
    session_manager.py, privacy_service.py
frontend/  Next.js (App Router) + TypeScript + Tailwind v4;  components/, lib/ (api, media capture, useIntake hook)
```

WebSocket protocol — client→server binary: `0x01 + float64 ts + JPEG` (video), `0x02 + uint32 rate + PCM16` (audio); text: `session.control`. Server→client events: `session.status`, `session.snapshot`, `video.status`, `vision.observation`, `audio.status`, `audio.feature`, `transcript.partial`, `transcript.final`, `symptom.extracted`, `safety.alert`, `clinical.summary`, `fhir.validation`, `system.error` (each with `event_id`, `session_id`, `ts`, `payload`).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `DLL load failed … filename or extension is too long` | Project/venv path too long → `$env:VP_VENV="C:\vpenv"` |
| "Backend unreachable" on the setup page | Start `.\start-backend.ps1`; check `NEXT_PUBLIC_API_URL` and `VP_CORS_ORIGINS` |
| Camera permission denied | Allow it in the browser's site settings (lock icon). Manual intake keeps working. Note: some embedded browser panes block devices entirely; use Edge/Chrome |
| Vision "Unavailable: model not ready" | Run `python backend/scripts/download_models.py` |
| Speech-to-text "unavailable" | `pip install faster-whisper` + `download_models.py --whisper`, or configure an API in `.env` |
| "Face partly outside the frame / too small / too dark / blurry / head rotated" | These are quality gates doing their job: face the camera, add light, move closer |
| Port in use | Stop the old process or change the ports in the start scripts |


## Iteration 2: noise-robust STT, rPPG, acoustic DSP, guardrails, explainability

| Feature | Where | Notes |
|---|---|---|
| STT noise pipeline | `services/audio_preprocess.py`, `transcription_service.py` | RMS + SNR per utterance; `noisereduce` spectral gating when SNR < 25 dB; RMS < 0.01 -> `audio_quality: "too_quiet"` and **no transcript is guessed**; Whisper `small.en`, `temperature=0`, clinical initial prompt. WebRTC VAD was not added (the existing adaptive VAD and Whisper's built-in VAD are used). |
| rPPG heart rate | `services/rppg.py` | POS algorithm on forehead + upper-cheek ROI, 5 s rolling window, 0.75-3.33 Hz Butterworth, FFT peak. The browser now sends 15 fps (was 5) because a pulse needs >= 10 fps. **Experimental, not a validated vital sign**; reported only when the spectral peak is dominant. |
| Acoustic DSP | `services/audio_dsp.py` | MFCC, STFT band energy, speech-to-pause ratio, frame-to-frame F0 variation, breath-like burst detection over a 15 s window. Descriptive acoustic observations only. |
| Guardrails | `services/guardrails.py` | Deterministic rules, no LLM: HR > 130 or < 40, asymmetry > 10 %, gasping + speech ratio < 0.4. Thresholds configurable (`VP_GUARD_*`). Only data fresher than 5 s counts. |
| XAI dashboard | `components/XaiPanel.tsx`, `CameraStage.tsx` | ROI + asymmetry-vector overlays, SHAP-inspired weighted contribution bars, priority badge (Level 1 red / 2 orange / 3 yellow), "Guardrail Status: ACTIVE OVERRIDE". |
| Unified JSON | `GET /api/sessions/{id}/unified`, WS `unified.metrics` | Matches the master schema; missing values are `null`, never invented. `dysarthria_confidence` is always `null` (no such model). |

Honest limits: the "Level N" badge is a **prototype ESI-style priority** driven by experimental signals and configurable thresholds. It is not an official ESI determination and a clinician decides acuity. The attribution bars are a weighted share of signal strength, not Shapley values. Rule 3 forces Level 2 by default (`VP_GUARD_GASP_LEVEL=1` for Level 1).

## Iteration 3: facial action-unit velocity, multilingual normalization, waiting-room monitor, EHR sync

All four are experimental workflow aids. None is a diagnosis, a validated measurement, or an official ESI/clinical decision.

- **Facial action-unit proxies** (`services/facs.py`): 3D landmark geometry over a rolling 3 s / 90-frame buffer. `au04_distress` (brow 70/300 to inner canthus 133/362), `au09_pain` (nostril sides 198/420), `au12_14_palsy_velocity` (mirror-asymmetry of the lip-corner velocity vectors 61/291, v = delta d / delta t). Scores are relative to a per-session baseline (first 20 accepted frames), so a static structural asymmetry gives ~0 velocity and only active one-sided motion raises it. These are geometric proxies, not certified FACS coding.
- **Multilingual normalization** (`services/clinical_terms.py`): a deterministic lexicon maps colloquial English and romanised/native Tamil and Hindi phrases to tags such as "Substernal Chest Pressure" and "Acute Dyspnea". `detected_languages` comes from script blocks and marker words plus Whisper's language. The lexicon is small and needs native-speaker and clinical review; a missing tag does not mean a symptom is absent. Tags do not feed the safety rules.
- **Whisper language auto-detection** needs a multilingual model. The default `small.en` is English-only and always decodes as English. To enable it set `VP_LOCAL_WHISPER_MODEL=small` (about 480 MB download, first use); `VP_STT_LANGUAGE=auto` is already the default.
- **Waiting-room monitor** (`services/waiting_room.py`): a 1 Hz asyncio poller reads the latest fresh derived metrics (rPPG heart rate, AU04/AU09). Baseline = median of the first 10 samples; every 5 minutes the 5-minute moving average is compared with it. `re_triage_alert` is raised when HR rises by more than 25 BPM or the AU09 score rises by more than 35 % (baseline floored at 0.2). The alert streams as an `Acuity_Escalation_Alert` WebSocket event, shows as a flashing banner, and latches until acknowledged. Endpoints: `POST /api/sessions/{id}/waiting-room/start|stop|ack`, `GET .../waiting-room`. A pulse cannot be recovered from a camera sampled at 1 frame/s, so the video stream keeps its normal rate; only the monitor runs at 1 Hz. There is no separate secondary-camera ingest.
- **FHIR**: the bundle now includes an experimental heart-rate Observation coded LOINC 8867-4 (preliminary), facial asymmetry %, an acoustic gasping score, an Encounter `priority` (prototype ESI-style level plus guardrail status) and a Flag for an active re-triage alert. No demographics are collected, so the Patient carries only the pseudonymous session id and reference.
- **`POST /api/ehr/sync`** validates a Bundle (fhir.resources R4B plus reference/value-set checks) and returns `201 Created` with `simulated: true`. Nothing is stored or sent anywhere; no Epic or Cerner system is contacted. Needs a valid session token.
- **Unified JSON** (`GET /api/sessions/{id}/unified`, WS `unified.metrics`) now also carries `patient_session_id`, `facs_action_units`, `pitch_instability_hz`, `detected_languages`, `normalized_clinical_symptoms`, `waiting_room_monitoring` and `fhir_bundle_ready` (true once the review is finalized).

### Follow-up fixes (2026-11-05)
- Whisper's internal VAD threshold was 0.6 and discarded real laptop-microphone speech; it is now `VP_STT_VAD_THRESHOLD=0.35`. If Whisper still returns nothing for an utterance that clearly contains a voice, it is decoded once more without the VAD (the confidence filters stay on). Quiet speech is boosted up to +18 dB. Each utterance logs its level, SNR and voiced fraction (no audio, no text) to the backend console.
- The symptom lexicon now understands everyday phrasing ("my chest hurts", "going to pass out", "it hurts when I breathe") plus cough, injury, swelling, self-reported stroke/heart-attack concern and a generic "pain, site not specified".
- New "Visual signs" list (`services/vision_findings.py`) turns the camera metrics into plain-language, evidence-backed notices (asymmetry >= 6 %, AU04/AU09 >= 0.5, one-sided lip motion, high/low pulse estimate). Experimental; an empty list is not a clearance.
