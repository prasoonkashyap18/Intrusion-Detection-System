import { formatBytes } from './format'

/**
 * Fast, friendly checks before a file is sent. This is a convenience, not a
 * security control: the backend validates everything again and is the only
 * check that counts.
 *
 * Returns a user-facing message, or null when the file looks acceptable.
 */
export function validateCsvFile(file: File, maxBytes: number): string | null {
  if (!file.name.toLowerCase().endsWith('.csv') || file.name.length <= '.csv'.length) {
    return 'Only .csv files are supported.'
  }
  if (file.size === 0) {
    return 'This file is empty.'
  }
  if (file.size > maxBytes) {
    return `This file is ${formatBytes(file.size)}. The maximum upload size is ${formatBytes(maxBytes)}.`
  }
  return null
}
