import { useEffect, useRef } from 'react'
import styles from './JamieAvatar.module.css'

// We use Vite's absolute filesystem path capability for the generated images
const JAMIE_IMG  = '/@fs/Users/virajdalsania/.gemini/antigravity/brain/339fa39d-4f20-42b4-88bb-cbe48ea78027/jamie_avatar_3d_1777173719753.png'
const CALLER_IMG = '/@fs/Users/virajdalsania/.gemini/antigravity/brain/339fa39d-4f20-42b4-88bb-cbe48ea78027/caller_avatar_3d_1777173801667.png'

export default function JamieAvatar({ speaking, callerSpeaking, mode }) {
  const canvasRef = useRef(null)
  const animRef   = useRef(null)
  const bars      = useRef(Array(14).fill(2))

  /* Center Waveform Connection */
  useEffect(() => {
    const cvs = canvasRef.current
    if (!cvs) return
    const ctx = cvs.getContext('2d')
    cvs.width = 100; cvs.height = 40

    const draw = () => {
      ctx.clearRect(0, 0, 100, 40)
      const active = speaking || callerSpeaking
      const hue    = speaking ? '262' : callerSpeaking ? '199' : '215'

      bars.current = bars.current.map((h, i) => {
        // Create a wave that leans towards the active speaker
        let target = 2
        if (active) {
          const intensity = speaking ? (i / 14) : (1 - i / 14)
          target = 4 + (Math.random() * 20 * intensity) + (Math.random() * 10)
        }
        return h + (target - h) * 0.25
      })

      const bw = 4, gap = 3
      const total = bars.current.length * (bw + gap) - gap
      let x = (100 - total) / 2
      bars.current.forEach(h => {
        const y = (40 - h) / 2
        const grad = ctx.createLinearGradient(0, y, 0, y + h)
        grad.addColorStop(0, `hsla(${hue},90%,65%,${active ? 0.9 : 0.3})`)
        grad.addColorStop(1, `hsla(${hue},80%,45%,${active ? 0.7 : 0.1})`)
        ctx.beginPath()
        ctx.roundRect(x, y, bw, h, 2)
        ctx.fillStyle = grad
        ctx.fill()
        x += bw + gap
      })
      animRef.current = requestAnimationFrame(draw)
    }
    draw()
    return () => cancelAnimationFrame(animRef.current)
  }, [speaking, callerSpeaking])

  const modeColor = { calm:'#10b981', distressed:'#ef4444', noisy:'#f59e0b' }[mode] || '#10b981'

  return (
    <div className={styles.wrap}>
      
      {/* ── Interactive Avatars ── */}
      <div className={styles.stage}>
        
        {/* Caller Avatar */}
        <div className={styles.persona}>
          <div className={`${styles.avatarFrame} ${callerSpeaking ? styles.speakingFrameCaller : ''}`}>
            {callerSpeaking && <div className={styles.ringCaller} />}
            <img src={CALLER_IMG} alt="Caller" className={styles.avatarImg} />
          </div>
          <div className={styles.personaName}>Max Müller</div>
          <div className={styles.personaRole}>Caller</div>
        </div>

        {/* Center Waveform */}
        <div className={styles.bridge}>
          <div className={styles.speakLabel} style={{ opacity: speaking ? 1 : 0, color: '#a78bfa' }}>◀ JAMIE</div>
          <canvas ref={canvasRef} className={styles.wave} />
          <div className={styles.speakLabel} style={{ opacity: callerSpeaking ? 1 : 0, color: '#38bdf8' }}>CALLER ▶</div>
        </div>

        {/* Jamie Avatar */}
        <div className={styles.persona}>
          <div className={`${styles.avatarFrame} ${speaking ? styles.speakingFrameJamie : ''}`}>
            {speaking && <div className={styles.ringJamie} />}
            <img src={JAMIE_IMG} alt="Jamie" className={styles.avatarImg} />
            <div className={styles.statusDot} style={{ background: modeColor, boxShadow: `0 0 8px ${modeColor}` }} />
          </div>
          <div className={styles.personaName}>Jamie Hofmann</div>
          <div className={styles.personaRole}>AI Agent</div>
        </div>

      </div>

      {/* Mode Badge */}
      <div className={styles.modePill} style={{ '--mc': modeColor, '--mc2': modeColor+'15' }}>
        <span className={styles.modeDot} />
        EMOTIONAL STATE: {mode.toUpperCase()}
      </div>

    </div>
  )
}
