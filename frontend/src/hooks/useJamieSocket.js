import { useEffect, useReducer, useRef, useState } from 'react'

const LABEL_ALIAS = {
  accident_date:     'incident_datetime',
  accident_time:     'incident_datetime',
  injury_description:'injuries_or_symptoms',
  damage_description:'how_it_happened',
  witness_name:      'witnesses',
}

const INIT = {
  transcript:    [],
  pillars:       {},
  fraud:         {},
  tools:         [],
  mode:          'calm',
  crm:           null,
  finalClaim:    null,
  callStartTime: null,
  callActive:    false,
}

function reducer(state, ev) {
  switch (ev.type) {
    case 'transcript':
      return { ...state, transcript: [...state.transcript, { ...ev, ts: ev.ts || new Date().toISOString() }] }
    case 'entity': {
      const lab = LABEL_ALIAS[ev.label] || ev.label
      return { ...state, pillars: { ...state.pillars, [lab]: ev } }
    }
    case 'fraud_signal':
      return { ...state, fraud: { ...state.fraud, [ev.signal]: ev } }
    case 'emotional_state':
      return { ...state, mode: ev.state }
    case 'tool_call':
    case 'tool_result':
      return { ...state, tools: [{ ...ev, ts: ev.ts || new Date().toISOString() }, ...state.tools].slice(0, 10) }
    case 'call_start':
      return { ...INIT, crm: ev.crm || {}, callStartTime: Date.now(), callActive: true }
    case 'call_end':
      return { ...state, finalClaim: ev.claim_json, callActive: false }
    default:
      return state
  }
}

export function useJamieSocket() {
  const [state, dispatch] = useReducer(reducer, INIT)
  const [connected, setConnected] = useState(false)
  const wsRef = useRef(null)

  useEffect(() => {
    let cancelled = false
    let retryTimer = null

    function connect() {
      const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
      const host  = location.hostname || '127.0.0.1'
      const port  = import.meta.env.VITE_BRIDGE_PORT || '8765'
      
      // Force connection to the bridge server port directly
      const url   = `${proto}//${host}:${port}/ws`

      const ws = new WebSocket(url)
      wsRef.current = ws

      ws.onopen  = () => !cancelled && setConnected(true)
      ws.onclose = () => {
        if (cancelled) return
        setConnected(false)
        retryTimer = setTimeout(connect, 2000)
      }
      ws.onerror = () => ws.close()
      ws.onmessage = (m) => {
        try { dispatch(JSON.parse(m.data)) } catch (_) {}
      }
    }

    connect()
    return () => {
      cancelled = true
      clearTimeout(retryTimer)
      wsRef.current?.close()
    }
  }, [])

  return { state, connected }
}
