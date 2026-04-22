import { useMemo } from 'react'

function fmtTime(ts) {
  if (!ts) return '—'
  const d = new Date(ts * 1000)
  return d.toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function fmtInterval(sec) {
  if (!sec || sec < 60) return `${sec}s`
  if (sec < 3600)       return `${Math.round(sec / 60)}m`
  return `${(sec / 3600).toFixed(1)}h`
}

export function Dashboard({ open, onClose, snapshot, jobs, jobLogs }) {
  const model   = snapshot?.model
  const agents  = snapshot?.agents  ?? { count: 0, names: [] }
  const modules = snapshot?.modules ?? { count: 0, names: [] }
  const links   = snapshot?.links   ?? []

  // Unisce snapshot.jobs (iniziale) con jobs da jobs_update (fresco)
  const jobList = jobs ?? snapshot?.jobs ?? []
  const logList = jobLogs ?? snapshot?.job_logs ?? []

  const logsByJob = useMemo(() => {
    const map = new Map()
    for (const l of logList) {
      const id = l.job_id ?? '_'
      if (!map.has(id)) map.set(id, [])
      map.get(id).push(l)
    }
    return map
  }, [logList])

  return (
    <aside className={`dashboard-drawer${open ? ' dashboard-drawer--open' : ''}`}>
      <div className="dashboard-head">
        <h3>Dashboard</h3>
        <button className="btn-icon" onClick={onClose} title="Chiudi">✕</button>
      </div>

      <div className="dashboard-body">
        <section className="dash-section">
          <div className="dash-section-title">Modello</div>
          {model ? (
            <div className="dash-kv">
              <div><span className="dash-k">Nome</span><span className="dash-v">{model.name}</span></div>
              <div><span className="dash-k">Context</span><span className="dash-v">{(model.context_window/1024).toFixed(0)}k tok</span></div>
              <div><span className="dash-k">URL</span><span className="dash-v dash-v--mono">{model.url}</span></div>
            </div>
          ) : <p className="dash-muted">—</p>}
        </section>

        <section className="dash-section">
          <div className="dash-section-title">Agenti <span className="dash-count">{agents.count}</span></div>
          <ul className="dash-list">
            {agents.names.map(n => <li key={n}>{n}</li>)}
          </ul>
        </section>

        <section className="dash-section">
          <div className="dash-section-title">Link locali</div>
          <ul className="dash-links">
            {links.map(l => (
              <li key={l.url}>
                <a href={l.url.startsWith('ws://') ? '#' : l.url} target="_blank" rel="noreferrer">
                  {l.label}
                </a>
                <span className="dash-v--mono">{l.url}</span>
              </li>
            ))}
          </ul>
        </section>

        <section className="dash-section">
          <div className="dash-section-title">Moduli dinamici <span className="dash-count">{modules.count}</span></div>
          {modules.count === 0 ? (
            <p className="dash-muted">Nessun modulo creato</p>
          ) : (
            <ul className="dash-list">{modules.names.map(m => <li key={m}><code>{m}</code></li>)}</ul>
          )}
        </section>

        <section className="dash-section">
          <div className="dash-section-title">Job ciclici <span className="dash-count">{jobList.length}</span></div>
          {jobList.length === 0 ? (
            <p className="dash-muted">Nessun job schedulato</p>
          ) : (
            <ul className="dash-jobs">
              {jobList.map(j => {
                const logs = logsByJob.get(j.id) ?? []
                const last = logs[logs.length - 1]
                return (
                  <li key={j.id} className={`job-row job-row--${j.status}`}>
                    <div className="job-row-head">
                      <span className="job-desc">{j.description || j.type}</span>
                      <span className={`job-status job-status--${j.status}`}>{j.status}</span>
                    </div>
                    <div className="job-row-meta">
                      <span title="Creato">🕑 {fmtTime(j.created_at)}</span>
                      <span title="Intervallo">⟳ {fmtInterval(j.interval_seconds)}</span>
                      <span title="Esecuzioni">× {j.run_count}</span>
                    </div>
                    {last && (
                      <div className="job-row-log">
                        <span className="job-log-time">{fmtTime(last.produced_at)}</span>
                        <span className="job-log-text">{last.description || last.content || ''}</span>
                      </div>
                    )}
                  </li>
                )
              })}
            </ul>
          )}
        </section>

        <section className="dash-section">
          <div className="dash-section-title">Log recenti <span className="dash-count">{logList.length}</span></div>
          {logList.length === 0 ? (
            <p className="dash-muted">Nessun output ancora</p>
          ) : (
            <ul className="dash-logs">
              {logList.slice().reverse().map((l, i) => (
                <li key={i}>
                  <span className="job-log-time">{fmtTime(l.produced_at)}</span>
                  <span className="job-log-text">
                    {l.description && <strong>{l.description} · </strong>}
                    {l.content}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </aside>
  )
}
