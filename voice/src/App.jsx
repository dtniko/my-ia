import { useState, useCallback, useRef, useEffect } from 'react'
import { useWebSocket }   from './hooks/useWebSocket.js'
import { useSpeech }      from './hooks/useSpeech.js'
import { useElevenLabs }  from './hooks/useElevenLabs.js'
import { useAudioStream } from './hooks/useAudioStream.js'
import { MicButton }      from './components/MicButton.jsx'
import { ChatMessage, StreamingMessage } from './components/ChatMessage.jsx'

const DEFAULT_WS_URL = `ws://${window.location.hostname}:8765`

export default function App() {
  const [wsUrl, setWsUrl]             = useState(DEFAULT_WS_URL)
  const [showSettings, setSettings]   = useState(false)
  const [appState, setAppState]       = useState('disconnected')
  const [messages, setMessages]       = useState([])
  const [streamText, setStreamText]   = useState('')
  const [streamTools, setStreamTools] = useState([])
  const [lang, setLang]               = useState('it-IT')
  const [ttsEnabled, setTtsEnabled]   = useState(true)
  const [isMuted, setIsMuted]         = useState(false)
  const [error, setError]             = useState(null)
  const [tokenStats, setTokenStats]   = useState(null)

  // ── Speaker recognition state ──────────────────────────────────────────────
  const [speakerPaused,     setSpeakerPaused]     = useState(false)
  const [isConfirming,      setIsConfirming]       = useState(false)
  const [confirmTranscript, setConfirmTranscript]  = useState('')
  const [isEnrolling,       setIsEnrolling]        = useState(false)
  const [enrollPct,         setEnrollPct]          = useState(0)
  const [enrollMsg,         setEnrollMsg]          = useState('')
  const [enrollNeeded,      setEnrollNeeded]       = useState(false)
  // feedback rilevamento voce
  const [speakerVerdict,    setSpeakerVerdict]     = useState(null)   // null | {verdict, score}
  const [sttLive,           setSttLive]            = useState('')     // testo STT in arrivo
  const [isTranscribing,    setIsTranscribing]     = useState(false)
  const speakerVerdictTimer = useRef(null)

  const [elKey,     setElKey]     = useState(() => localStorage.getItem('el_key')      ?? '')
  const [elVoiceId, setElVoiceId] = useState(() => localStorage.getItem('el_voice_id') ?? '21m00Tcm4TlvDq8ikWAM')

  const chatEndRef    = useRef(null)
  const msgId         = useRef(0)
  const serverAudioRef = useRef(null)
  // Coda dei segmenti audio della risposta corrente (streaming TTS per frase):
  // il server invia più messaggi `audio` consecutivi, riprodurli in ordine.
  const serverAudioQueueRef = useRef([])
  // True se il server ha inviato almeno un audio nella risposta corrente
  // (usato da onDone per decidere se il TTS browser va saltato).
  const serverAudioUsedRef = useRef(false)
  const notifQueueRef  = useRef([])
  const notifAudioRef  = useRef(null)

  const addMessage = useCallback((role, text, tools = []) => {
    setMessages(prev => [...prev, { id: msgId.current++, role, text, tools }])
  }, [])

  const eleven  = useElevenLabs({ apiKey: elKey, voiceId: elVoiceId })
  const speech  = useSpeech({ lang })   // usato solo per TTS browser (no STT)

  // ── Audio notifiche ────────────────────────────────────────────────────────
  const playNextNotif = useCallback(() => {
    if (notifQueueRef.current.length === 0) { notifAudioRef.current = null; return }
    if (serverAudioRef.current)            { notifAudioRef.current = null; return }
    const { data, format } = notifQueueRef.current.shift()
    const blob  = new Blob([Uint8Array.from(atob(data), c => c.charCodeAt(0))], { type: `audio/${format}` })
    const url   = URL.createObjectURL(blob)
    const audio = new Audio(url)
    notifAudioRef.current = audio
    const cleanup = () => { URL.revokeObjectURL(url); notifAudioRef.current = null; playNextNotif() }
    audio.onended = cleanup; audio.onerror = cleanup
    audio.play().catch(cleanup)
  }, [])

  // ── Callback WebSocket base ────────────────────────────────────────────────
  const onChunk = useCallback((text) => {
    setStreamText(prev => prev + text)
  }, [])

  const onDone = useCallback((fullText) => {
    const tools = streamTools
    setStreamText(''); setStreamTools([])
    if (!fullText) { setAppState('idle'); return }
    addMessage('assistant', fullText, tools)
    // Se il server ha fornito audio (streaming TTS per frase), non duplichiamo
    // con il TTS browser: la riproduzione è già in corso o in coda.
    if (serverAudioUsedRef.current) { serverAudioUsedRef.current = false; return }
    if (!ttsEnabled) { setAppState('idle'); return }
    setAppState('speaking')
    const afterSpeak = () => setAppState('idle')
    if (eleven.isSupported) eleven.speak(fullText, afterSpeak)
    else speech.speak(fullText, afterSpeak)
  }, [streamTools, addMessage, ttsEnabled, eleven, speech])

  const playNextServerAudio = useCallback(() => {
    if (serverAudioQueueRef.current.length === 0) {
      serverAudioRef.current = null
      setAppState('idle')
      if (notifQueueRef.current.length > 0 && !notifAudioRef.current) playNextNotif()
      return
    }
    const { data, format } = serverAudioQueueRef.current.shift()
    const blob  = new Blob([Uint8Array.from(atob(data), c => c.charCodeAt(0))], { type: `audio/${format}` })
    const url   = URL.createObjectURL(blob)
    const audio = new Audio(url)
    serverAudioRef.current = audio
    setAppState('speaking')
    const onEnd = () => { URL.revokeObjectURL(url); playNextServerAudio() }
    audio.onended = onEnd; audio.onerror = onEnd
    audio.play().catch(onEnd)
  }, [playNextNotif])

  const onAudio = useCallback((data, format) => {
    if (!data) return
    serverAudioUsedRef.current = true
    // Accoda il segmento. Se non sta già riproducendo qualcosa, parte ora;
    // altrimenti il callback onended della riproduzione corrente lo prenderà.
    serverAudioQueueRef.current.push({ data, format })
    if (!serverAudioRef.current) playNextServerAudio()
  }, [playNextServerAudio])

  const onNotification = useCallback((notif) => {
    addMessage('notification', notif.content || notif.description)
    if (!notif.data) return
    notifQueueRef.current.push({ data: notif.data, format: notif.format || 'mp3' })
    if (!notifAudioRef.current && !serverAudioRef.current) playNextNotif()
  }, [addMessage, playNextNotif])

  const onTool     = useCallback((name) => { setStreamTools(prev => prev.includes(name) ? prev : [...prev, name]) }, [])
  const onWsStatus = useCallback((state) => { if (state === 'idle') setAppState('idle') }, [])
  const onWsError  = useCallback((msg)   => { setError(msg); setAppState('disconnected') }, [])
  const onStats    = useCallback((tokens, max, pct, compacting) => setTokenStats({ tokens, max, pct, compacting }), [])

  // ── Callback speaker recognition ───────────────────────────────────────────
  const onSpeakerStatus = useCallback(({ state }) => {
    if (state === 'paused')   setSpeakerPaused(true)
    if (state === 'resumed')  setSpeakerPaused(false)
  }, [])

  const onConfirmSpeaking = useCallback(({ transcript }) => {
    setIsConfirming(true)
    setConfirmTranscript(transcript)
  }, [])

  const onEnrollNeeded = useCallback(() => {
    setEnrollNeeded(true)
  }, [])

  const onEnrollStart = useCallback(({ message }) => {
    setIsEnrolling(true); setEnrollPct(0); setEnrollMsg(message)
    setEnrollNeeded(false)
    addMessage('notification', '🎤 ' + message)
  }, [addMessage])

  const onEnrollProgress = useCallback(({ pct }) => {
    setEnrollPct(pct)
  }, [])

  const onEnrollDone = useCallback(({ message }) => {
    setIsEnrolling(false); setEnrollNeeded(false); setEnrollMsg(message)
    addMessage('notification', '✓ ' + message)
    setTimeout(() => setEnrollMsg(''), 5000)
  }, [addMessage])

  const onEnrollError = useCallback(({ message }) => {
    setIsEnrolling(false)
    setError(`Enrollment: ${message}`)
  }, [])

  const onSpeakerResult = useCallback(({ verdict, score }) => {
    setSpeakerVerdict({ verdict, score })
    // Resetta l'indicatore dopo 4 secondi
    clearTimeout(speakerVerdictTimer.current)
    speakerVerdictTimer.current = setTimeout(() => setSpeakerVerdict(null), 4000)
  }, [])

  const onSttText = useCallback(({ text }) => {
    setSttLive(text)
    setIsTranscribing(false)
    // Pulisce dopo 6 secondi (il messaggio completo arriva presto)
    setTimeout(() => setSttLive(''), 6000)
  }, [])

  const onSttStatus = useCallback(({ state }) => {
    setIsTranscribing(state === 'transcribing')
    if (state === 'idle') setSttLive('')
  }, [])

  // ── WebSocket ──────────────────────────────────────────────────────────────
  const ws = useWebSocket({
    onChunk, onDone, onTool, onStatus: onWsStatus, onError: onWsError,
    onAudio, onNotification, onStats,
    onSpeakerStatus, onConfirmSpeaking, onEnrollNeeded,
    onEnrollStart, onEnrollProgress, onEnrollDone, onEnrollError,
    onSpeakerResult, onSttText, onSttStatus,
  })

  const isConnected = ws.status === 'connected'

  // ── Audio stream: cattura continua verso il server ─────────────────────────
  // Sopprime l'invio dei chunk durante il TTS per evitare che Daniela
  // catturi la propria voce.
  const isTtsSpeaking  = appState === 'speaking'
  const audioEnabled   = isConnected && !isMuted
  const audioMuted     = isTtsSpeaking

  const audioStream = useAudioStream({
    sendRaw: ws.sendRaw,
    enabled: audioEnabled,
    muted:   audioMuted,
  })

  // Quando connected+idle → mostra 'listening' (pipeline sempre attiva)
  useEffect(() => {
    if (appState === 'idle' && isConnected && !isMuted && !speakerPaused) {
      setAppState('listening')
    }
  }, [appState, isConnected, isMuted, speakerPaused])

  // ── Confirm speaker ────────────────────────────────────────────────────────
  const handleConfirmYes = useCallback(() => {
    setIsConfirming(false); setConfirmTranscript('')
    ws.sendRaw({ type: 'confirm_yes' })
  }, [ws])

  const handleConfirmNo = useCallback(() => {
    setIsConfirming(false); setConfirmTranscript('')
    ws.sendRaw({ type: 'confirm_no' })
  }, [ws])

  // ── Enrollment da UI ───────────────────────────────────────────────────────
  const handleStartEnroll = useCallback(() => {
    ws.sendRaw({ type: 'enroll_request' })
  }, [ws])

  // ── Connessione ────────────────────────────────────────────────────────────
  useEffect(() => { localStorage.setItem('el_key',      elKey)     }, [elKey])
  useEffect(() => { localStorage.setItem('el_voice_id', elVoiceId) }, [elVoiceId])

  const connect = useCallback(() => {
    setError(null); ws.connect(wsUrl); setAppState('idle')
    setSpeakerPaused(false); setIsConfirming(false); setIsEnrolling(false)
  }, [ws, wsUrl])

  const disconnect = useCallback(() => {
    speech.cancelSpeech(); eleven.cancelSpeech()
    if (serverAudioRef.current) { serverAudioRef.current.pause(); serverAudioRef.current = null }
    ws.disconnect(); setAppState('disconnected')
    setSpeakerPaused(false); setIsConfirming(false); setIsEnrolling(false)
  }, [speech, eleven, ws])

  useEffect(() => {
    if (ws.status === 'disconnected' && appState !== 'disconnected') setAppState('disconnected')
  }, [ws.status])

  useEffect(() => { chatEndRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages, streamText])

  // ── Mute toggle ────────────────────────────────────────────────────────────
  const handleMuteToggle = useCallback(() => {
    if (!isMuted) { setAppState('idle'); setIsMuted(true) }
    else          { setIsMuted(false) }
  }, [isMuted])

  const handleMicClick = useCallback(() => {
    if (appState === 'disconnected') { connect(); return }
    if (appState === 'thinking' || appState === 'connecting') return
    if (appState === 'speaking') {
      speech.cancelSpeech(); eleven.cancelSpeech()
      if (serverAudioRef.current) { serverAudioRef.current.pause(); serverAudioRef.current = null }
      setAppState('idle'); return
    }
    handleMuteToggle()
  }, [appState, connect, speech, eleven, handleMuteToggle])

  // ── Status text ────────────────────────────────────────────────────────────
  const ttsEngine  = ttsEnabled ? (eleven.isSupported ? 'ElevenLabs' : 'Browser') : 'Off'

  const statusText = () => {
    if (isEnrolling)    return `Alleno il riconoscimento vocale… ${enrollPct}%`
    if (isConfirming)   return 'Stai parlando con me?'
    if (speakerPaused)  return 'Ascolto in pausa — di\' «Daniela parla con me»'
    if (isMuted)        return 'Microfono silenziato'
    switch (appState) {
      case 'disconnected': return ws.status === 'connecting' ? 'Connessione…' : 'Clicca per connettere'
      case 'idle':         return 'In attesa…'
      case 'listening':    return enrollMsg || 'In ascolto — parla normalmente'
      case 'thinking':     return streamTools.length > 0 ? `${toolLabel(streamTools[streamTools.length-1])}…` : 'Sto pensando…'
      case 'speaking':     return serverAudioRef.current ? 'Rispondo… (edge-tts)' : eleven.isSupported ? 'Rispondo… (ElevenLabs)' : 'Rispondo…'
      default:             return ''
    }
  }

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div className="app">
      <header className="app-header">
        <div className="header-left">
          <span className="app-logo">LTSIA</span>
          <span className={`conn-dot conn-dot--${ws.status}`} title={ws.status} />
          {ttsEnabled && <span className="tts-badge" title={`TTS: ${ttsEngine}`}>{eleven.isSupported ? '🎙 EL' : '🔊'}</span>}
          {isConnected && audioStream.isCapturing && (
            <span className={`speaker-dot ${speakerPaused ? 'speaker-dot--paused' : 'speaker-dot--active'}`}
              title={speakerPaused ? 'Ascolto in pausa' : 'Pipeline vocale attiva'} />
          )}
        </div>
        <div className="header-right">
          {isConnected && <button className="btn-icon" onClick={disconnect} title="Disconnetti">✕</button>}
          <button className="btn-icon" onClick={() => setSettings(s => !s)} title="Impostazioni">⚙</button>
        </div>
      </header>

      {showSettings && (
        <div className="settings-panel">
          <h3>Impostazioni</h3>
          <label>
            Server WebSocket
            <input type="text" value={wsUrl} onChange={e => setWsUrl(e.target.value)} placeholder="ws://localhost:8765" />
          </label>
          <label>
            Lingua voce
            <select value={lang} onChange={e => setLang(e.target.value)}>
              <option value="it-IT">Italiano</option>
              <option value="en-US">English (US)</option>
              <option value="en-GB">English (UK)</option>
              <option value="fr-FR">Français</option>
              <option value="de-DE">Deutsch</option>
              <option value="es-ES">Español</option>
            </select>
          </label>
          <label className="label-checkbox">
            <input type="checkbox" checked={ttsEnabled} onChange={e => setTtsEnabled(e.target.checked)} />
            Risposta vocale (TTS)
          </label>

          {/* Sezione riconoscimento vocale */}
          <div className="settings-section">
            <div className="settings-section-title">Riconoscimento vocale</div>
            {isConnected ? (
              <button className="btn-primary" onClick={handleStartEnroll} disabled={isEnrolling}>
                {isEnrolling ? `Alleno… ${enrollPct}%` : enrollNeeded ? 'Registra la tua voce' : 'Aggiorna riconoscimento vocale'}
              </button>
            ) : (
              <p className="settings-hint">Connettiti per gestire il riconoscimento vocale.</p>
            )}
            <p className="settings-hint" style={{ marginTop: 8 }}>
              Oppure di' <em>«Daniela allena il riconoscimento vocale»</em> a voce.
            </p>
          </div>

          <div className="settings-section">
            <div className="settings-section-title">
              ElevenLabs TTS
              <a href="https://elevenlabs.io" target="_blank" rel="noreferrer" className="settings-link">
                (gratuito — 10k char/mese)
              </a>
            </div>
            <label>
              API Key
              <input type="password" value={elKey} onChange={e => setElKey(e.target.value)}
                placeholder="sk-... oppure lascia vuoto per TTS browser" autoComplete="off" />
            </label>
            <label>
              Voice ID
              <input type="text" value={elVoiceId} onChange={e => setElVoiceId(e.target.value)} placeholder="21m00Tcm4TlvDq8ikWAM" />
              <span className="settings-hint">
                Trova voice ID su elevenlabs.io/voice-library. Consigliati:
                Rachel (21m00Tcm4TlvDq8ikWAM), Matilda (XrExE9yKIg1WjnnlVkGX)
              </span>
            </label>
          </div>

          <div className="settings-actions">
            <button className="btn-primary" onClick={() => { setSettings(false); connect() }}>Riconnetti</button>
          </div>
        </div>
      )}

      {/* Banner: enrollment necessario */}
      {enrollNeeded && !showSettings && isConnected && (
        <div className="enroll-banner" onClick={handleStartEnroll}>
          <span>Nessun voice print — clicca per registrare la tua voce (o di' «Daniela allena il riconoscimento vocale»)</span>
        </div>
      )}

      <main className="chat-area">
        {messages.length === 0 && (
          <div className="empty-state">
            <p>Connettiti a ltsia e parla normalmente</p>
            <p className="empty-hint">
              Avvia ltsia con:<br />
              <code>./ltsia</code>
            </p>
          </div>
        )}
        {messages.map(msg => <ChatMessage key={msg.id} role={msg.role} text={msg.text} tools={msg.tools} />)}
        {(streamText || streamTools.length > 0) && <StreamingMessage text={streamText} tools={streamTools} />}
        <div ref={chatEndRef} />
      </main>

      {/* Dialogo conferma speaker */}
      {isConfirming && (
        <div className="confirm-panel">
          <p className="confirm-text">
            Ho sentito: <em>«{confirmTranscript}»</em><br />
            Stai parlando con me?
          </p>
          <p className="confirm-hint">Di' <em>«sì sono io»</em> oppure <em>«no»</em>, o clicca:</p>
          {isTranscribing && <p className="stt-badge">⟳ Ascolto…</p>}
          {sttLive && !isTranscribing && <p className="stt-live">«{sttLive}»</p>}
          <div className="confirm-btns">
            <button className="confirm-btn confirm-btn--yes" onClick={handleConfirmYes}>Sì, sono io</button>
            <button className="confirm-btn confirm-btn--no"  onClick={handleConfirmNo}>No</button>
          </div>
        </div>
      )}

      {error && <div className="error-banner" onClick={() => setError(null)}>⚠ {error}</div>}

      <footer className="mic-area">
        {tokenStats && (
          <div className={`token-bar-wrap${tokenStats.compacting ? ' token-bar-wrap--compacting' : ''}`}>
            <div className="token-bar-track">
              <div className="token-bar-fill" style={{ width: `${Math.min(tokenStats.pct, 100)}%` }} />
            </div>
            <span className="token-bar-label">
              {tokenStats.compacting
                ? 'Compattando…'
                : `${(tokenStats.tokens/1000).toFixed(1)}k / ${(tokenStats.max/1000).toFixed(0)}k tok (${tokenStats.pct}%)`}
            </span>
          </div>
        )}

        {/* Barra progresso enrollment */}
        {isEnrolling && (
          <div className="enroll-bar-wrap">
            <div className="enroll-bar-track">
              <div className="enroll-bar-fill" style={{ width: `${enrollPct}%` }} />
            </div>
            <span className="enroll-bar-label">{enrollPct}%</span>
          </div>
        )}

        {/* Feedback rilevamento voce + STT */}
        {isConnected && !isEnrolling && (speakerVerdict || isTranscribing || sttLive) && (
          <div className="voice-feedback">
            {speakerVerdict && (
              <span className={`verdict-badge verdict-badge--${speakerVerdict.verdict}`}>
                {speakerVerdict.verdict === 'match'     && `✓ Voce riconosciuta (${Math.round(speakerVerdict.score * 100)}%)`}
                {speakerVerdict.verdict === 'uncertain' && `? Voce incerta (${Math.round(speakerVerdict.score * 100)}%)`}
                {speakerVerdict.verdict === 'no_match'  && `✕ Voce non riconosciuta (${Math.round(speakerVerdict.score * 100)}%)`}
                {speakerVerdict.verdict === 'no_voiceprint' && '· Nessun voice print'}
                {speakerVerdict.verdict === 'too_short' && '· Segmento troppo breve'}
              </span>
            )}
            {isTranscribing && <span className="stt-badge">⟳ Trascrivo…</span>}
            {sttLive && !isTranscribing && <span className="stt-live">«{sttLive}»</span>}
          </div>
        )}

        <p className="status-text">{statusText()}</p>

        <div className="mic-controls">
          <MicButton
            state={ws.status === 'connecting' ? 'thinking' :
                   speakerPaused ? 'muted' :
                   appState}
            onClick={handleMicClick}
            disabled={ws.status === 'connecting' || appState === 'thinking'}
            muted={isMuted}
          />
          {isConnected && (
            <button
              className={`mute-btn${isMuted ? ' mute-btn--on' : ''}`}
              onClick={handleMuteToggle}
              title={isMuted ? 'Riattiva microfono' : 'Silenzia microfono'}
            >
              <svg viewBox="0 0 24 24" fill="currentColor" width="20" height="20">
                {isMuted
                  ? <path d="M19 11h-1.7c0 .74-.16 1.43-.43 2.05l1.23 1.23c.56-.98.9-2.09.9-3.28zm-4.02.17c0-.06.02-.11.02-.17V5c0-1.66-1.34-3-3-3S9 3.34 9 5v.18l5.98 5.99zM4.27 3L3 4.27l6.01 6.01V11c0 1.66 1.33 3 2.99 3 .22 0 .44-.03.65-.08l1.66 1.66c-.71.33-1.5.52-2.31.52-2.76 0-5.3-2.1-5.3-5.1H5c0 3.41 2.72 6.23 6 6.72V21h2v-3.28c.91-.13 1.77-.45 2.54-.9L19.73 21 21 19.73 4.27 3z"/>
                  : <path d="M12 14c1.66 0 2.99-1.34 2.99-3L15 5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3zm5.3-3c0 3-2.54 5.1-5.3 5.1S6.7 14 6.7 11H5c0 3.41 2.72 6.23 6 6.72V21h2v-3.28c3.28-.48 6-3.3 6-6.72h-1.7z"/>
                }
              </svg>
              <span>{isMuted ? 'Riattiva' : 'Silenzia'}</span>
            </button>
          )}
        </div>
        {!audioStream.isCapturing && isConnected && !isMuted && (
          <p className="warn-text">
            {audioStream.error ?? 'AudioWorklet non supportato — usa Chrome o Edge'}
          </p>
        )}
      </footer>
    </div>
  )
}

// ── Tool labels ───────────────────────────────────────────────────────────────
const TOOL_LABELS = {
  web_search:             'Ricerca web',
  web_fetch:              'Fetch pagina',
  execute_command:        'Esecuzione comando',
  write_file:             'Scrittura file',
  read_file:              'Lettura file',
  plan_project:           'Pianificazione',
  delegate_file_creation: 'Generazione file',
  run_tests:              'Test',
  install_packages:       'Installazione pacchetti',
  create_module:          'Nuovo modulo',
}

function toolLabel(name) {
  return TOOL_LABELS[name] ?? name
}
