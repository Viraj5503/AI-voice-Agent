import { useEffect, useMemo, useRef, useState, useCallback } from 'react'
import { useJamieSocket } from './hooks/useJamieSocket'
import JamieAvatar   from './components/JamieAvatar'
import Transcript    from './components/Transcript'
import ClaimProgress from './components/ClaimProgress'
import styles        from './App.module.css'

/* ── 3D tilt ──────────────────────────────────────────────── */
function Tilt({ children, className = '', intensity = 8 }) {
  const ref = useRef(null)
  const raf = useRef(null)
  const onMove = useCallback((e) => {
    cancelAnimationFrame(raf.current)
    raf.current = requestAnimationFrame(() => {
      if (!ref.current) return
      const r  = ref.current.getBoundingClientRect()
      const cx = (e.clientX - r.left) / r.width  - 0.5
      const cy = (e.clientY - r.top)  / r.height - 0.5
      ref.current.style.transform =
        `perspective(900px) rotateY(${cx * intensity}deg) rotateX(${-cy * intensity}deg) translateZ(4px)`
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
            {t.result?.summary && <div className={styles.toolSum}>{String(t.result.summary).slice(0,72)}</div>}
          </div>
        </div>
      ))}
    </div>
  )
}

/* ── CRM panel (Health-aware) ──────────────────────────────── */
function CRMPanel({ crm }) {
  if (!crm) return <div className={styles.empty}>Loads on call start</div>
  const p = crm.policyholder || {}
  const v = crm.vehicle      || {}
  const c = crm.coverage     || {}
  const rows = [
    ['👤', 'Name',    p.name],
    ['🎂', 'DOB',     p.dob],
    ['📄', 'Policy',  crm.policy?.policy_number],
    ['📦', 'Product', crm.policy?.product],
    ['🛡', 'Coverage',c.type],
  ].filter(([,, v]) => v)
  return (
    <div className={styles.crm}>
      <div style={{ display:'flex', alignItems:'center', gap:'6px', marginBottom:'12px', paddingBottom:'8px', borderBottom:'1px solid rgba(255,255,255,0.05)' }}>
        <span style={{ fontSize:'12px' }}>🔒</span>
        <span style={{ fontSize:'10px', fontWeight:700, letterSpacing:'1px', color:'#10b981' }}>VERIFIED POLICYHOLDER</span>
      </div>
      {rows.map(([icon, k, v]) => (
        <div key={k} className={styles.crmRow}>
          <span className={styles.crmIcon}>{icon}</span>
          <span className={styles.crmKey}>{k}</span>
          <span className={styles.crmVal}>{v}</span>
        </div>
      ))}
    </div>
  )
}

