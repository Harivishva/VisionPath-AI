// AudioWorklet: downsample microphone input to 16 kHz mono PCM16 in 250 ms chunks.
// Audio stays in memory and is streamed to the backend; nothing is recorded to disk.
class PcmDownsampler extends AudioWorkletProcessor {
  constructor() {
    super();
    this.target = 16000;
    this.ratio = sampleRate / this.target;
    this.acc = 0;
    this.count = 0;
    this.pos = 0; // fractional read position inside the current averaging window
    this.out = new Int16Array(4000);
    this.n = 0;
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      this.acc += ch[i];
      this.count++;
      this.pos += 1;
      if (this.pos >= this.ratio) {
        this.pos -= this.ratio;
        const v = Math.max(-1, Math.min(1, this.acc / this.count));
        this.out[this.n++] = v < 0 ? v * 32768 : v * 32767;
        this.acc = 0;
        this.count = 0;
        if (this.n === this.out.length) {
          const copy = this.out.slice(0);
          this.port.postMessage(copy.buffer, [copy.buffer]);
          this.n = 0;
        }
      }
    }
    return true;
  }
}
registerProcessor("pcm-downsampler", PcmDownsampler);
