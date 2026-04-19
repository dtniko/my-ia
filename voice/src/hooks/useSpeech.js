import { useRef, useState, useCallback, useEffect } from 'react'

const SpeechRecognition =
  window.SpeechRecognition || window.webkitSpeechRecognition || null

export function useSpeech({ lang = 'it-IT', onTranscript, onError, continuous = false } = {}) {
  const recognitionRef                       = useRef(null)
  const synthRef                             = useRef(window.speechSynthesis)
  const [isListening,       setIsListening]  = useState(false)
  const [isSpeaking,        setIsSpeaking]   = useState(false)
  const [interimTranscript, setInterim]      = useState('')

  const isSupported = SpeechRecognition !== null

  const pickBestVoice = useCallback(() => {
    const voices = synthRef.current?.getVoices() ?? []
    const it = voices.filter(v => v.lang.startsWith('it'))
    if (!it.length) return null
    const score = (v) => {
      const n = v.name.toLowerCase()
      if (n.includes('federica'))            return 10
      if (n.includes('neural'))              return 9
      if (n.includes('premium'))             return 8
      if (n.includes('enhanced'))            return 7
      if (n.includes('alice') && v.localService) return 6
      if (n.includes('google'))              return 5
      if (v.localService)                    return 3
      return 1
    }
    return it.slice().sort((a, b) => score(b) - score(a))[0] ?? null
  }, [])

  const startListening = useCallback(() => {
    if (!isSupported || isListening) return
    synthRef.current?.cancel()
    setIsSpeaking(false)

    const rec = new SpeechRecognition()
    rec.lang            = lang
    rec.continuous      = continuous
    rec.interimResults  = true
    rec.maxAlternatives = 1

    rec.onstart  = () => { setIsListening(true); setInterim('') }
    rec.onresult = (event) => {
      let interim = '', final = ''
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const t = event.results[i][0].transcript
        if (event.results[i].isFinal) final += t
        else interim += t
      }
      setInterim(interim)
      if (final) { setInterim(''); onTranscript?.(final.trim()) }
    }
    rec.onerror = (event) => {
      setIsListening(false); setInterim('')
      if (event.error !== 'no-speech' && event.error !== 'aborted')
        onError?.(`Errore microfono: ${event.error}`)
    }
    rec.onend = () => { setIsListening(false); setInterim('') }

    recognitionRef.current = rec
    rec.start()
  }, [isSupported, isListening, lang, onTranscript, onError, continuous])

  const stopListening = useCallback(() => {
    if (recognitionRef.current) { recognitionRef.current.stop(); recognitionRef.current = null }
    setIsListening(false); setInterim('')
  }, [])

  const speak = useCallback((text, onEnd) => {
    if (!text || !synthRef.current) return
    synthRef.current.cancel()
    const utterance = new SpeechSynthesisUtterance(text)
    utterance.lang  = lang
    utterance.rate  = 1.0
    utterance.pitch = 1.0
    const voice = pickBestVoice()
    if (voice) utterance.voice = voice
    utterance.onstart = () => setIsSpeaking(true)
    utterance.onend   = () => { setIsSpeaking(false); onEnd?.() }
    utterance.onerror = () => setIsSpeaking(false)
    synthRef.current.speak(utterance)
  }, [lang, pickBestVoice])

  const cancelSpeech = useCallback(() => {
    synthRef.current?.cancel(); setIsSpeaking(false)
  }, [])

  useEffect(() => () => {
    recognitionRef.current?.abort(); synthRef.current?.cancel()
  }, [])

  return { startListening, stopListening, speak, cancelSpeech, isListening, isSpeaking, isSupported, interimTranscript }
}
