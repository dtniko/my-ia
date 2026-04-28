import { useRef, useState, useCallback, useEffect } from 'react'

/**
 * useAudioStream — cattura audio dal microfono e invia chunk PCM float32
 * al server via WebSocket (come messaggi audio_chunk base64).
 *
 * Il codice del AudioWorklet processor è inlineato come stringa per evitare
 * qualsiasi problema di path, MIME type o fetch in dev/prod con Vite.
 *
 * Il parametro `muted` sospende l'invio dei chunk senza fermare la cattura
 * (utile durante la riproduzione TTS per evitare di catturare la propria voce).
 */

// ── Worklet inlineato ─────────────────────────────────────────────────────────
// Stesso codice di voice/public/audioProcessor.js, inlineato per massima
// compatibilità. Vite non processa i file in public/ come moduli ES, e alcuni
// browser rifiutano addModule() su file serviti senza header espliciti.

const WORKLET_CODE = /* js */`
const TARGET_SR     = 16000;
const CHUNK_SAMPLES = 3200;   // 200 ms @ 16 kHz

class AudioProcessor extends AudioWorkletProcessor {
  constructor () {
    super();
    this._ratio      = sampleRate / TARGET_SR;
    this._accumBuf   = [];
    this._outBuf     = [];
    this._accumCount = 0.0;
  }

  process (inputs) {
    const input = inputs[0];
    if (!input || !input[0] || !input[0].length) return true;

    const samples = input[0];

    if (Math.abs(this._ratio - 1.0) < 0.001) {
      // Già a 16 kHz — nessun resampling
      for (let i = 0; i < samples.length; i++) {
        this._outBuf.push(samples[i]);
      }
    } else if (Math.abs(this._ratio - Math.round(this._ratio)) < 0.01) {
      // Ratio intero (es. 3 per 48 kHz → 16 kHz): media di blocchi
      const factor = Math.round(this._ratio);
      for (let i = 0; i < samples.length; i++) {
        this._accumBuf.push(samples[i]);
        if (this._accumBuf.length >= factor) {
          let sum = 0;
          for (let j = 0; j < this._accumBuf.length; j++) sum += this._accumBuf[j];
          this._outBuf.push(sum / this._accumBuf.length);
          this._accumBuf = [];
        }
      }
    } else {
      // Ratio non intero (es. 44100 → 16000): interpolazione lineare
      for (let i = 0; i < samples.length; i++) {
        this._accumCount += 1.0;
        if (this._accumCount >= this._ratio) {
          this._accumCount -= this._ratio;
          const prev = i > 0 ? samples[i - 1] : samples[i];
          const t    = this._accumCount / this._ratio;
          this._outBuf.push(prev + (samples[i] - prev) * t);
        }
      }
    }

    while (this._outBuf.length >= CHUNK_SAMPLES) {
      const chunk = new Float32Array(this._outBuf.splice(0, CHUNK_SAMPLES));
      this.port.postMessage(chunk, [chunk.buffer]);
    }

    return true;
  }
}

registerProcessor('audio-processor', AudioProcessor);
`

async function loadWorklet (ctx) {
  const blob = new Blob([WORKLET_CODE], { type: 'application/javascript' })
  const url  = URL.createObjectURL(blob)
  try {
    await ctx.audioWorklet.addModule(url)
  } finally {
    URL.revokeObjectURL(url)
  }
}

// ── Performance logging ───────────────────────────────────────────────────────

// Conta i chunk inviati e logga rate + tempo encode ogni 5s
function makeChunkLogger () {
  let count     = 0
  let totalMs   = 0
  let lastLog   = performance.now()

  return function logChunk (encodeMs) {
    count++
    totalMs += encodeMs
    const now  = performance.now()
    const elapsed = now - lastLog
    if (elapsed >= 5000) {
      const rate    = (count / (elapsed / 1000)).toFixed(1)
      const avgEnc  = count > 0 ? (totalMs / count).toFixed(2) : 0
      console.debug(`[audio] ${rate} chunk/s | encode avg ${avgEnc}ms (${count} chunk in ${(elapsed/1000).toFixed(1)}s)`)
      count = 0; totalMs = 0; lastLog = now
    }
  }
}

// Rileva long task (>50 ms) nel main thread tramite PerformanceObserver
function observeLongTasks () {
  if (typeof PerformanceObserver === 'undefined') return
  try {
    const obs = new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        console.warn(`[perf] Long task: ${entry.duration.toFixed(1)}ms @ ${entry.startTime.toFixed(0)}ms`)
      }
    })
    obs.observe({ type: 'longtask', buffered: false })
    console.debug('[perf] LongTask observer attivo')
  } catch (_) { /* browser non supporta longtask */ }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function float32ToBase64 (f32) {
  const bytes  = new Uint8Array(f32.buffer)
  let   binary = ''
  for (let i = 0; i < bytes.length; i += 8192) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 8192))
  }
  return btoa(binary)
}

// ── Hook ──────────────────────────────────────────────────────────────────────

export function useAudioStream ({ sendRaw, enabled = true, muted = false } = {}) {
  const ctxRef     = useRef(null)
  const nodesRef   = useRef(null)   // { source, worklet, stream }
  const sendRef    = useRef(sendRaw)
  const mutedRef   = useRef(muted)
  const enabledRef = useRef(enabled)

  const [isCapturing, setIsCapturing] = useState(false)
  const [error,       setError]       = useState(null)

  useEffect(() => { sendRef.current    = sendRaw  }, [sendRaw])
  useEffect(() => { mutedRef.current   = muted    }, [muted])
  useEffect(() => { enabledRef.current = enabled  }, [enabled])

  const logChunkRef = useRef(null)

  const start = useCallback(async () => {
    if (ctxRef.current) return
    if (typeof AudioWorkletNode === 'undefined') {
      setError('AudioWorklet non supportato in questo browser')
      return
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount:     1,
          sampleRate:       16000,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl:  true,
        }
      })

      const ctx = new AudioContext({ sampleRate: 16000 })
      console.debug(`[audio] AudioContext sampleRate effettivo: ${ctx.sampleRate}Hz`)
      await loadWorklet(ctx)

      observeLongTasks()
      logChunkRef.current = makeChunkLogger()

      const source  = ctx.createMediaStreamSource(stream)
      const worklet = new AudioWorkletNode(ctx, 'audio-processor')

      worklet.port.onmessage = (e) => {
        if (mutedRef.current || !enabledRef.current) return
        const t0  = performance.now()
        const b64 = float32ToBase64(e.data)
        const enc = performance.now() - t0
        logChunkRef.current?.(enc)
        sendRef.current?.({
          type: 'audio_chunk',
          data: b64,
          sr:   16000,
        })
      }

      source.connect(worklet)

      ctxRef.current   = ctx
      nodesRef.current = { source, worklet, stream }
      setIsCapturing(true)
      setError(null)
    } catch (err) {
      setError(`Microfono: ${err.message}`)
    }
  }, [])

  const stop = useCallback(() => {
    if (!ctxRef.current) return
    try {
      nodesRef.current?.source?.disconnect()
      nodesRef.current?.worklet?.disconnect()
      nodesRef.current?.stream?.getTracks().forEach(t => t.stop())
      ctxRef.current?.close()
    } catch (_) { /* ignora */ }
    ctxRef.current   = null
    nodesRef.current = null
    setIsCapturing(false)
  }, [])

  useEffect(() => {
    if (enabled) { start() }
    else         { stop()  }
  }, [enabled])  // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => () => stop(), [stop])

  return { isCapturing, error, start, stop }
}
