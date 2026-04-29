import { useState } from 'react'

export function ConfigPanel({ wsUrl, onApply, onClose, isConnected }) {
  const [draft, setDraft] = useState(wsUrl)

  const handleApply = () => {
    const url = draft.trim()
    if (!url) return
    localStorage.setItem('ws_url', url)
    onApply(url)
    onClose()
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter') handleApply()
    if (e.key === 'Escape') onClose()
  }

  return (
    <div className="config-overlay">
      <div className="config-panel">
        <div className="debug-hdr">
          <span className="debug-title">CONFIGURAZIONE</span>
          <button className="debug-close" onClick={onClose}>✕</button>
        </div>

        <div className="debug-body">
          <section className="debug-section">
            <div className="debug-section-title">ENDPOINT WEBSOCKET</div>
            <label className="debug-label">
              URL
              <input
                className="debug-input"
                type="text"
                value={draft}
                onChange={e => setDraft(e.target.value)}
                onKeyDown={handleKeyDown}
                autoFocus
                spellCheck={false}
              />
            </label>
            <div className="debug-hint">
              Formato: <code>ws://&lt;ip&gt;:8765</code>
            </div>
            <div className="debug-actions">
              <button
                className="debug-btn debug-btn-primary"
                onClick={handleApply}
                disabled={!draft.trim() || draft.trim() === wsUrl}
              >
                Salva e riconnetti
              </button>
              <button className="debug-btn" onClick={onClose}>
                Annulla
              </button>
            </div>
            <div className="debug-info">
              Endpoint attivo: <strong>{wsUrl}</strong>
              {' — '}
              WS: <strong>{isConnected ? 'connesso' : 'disconnesso'}</strong>
            </div>
          </section>
        </div>
      </div>
    </div>
  )
}
