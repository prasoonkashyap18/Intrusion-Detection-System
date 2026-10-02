import { describe, expect, it } from 'vitest'
import { ApiError } from '@/services'
import { validateCsvFile } from '@/utils/csvFile'
import { formatBytes, formatCount, formatUtcDateTime } from '@/utils/format'
import { describeUploadFailure } from '@/utils/uploadFailure'
import { csvFile } from './helpers'

describe('formatBytes', () => {
  it.each([
    [0, '0 B'],
    [1023, '1023 B'],
    [1024, '1 KB'],
    [1536, '1.5 KB'],
    [2.4 * 1024 * 1024, '2.4 MB'],
    [50 * 1024 * 1024, '50 MB'],
    [1024 ** 3, '1 GB'],
  ])('formats %d bytes as %s', (bytes, expected) => {
    expect(formatBytes(bytes)).toBe(expected)
  })
})

describe('formatCount / formatUtcDateTime', () => {
  it('separates thousands', () => {
    expect(formatCount(1234567)).toBe('1,234,567')
  })

  it('renders an ISO timestamp in UTC', () => {
    expect(formatUtcDateTime('2026-10-03T08:15:30Z')).toBe('2026-10-03 08:15:30 UTC')
    expect(formatUtcDateTime('2026-10-03T13:45:30+05:30')).toBe('2026-10-03 08:15:30 UTC')
  })

  it('returns an unparseable value unchanged rather than "Invalid Date"', () => {
    expect(formatUtcDateTime('not a date')).toBe('not a date')
  })
})

describe('validateCsvFile', () => {
  const limit = 1000

  it('accepts a normal CSV, case-insensitively', () => {
    expect(validateCsvFile(csvFile('a.csv', { size: 10 }), limit)).toBeNull()
    expect(validateCsvFile(csvFile('A.CSV', { size: 10 }), limit)).toBeNull()
  })

  it.each(['a.txt', 'a.csv.exe', 'a', '.csv', 'csv'])('rejects the name %s', (name) => {
    expect(validateCsvFile(csvFile(name, { size: 10 }), limit)).toMatch(/\.csv/)
  })

  it('rejects empty and oversized files, accepting the exact limit', () => {
    expect(validateCsvFile(csvFile('a.csv', { size: 0 }), limit)).toMatch(/empty/)
    expect(validateCsvFile(csvFile('a.csv', { size: limit + 1 }), limit)).toMatch(/maximum upload size/)
    expect(validateCsvFile(csvFile('a.csv', { size: limit }), limit)).toBeNull()
  })
})

describe('describeUploadFailure', () => {
  it('uses the backend explanation for a rejected file and does not offer retry', () => {
    const error = new ApiError('http_error', 'Request failed with status 400', 400, { detail: 'CSV contains no data rows.' })

    expect(describeUploadFailure(error)).toEqual({ message: 'CSV contains no data rows.', retryable: false })
  })

  it('offers retry for server errors, with a safe fallback message', () => {
    const error = new ApiError('http_error', 'Request failed with status 503', 503)

    expect(describeUploadFailure(error)).toMatchObject({ retryable: true })
    expect(describeUploadFailure(error).message).not.toMatch(/503|status/)
  })

  it.each(['network_error', 'timeout', 'invalid_response', 'aborted'] as const)('offers retry for %s', (code) => {
    expect(describeUploadFailure(new ApiError(code, 'internal text'))).toMatchObject({ retryable: true })
  })

  it('never leaks internal error text', () => {
    const error = new ApiError('network_error', 'Could not reach the backend', null, { cause: new TypeError('Failed to fetch') })

    expect(describeUploadFailure(error).message).not.toMatch(/failed to fetch|typeerror/i)
  })

  it('handles a non-ApiError defensively', () => {
    expect(describeUploadFailure(new Error('boom'))).toMatchObject({ retryable: true })
    expect(describeUploadFailure('boom').message).not.toContain('boom')
  })
})
