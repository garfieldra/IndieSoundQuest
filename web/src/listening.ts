import { useEffect, useRef, useState } from 'react'

export type ListeningOptions = {
  recordingId: string
  status: 'AVAILABLE' | 'UNAVAILABLE'
  preview: null | {
    provider: 'APPLE_ITUNES'
    url: string
    providerTrackUrl: string
    attribution: string
  }
  platformLinks: { provider: string; label: string; url: string }[]
}

export type PlaybackPhase = 'idle' | 'loading' | 'playing' | 'paused' | 'unavailable' | 'error'
export type PlaybackState = { phase: PlaybackPhase; options?: ListeningOptions }

export function playbackLabel(phase: PlaybackPhase) {
  if (phase === 'loading') return '正在准备试听…'
  if (phase === 'playing') return '正在播放 · 点击暂停'
  if (phase === 'paused') return '已暂停 · 点击继续'
  if (phase === 'unavailable') return '暂无试听'
  if (phase === 'error') return '试听暂不可用'
  return '点击卡片试听'
}

export function useListeningPreview() {
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const playingIdRef = useRef<string | null>(null)
  const cacheRef = useRef(new Map<string, ListeningOptions>())
  const pendingRef = useRef(new Map<string, Promise<ListeningOptions>>())
  const requestGenerationRef = useRef(0)
  const [states, setStates] = useState<Record<string, PlaybackState>>({})

  function update(recordingId: string, state: PlaybackState) {
    setStates(current => ({ ...current, [recordingId]: state }))
  }

  function stop() {
    requestGenerationRef.current += 1
    const currentId = playingIdRef.current
    if (audioRef.current) {
      audioRef.current.onended = null
      audioRef.current.onerror = null
      audioRef.current.pause()
      audioRef.current.removeAttribute('src')
      audioRef.current = null
    }
    if (currentId) setStates(current => ({
      ...current,
      [currentId]: { ...current[currentId], phase: 'idle' },
    }))
    playingIdRef.current = null
  }

  async function load(recordingId: string) {
    const cached = cacheRef.current.get(recordingId)
    if (cached) return cached
    const pending = pendingRef.current.get(recordingId)
    if (pending) return pending
    const request = fetch(`/api/v1/recordings/${recordingId}/listening-options`, { credentials: 'include' })
      .then(async response => {
        if (!response.ok) throw new Error(`试听资源请求失败（${response.status}）`)
        const options = await response.json() as ListeningOptions
        cacheRef.current.set(recordingId, options)
        return options
      })
      .finally(() => pendingRef.current.delete(recordingId))
    pendingRef.current.set(recordingId, request)
    return request
  }

  async function toggle(recordingId: string) {
    const current = states[recordingId]
    if (playingIdRef.current === recordingId && audioRef.current) {
      if (audioRef.current.paused) {
        try { await audioRef.current.play(); update(recordingId, { ...current, phase: 'playing' }) }
        catch { update(recordingId, { ...current, phase: 'error' }) }
      } else {
        audioRef.current.pause()
        update(recordingId, { ...current, phase: 'paused' })
      }
      return
    }

    stop()
    const requestGeneration = requestGenerationRef.current
    update(recordingId, { ...current, phase: 'loading' })
    try {
      const options = await load(recordingId)
      if (requestGeneration !== requestGenerationRef.current) return
      if (!options.preview) {
        update(recordingId, { phase: 'unavailable', options })
        return
      }
      const audio = new Audio(options.preview.url)
      audio.preload = 'none'
      audioRef.current = audio
      playingIdRef.current = recordingId
      audio.onended = () => {
        playingIdRef.current = null
        audioRef.current = null
        update(recordingId, { phase: 'idle', options })
      }
      audio.onerror = () => {
        playingIdRef.current = null
        audioRef.current = null
        update(recordingId, { phase: 'error', options })
      }
      await audio.play()
      if (requestGeneration !== requestGenerationRef.current) {
        audio.pause()
        return
      }
      update(recordingId, { phase: 'playing', options })
    } catch {
      if (requestGeneration !== requestGenerationRef.current) return
      playingIdRef.current = null
      audioRef.current = null
      update(recordingId, { phase: 'error', options: cacheRef.current.get(recordingId) })
    }
  }

  async function prefetch(recordingId: string) {
    if (cacheRef.current.has(recordingId)) return
    try {
      const options = await load(recordingId)
      update(recordingId, { phase: options.preview ? 'idle' : 'unavailable', options })
    } catch {
      // Hover/focus prefetch is best-effort; an explicit click will retry visibly.
    }
  }

  useEffect(() => stop, [])

  return {
    stateFor: (recordingId: string): PlaybackState => states[recordingId] ?? { phase: 'idle' },
    toggle,
    prefetch,
    stop,
  }
}
