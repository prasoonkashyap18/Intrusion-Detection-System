import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { MAX_UPLOAD_BYTES, uploadDetectionCsv } from '../services'
import type { UploadBatchResponse } from '../types/detection'
import { validateCsvFile } from '../utils/csvFile'
import { describeUploadFailure, type UploadFailure } from '../utils/uploadFailure'

export type UploadPhase = 'idle' | 'selected' | 'uploading' | 'success' | 'error'

interface UploadState {
  phase: UploadPhase
  file: File | null
  /** Why the last picked file was refused before upload. */
  validationMessage: string | null
  batch: UploadBatchResponse | null
  failure: UploadFailure | null
}

const IDLE: UploadState = { phase: 'idle', file: null, validationMessage: null, batch: null, failure: null }

export interface CsvUpload extends UploadState {
  selectFile: (file: File) => void
  /** Reports a refused selection (e.g. several files dropped at once) without changing the current file. */
  rejectSelection: (message: string) => void
  upload: () => void
  reset: () => void
}

interface UseCsvUploadOptions {
  /** Called once with the batch the backend registered. */
  onUploaded?: (batch: UploadBatchResponse) => void
}

/**
 * Owns the upload lifecycle: pick → validate → send → result. Networking stays
 * in the service layer; this hook only sequences it and guards against
 * duplicate submissions.
 */
export function useCsvUpload({ onUploaded }: UseCsvUploadOptions = {}): CsvUpload {
  const [state, setState] = useState<UploadState>(IDLE)
  const fileRef = useRef<File | null>(null)
  const inFlightRef = useRef(false)
  const controllerRef = useRef<AbortController | null>(null)
  const onUploadedRef = useRef(onUploaded)

  useEffect(() => {
    onUploadedRef.current = onUploaded
  }, [onUploaded])

  useEffect(() => () => controllerRef.current?.abort(), [])

  const selectFile = useCallback((file: File) => {
    if (inFlightRef.current) return
    const problem = validateCsvFile(file, MAX_UPLOAD_BYTES)
    if (problem) {
      fileRef.current = null
      setState({ ...IDLE, validationMessage: problem })
      return
    }
    fileRef.current = file
    setState({ ...IDLE, phase: 'selected', file })
  }, [])

  const rejectSelection = useCallback((message: string) => {
    if (inFlightRef.current) return
    setState((current) => ({ ...current, validationMessage: message }))
  }, [])

  const reset = useCallback(() => {
    if (inFlightRef.current) return
    fileRef.current = null
    setState(IDLE)
  }, [])

  const upload = useCallback(() => {
    const file = fileRef.current
    if (!file || inFlightRef.current) return

    inFlightRef.current = true
    const controller = new AbortController()
    controllerRef.current = controller
    setState((current) => ({ ...current, phase: 'uploading', failure: null, validationMessage: null }))

    uploadDetectionCsv(file, controller.signal)
      .then((batch) => {
        if (controller.signal.aborted) return
        setState({ phase: 'success', file, validationMessage: null, batch, failure: null })
        onUploadedRef.current?.(batch)
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setState({ phase: 'error', file, validationMessage: null, batch: null, failure: describeUploadFailure(error) })
      })
      .finally(() => {
        inFlightRef.current = false
      })
  }, [])

  return useMemo(
    () => ({ ...state, selectFile, rejectSelection, upload, reset }),
    [state, selectFile, rejectSelection, upload, reset],
  )
}
