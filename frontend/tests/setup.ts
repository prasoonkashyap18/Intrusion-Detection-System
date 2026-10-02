import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, beforeEach } from 'vitest'
import { stubMatchMedia } from './helpers'

// Tests import Vitest helpers explicitly (globals are off), so React Testing
// Library's automatic cleanup is not registered for us.
afterEach(cleanup)

// Default environment: a pointer-less jsdom with motion allowed. Tests that
// care about reduced motion stub it again.
beforeEach(() => stubMatchMedia(false))
