import styles from './ClaimProgress.module.css'

/* Pillars that always apply regardless of claim type */
const COMMON_PILLARS = [
  ['claim_type',            '🏷️', 'Claim Type'],
  ['incident_datetime',     '🕐', 'Date / Time'],
  ['incident_location',     '📍', 'Location'],
  ['injuries_or_symptoms',  '🩺', 'Injuries / Symptoms'],
  ['how_it_happened',       '💥', 'What Happened'],
]

/* Shown only for AUTO claims */
const AUTO_PILLARS = [
  ['vehicle_drivable',     '🚗', 'Vehicle Drivable?'],
  ['other_party_involved', '👥', 'Other Party?'],
  ['police_or_ambulance',  '🚨', 'Police / Ambo?'],
  ['witnesses',            '👁', 'Witnesses'],
  ['fault_admission',      '⚖️', 'Fault Admission'],
  ['settlement_preference','💳', 'Settlement Pref.'],
]

/* Shown only for HEALTH claims */
const HEALTH_PILLARS = [
  ['treatment_received',   '🏥', 'Treatment Received'],
  ['provider_name',        '👨‍⚕️', 'Doctor / Hospital'],
  ['police_or_ambulance',  '🚑', 'Ambulance Called?'],
  ['settlement_preference','💳', 'Reimbursement Pref.'],
]

function detectClaimType(pillars) {
  const raw = (pillars.claim_type?.value || '').toLowerCase()
  if (raw.includes('health') || raw.includes('medical') || raw.includes('gesundheit')) return 'health'
  if (raw.includes('auto') || raw.includes('car') || raw.includes('vehicle') || raw.includes('kfz')) return 'auto'
  return null // unknown — show hint only
}

function ring(pct) {
  const r = 30, circ = 2 * Math.PI * r
  return { strokeDasharray: circ, strokeDashoffset: circ * (1 - pct) }
}

function PillarRow({ id, emoji, label, data }) {
  const done = !!data
  return (
    <div className={`${styles.row} ${done ? styles.done : ''}`}>
      <div className={styles.check}>{done ? '✓' : <span className={styles.checkEmpty}/>}</div>
      <span className={styles.emoji}>{emoji}</span>
      <div className={styles.info}>
        <div className={styles.label}>{label}</div>
        {done && <div className={styles.value}>{(data.value || '').slice(0, 28)}</div>}
      </div>
      {done && data.confidence && (
        <div className={styles.conf} style={{ '--w': `${Math.round(data.confidence * 100)}%` }}/>
      )}
    </div>
  )
}

export default function ClaimProgress({ pillars }) {
  const claimType = detectClaimType(pillars)
  const extraPillars = claimType === 'health' ? HEALTH_PILLARS
                     : claimType === 'auto'   ? AUTO_PILLARS
                     : []

  const allPillars = [...COMMON_PILLARS, ...extraPillars]
  const filled = allPillars.filter(([id]) => pillars[id]).length
  const pct = filled / allPillars.length

  return (
    <div className={styles.wrap}>

      {/* Donut ring */}
      <div className={styles.ringWrap}>
        <svg width="72" height="72" viewBox="0 0 72 72">
          <circle cx="36" cy="36" r="30" fill="none" stroke="rgba(100,120,200,.12)" strokeWidth="7"/>
          <circle cx="36" cy="36" r="30" fill="none"
            stroke="url(#prog-grad)" strokeWidth="7"
            strokeLinecap="round"
            transform="rotate(-90 36 36)"
            style={{ ...ring(pct), transition:'stroke-dashoffset .6s ease' }}
          />
          <defs>
            <linearGradient id="prog-grad" x1="0%" y1="0%" x2="100%" y2="0%">
              <stop offset="0%" stopColor="#7c3aed"/>
              <stop offset="100%" stopColor={claimType === 'health' ? '#10b981' : '#0ea5e9'}/>
            </linearGradient>
          </defs>
        </svg>
        <div className={styles.ringLabel}>
          <span className={styles.ringNum}>{filled}</span>
          <span className={styles.ringDen}>/{allPillars.length}</span>
        </div>
      </div>

      {/* Claim type badge */}
      {claimType ? (
        <div className={styles.typeBadge} style={{
          background: claimType === 'health' ? 'rgba(16,185,129,.1)' : 'rgba(14,165,233,.1)',
          borderColor: claimType === 'health' ? 'rgba(16,185,129,.3)' : 'rgba(14,165,233,.3)',
          color:       claimType === 'health' ? '#059669' : '#0284c7',
        }}>
          {claimType === 'health' ? '🏥 Health Claim' : '🚗 Auto Claim'}
        </div>
      ) : (
        <div className={styles.typeBadge} style={{ background:'rgba(148,163,184,.1)', borderColor:'rgba(148,163,184,.3)', color:'#64748b' }}>
          🏷️ Awaiting claim type…
        </div>
      )}

      {/* Pillar list */}
      <div className={styles.list}>
        <div className={styles.section}>Common</div>
        {COMMON_PILLARS.map(([id, emoji, label]) => (
          <PillarRow key={id} id={id} emoji={emoji} label={label} data={pillars[id]}/>
        ))}
        {extraPillars.length > 0 && <>
          <div className={styles.section}>
            {claimType === 'health' ? '🏥 Health Specific' : '🚗 Auto Specific'}
          </div>
          {extraPillars.map(([id, emoji, label]) => (
            <PillarRow key={id} id={id} emoji={emoji} label={label} data={pillars[id]}/>
          ))}
        </>}
      </div>
    </div>
  )
}
