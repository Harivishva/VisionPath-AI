/** Device capture helpers. Every function must be called from an explicit user action. */

export type DeviceStatus =
  | "idle"
  | "requesting"
  | "live"
  | "paused"
  | "stopped"
  | "denied"
  | "unavailable"
  | "disconnected"
  | "error";

export interface DeviceState {
  status: DeviceStatus;
  message?: string;
}

export class DeviceError extends Error {
  constructor(public kind: "denied" | "unavailable" | "error", message: string) {
    super(message);
  }
}

export function classifyMediaError(err: unknown, device: "camera" | "microphone"): DeviceError {
  const name = (err as { name?: string })?.name ?? "";
  if (name === "NotAllowedError" || name === "SecurityError" || name === "PermissionDeniedError") {
    return new DeviceError(
      "denied",
      `${device[0].toUpperCase() + device.slice(1)} permission was denied. Allow access in the browser site settings, or continue with manual intake.`,
    );
  }
  if (name === "NotFoundError" || name === "OverconstrainedError" || name === "DevicesNotFoundError") {
    return new DeviceError("unavailable", `No ${device} was found on this device.`);
  }
  if (name === "NotReadableError" || name === "AbortError") {
    return new DeviceError("error", `The ${device} is in use by another application or could not be started.`);
  }
  return new DeviceError("error", `The ${device} could not be started (${name || "unknown error"}).`);
}

export async function openCamera(): Promise<MediaStream> {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new DeviceError("unavailable", "This browser does not expose camera access (requires https or localhost).");
  }
  try {
    return await navigator.mediaDevices.getUserMedia({
      video: { width: { ideal: 640 }, height: { ideal: 480 }, frameRate: { ideal: 15 } },
      audio: false,
    });
  } catch (e) {
    throw classifyMediaError(e, "camera");
  }
}

export async function openMic(): Promise<MediaStream> {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new DeviceError("unavailable", "This browser does not expose microphone access (requires https or localhost).");
  }
  try {
    return await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: false },
      video: false,
    });
  } catch (e) {
    throw classifyMediaError(e, "microphone");
  }
}

export function stopStream(stream: MediaStream | null | undefined): void {
  stream?.getTracks().forEach((t) => {
    try {
      t.stop();
    } catch {
      /* already stopped */
    }
  });
}

export function watchEnded(stream: MediaStream, onEnded: () => void): void {
  stream.getTracks().forEach((t) => t.addEventListener("ended", onEnded));
}

export interface FrameSamplerOptions {
  fps: number;
  width: number;
  quality: number;
}

/** Samples the live <video> into small JPEG frames and sends them over the socket. */
export class FrameSampler {
  private timer: ReturnType<typeof setInterval> | null = null;
  private canvas = typeof document !== "undefined" ? document.createElement("canvas") : null;
  private busy = false;
  sent = 0;
  skipped = 0;
  private stamps: number[] = [];

  constructor(
    private video: HTMLVideoElement,
    private send: (buf: ArrayBuffer) => boolean,
    private canSend: () => boolean,
    private opts: FrameSamplerOptions,
    private onStats?: (s: { sent: number; skipped: number; fps: number }) => void,
  ) {}

  start(): void {
    this.stop();
    this.timer = setInterval(() => void this.tick(), 1000 / this.opts.fps);
  }

  stop(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  private async tick(): Promise<void> {
    const v = this.video;
    if (this.busy || !this.canvas || v.readyState < 2 || !v.videoWidth) return;
    if (!this.canSend()) {
      this.skipped++;
      return;
    }
    this.busy = true;
    try {
      const w = Math.min(this.opts.width, v.videoWidth);
      const h = Math.round((w * v.videoHeight) / v.videoWidth);
      this.canvas.width = w;
      this.canvas.height = h;
      const ctx = this.canvas.getContext("2d");
      if (!ctx) return;
      ctx.drawImage(v, 0, 0, w, h);
      const blob: Blob | null = await new Promise((r) => this.canvas!.toBlob(r, "image/jpeg", this.opts.quality));
      if (!blob) return;
      const jpeg = new Uint8Array(await blob.arrayBuffer());
      const buf = new ArrayBuffer(9 + jpeg.length);
      const dv = new DataView(buf);
      dv.setUint8(0, 0x01);
      dv.setFloat64(1, Date.now(), false);
      new Uint8Array(buf, 9).set(jpeg);
      if (this.send(buf)) {
        this.sent++;
        const now = performance.now();
        this.stamps.push(now);
        this.stamps = this.stamps.filter((t) => now - t < 3000);
        const span = (now - this.stamps[0]) / 1000;
        const fps = this.stamps.length > 1 && span > 0 ? (this.stamps.length - 1) / span : 0;
        this.onStats?.({ sent: this.sent, skipped: this.skipped, fps: Math.round(fps * 10) / 10 });
      }
    } finally {
      this.busy = false;
    }
  }
}

/** Streams 16 kHz mono PCM16 chunks from the microphone via an AudioWorklet. */
export class AudioCapture {
  private ctx: AudioContext | null = null;
  private node: AudioWorkletNode | null = null;
  private src: MediaStreamAudioSourceNode | null = null;
  chunks = 0;

  async start(stream: MediaStream, onChunk: (buf: ArrayBuffer) => void, canSend: () => boolean): Promise<void> {
    const Ctx = (globalThis as unknown as { AudioContext?: typeof AudioContext }).AudioContext;
    if (!Ctx) throw new DeviceError("unavailable", "AudioContext is not supported in this browser.");
    this.ctx = new Ctx();
    await this.ctx.audioWorklet.addModule("/pcm-worklet.js");
    this.src = this.ctx.createMediaStreamSource(stream);
    this.node = new AudioWorkletNode(this.ctx, "pcm-downsampler");
    this.node.port.onmessage = (ev: MessageEvent<ArrayBuffer>) => {
      if (!canSend()) return;
      const pcm = new Uint8Array(ev.data);
      const buf = new ArrayBuffer(5 + pcm.length);
      const dv = new DataView(buf);
      dv.setUint8(0, 0x02);
      dv.setUint32(1, 16000, false);
      new Uint8Array(buf, 5).set(pcm);
      this.chunks++;
      onChunk(buf);
    };
    this.src.connect(this.node);
    // The node is intentionally not connected to the destination: no local playback/echo.
    if (this.ctx.state === "suspended") await this.ctx.resume();
  }

  async stop(): Promise<void> {
    try {
      this.node?.port.close();
      this.src?.disconnect();
      this.node?.disconnect();
      await this.ctx?.close();
    } catch {
      /* ignore */
    }
    this.ctx = this.node = this.src = null;
  }
}
