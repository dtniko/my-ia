import { useState, useCallback, useRef, useEffect } from 'react'
import { useWebSocket }   from './hooks/useWebSocket.js'
import { useSpeech }      from './hooks/useSpeech.js'
import { useElevenLabs }  from './hooks/useElevenLabs.js'
import { useAudioStream } from './hooks/useAudioStream.js'
import { AiFace }       from './components/AiFace.jsx'
import { SidePanel }    from './components/SidePanel.jsx'
import { QdrantPanel }  from './components/QdrantPanel.jsx'
import { ConfigPanel }  from './components/ConfigPanel.jsx'
import { ChatMessage, StreamingMessage } from './components/ChatMessage.jsx'

const DEFAULT_WS_URL = `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.hostname}:8765`

export default function App() {
  // ── Connection ─────────────────────────────────────────────────────────────
  const [wsUrl, setWsUrl]           = useState(() => localStorage.getItem('ws_url') || DEFAULT_WS_URL)
  const [appState, setAppState]     = useState('disconnected')
  const [error, setError]           = useState(null)

  // ── Chat ───────────────────────────────────────────────────────────────────
  const [messages, setMessages]     = useState([])
  const [streamText, setStreamText] = useState('')
  const [streamTools, setStreamTools] = useState([])
  const [textInput, setTextInput]   = useState('')
  const [lang, setLang]             = useState('it-IT')
  const [ttsEnabled, setTtsEnabled] = useState(true)
  const [isMuted, setIsMuted]       = useState(false)
  const [tokenStats, setTokenStats] = useState(null)

  // ── Speaker recognition ────────────────────────────────────────────────────
  const [speakerPaused,    setSpeakerPaused]    = useState(false)
  const [isConfirming,     setIsConfirming]      = useState(false)
  const [confirmTranscript,setConfirmTranscript] = useState('')
  const [isEnrolling,      setIsEnrolling]       = useState(false)
  const [enrollPct,        setEnrollPct]         = useState(0)
  const [enrollMsg,        setEnrollMsg]         = useState('')
  const [enrollNeeded,     setEnrollNeeded]      = useState(false)
  const [speakerVerdict,   setSpeakerVerdict]    = useState(null)
  const [sttLive,          setSttLive]           = useState('')
  const [isTranscribing,   setIsTranscribing]    = useState(false)
  const speakerVerdictTimer = useRef(null)

  // ── Dashboard ──────────────────────────────────────────────────────────────
  const [snapshot,    setSnapshot]    = useState(null)
  const [jobsLive,    setJobsLive]    = useState(null)
  const [jobLogsLive, setJobLogsLive] = useState(null)

  // ── UI state ───────────────────────────────────────────────────────────────
  const [showSettings,   setShowSettings]   = useState(false)
  const [showDebug,      setShowDebug]      = useState(false)
  const [debugTab,       setDebugTab]       = useState('backend')
  const [showChat,       setShowChat]       = useState(false)
  const [chatMaximized,  setChatMaximized]  = useState(false)
  const [showConfig,     setShowConfig]     = useState(false)
  const [showCommands,   setShowCommands]   = useState(false)
  const [installPrompt,  setInstallPrompt]  = useState(null)

  // ── Log capture ────────────────────────────────────────────────────────────
  const MAX_LOGS = 300
  const [frontendLogs, setFrontendLogs] = useState([])
  const [backendLogs,  setBackendLogs]  = useState([])
  const debugLogEndRef = useRef(null)

  // ── ElevenLabs ─────────────────────────────────────────────────────────────
  const [elKey,     setElKey]     = useState(() => localStorage.getItem('el_key')      ?? '')
  const [elVoiceId, setElVoiceId] = useState(() => localStorage.getItem('el_voice_id') ?? '21m00Tcm4TlvDq8ikWAM')

  const msgId          = useRef(0)
  const serverAudioRef = useRef(null)
  const serverAudioQueueRef = useRef([])
  const serverAudioUsedRef  = useRef(false)
  const notifQueueRef  = useRef([])
  const notifAudioRef  = useRef(null)
  const chatEndRef     = useRef(null)
  const reconnectTimer = useRef(null)
  const connectRef     = useRef(null)

  const addMessage = useCallback((role, text, tools = []) => {
    setMessages(prev => [...prev, { id: msgId.current++, role, text, tools }])
  }, [])

  const eleven = useElevenLabs({ apiKey: elKey, voiceId: elVoiceId })
  const speech = useSpeech({ lang })

  // ── Audio notifiche ────────────────────────────────────────────────────────
  const playNextNotif = useCallback(() => {
    if (notifQueueRef.current.length === 0) { notifAudioRef.current = null; return }
    if (serverAudioRef.current)             { notifAudioRef.current = null; return }
    const { data, format } = notifQueueRef.current.shift()
    const blob  = new Blob([Uint8Array.from(atob(data), c => c.charCodeAt(0))], { type: `audio/${format}` })
    const url   = URL.createObjectURL(blob)
    const audio = new Audio(url)
    notifAudioRef.current = audio
    const cleanup = () => { URL.revokeObjectURL(url); notifAudioRef.current = null; playNextNotif() }
    audio.onended = cleanup; audio.onerror = cleanup
    audio.play().catch(cleanup)
  }, [])

  // ── WebSocket callbacks ────────────────────────────────────────────────────
  const onChunk = useCallback((text) => {
    setStreamText(prev => prev + text)
  }, [])

  const onDone = useCallback((fullText) => {
    const tools = streamTools
    setStreamText(''); setStreamTools([])
    if (!fullText) { setAppState('idle'); return }
    addMessage('assistant', fullText, tools)
    if (serverAudioUsedRef.current) { serverAudioUsedRef.current = false; return }
    if (!ttsEnabled) { setAppState('idle'); return }
    setAppState('speaking')
    const afterSpeak = () => setAppState('idle')
    if (eleven.isSupported) eleven.speak(fullText, afterSpeak)
    else speech.speak(fullText, afterSpeak)
  }, [streamTools, addMessage, ttsEnabled, eleven, speech])

  const playNextServerAudio = useCallback(() => {
    if (serverAudioQueueRef.current.length === 0) {
      serverAudioRef.current = null; setAppState('idle')
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
    serverAudioQueueRef.current.push({ data, format })
    if (!serverAudioRef.current) playNextServerAudio()
  }, [playNextServerAudio])

  const onNotification = useCallback((notif) => {
    addMessage('notification', notif.content || notif.description)
    if (!notif.data) return
    notifQueueRef.current.push({ data: notif.data, format: notif.format || 'mp3' })
    if (!notifAudioRef.current && !serverAudioRef.current) playNextNotif()
  }, [addMessage, playNextNotif])

  const onTool     = useCallback((name)  => { setStreamTools(prev => prev.includes(name) ? prev : [...prev, name]) }, [])
  const onWsStatus = useCallback((state) => {
    if (state === 'idle')     setAppState('idle')
    if (state === 'thinking') setAppState('thinking')
  }, [])
  // Gli errori di connessione WS non vengono mostrati come banner:
  // il reconnect-banner sopra il viso già comunica lo stato disconnesso.
  const onWsError  = useCallback(() => { setAppState('disconnected') }, [])
  const onStats    = useCallback((tokens, max, pct, compacting) => setTokenStats({ tokens, max, pct, compacting }), [])
  const onLog      = useCallback(({ level, msg, ts }) => {
    setBackendLogs(prev => [...prev.slice(-(MAX_LOGS - 1)), { level, msg, ts }])
  }, []) // eslint-disable-line

  // Speaker recognition
  const onSpeakerStatus   = useCallback(({ state }) => {
    if (state === 'paused')  setSpeakerPaused(true)
    if (state === 'resumed') setSpeakerPaused(false)
  }, [])
  const onConfirmSpeaking = useCallback(({ transcript }) => {
    setIsConfirming(true); setConfirmTranscript(transcript)
  }, [])
  const onEnrollNeeded    = useCallback(() => setEnrollNeeded(true), [])
  const onEnrollStart     = useCallback(({ message }) => {
    setIsEnrolling(true); setEnrollPct(0); setEnrollMsg(message)
    setEnrollNeeded(false); addMessage('notification', '🎤 ' + message)
  }, [addMessage])
  const onEnrollProgress  = useCallback(({ pct }) => setEnrollPct(pct), [])
  const onEnrollDone      = useCallback(({ message }) => {
    setIsEnrolling(false); setEnrollNeeded(false); setEnrollMsg(message)
    addMessage('notification', '✓ ' + message)
    setTimeout(() => setEnrollMsg(''), 5000)
  }, [addMessage])
  const onEnrollError     = useCallback(({ message }) => {
    setIsEnrolling(false); setError(`Enrollment: ${message}`)
  }, [])
  const onSpeakerResult   = useCallback(({ verdict, score }) => {
    setSpeakerVerdict({ verdict, score })
    clearTimeout(speakerVerdictTimer.current)
    speakerVerdictTimer.current = setTimeout(() => setSpeakerVerdict(null), 4000)
  }, [])
  const onSttText  = useCallback(({ text }) => {
    setSttLive(text); setIsTranscribing(false)
    setTimeout(() => setSttLive(''), 6000)
  }, [])
  const onSttStatus = useCallback(({ state }) => {
    setIsTranscribing(state === 'transcribing')
    if (state === 'idle') setSttLive('')
  }, [])

  // Dashboard
  const onSnapshot   = useCallback((payload) => {
    setSnapshot(payload)
    if (payload?.jobs)     setJobsLive(payload.jobs)
    if (payload?.job_logs) setJobLogsLive(payload.job_logs)
  }, [])
  const onJobsUpdate = useCallback(({ jobs, job_logs }) => {
    setJobsLive(jobs); setJobLogsLive(job_logs)
  }, [])

  // ── WebSocket ──────────────────────────────────────────────────────────────
  const ws = useWebSocket({
    onChunk, onDone, onTool, onStatus: onWsStatus, onError: onWsError,
    onAudio, onNotification, onStats, onLog,
    onSpeakerStatus, onConfirmSpeaking, onEnrollNeeded,
    onEnrollStart, onEnrollProgress, onEnrollDone, onEnrollError,
    onSpeakerResult, onSttText, onSttStatus,
    onSnapshot, onJobsUpdate,
  })

  const isConnected = ws.status === 'connected'

  // ── Audio stream ───────────────────────────────────────────────────────────
  const isTtsSpeaking = appState === 'speaking'
  const audioEnabled  = isConnected && !isMuted
  const audioMuted    = isTtsSpeaking

  const audioStream = useAudioStream({
    sendRaw: ws.sendRaw,
    enabled: audioEnabled,
    muted:   audioMuted,
  })

  useEffect(() => {
    if (appState === 'idle' && isConnected && !isMuted && !speakerPaused) {
      setAppState('listening')
    }
  }, [appState, isConnected, isMuted, speakerPaused])

  // ── Connect / disconnect ───────────────────────────────────────────────────
  const connect = useCallback(() => {
    clearTimeout(reconnectTimer.current)
    setError(null)
    ws.connect(wsUrl)
    setAppState('idle')
    setSpeakerPaused(false); setIsConfirming(false); setIsEnrolling(false)
  }, [ws, wsUrl])

  const disconnect = useCallback(() => {
    clearTimeout(reconnectTimer.current)
    speech.cancelSpeech(); eleven.cancelSpeech()
    if (serverAudioRef.current) { serverAudioRef.current.pause(); serverAudioRef.current = null }
    ws.disconnect(); setAppState('disconnected')
    setSpeakerPaused(false); setIsConfirming(false); setIsEnrolling(false)
  }, [speech, eleven, ws])

  const handleApplyConfig = useCallback((newUrl) => {
    setWsUrl(newUrl)
    clearTimeout(reconnectTimer.current)
    speech.cancelSpeech(); eleven.cancelSpeech()
    if (serverAudioRef.current) { serverAudioRef.current.pause(); serverAudioRef.current = null }
    ws.disconnect()
    setAppState('disconnected')
    // connect con il nuovo URL
    setTimeout(() => {
      ws.connect(newUrl)
      setAppState('idle')
    }, 200)
  }, [speech, eleven, ws])

  // Keep ref current for reconnect timer closure
  connectRef.current = connect

  // ── Auto-connect on mount ──────────────────────────────────────────────────
  useEffect(() => {
    connectRef.current()
  }, []) // eslint-disable-line

  // ── Auto-reconnect on unexpected disconnect ────────────────────────────────
  const isReconnecting = useRef(false)
  useEffect(() => {
    if (ws.status === 'disconnected') {
      setAppState('disconnected')
      clearTimeout(reconnectTimer.current)
      reconnectTimer.current = setTimeout(() => {
        connectRef.current?.()
      }, 10000)
    }
    if (ws.status === 'connected') {
      clearTimeout(reconnectTimer.current)
      ws.sendRaw({ type: 'snapshot_request' })
    }
    return () => clearTimeout(reconnectTimer.current)
  }, [ws.status]) // eslint-disable-line

  // ── Speaker confirm ────────────────────────────────────────────────────────
  const handleConfirmYes = useCallback(() => {
    setIsConfirming(false); setConfirmTranscript('')
    ws.sendRaw({ type: 'confirm_yes' })
  }, [ws])
  const handleConfirmNo = useCallback(() => {
    setIsConfirming(false); setConfirmTranscript('')
    ws.sendRaw({ type: 'confirm_no' })
  }, [ws])

  const handleStartEnroll = useCallback(() => {
    setShowSettings(false)
    ws.sendRaw({ type: 'enroll_request' })
  }, [ws])

  // ── Mute / mic ────────────────────────────────────────────────────────────
  const handleMuteToggle = useCallback(() => {
    if (!isMuted) { setAppState('idle'); setIsMuted(true) }
    else          { setIsMuted(false) }
  }, [isMuted])

  const stopAudio = useCallback(() => {
    speech.cancelSpeech(); eleven.cancelSpeech()
    if (serverAudioRef.current) { serverAudioRef.current.pause(); serverAudioRef.current = null }
    setAppState('idle')
  }, [speech, eleven])

  // ── Text input (debug) ────────────────────────────────────────────────────
  const handleTextSubmit = useCallback(() => {
    const text = textInput.trim()
    if (!text || !isConnected || appState === 'thinking') return
    addMessage('user', text)
    ws.sendMessage(text)
    setTextInput('')
    setAppState('thinking')
  }, [textInput, isConnected, appState, addMessage, ws])

  const handleTextKeyDown = useCallback((e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleTextSubmit() }
  }, [handleTextSubmit])

  useEffect(() => { chatEndRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages, streamText])
  useEffect(() => { localStorage.setItem('el_key',      elKey)     }, [elKey])
  useEffect(() => { localStorage.setItem('el_voice_id', elVoiceId) }, [elVoiceId])

  useEffect(() => {
    const handler = (e) => { e.preventDefault(); setInstallPrompt(e) }
    window.addEventListener('beforeinstallprompt', handler)
    return () => window.removeEventListener('beforeinstallprompt', handler)
  }, [])

  // Intercetta console per log frontend
  useEffect(() => {
    const orig = { log: console.log, warn: console.warn, error: console.error }
    const capture = (level) => (...args) => {
      orig[level](...args)
      const msg = args.map(a => typeof a === 'object' ? JSON.stringify(a) : String(a)).join(' ')
      setFrontendLogs(prev => [...prev.slice(-(MAX_LOGS - 1)), { level, msg, ts: Date.now() / 1000 }])
    }
    console.log   = capture('log')
    console.warn  = capture('warn')
    console.error = capture('error')
    return () => { console.log = orig.log; console.warn = orig.warn; console.error = orig.error }
  }, []) // eslint-disable-line

  useEffect(() => {
    if (showDebug) debugLogEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [frontendLogs, backendLogs, showDebug])

  const handleInstall = useCallback(async () => {
    if (!installPrompt) return
    installPrompt.prompt()
    const { outcome } = await installPrompt.userChoice
    if (outcome === 'accepted') setInstallPrompt(null)
  }, [installPrompt])

  // ── Derived: qdrant viz URL from snapshot ──────────────────────────────────
  // qdrant viz ora integrata su /viz — nessun URL esterno necessario

  // ── Status label ──────────────────────────────────────────────────────────
  const statusLabel = () => {
    if (isEnrolling)    return `ALLENO… ${enrollPct}%`
    if (isConfirming)   return 'CONFERMA VOCE'
    if (speakerPaused)  return 'ASCOLTO IN PAUSA'
    if (isMuted)        return 'MICROFONO SILENZIATO'
    switch (appState) {
      case 'disconnected': return ws.status === 'connecting' ? 'CONNESSIONE…' : 'DISCONNESSO — TENTATIVO IN 10s'
      case 'idle':         return 'STANDBY'
      case 'listening':    return enrollMsg || 'IN ASCOLTO'
      case 'thinking':     return streamTools.length > 0 ? TOOL_LABELS[streamTools[streamTools.length - 1]] || streamTools[streamTools.length - 1] : 'ELABORO…'
      case 'speaking':     return 'RISPONDO'
      default:             return ''
    }
  }

  const statusClass = () => {
    if (appState === 'disconnected') return 'status-off'
    if (appState === 'thinking')     return 'status-think'
    if (appState === 'speaking')     return 'status-speak'
    return 'status-listen'
  }

  // ── Config for panels ─────────────────────────────────────────────────────
  const panelConfig = {
    execModel: snapshot?.model?.name,
    execUrl:   snapshot?.model?.url,
  }

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div className="holo-app">

      {/* ── Disconnected reconnect banner ── */}
      {appState === 'disconnected' && (
        <div className="reconnect-banner">
          {ws.status === 'connecting' ? 'CONNESSIONE IN CORSO…' : 'BACKEND NON RAGGIUNGIBILE — RICONNESSIONE IN 10s'}
        </div>
      )}

      {/* ── Error badge ── */}
      {error && (
        <div className="error-badge" onClick={() => setError(null)} title="Clicca per chiudere">
          ⚠ {error}
        </div>
      )}

      {/* ── Left panel ── */}
      <SidePanel
        side="left"
        snapshot={snapshot}
        wsStatus={ws.status}
        tokenStats={tokenStats}
        config={panelConfig}
      />

      {/* ── Center column ── */}
      <div className="center-col">
        <AiFace state={appState} />

        {/* Status */}
        <div className="status-block">
          <div className={`status-label ${statusClass()}`}>{statusLabel()}</div>

          {/* STT live text */}
          {sttLive && !isConfirming && (
            <div className="stt-live fade-in">«{sttLive}»</div>
          )}

          {/* Streaming text (truncated preview) */}
          {streamText && !showSettings && !showDebug && (
            <div className="stream-preview fade-in">
              {streamText.slice(-120)}
            </div>
          )}

          {/* Speaker verdict */}
          {speakerVerdict && (
            <div className={`verdict verdict-${speakerVerdict.verdict}`}>
              {speakerVerdict.verdict === 'match'        && `✓ Voce riconosciuta (${Math.round(speakerVerdict.score * 100)}%)`}
              {speakerVerdict.verdict === 'uncertain'    && `? Incerta (${Math.round(speakerVerdict.score * 100)}%)`}
              {speakerVerdict.verdict === 'no_match'     && `✕ Non riconosciuta (${Math.round(speakerVerdict.score * 100)}%)`}
              {speakerVerdict.verdict === 'no_voiceprint'&& '· Nessun voice print'}
              {speakerVerdict.verdict === 'too_short'    && '· Segmento troppo breve'}
            </div>
          )}

          {/* Enrollment bar */}
          {isEnrolling && (
            <div className="enroll-bar-wrap">
              <div className="enroll-bar">
                <div className="enroll-fill" style={{ width: `${enrollPct}%` }} />
              </div>
              <span className="enroll-pct">{enrollPct}%</span>
            </div>
          )}
        </div>

        {/* Confirm speaker (inline) */}
        {isConfirming && (
          <div className="confirm-inline">
            <p>Ho sentito: <em>«{confirmTranscript}»</em></p>
            <p>Stai parlando con me?</p>
            <div className="confirm-btns">
              <button className="confirm-yes" onClick={handleConfirmYes}>Sì</button>
              <button className="confirm-no"  onClick={handleConfirmNo}>No</button>
            </div>
          </div>
        )}
      </div>

      {/* ── Right panel ── */}
      <SidePanel
        side="right"
        snapshot={snapshot}
        wsStatus={ws.status}
        tokenStats={null}
        config={panelConfig}
      />

      {/* ── Qdrant viz panel ── */}
      <QdrantPanel />

      {/* ── Bottom-left controls ── */}
      <div className="bottom-left-btns">
        <button className="debug-toggle" onClick={() => window.location.reload()} title="Ricarica applicazione">
          ↺ reload
        </button>
        <button className="debug-toggle" onClick={() => { setShowChat(c => !c); setShowSettings(false); setShowDebug(false); setShowConfig(false) }} title="Chat testuale">
          {showChat ? '✕ chat' : '💬 chat'}
        </button>
        <button className="debug-toggle" onClick={() => { setShowConfig(c => !c); setShowSettings(false); setShowDebug(false); setShowChat(false) }} title="Configurazione endpoint">
          {showConfig ? '✕ config' : '⚙ config'}
        </button>
        <button className="debug-toggle" onClick={() => { setShowSettings(s => !s); setShowDebug(false); setShowChat(false); setShowConfig(false) }} title="Impostazioni">
          {showSettings ? '✕ settings' : '⚙ settings'}
        </button>
        <button className="debug-toggle" onClick={() => { setShowDebug(d => !d); setShowSettings(false); setShowChat(false); setShowConfig(false); setShowCommands(false) }} title="Log di runtime">
          {showDebug ? '✕ debug' : '🛠 debug'}
        </button>
        <button className="debug-toggle" onClick={() => { setShowCommands(c => !c); setShowSettings(false); setShowDebug(false); setShowChat(false); setShowConfig(false) }} title="Comandi vocali">
          {showCommands ? '✕ comandi' : '🎙 comandi'}
        </button>
        {installPrompt ? (
          <button className="debug-toggle debug-toggle-install" onClick={handleInstall} title="Installa come app PWA">
            ⬇ installa
          </button>
        ) : window.location.protocol !== 'https:' && (
          <a
            className="debug-toggle debug-toggle-setup"
            href={`http://${window.location.hostname}:8081`}
            target="_blank"
            rel="noopener noreferrer"
            title="Prima configurazione: installa il certificato per abilitare PWA"
          >
            🔒 setup
          </a>
        )}
      </div>

      {/* ── Chat panel ── */}
      {showChat && (
        <div className={`chat-panel${chatMaximized ? ' chat-panel-maximized' : ''}`}>
          <div className="chat-panel-hdr">
            <span className="chat-panel-title">CHAT</span>
            <div style={{ display: 'flex', gap: '6px' }}>
              <button className="debug-close" onClick={() => setChatMaximized(m => !m)} title={chatMaximized ? 'Riduci' : 'Massimizza'}>
                {chatMaximized ? '⊡' : '⊞'}
              </button>
              <button className="debug-close" onClick={() => { setShowChat(false); setChatMaximized(false) }}>✕</button>
            </div>
          </div>

          <div className="chat-panel-messages">
            {messages.length === 0 && (
              <div className="chat-panel-empty">Nessun messaggio — parla o scrivi qualcosa</div>
            )}
            {messages.map(msg => (
              <div key={msg.id} className={`chat-bubble chat-bubble-${msg.role}`}>
                {msg.tools?.length > 0 && (
                  <div className="chat-tools">
                    {msg.tools.map(t => <span key={t} className="dbg-tool">[{TOOL_LABELS[t] || t}]</span>)}
                  </div>
                )}
                <span>{msg.text}</span>
              </div>
            ))}
            {(streamText || streamTools.length > 0) && (
              <div className="chat-bubble chat-bubble-assistant chat-bubble-streaming">
                {streamTools.map(t => <span key={t} className="dbg-tool">[{TOOL_LABELS[t] || t}]</span>)}
                <span>{streamText}</span>
              </div>
            )}
            <div ref={chatEndRef} />
          </div>

          <div className="chat-panel-input">
            <textarea
              className="chat-textarea"
              placeholder="Scrivi un messaggio… (Invio per inviare, Shift+Invio a capo)"
              value={textInput}
              onChange={e => setTextInput(e.target.value)}
              onKeyDown={handleTextKeyDown}
              rows={2}
              disabled={!isConnected || appState === 'thinking'}
            />
            <button
              className="chat-send-btn"
              onClick={handleTextSubmit}
              disabled={!textInput.trim() || !isConnected || appState === 'thinking'}
              title="Invia"
            >▶</button>
          </div>
        </div>
      )}

      {/* ── Config panel ── */}
      {showConfig && (
        <ConfigPanel
          wsUrl={wsUrl}
          onApply={handleApplyConfig}
          onClose={() => setShowConfig(false)}
          isConnected={isConnected}
        />
      )}

      {/* ── Settings overlay ── */}
      {showSettings && (
        <div className="debug-overlay">
          <div className="debug-hdr">
            <span className="debug-title">IMPOSTAZIONI</span>
            <button className="debug-close" onClick={() => setShowSettings(false)}>✕</button>
          </div>

          <div className="debug-body">
            {/* Connection */}
            <section className="debug-section">
              <div className="debug-section-title">CONNESSIONE</div>
              <label className="debug-label">
                WebSocket URL
                <input className="debug-input" type="text" value={wsUrl}
                  onChange={e => setWsUrl(e.target.value)} />
              </label>
              <div className="debug-actions">
                {isConnected
                  ? <button className="debug-btn" onClick={disconnect}>Disconnetti</button>
                  : <button className="debug-btn debug-btn-primary" onClick={connect}>Connetti</button>
                }
                {isConnected && appState === 'speaking' && (
                  <button className="debug-btn" onClick={stopAudio}>Interrompi audio</button>
                )}
              </div>
              <div className="debug-info">
                Stato WS: <strong>{ws.status}</strong> — App: <strong>{appState}</strong>
              </div>
            </section>

            {/* Mic */}
            <section className="debug-section">
              <div className="debug-section-title">MICROFONO</div>
              <div className="debug-actions">
                <button className={`debug-btn ${isMuted ? 'debug-btn-active' : ''}`} onClick={handleMuteToggle}>
                  {isMuted ? '🔇 Riattiva' : '🎤 Silenzia'}
                </button>
              </div>
              {!audioStream.isCapturing && isConnected && !isMuted && (
                <div className="debug-warn">
                  {audioStream.error ?? 'AudioWorklet non supportato — usa Chrome/Edge'}
                </div>
              )}
            </section>

            {/* Speaker recognition */}
            <section className="debug-section">
              <div className="debug-section-title">RICONOSCIMENTO VOCALE</div>
              {isConnected
                ? <button className="debug-btn" onClick={handleStartEnroll} disabled={isEnrolling}>
                    {isEnrolling ? `Alleno… ${enrollPct}%` : enrollNeeded ? 'Registra voce' : 'Aggiorna riconoscimento'}
                  </button>
                : <span className="debug-hint">Connettiti per gestire il riconoscimento vocale.</span>
              }
            </section>

            {/* TTS */}
            <section className="debug-section">
              <div className="debug-section-title">TTS</div>
              <label className="debug-checkbox">
                <input type="checkbox" checked={ttsEnabled} onChange={e => setTtsEnabled(e.target.checked)} />
                Risposta vocale attiva
              </label>
              <label className="debug-label">
                Lingua
                <select className="debug-input" value={lang} onChange={e => setLang(e.target.value)}>
                  <option value="it-IT">Italiano</option>
                  <option value="en-US">English (US)</option>
                  <option value="en-GB">English (UK)</option>
                </select>
              </label>
            </section>

            {/* ElevenLabs */}
            <section className="debug-section">
              <div className="debug-section-title">ELEVENLABS (opzionale)</div>
              <label className="debug-label">
                API Key
                <input className="debug-input" type="password" value={elKey}
                  onChange={e => setElKey(e.target.value)} placeholder="sk-… lascia vuoto per TTS browser" />
              </label>
              <label className="debug-label">
                Voice ID
                <input className="debug-input" type="text" value={elVoiceId}
                  onChange={e => setElVoiceId(e.target.value)} />
              </label>
            </section>
          </div>
        </div>
      )}

      {/* ── Commands overlay ── */}
      {showCommands && (
        <div className="debug-overlay">
          <div className="debug-hdr">
            <span className="debug-title">COMANDI VOCALI</span>
            <button className="debug-close" onClick={() => setShowCommands(false)}>✕</button>
          </div>
          <div className="debug-body">
            {VOICE_COMMANDS.map(section => (
              <section className="debug-section" key={section.title}>
                <div className="debug-section-title">{section.title}</div>
                <div className="cmd-list">
                  {section.commands.map(cmd => (
                    <div className="cmd-item" key={cmd.phrase}>
                      <span className="cmd-phrase">«{cmd.phrase}»</span>
                      {cmd.note && <span className="cmd-note">{cmd.note}</span>}
                    </div>
                  ))}
                </div>
              </section>
            ))}
          </div>
        </div>
      )}

      {/* ── Debug overlay ── */}
      {showDebug && (
        <div className="debug-overlay">
          <div className="debug-hdr">
            <span className="debug-title">DEBUG</span>
            <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
              <button
                className={`log-tab ${debugTab === 'backend' ? 'log-tab-active' : ''}`}
                onClick={() => setDebugTab('backend')}
              >backend</button>
              <button
                className={`log-tab ${debugTab === 'frontend' ? 'log-tab-active' : ''}`}
                onClick={() => setDebugTab('frontend')}
              >frontend</button>
              <button className="debug-btn" style={{ padding: '3px 10px', fontSize: '10px' }}
                onClick={() => { setBackendLogs([]); setFrontendLogs([]) }}>
                pulisci
              </button>
              <button className="debug-close" onClick={() => setShowDebug(false)}>✕</button>
            </div>
          </div>

          <div className="log-list">
            {(debugTab === 'backend' ? backendLogs : frontendLogs).map((entry, i) => (
              <div key={i} className={`log-entry log-entry-${entry.level}`}>
                <span className="log-ts">{new Date(entry.ts * 1000).toLocaleTimeString('it-IT', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })}</span>
                <span className="log-msg">{entry.msg}</span>
              </div>
            ))}
            {(debugTab === 'backend' ? backendLogs : frontendLogs).length === 0 && (
              <div className="log-empty">Nessun log — i messaggi appariranno qui in tempo reale</div>
            )}
            <div ref={debugLogEndRef} />
          </div>
        </div>
      )}
    </div>
  )
}