/* ── Location Map ──────────────────────────────────────────── */
function MapPanel({ location }) {
  if (!location) return null
  const q = encodeURIComponent(location)
  return (
    <div className={styles.mapWrap}>
      <div className={styles.mapLabel}>📍 {location}</div>
      <iframe
        width="100%" height="160"
        frameBorder="0" style={{ border:0, borderRadius:'10px' }}
        src={`https://maps.google.com/maps?q=${q}&t=&z=13&ie=UTF8&iwloc=&output=embed`}
        allowFullScreen/>
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
  
  const playSummary = () => {
    try {
      const p = claim.pillars || {}
      // Robustly get values regardless of nesting
      const getVal = (id) => p[id]?.value || p[id] || ''
      
      const summaryParts = [
        `Claim summary for ${claim.type || 'insurance'} incident.`,
        getVal('incident_datetime') ? `Occurred on ${getVal('incident_datetime')}.` : '',
        getVal('incident_location') ? `At ${getVal('incident_location')}.` : '',
        getVal('injuries_or_symptoms') ? `Injuries noted: ${getVal('injuries_or_symptoms')}.` : '',
        getVal('how_it_happened') ? `Context: ${getVal('how_it_happened')}.` : '',
        `Status: All required information gathered and forwarded to adjuster.`
      ].filter(s => s)

      const text = summaryParts.join(' ')
      console.log('Playing summary:', text)
      
      window.speechSynthesis.cancel() // Stop any current speech
      const msg = new SpeechSynthesisUtterance(text)
      msg.rate = 0.95; msg.pitch = 1.0; msg.volume = 1.0
      window.speechSynthesis.speak(msg)
    } catch (err) {
      console.error('TTS failed:', err)
    }
  }

  return (
    <div className={styles.claimJSON}>
      <div style={{ display: 'flex', gap: '8px', marginBottom: '8px' }}>
        <button className={styles.copyBtn} style={{ flex: 1, marginBottom: 0 }} onClick={copy}>
          {copied ? '✓ Copied!' : '⊕ Copy JSON'}
        </button>
        <button className={styles.copyBtn} style={{ flex: 1, marginBottom: 0 }} onClick={playSummary}>
          🔊 Play Summary
        </button>
      </div>
      <pre className={styles.json}>{JSON.stringify(claim, null, 2)}</pre>
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
  const callerName = crm?.policyholder?.name?.split(' ')[0] || 'Caller'

  /* Speaker detection */
  const [jamieSpeak,  setJ] = useState(false)
  const [callerSpeak, setC] = useState(false)
  const spkTimer = useRef(null)
  useEffect(() => {
    if (!transcript.length) return
    const last = transcript[transcript.length - 1]
    clearTimeout(spkTimer.current)
    if (last.speaker === 'jamie') { setJ(true); setC(false) }
    else                          { setC(true); setJ(false) }
    spkTimer.current = setTimeout(() => { setJ(false); setC(false) }, 2600)
  }, [transcript.length])

  /* Glow on new data */
  const [glowClaim, setGC] = useState(false)
  useEffect(() => { setGC(true); setTimeout(()=>setGC(false),900) }, [filled])

  const totalPillars = filled  // updated dynamically by ClaimProgress component

  return (
    <div className={styles.app}>

      {/* ══ HEADER ════════════════════════════════════════════════ */}
      <header className={styles.header}>
        <div className={styles.brand} onClick={() => window.location.reload()} style={{ cursor: 'pointer' }}>
          <img src="/logo.png" alt="EchoClaim Logo" style={{ height: '56px', width: 'auto', display: 'block', padding: '4px 0' }} />
        </div>

        <div className={styles.headerMid}>
          {callActive && (
            <div className={styles.spectrumWrap}>
              {Array.from({length:18}).map((_,i)=>(
                <div key={i} className={styles.specBar}
                     style={{ animationDelay:`${i*55}ms`, '--h': `${8+Math.random()*28}px` }}/>
              ))}
            </div>
          )}
        </div>

        <div className={styles.headerRight}>
          <div className={`${styles.pill} ${connected ? styles.pillGreen : styles.pillRed}`}>
            <span className={`${styles.pillDot} ${connected ? styles.pulseGreen : ''}`}/>
            {connected ? 'BRIDGE LIVE' : 'OFFLINE'}
          </div>
          {callActive && (
            <div className={`${styles.pill} ${styles.pillViolet}`}>
              <span className={styles.pillDot} style={{background:'#a78bfa', animation:'pulse-dot 1s infinite'}}/>
              ON CALL
            </div>
          )}
          <div className={styles.chips}>
            <div className={styles.chip}>
              <div className={styles.chipLabel}>Pillars</div>
              <div className={styles.chipVal}>{filled}</div>
            </div>
            {callStartTime && (
              <div className={styles.timerBadge}>
                <span style={{ color: '#10b981', marginRight: '6px', fontSize: '9px', fontWeight: 800 }}>⚡ DSP LIVE</span>
                {timer}
              </div>
            )}
          </div>
        </div>
      </header>

      {/* ══ DEMO CONTEXT BANNER (collapsed once call starts) ═════ */}
      {!callActive && !transcript.length && (
        <div className={styles.demoBanner}>
          <div className={styles.demoLeft}>
            <span className={styles.demoIcon}>🏢</span>
            <div>
              <div className={styles.demoTitle}>Adjuster View — Real-time AI Claims Dashboard</div>
              <div className={styles.demoDesc}>Jamie AI handles the phone call. This dashboard shows the adjuster everything in real time: transcript, extracted claim data, fraud signals, and location.</div>
            </div>
          </div>
          <div className={styles.demoRight}>
            <div className={styles.demoStep}>
              <span className={styles.demoNum}>1</span>
              <span>Call <strong>+49 30 7567 5681</strong> — or speak into your mic for a local demo</span>
            </div>
            <div className={styles.demoStep}>
              <span className={styles.demoNum}>2</span>
              <span>Jamie answers, takes the claim, extracts data live</span>
            </div>
            <div className={styles.demoStep}>
              <span className={styles.demoNum}>3</span>
              <span>Watch pillars tick, fraud signals appear, and the map pin the location</span>
            </div>
          </div>
        </div>
      )}

      {/* ══ BODY: left sidebar | centre | right sidebar ═════════ */}
      <div className={styles.body}>

        {/* ── LEFT ─────────────────────────────────────────────── */}
        <aside className={styles.left}>
          <Tilt intensity={5}>
            <Card title="Claim Pillars" badge={`${filled}`} glow={glowClaim} accent="#10b981" className={styles.claimCard}>
              <ClaimProgress pillars={pillars}/>
            </Card>
          </Tilt>
        </aside>

        {/* ── CENTRE ───────────────────────────────────────────── */}
        <main className={styles.centre}>

          {/* Stage: Avatars full width */}
          <div className={styles.stageCard}>
            <JamieAvatar speaking={jamieSpeak} callerSpeaking={callerSpeak} mode={mode} callerName={callerName}/>
          </div>

          {/* Transcript below */}
          <div className={styles.transcriptPanel}>
            <div className={styles.transcriptBar}>
              <span className={styles.transcriptTitle}>
                <span className={styles.recDot}/>
                Live Transcript
              </span>
              <span className={styles.msgPill}>{transcript.length} messages</span>
            </div>
            <Transcript transcript={transcript}/>
          </div>
        </main>

        {/* ── RIGHT ─────────────────────────────────────────────── */}
        <aside className={styles.right}>
          <Tilt intensity={5}>
            <Card title="Live Tool Calls" badge={tools.length||null} accent="#0ea5e9">
              <ToolFeed tools={tools}/>
            </Card>
          </Tilt>

          {incidentLocation && (
            <Tilt intensity={4}>
              <Card title="Detected Location" accent="#f59e0b">
                <MapPanel location={incidentLocation}/>
              </Card>
            </Tilt>
          )}

          <Tilt intensity={4}>
            <Card title="Known Context · CRM" accent="#0ea5e9">
              <CRMPanel crm={crm}/>
            </Card>
          </Tilt>

          <Tilt intensity={4}>
            <Card title="Final Claim Export" accent="#a78bfa">
              <FinalClaim claim={finalClaim}/>
            </Card>
          </Tilt>
        </aside>

      </div>
    </div>
  )
}
