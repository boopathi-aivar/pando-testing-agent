import { createContext, useContext, useLayoutEffect, useMemo, useState } from 'react'

const ThemeContext = createContext({
  theme: 'light',
  isDark: false,
  toggleTheme: () => {},
  setTheme: () => {},
})

export function readStoredTheme() {
  try {
    const stored = localStorage.getItem('pando_theme')
    if (stored === 'dark' || stored === 'light') return stored
  } catch { /* ignore */ }
  return 'light'
}

export function applyThemeClass(theme) {
  const dark = theme === 'dark'
  const root = document.documentElement
  root.classList.toggle('dark', dark)
  root.setAttribute('data-theme', theme)
  root.style.colorScheme = dark ? 'dark' : 'light'
  if (document.body) {
    document.body.style.colorScheme = dark ? 'dark' : 'light'
  }
}

export function ThemeProvider({ children }) {
  const [theme, setThemeState] = useState(() => readStoredTheme())

  useLayoutEffect(() => {
    applyThemeClass(theme)
    try {
      localStorage.setItem('pando_theme', theme)
    } catch { /* ignore */ }
  }, [theme])

  const value = useMemo(() => ({
    theme,
    isDark: theme === 'dark',
    setTheme: (next) => {
      applyThemeClass(next)
      setThemeState(next)
    },
    toggleTheme: () => {
      setThemeState((current) => {
        const next = current === 'dark' ? 'light' : 'dark'
        applyThemeClass(next)
        try {
          localStorage.setItem('pando_theme', next)
        } catch { /* ignore */ }
        return next
      })
    },
  }), [theme])

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

export function useTheme() {
  return useContext(ThemeContext)
}
