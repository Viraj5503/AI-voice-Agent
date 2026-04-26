import styles from './ClaimProgress.module.css'

const PILLARS = [
  ['claim_type',            '🏷️ Claim Type'],
  ['incident_datetime',     '🕐 Date / Time'],
  ['incident_location',     '📍 Location'],
  ['injuries_or_symptoms',  '🩺 Symptoms/Injuries'],
  ['how_it_happened',       '💥 Incident Details'],
  ['treatment_received',    '🏥 Treatment'],
  ['provider_name',         '👨‍⚕️ Provider'],
  ['vehicle_drivable',      '🚗 Drivable?'],
  ['other_party_involved',  '👥 Other Party?'],
  ['police_or_ambulance',   '🚨 Police/Ambo?'],
  ['witnesses',             '👁 Witnesses'],
  ['fault_admission',       '⚖️ Fault'],
  ['settlement_preference', '💳 Settlement'],
]

function ring(pct) {
  const r = 28, circ = 2 * Math.PI * r
  return { strokeDasharray: circ, strokeDashoffset: circ * (1 - pct) }
}

export default function ClaimProgress({ pillars }) {
  const filled = Object.keys(pillars).length
  const pct    = filled / PILLARS.length

  return (
    <div className={styles.wrap}>
      {/* Donut ring */}
      <div className={styles.ringWrap}>
        <svg width="70" height="70" viewBox="0 0 70 70" className={styles.donut}>
          <circle cx="35" cy="35" r="28" fill="none" stroke="rgba(100,120,200,.15)" strokeWidth="6" />
          <circle cx="35" cy="35" r="28" fill="none"
            stroke="url(#prog-grad)" strokeWidth="6"
            strokeLinecap="round"
            transform="rotate(-90 35 35)"
            style={{ ...ring(pct), transition: 'stroke-dashoffset .6s ease' }}
          />
          <defs>
            <linearGradient id="prog-grad" x1="0%" y1="0%" x2="100%" y2="0%">
              <stop offset="0%" stopColor="#7c3aed" />
              <stop offset="100%" stopColor="#0ea5e9" />
            </linearGradient>
          </defs>
        </svg>
        <div className={styles.ringLabel}>
          <span className={styles.ringNum}>{filled}</span>
          <span className={styles.ringDen}>/{PILLARS.length}</span>
        </div>
      </div>

      {/* Pillar list */}
      <div className={styles.list}>
        {PILLARS.map(([id, label]) => {
          const data = pillars[id]
          return (
            <div key={id} className={`${styles.row} ${data ? styles.filled : ''}`}>
              <div className={styles.check}>{data ? '✓' : ''}</div>
              <div className={styles.label}>{label}</div>
              {data && (
                <div className={styles.value} title={data.value || data.text}>
                  {(data.value || data.text || '').slice(0, 22)}
                </div>
              )}
              {data?.confidence && (
                <div className={styles.conf} style={{ '--w': `${Math.round(data.confidence * 100)}%` }} />
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
