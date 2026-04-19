export function MicButton({ state, onClick, disabled, muted }) {
  const isActive   = state === 'listening' && !muted
  const isThinking = state === 'thinking'
  const isSpeaking = state === 'speaking'

  return (
    <div className="mic-wrapper">
      {isActive && (
        <>
          <div className="pulse-ring ring-1" />
          <div className="pulse-ring ring-2" />
          <div className="pulse-ring ring-3" />
        </>
      )}
      {isSpeaking && (
        <>
          <div className="speak-ring ring-1" />
          <div className="speak-ring ring-2" />
        </>
      )}
      <button
        className={`mic-btn mic-btn--${muted ? 'muted' : state}`}
        onClick={onClick}
        disabled={disabled}
        aria-label={muted ? 'Riattiva microfono' : isActive ? 'Silenzia microfono' : 'Microfono'}
      >
        {isThinking ? <span className="spinner" /> : <MicIcon listening={isActive} speaking={isSpeaking} muted={muted} />}
      </button>
    </div>
  )
}

function MicIcon({ listening, speaking, muted }) {
  if (muted) return (
    <svg viewBox="0 0 24 24" fill="currentColor" width="36" height="36">
      <path d="M19 11h-1.7c0 .74-.16 1.43-.43 2.05l1.23 1.23c.56-.98.9-2.09.9-3.28zm-4.02.17c0-.06.02-.11.02-.17V5c0-1.66-1.34-3-3-3S9 3.34 9 5v.18l5.98 5.99zM4.27 3L3 4.27l6.01 6.01V11c0 1.66 1.33 3 2.99 3 .22 0 .44-.03.65-.08l1.66 1.66c-.71.33-1.5.52-2.31.52-2.76 0-5.3-2.1-5.3-5.1H5c0 3.41 2.72 6.23 6 6.72V21h2v-3.28c.91-.13 1.77-.45 2.54-.9L19.73 21 21 19.73 4.27 3z"/>
    </svg>
  )
  if (speaking) return (
    <svg viewBox="0 0 24 24" fill="currentColor" width="36" height="36">
      <path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02z"/>
      <path d="M14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z"/>
    </svg>
  )
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" width="36" height="36">
      <path d="M12 14c1.66 0 2.99-1.34 2.99-3L15 5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3zm5.3-3c0 3-2.54 5.1-5.3 5.1S6.7 14 6.7 11H5c0 3.41 2.72 6.23 6 6.72V21h2v-3.28c3.28-.48 6-3.3 6-6.72h-1.7z"/>
    </svg>
  )
}
