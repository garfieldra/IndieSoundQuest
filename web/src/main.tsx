import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './App'
import { ConversationApp } from './ConversationApp'
import './styles.css'

function RootRouter() {
  const [worldCup, setWorldCup] = useState(window.location.hash === '#worldcup')
  useEffect(() => {
    const sync = () => setWorldCup(window.location.hash === '#worldcup')
    window.addEventListener('hashchange', sync)
    return () => window.removeEventListener('hashchange', sync)
  }, [])
  return worldCup ? <App /> : <ConversationApp />
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <RootRouter />
  </StrictMode>,
)
