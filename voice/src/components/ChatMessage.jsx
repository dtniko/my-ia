export function ChatMessage({ role, text, tools = [] }) {
  if (role === 'notification') {
    return (
      <div className="message message--notification">
        <div className="message__avatar">◆</div>
        <div className="message__body">
          <p className="message__text">{text}</p>
        </div>
      </div>
    )
  }
  const isUser = role === 'user'
  return (
    <div className={`message message--${role}`}>
      <div className="message__avatar">{isUser ? '👤' : '🤖'}</div>
      <div className="message__body">
        {tools.length > 0 && (
          <div className="message__tools">
            {tools.map((t, i) => <span key={i} className="tool-badge">{toolLabel(t)}</span>)}
          </div>
        )}
        <p className="message__text">{text}</p>
      </div>
    </div>
  )
}

export function StreamingMessage({ text, tools = [] }) {
  return (
    <div className="message message--assistant message--streaming">
      <div className="message__avatar">🤖</div>
      <div className="message__body">
        {tools.length > 0 && (
          <div className="message__tools">
            {tools.map((t, i) => <span key={i} className="tool-badge">{toolLabel(t)}</span>)}
          </div>
        )}
        <p className="message__text">
          {text}<span className="cursor-blink">▋</span>
        </p>
      </div>
    </div>
  )
}

const TOOL_LABELS = {
  web_search:               '🔍 Ricerca web',
  web_fetch:                '🌐 Fetch pagina',
  execute_command:          '⚙️ Comando',
  write_file:               '📝 Scrittura file',
  read_file:                '📖 Lettura file',
  list_directory:           '📁 Directory',
  create_directory:         '📂 Crea cartella',
  install_packages:         '📦 Installazione',
  plan_project:             '🗺️ Pianificazione',
  delegate_file_creation:   '🔨 Generazione file',
  run_tests:                '🧪 Test',
  glob_search:              '🔎 Ricerca file',
  grep_search:              '🔎 Ricerca testo',
  create_module:            '🔧 Nuovo modulo',
}

function toolLabel(name) {
  return TOOL_LABELS[name] ?? `🔧 ${name}`
}
