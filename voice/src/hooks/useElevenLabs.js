import { useRef, useState, useCallback } from 'react'

export function useElevenLabs({
  apiKey  = '',
  voiceId = '21m00Tcm4TlvDq8ikWAM',
} = {}) {
  const audioRef              = useRef(null)
  const abortRef              = useRef(null)
  const [isSpeaking, setIsSpeaking] = useState(false)

  const isSupported = apiKey.trim() !== ''

  const cancelSpeech = useCallback(() => {
    abortRef.current?.abort()
    if (audioRef.current) { audioRef.current.pause(); audioRef.current.src = ''; audioRef.current = null }
    setIsSpeaking(false)
  }, [])

  const speak = useCallback(async (text, onEnd) => {
    if (!isSupported || !text) return
    cancelSpeech()

    const controller = new AbortController()
    abortRef.current = controller
    setIsSpeaking(true)

    try {
      const response = await fetch(`https://api.elevenlabs.io/v1/text-to-speech/${voiceId}`, {
        method: 'POST',
        signal: controller.signal,
        headers: {
          'xi-api-key':   apiKey.trim(),
          'Content-Type': 'application/json',
          'Accept':       'audio/mpeg',
        },
        body: JSON.stringify({
          text,
          model_id: 'eleven_multilingual_v2',
          voice_settings: { stability: 0.45, similarity_boost: 0.80, style: 0.30, use_speaker_boost: true },
        }),
      })

      if (!response.ok) throw new Error(`ElevenLabs ${response.status}`)

      const blob  = await response.blob()
      const url   = URL.createObjectURL(blob)
      const audio = new Audio(url)
      audioRef.current = audio

      audio.onended = () => { URL.revokeObjectURL(url); audioRef.current = null; setIsSpeaking(false); onEnd?.() }
      audio.onerror = () => { URL.revokeObjectURL(url); audioRef.current = null; setIsSpeaking(false); onEnd?.() }
      audio.play()
    } catch (err) {
      if (err.name !== 'AbortError') console.error('[ElevenLabs]', err)
      setIsSpeaking(false); onEnd?.()
    }
  }, [apiKey, voiceId, isSupported, cancelSpeech])

  return { speak, cancelSpeech, isSpeaking, isSupported }
}
