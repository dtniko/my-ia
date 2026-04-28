/** Left/right info panels showing model, agent and connection status */
export function SidePanel({ side = 'left', snapshot, wsStatus, tokenStats, config = {} }) {
  const isLeft = side === 'left'

  if (isLeft) {
    return (
      <aside className="side-panel side-panel-left">
        <InfoCard title="PLANNING MODEL">
          <InfoRow label="Host"   value={snapshot?.planning_model?.url?.replace(/^https?:\/\//, '') || config.thinkingUrl || '192.168.250.203:11434'} />
          <InfoRow label="Model"  value={snapshot?.planning_model?.name || 'gemma4:26b-a4b-it-q4_K_M'} />
          <InfoRow label="Status" value={wsStatus === 'connected' ? 'online' : 'offline'}
            valueClass={wsStatus === 'connected' ? 'ok' : 'err'} />
        </InfoCard>

        <InfoCard title="EXECUTION MODEL">
          <InfoRow label="Host"   value={config.execUrl || '10.149.245.212:8807'} />
          <InfoRow label="Model"  value={snapshot?.model?.name || config.execModel || 'qwen3-instruct'} />
          <InfoRow label="CTX"    value={snapshot?.model?.context_window
            ? `${(snapshot.model.context_window / 1024).toFixed(0)}k tokens`
            : '—'} />
        </InfoCard>

        {tokenStats && (
          <InfoCard title="CONTEXT">
            <div className="token-track">
              <div className="token-fill" style={{ width: `${Math.min(tokenStats.pct, 100)}%`,
                background: tokenStats.pct > 80 ? 'var(--yellow)' : undefined }} />
            </div>
            <div className="token-label">
              {tokenStats.compacting
                ? 'Compattando…'
                : `${(tokenStats.tokens / 1000).toFixed(1)}k / ${(tokenStats.max / 1000).toFixed(0)}k (${tokenStats.pct}%)`}
            </div>
          </InfoCard>
        )}
      </aside>
    )
  }

  // Right panel: agents + modules + links
  const agents  = snapshot?.agents?.names  || []
  const modules = snapshot?.modules?.names || []
  const links   = snapshot?.links          || []

  return (
    <aside className="side-panel side-panel-right">
      <InfoCard title="AGENTS">
        {agents.length === 0
          ? <span className="info-empty">—</span>
          : <div className="pill-list">
              {agents.map(a => <span key={a} className="pill">{a}</span>)}
            </div>
        }
      </InfoCard>

      {modules.length > 0 && (
        <InfoCard title="MODULES">
          <div className="pill-list">
            {modules.map(m => <span key={m} className="pill pill-module">{m}</span>)}
          </div>
        </InfoCard>
      )}

      <InfoCard title="ENDPOINTS">
        {links.map(l => (
          <InfoRow key={l.label} label={l.label}
            value={l.url.replace(/^https?:\/\//, '').replace(/^ws:\/\//, 'ws://')} />
        ))}
        {links.length === 0 && <span className="info-empty">—</span>}
      </InfoCard>
    </aside>
  )
}

function InfoCard({ title, children }) {
  return (
    <div className="info-card">
      <div className="info-card-title">{title}</div>
      {children}
    </div>
  )
}

function InfoRow({ label, value, valueClass = '' }) {
  return (
    <div className="info-row">
      <span className="info-label">{label}</span>
      <span className={`info-value ${valueClass}`}>{value || '—'}</span>
    </div>
  )
}
