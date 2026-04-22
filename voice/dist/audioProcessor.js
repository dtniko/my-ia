/**
 * AudioWorklet processor — cattura PCM float32 e lo invia al main thread.
 *
 * Se il browser lavora a una sample rate diversa da 16000 Hz (es. 48000 Hz),
 * esegue un downsampling tramite media di blocchi (ottimo per voce, CPU-zero).
 *
 * Invia chunk al main thread ogni TARGET_SAMPLES campioni a 16 kHz (~200 ms).
 */

const TARGET_SR      = 16000
const CHUNK_SAMPLES  = 3200   // 200 ms @ 16 kHz

class AudioProcessor extends AudioWorkletProcessor {
  constructor () {
    super()
    // sampleRate è globale nell'AudioWorklet scope
    this._ratio      = sampleRate / TARGET_SR   // es. 3.0 per 48kHz→16kHz
    this._accumBuf   = []    // accumulo per downsampling
    this._outBuf     = []    // campioni a 16kHz pronti per l'invio
    this._accumCount = 0.0   // contatore frazionario (per ratio non interi)
  }

  process (inputs) {
    const input = inputs[0]
    if (!input || !input[0] || !input[0].length) return true

    const samples = input[0]   // Float32Array, di solito 128 campioni

    if (Math.abs(this._ratio - 1.0) < 0.001) {
      // Nessun resampling necessario (già a 16 kHz)
      for (let i = 0; i < samples.length; i++) {
        this._outBuf.push(samples[i])
      }
    } else if (Number.isInteger(this._ratio) || Math.abs(this._ratio - Math.round(this._ratio)) < 0.01) {
      // Ratio intero (es. 3 per 48kHz→16kHz): media di blocchi — ottima qualità per voce
      const factor = Math.round(this._ratio)
      for (let i = 0; i < samples.length; i++) {
        this._accumBuf.push(samples[i])
        if (this._accumBuf.length >= factor) {
          let sum = 0
          for (let j = 0; j < this._accumBuf.length; j++) sum += this._accumBuf[j]
          this._outBuf.push(sum / this._accumBuf.length)
          this._accumBuf = []
        }
      }
    } else {
      // Ratio non intero (es. 44100→16000 ≈ 2.756): interpolazione lineare
      for (let i = 0; i < samples.length; i++) {
        this._accumCount += 1.0
        if (this._accumCount >= this._ratio) {
          this._accumCount -= this._ratio
          // Interpola tra campione precedente e corrente
          const prev = i > 0 ? samples[i - 1] : samples[i]
          const t    = this._accumCount / this._ratio
          this._outBuf.push(prev + (samples[i] - prev) * t)
        }
      }
    }

    // Invia al main thread quando abbiamo abbastanza campioni
    while (this._outBuf.length >= CHUNK_SAMPLES) {
      const chunk = new Float32Array(this._outBuf.splice(0, CHUNK_SAMPLES))
      this.port.postMessage(chunk, [chunk.buffer])
    }

    return true  // mantieni il processor attivo
  }
}

registerProcessor('audio-processor', AudioProcessor)
