import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'
import { setCsrfToken, setUnauthorizedHandler } from '../api'

afterEach(() => {
  cleanup()
  localStorage.clear()
  window.location.hash = ''
  setCsrfToken(null)
  setUnauthorizedHandler(null)
  vi.unstubAllGlobals()
})
