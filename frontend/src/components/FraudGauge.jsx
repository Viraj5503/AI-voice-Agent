import { useMemo } from 'react'
import styles from './FraudGauge.module.css'

const SEV = { low: 1, medium: 2.5, high: 5 }

export default function FraudGauge({ fraud }) {
  const signals = Object.values(fraud)
  const score   = useMemo(() => Math.min(10, signals.reduce((a, s) => a + (SEV[s.severity] || 1), 0)), [fraud])
  const pct     = score / 10

  // Arc from -140deg to +140deg (280deg sweep)
  const R = 40, cx = 55, cy = 55
  const startAngle = -220, sweep = 260
  const toRad = d => d * Math.PI / 180
  function arcPoint(deg) {
    return [cx + R * Math.cos(toRad(deg)), cy + R * Math.sin(toRad(deg))]
  }
  function arc(pct) {
    const end = startAngle + sweep * pct
    const [sx, sy] = arcPoint(startAngle)
    const [ex, ey] = arcPoint(end)
    const large = sweep * pct > 180 ? 1 : 0
    return `M ${sx} ${sy} A ${R} ${R} 0 ${large} 1 ${ex} ${ey}`
  }

  const color = score >= 7 ? '#ef4444' : score >= 4 ? '#f59e0b' : '#10b981'

  return (
    <div className={styles.wrap}>
      <div className={styles.gauge}>
        <svg width="120" height="85" viewBox="0 0 110 80">
          {/* Track */}
          <path d={arc(1)} fill="none" stroke="rgba(100,120,200,.15)" strokeWidth="8" strokeLinecap="round" />
          {/* Fill */}
          <path d={arc(pct)} fill="none" stroke={color} strokeWidth="8" strokeLinecap="round"
            style={{ transition: 'stroke .5s ease, d .6s ease', filter: `drop-shadow(0 0 6px ${color}80)` }} />
          {/* Needle dot */}
          {pct > 0 && (() => {
            const ang = startAngle + sweep * pct
            const [nx, ny] = arcPoint(ang)
            return <circle cx={nx} cy={ny} r="4" fill={color} style={{ filter: `drop-shadow(0 0 4px ${color})` }} />
          })()}
        </svg>
        <div className={styles.score} style={{ color }}>
          <span className={styles.num}>{score.toFixed(1)}</span>
          <span className={styles.den}>/10</span>
        </div>
        <div className={styles.label} style={{ color }}>
          {score >= 7 ? 'HIGH RISK' : score >= 4 ? 'MODERATE' : 'LOW RISK'}
        </div>
      </div>

      <div className={styles.signals}>
        {signals.length === 0 && <div className={styles.none}>No fraud signals detected</div>}
        {signals.map((s, i) => (
          <div key={i} className={`${styles.signal} ${styles[s.severity]}`}>
            <span className={styles.sigDot} />
            <div>
              <div className={styles.sigName}>{s.signal.replace(/_/g, ' ')}</div>
              {s.evidence && <div className={styles.sigEv}>{s.evidence.slice(0, 40)}</div>}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
