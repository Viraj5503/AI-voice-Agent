import { useEffect, useMemo, useRef, useState, useCallback } from 'react'
import { useJamieSocket } from './hooks/useJamieSocket'
import JamieAvatar   from './components/JamieAvatar'
import Transcript    from './components/Transcript'
import ClaimProgress from './components/ClaimProgress'
import FraudGauge    from './components/FraudGauge'
import styles        from './App.module.css'

/* ── 3D card tilt wrapper ──────────────────────────────────── */
function Tilt({ children, className = '', intensity = 8 }) {
  const ref = useRef(null)
  const raf = useRef(null)
  const onMove = useCallback((e) => {
    cancelAnimationFrame(raf.current)
    raf.current = requestAnimationFrame(() => {
      if (!ref.current) return
      const r  = ref.current.getBoundingClientRect()
      const cx = (e.clientX - r.left)  / r.width  - 0.5
      const cy = (e.clientY - r.top)   / r.height - 0.5
      ref.current.style.transform =
        `perspective(900px) rotateY(${cx * intensity}deg) rotateX(${-cy * intensity}deg) translateZ(6px)`
    })
  }, [intensity])
  const onLeave = useCallback(() => {
    cancelAnimationFrame(raf.current)
    if (ref.current) ref.current.style.transform = ''
  }, [])
  return (
    <div ref={ref} className={className}
      onMouseMove={onMove} onMouseLeave={onLeave}
      style={{ transition:'transform .35s cubic-bezier(.34,1.56,.64,1)', transformStyle:'preserve-3d' }}>
      {children}
    </div>
  )
}

/* ── Card ──────────────────────────────────────────────────── */
function Card({ title, badge, children, className='', glow=false, accent }) {
  return (
    <div className={`${styles.card} ${glow ? styles.cardGlow : ''} ${className}`}
         style={accent ? { '--accent': accent } : {}}>
      {title && (
        <div className={styles.cardHeader}>
          <span className={styles.cardTitle}>
            <span className={styles.dot} style={accent ? { background: accent } : {}} />
            {title}
          </span>
          {badge != null && <span className={styles.badge}>{badge}</span>}
        </div>
      )}
      <div className={styles.cardBody}>{children}</div>
    </div>
  )
}

/* ── Call timer ────────────────────────────────────────────── */
function useTimer(startTime) {
  const [t, setT] = useState(0)
  useEffect(() => {
    if (!startTime) { setT(0); return }
    const id = setInterval(() => setT(Date.now() - startTime), 500)
    return () => clearInterval(id)
  }, [startTime])
  const s = Math.floor(t / 1000)
  return `${String(Math.floor(s / 60)).padStart(2,'0')}:${String(s % 60).padStart(2,'0')}`
}

/* ── Tool feed ─────────────────────────────────────────────── */
function ToolFeed({ tools }) {
  return (
    <div className={styles.toolFeed}>
      {tools.length === 0 && <div className={styles.empty}>No tool calls yet</div>}
      {tools.map((t, i) => (
        <div key={i} className={`${styles.toolRow} ${t.type === 'tool_result' ? styles.toolResult : ''}`}>
          <span className={styles.toolArrow}>{t.type === 'tool_call' ? '↗' : '↙'}</span>
          <div className={styles.toolContent}>
            <div className={styles.toolName}>{(t.name || '').replace(/_/g, ' ')}</div>
            {t.result?.summary && <div className={styles.toolSum}>{String(t.result.summary).slice(0,60)}</div>}
          </div>
        </div>
      ))}
    </div>
  )
}

