import { useRef, useState, useCallback, useEffect } from 'react'

export function useWebSocket ({
  onChunk, onDone, onTool, onStatus, onError,
  onAudio, onNotification, onStats,
  onLog,                // ({level, msg, ts})   — log dal backend
  // nuovi callback audio pipeline
  onSpeakerStatus,      // ({state: "paused"|"resumed"})
  onConfirmSpeaking,    // ({transcript: "..."})
  onEnrollNeeded,       // ()
  onEnrollStart,        // ({duration, message})
  onEnrollProgress,     // ({pct})
  onEnrollDone,         // ({message})
  onEnrollError,        // ({message})
  onSpeakerResult,      // ({verdict, score})  — feedback rilevamento voce
  onSttText,            // ({text})             — testo trascritto da STT
  onSttStatus,          // ({state})            — "transcribing"|"idle"
  onSnapshot,           // (payload)            — snapshot dashboard
  onJobsUpdate,         // ({jobs, job_logs})   — push periodico job
} = {}) {
  const wsRef               = useRef(null)
  const [status, setStatus] = useState('disconnected')
  const [error, setError]   = useState(null)

  const disconnect = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.onclose = null
      wsRef.current.close()
      wsRef.current = null
    }
    setStatus('disconnected')
  }, [])

  const connect = useCallback((url) => {
    disconnect()
    setStatus('connecting')
    setError(null)

    const ws = new WebSocket(url)
    wsRef.current = ws

    ws.onopen = () => { setStatus('connected'); setError(null) }

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data)
        switch (msg.type) {
          // ── esistenti ──────────────────────────────────────────────────────
          case 'chunk':        onChunk?.(msg.text ?? '');                                                         break
          case 'done':         onDone?.(msg.full ?? '');                                                          break
          case 'tool':         onTool?.(msg.name ?? '');                                                          break
          case 'status':       onStatus?.(msg.state ?? 'idle');                                                   break
          case 'audio':        onAudio?.(msg.data ?? '', msg.format ?? 'mp3');                                    break
          case 'notification': onNotification?.({ description: msg.description ?? '', content: msg.content ?? '', data: msg.data ?? '', format: msg.format ?? 'mp3' }); break
          case 'stats':        onStats?.(msg.tokens ?? 0, msg.max ?? 0, msg.pct ?? 0, msg.compacting ?? false);  break
          case 'error':        onError?.(msg.message ?? 'Unknown error');                                         break
          case 'log':          onLog?.({ level: msg.level ?? 'info', msg: msg.msg ?? '', ts: msg.ts ?? 0 });     break
          // ── speaker recognition ───────────────────────────────────────────
          case 'speaker_status':   onSpeakerStatus?.({ state: msg.state ?? '' });                                    break
          case 'confirm_speaking': onConfirmSpeaking?.({ transcript: msg.transcript ?? '' });                        break
          case 'enroll_needed':    onEnrollNeeded?.();                                                                break
          case 'enroll_start':     onEnrollStart?.({ duration: msg.duration ?? 25, message: msg.message ?? '' });    break
          case 'enroll_progress':  onEnrollProgress?.({ pct: msg.pct ?? 0 });                                        break
          case 'enroll_done':      onEnrollDone?.({ message: msg.message ?? '' });                                    break
          case 'enroll_error':     onEnrollError?.({ message: msg.message ?? '' });                                   break
          case 'speaker_result':   onSpeakerResult?.({ verdict: msg.verdict ?? '', score: msg.score ?? 0 });         break
          case 'stt_text':         onSttText?.({ text: msg.text ?? '' });                                             break
          case 'stt_status':       onSttStatus?.({ state: msg.state ?? 'idle' });                                    break
          // ── dashboard ─────────────────────────────────────────────────────
          case 'snapshot':         onSnapshot?.(msg.payload ?? {});                                                   break
          case 'jobs_update':      onJobsUpdate?.({ jobs: msg.jobs ?? [], job_logs: msg.job_logs ?? [] });           break
        }
      } catch { /* ignora non-JSON */ }
    }

    ws.onerror = () => {
      // Non notifica: onclose parte subito dopo e gestisce la riconnessione
      setStatus('error')
    }

    ws.onclose = () => { wsRef.current = null; setStatus('disconnected') }
  }, [disconnect, onChunk, onDone, onTool, onStatus, onError, onAudio, onStats, onLog,
      onSpeakerStatus, onConfirmSpeaking, onEnrollNeeded, onEnrollStart,
      onEnrollProgress, onEnrollDone, onEnrollError,
      onSpeakerResult, onSttText, onSttStatus,
      onSnapshot, onJobsUpdate])

  const sendMessage = useCallback((text) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'message', text }))
    }
  }, [])

  const sendRaw = useCallback((obj) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(obj))
    }
  }, [])

  useEffect(() => () => disconnect(), [disconnect])

  return { connect, disconnect, sendMessage, sendRaw, status, error }
}

