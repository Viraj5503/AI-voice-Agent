import { useEffect, useRef } from 'react'
import styles from './Transcript.module.css'

const JAMIE_IMG  = '/@fs/Users/virajdalsania/.gemini/antigravity/brain/339fa39d-4f20-42b4-88bb-cbe48ea78027/jamie_avatar_3d_1777173719753.png'
const CALLER_IMG = '/@fs/Users/virajdalsania/.gemini/antigravity/brain/339fa39d-4f20-42b4-88bb-cbe48ea78027/caller_avatar_3d_1777173801667.png'

function Bubble({ entry }) {
  const isJamie = entry.speaker === 'jamie'
  return (
    <div className={`${styles.bubble} ${isJamie ? styles.jamie : styles.caller}`}>
      <div className={styles.avatar}>
        {isJamie
          ? <img src={JAMIE_IMG} alt="J" className={styles.avatarImgJamie} />
          : <img src={CALLER_IMG} alt="C" className={styles.avatarImgCaller} />
        }
      </div>
      <div className={styles.content}>
        <div className={styles.speaker}>{isJamie ? 'EchoClaim' : 'Caller'}</div>
        <div className={styles.text}>{entry.text}</div>
        {entry.ts && (
          <div className={styles.ts}>
            {new Date(entry.ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
          </div>
        )}
      </div>
    </div>
  )
}

export default function Transcript({ transcript }) {
  const endRef = useRef(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [transcript.length])

  // Group messages from same speaker if they occur within 5 seconds of each other
  const grouped = transcript.reduce((acc, curr) => {
    const last = acc[acc.length - 1]
    if (last && last.speaker === curr.speaker) {
      last.text += ' ' + curr.text
      last.ts = curr.ts // Update to latest timestamp
      return acc
    }
    return [...acc, { ...curr }]
  }, [])

  return (
    <div className={styles.feed}>
      {grouped.length === 0 && (
        <div className={styles.empty}>
          <div className={styles.emptyIcon}>🎙</div>
          <div>Waiting for the call to connect…</div>
          <div className={styles.emptyHint}>Make sure the LiveKit agent worker is running</div>
        </div>
      )}
      {grouped.map((t, i) => <Bubble key={i} entry={t} />)}
      <div ref={endRef} />
    </div>
  )
}