/* ── CRM panel ─────────────────────────────────────────────── */
function CRMPanel({ crm }) {
  if (!crm) return <div className={styles.empty}>Loads on call start</div>
  const p = crm.policyholder || {}
  const v = crm.vehicle || {}
  const c = crm.coverage || {}
  const rows = [
    ['👤 Name', p.name], ['🎂 DOB', p.dob], ['📞 Phone', p.phone],
    ['📄 Policy', crm.policy?.policy_number], ['📦 Product', crm.policy?.product],
    ['🚗 Vehicle', `${v.make||''} ${v.model||''}`.trim()],
    ['🪪 Plate', v.plate], ['🛡 Coverage', c.type],
    ['💰 Deduct.', c.deductible_kasko != null ? `€${c.deductible_kasko}` : null],
    ['⭐ SF-Class', c.sf_class],
    ['➕ Addons', Array.isArray(c.addons) ? c.addons.join(', ') : c.addons],
  ].filter(([,v]) => v)
  return (
    <div className={styles.crm}>
      {rows.map(([k,v]) => (
        <div key={k} className={styles.crmRow}>
          <span className={styles.crmKey}>{k}</span>
          <span className={styles.crmVal}>{v}</span>
        </div>
      ))}
    </div>
  )
}

/* ── Final claim ───────────────────────────────────────────── */
function FinalClaim({ claim }) {
  const [copied, setCopied] = useState(false)
  if (!claim) return <div className={styles.empty}>Available at call end</div>
  const copy = () => {
    navigator.clipboard.writeText(JSON.stringify(claim, null, 2))
    setCopied(true); setTimeout(() => setCopied(false), 2000)
  }
  return (
    <div className={styles.claimJSON}>
      <button className={styles.copyBtn} onClick={copy}>
        {copied ? '✓ Copied!' : '⊕ Copy JSON'}
      </button>
      <pre className={styles.json}>{JSON.stringify(claim, null, 2)}</pre>
    </div>
  )
}

/* ── Dynamic Location Map ─────────────────────────────────────── */
function MapPanel({ location }) {
  if (!location) return null
  const encodedQuery = encodeURIComponent(location)
  return (
    <div style={{ borderRadius: '8px', overflow: 'hidden', height: '200px', width: '100%', marginTop: '0.5rem' }}>
      <iframe 
        width="100%" 
        height="100%" 
        frameBorder="0" 
        style={{ border: 0 }}
        src={`https://maps.google.com/maps?q=${encodedQuery}&t=&z=13&ie=UTF8&iwloc=&output=embed`}
        allowFullScreen>
      </iframe>
    </div>
  )
}

