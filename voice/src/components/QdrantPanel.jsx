import { useState } from 'react'

export function QdrantPanel() {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className={`qdrant-panel ${expanded ? 'qdrant-expanded' : 'qdrant-collapsed'}`}>
      <div className="qdrant-widget">
        <div className="qdrant-header" onClick={() => setExpanded(e => !e)}>
          <div className="qdrant-title">
            <span className="qdrant-dot" />
            QDRANT VIZ
          </div>
          <button
            className="qdrant-btn"
            onClick={e => { e.stopPropagation(); setExpanded(e2 => !e2) }}
            title={expanded ? 'Riduci' : 'Espandi'}
          >
            {expanded ? '⊡' : '⊞'}
          </button>
        </div>

        {expanded ? (
          <div className="qdrant-iframe-wrap">
            <iframe
              src="/viz"
              title="Qdrant Viz"
              allow="*"
              sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
            />
          </div>
        ) : (
          <div className="qdrant-mini">
            <span className="qdrant-mini-url">/viz</span>
            <span className="qdrant-mini-hint">Clicca per esplorare la memoria</span>
          </div>
        )}
      </div>
    </div>
  )
}