const VOICE_COMMANDS = [
  {
    title: 'WAKE WORD — avvia conversazione',
    commands: [
      { phrase: 'Daniela …', note: 'pronuncia il suo nome per iniziare a parlarle' },
    ],
  },
  {
    title: 'PAUSA / RIPRESA',
    commands: [
      { phrase: 'Daniela non ascoltare', note: 'sospende il microfono' },
      { phrase: 'Daniela non ascoltare ora' },
      { phrase: 'Daniela parla con me', note: 'riprende l\'ascolto' },
      { phrase: 'Daniela riprendi ad ascoltare' },
    ],
  },
  {
    title: 'ALLENAMENTO VOCE',
    commands: [
      { phrase: 'Daniela allena il riconoscimento vocale', note: 'avvia enrollment 40s' },
      { phrase: 'Daniela allena riconoscimento vocale' },
      { phrase: 'Daniela aggiorna il riconoscimento vocale', note: 'aggiorna voice print' },
    ],
  },
  {
    title: 'CONFERMA IDENTITÀ (quando chiede "sei tu?")',
    commands: [
      { phrase: 'Sì sono io' },
      { phrase: 'Sono io' },
      { phrase: 'Sto parlando con te' },
      { phrase: 'Confermo' },
      { phrase: 'No non sono io' },
      { phrase: 'Non stavo parlando con te' },
    ],
  },
  {
    title: 'CHIUSURA CONVERSAZIONE',
    commands: [
      { phrase: 'Ok grazie', note: 'torna in standby' },
      { phrase: 'Grazie Daniela' },
      { phrase: 'Ho capito grazie' },
      { phrase: 'Va bene' },
      { phrase: 'Ciao Daniela' },
      { phrase: 'A dopo Daniela' },
    ],
  },
]

const TOOL_LABELS = {
  web_search:             'RICERCA WEB',
  web_fetch:              'FETCH PAGINA',
  execute_command:        'ESEGUO COMANDO',
  write_file:             'SCRIVO FILE',
  read_file:              'LEGGO FILE',
  plan_project:           'PIANIFICAZIONE',
  delegate_file_creation: 'GENERO FILE',
  run_tests:              'TEST',
  install_packages:       'INSTALLAZIONE',
  create_module:          'NUOVO MODULO',
}
