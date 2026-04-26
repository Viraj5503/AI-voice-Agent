import { useEffect, useRef } from 'react'
import styles from './JamieAvatar.module.css'

const JAMIE_IMG  = '/@fs/Users/virajdalsania/.gemini/antigravity/brain/339fa39d-4f20-42b4-88bb-cbe48ea78027/jamie_avatar_3d_1777173719753.png'
const CALLER_IMG = '/@fs/Users/virajdalsania/.gemini/antigravity/brain/339fa39d-4f20-42b4-88bb-cbe48ea78027/caller_avatar_3d_1777173801667.png'

export default function JamieAvatar({ speaking, callerSpeaking, mode, callerName }) {
  const canvasRef = useRef(null)
  const animRef   = useRef(null)
  const bars      = useRef(Array(20).fill(2))

  useEffect(() => {
    const cvs = canvasRef.current
    if (!cvs) return
    const ctx = cvs.getContext('2d')
    cvs.width = 160; cvs.height = 48

    const draw = () => {
      ctx.clearRect(0, 0, 160, 48)
      const active = speaking || callerSpeaking
      const hue    = speaking ? '262' : callerSpeaking ? '199' : '220'

      bars.current = bars.current.map((h, i) => {
        let target = 2
        if (active) {
          const mid = bars.current.length / 2
          const dist = Math.abs(i - mid) / mid
          const bias = speaking ? (1 - dist * 0.5) : (dist * 0.5 + 0.3)
          target = 4 + Math.random() * 30 * bias + Math.random() * 8
        }
        return h + (target - h) * 0.22
      })

      const bw = 4, gap = 3.5
      const total = bars.current.length * (bw + gap) - gap
      let x = (160 - total) / 2
      bars.current.forEach((h, i) => {
        const y = (48 - h) / 2
        const grad = ctx.createLinearGradient(0, y, 0, y + h)
        grad.addColorStop(0, `hsla(${hue},90%,70%,${active ? 0.95 : 0.25})`)
        grad.addColorStop(1, `hsla(${hue},80%,45%,${active ? 0.6 : 0.08})`)
        ctx.beginPath()
        ctx.roundRect(x, y, bw, Math.max(h, 2), 2)
        ctx.fillStyle = grad
        ctx.fill()
        x += bw + gap
      })
      animRef.current = requestAnimationFrame(draw)
    }
    draw()
    return () => cancelAnimationFrame(animRef.current)
  }, [speaking, callerSpeaking])

  const modeColor   = { calm:'#10b981', distressed:'#ef4444', noisy:'#f59e0b' }[mode] || '#10b981'
  const modeLabel   = { calm:'Calm', distressed:'Distressed', noisy:'Noisy' }[mode] || mode
  const modeEmoji   = { calm:'😌', distressed:'😰', noisy:'🔊' }[mode] || '😌'

  return (
    <div className={styles.stage}>

      {/* ── Caller ── */}
      <div className={styles.avatarCol}>
        <div className={`${styles.avatarWrap} ${callerSpeaking ? styles.activeSpeaker : ''}`}
             style={callerSpeaking ? { '--ring': '#0ea5e9' } : {}}>
          {callerSpeaking && <>
            <span className={styles.ring1} style={{ borderColor:'rgba(14,165,233,.6)' }}/>
            <span className={styles.ring2} style={{ borderColor:'rgba(14,165,233,.35)' }}/>
          </>}
          <img src={CALLER_IMG} alt="Caller" className={styles.avatarImg}/>
          {callerSpeaking && <div className={styles.speakBadge} style={{ background:'#0ea5e9' }}>speaking</div>}
        </div>
        <div className={styles.name}>{callerName || 'Caller'}</div>
        <div className={styles.role}>📞 Policyholder</div>
        <div className={`${styles.statusLine} ${callerSpeaking ? styles.statusActive : ''}`}
             style={{ '--c': '#0ea5e9' }}>
          {callerSpeaking ? '🟢 Speaking' : '⬤ Listening'}
        </div>
      </div>

      {/* ── Waveform Bridge ── */}
      <div className={styles.bridge}>
        <div className={styles.bridgeTop}>
          {speaking && <span className={styles.flowLabel} style={{ color:'#a78bfa' }}>◀ JAMIE</span>}
          {callerSpeaking && <span className={styles.flowLabel} style={{ color:'#38bdf8' }}>CALLER ▶</span>}
          {!speaking && !callerSpeaking && <span className={styles.flowLabel} style={{ color:'#94a3b8', letterSpacing:'.5px' }}>● ● ●</span>}
        </div>
        <canvas ref={canvasRef} className={styles.wave}/>
        <div className={styles.modeRow} style={{ '--mc': modeColor }}>
          <span>{modeEmoji}</span>
          <span className={styles.modeTxt}>{modeLabel}</span>
        </div>
      </div>

      {/* ── Jamie ── */}
      <div className={styles.avatarCol}>
        <div className={`${styles.avatarWrap} ${speaking ? styles.activeSpeaker : ''}`}
             style={speaking ? { '--ring': '#7c3aed' } : {}}>
          {speaking && <>
            <span className={styles.ring1} style={{ borderColor:'rgba(124,58,237,.65)' }}/>
            <span className={styles.ring2} style={{ borderColor:'rgba(124,58,237,.35)' }}/>
          </>}
          <img src={JAMIE_IMG} alt="Jamie" className={styles.avatarImg}/>
          <div className={styles.aiBadge}>AI</div>
          {speaking && <div className={styles.speakBadge} style={{ background:'#7c3aed' }}>speaking</div>}
        </div>
        <div className={styles.name}>Jamie Hofmann</div>
        <div className={styles.role}>🤖 AI Claims Agent</div>
        <div className={`${styles.statusLine} ${speaking ? styles.statusActive : ''}`}
             style={{ '--c': '#7c3aed' }}>
          {speaking ? '🟢 Speaking' : '⬤ Listening'}
        </div>
      </div>

    </div>
  )
}
