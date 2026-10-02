import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// Tests import Vitest helpers explicitly (globals are off), so React Testing
// Library's automatic cleanup is not registered for us.
afterEach(cleanup)
