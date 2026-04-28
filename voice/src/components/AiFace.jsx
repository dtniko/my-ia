export function AiFace({ state = 'idle' }) {
  const faceClass = [
    'face-wrap',
    `face-${state}`,
  ].join(' ')

  return (
    <div className={faceClass}>
      {/* Orbital rings */}
      <div className="orbit orbit-1" />
      <div className="orbit orbit-2" />

      {/* Image with holographic overlay */}
      <div className="face-img-wrap">
        <img
          src="/ai-face-2.png"
          alt="AI"
          className="face-img"
          draggable={false}
        />
        {/* Scan line */}
        <div className="face-scanline" />
        {/* Color/glow overlay */}
        <div className="face-overlay" />
        {/* Grid overlay */}
        <svg className="face-grid" viewBox="0 0 300 380" preserveAspectRatio="xMidYMid slice">
          <defs>
            <clipPath id="imgClip">
              <ellipse cx="150" cy="185" rx="148" ry="188" />
            </clipPath>
          </defs>
          <g clipPath="url(#imgClip)" opacity="0.12">
            {[50,80,110,140,170,200,230,260,290,320,350].map(y => (
              <line key={y} x1="0" y1={y} x2="300" y2={y}
                stroke="currentColor" strokeWidth="0.8" />
            ))}
            {[40,80,120,160,200,240,280].map(x => (
              <line key={x} x1={x} y1="0" x2={x} y2="380"
                stroke="currentColor" strokeWidth="0.8" />
            ))}
          </g>
          {/* Outline glow */}
          <ellipse cx="150" cy="185" rx="148" ry="188"
            stroke="currentColor" strokeWidth="1.5" fill="none"
            opacity="0.5" />
        </svg>
      </div>
    </div>
  )
}