/* ══════════════════════════════════════════════════
   MAIN APP
══════════════════════════════════════════════════ */
export default function App() {
  const { state, connected } = useJamieSocket()
  const { transcript, pillars, fraud, tools, mode, crm, finalClaim, callStartTime, callActive } = state
  const timer  = useTimer(callStartTime)
  const filled = Object.keys(pillars).length
  const incidentLocation = pillars.incident_location?.value

  // Speaking detection
  const [jamieSpeak,  setJ] = useState(false)
  const [callerSpeak, setC] = useState(false)
  const spkTimer = useRef(null)
  useEffect(() => {
    if (!transcript.length) return
    const last = transcript[transcript.length - 1]
    clearTimeout(spkTimer.current)
    if (last.speaker === 'jamie') { setJ(true); setC(false) }
    else                          { setC(true); setJ(false) }
    spkTimer.current = setTimeout(() => { setJ(false); setC(false) }, 2400)
  }, [transcript.length])

  // Glow on new data
  const [glowClaim, setGC] = useState(false)
  const [glowFraud, setGF] = useState(false)
  useEffect(() => { setGC(true); setTimeout(()=>setGC(false),900) }, [filled])
  useEffect(() => {
    if (!Object.keys(fraud).length) return
    setGF(true); setTimeout(()=>setGF(false),900)
  }, [Object.keys(fraud).length])

  return (
    <div className={styles.app}>

      {/* ══ HEADER ══════════════════════════════════════════════ */}
      <header className={styles.header}>
        <div className={styles.brand}>
          <div className={styles.logo}>
            <span>V</span>
          </div>
          <div>
            <div className={styles.brandName}>VORSICHT <em>Claims</em></div>
            <div className={styles.brandSub}>AI-Powered FNOL Console · Jamie v2</div>
          </div>
        </div>

        <div className={styles.headerMid}>
          {/* Animated spectrum bar — visible only when call active */}
          {callActive && (
            <div className={styles.spectrumWrap}>
              {Array.from({length:16}).map((_,i)=>(
                <div key={i} className={styles.specBar}
                     style={{ animationDelay:`${i*60}ms`, '--h': `${8+Math.random()*26}px` }} />
              ))}
            </div>
          )}
        </div>

        <div className={styles.headerRight}>
          <div className={`${styles.pill} ${connected ? styles.pillGreen : styles.pillRed}`}>
            <span className={`${styles.pillDot} ${connected ? styles.pulseGreen : ''}`} />
            {connected ? 'BRIDGE LIVE' : 'OFFLINE'}
          </div>
          {callActive && (
            <div className={`${styles.pill} ${styles.pillViolet}`}>
              <span className={styles.pillDot} style={{background:'#a78bfa', animation:'pulse-dot 1s infinite'}} />
              ON CALL
            </div>
          )}
          <div className={styles.chips}>
            <div className={styles.chip}>
              <div className={styles.chipLabel}>Pillars</div>
              <div className={styles.chipVal}>{filled}<span>/15</span></div>
            </div>
            <div className={styles.chip}>
              <div className={styles.chipLabel}>Fraud</div>
              <div className={styles.chipVal} style={{color: Object.keys(fraud).length ? '#f59e0b':'#10b981'}}>
                {Object.keys(fraud).length}
              </div>
            </div>
            {callActive && <div className={styles.timerBadge}>{timer}</div>}
          </div>
        </div>
      </header>

      {/* ══ LEFT ══════════════════════════════════════════════════ */}
      <aside className={styles.left}>
        <Tilt intensity={6}>
          <Card title="Jamie · AI Agent" accent="#7c3aed">
            <JamieAvatar speaking={jamieSpeak} callerSpeaking={callerSpeak} mode={mode} />
          </Card>
        </Tilt>

        <Tilt intensity={5} className={styles.grow}>
          <Card title="Live Tool Calls" badge={tools.length||null} accent="#0ea5e9" className={styles.fill}>
            <ToolFeed tools={tools} />
          </Card>
        </Tilt>

        {incidentLocation && (
          <Tilt intensity={4}>
            <Card title="Detected Location" accent="#f59e0b">
              <MapPanel location={incidentLocation} />
            </Card>
          </Tilt>
        )}
      </aside>

      {/* ══ MAIN ══════════════════════════════════════════════════ */}
      <main className={styles.main}>
        <div className={styles.transcriptBar}>
          <span className={styles.transcriptTitle}>
            <span className={styles.recDot} /> Live Transcript
          </span>
          <span className={styles.msgPill}>{transcript.length} messages</span>
        </div>
        <Transcript transcript={transcript} />
      </main>

      {/* ══ RIGHT ══════════════════════════════════════════════════ */}
      <aside className={styles.right}>
        <Tilt intensity={5}>
          <Card title="Claim Pillars" badge={`${filled}/15`} glow={glowClaim} accent="#10b981" className={styles.claimCard}>
            <ClaimProgress pillars={pillars} />
          </Card>
        </Tilt>

        <Tilt intensity={5}>
          <Card title="Fraud Risk Monitor" glow={glowFraud} accent="#ef4444">
            <FraudGauge fraud={fraud} />
          </Card>
        </Tilt>

        <Tilt intensity={4}>
          <Card title="Known Context · CRM" accent="#0ea5e9">
            <CRMPanel crm={crm} />
          </Card>
        </Tilt>

        <Tilt intensity={4}>
          <Card title="Final Claim Export" accent="#a78bfa">
            <FinalClaim claim={finalClaim} />
          </Card>
        </Tilt>
      </aside>

    </div>
  )
}
