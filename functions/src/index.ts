import { initializeApp } from 'firebase-admin/app'
import { FieldValue, getFirestore } from 'firebase-admin/firestore'
import { HttpsError, onCall } from 'firebase-functions/v2/https'
import { onDocumentCreated } from 'firebase-functions/v2/firestore'
import { defineString } from 'firebase-functions/params'
import { logger } from 'firebase-functions'
import { z } from 'zod'
import { GoogleAuth } from 'google-auth-library'

initializeApp()
const db = getFirestore()
const runnerUrl = defineString('TRAINING_RUNNER_URL', { default: '' })
const region = 'asia-south1'

const trainingInput = z.object({
  symbol: z.string().trim().toUpperCase().regex(/^[A-Z0-9&-]{1,24}$/),
  exchange: z.enum(['NSE', 'BSE']),
  historyYears: z.number().int().min(3).max(30),
  timeframe: z.enum(['1d', '1h', '15m']),
})

export const startTraining = onCall({ region, timeoutSeconds: 30, memory: '256MiB', maxInstances: 20 }, async request => {
  if (!request.auth) throw new HttpsError('unauthenticated', 'Sign in before starting a training job.')
  const parsed = trainingInput.safeParse(request.data)
  if (!parsed.success) throw new HttpsError('invalid-argument', 'Invalid stock or training configuration.')
  const uid = request.auth.uid
  const recent = await db.collection('trainingJobs').where('ownerId', '==', uid).where('createdAt', '>=', new Date(Date.now() - 60_000)).limit(3).get()
  if (recent.size >= 3) throw new HttpsError('resource-exhausted', 'Please wait before starting another training job.')
  const job = db.collection('trainingJobs').doc()
  const now = FieldValue.serverTimestamp()
  await db.runTransaction(async tx => {
    tx.create(job, { ...parsed.data, ownerId: uid, status: 'queued', stage: 'awaiting_runner', progress: 0, createdAt: now, updatedAt: now, schemaVersion: 1 })
    tx.create(db.collection('auditEvents').doc(), { ownerId: uid, actorId: uid, action: 'training.requested', targetId: job.id, createdAt: now, metadata: { symbol: parsed.data.symbol, exchange: parsed.data.exchange } })
  })
  return { jobId: job.id, status: 'queued' }
})

export const dispatchTraining = onDocumentCreated({ document: 'trainingJobs/{jobId}', region, timeoutSeconds: 60, memory: '256MiB', maxInstances: 10, retry: false }, async event => {
  const snapshot = event.data
  if (!snapshot) return
  const url = runnerUrl.value()
  if (!url) {
    await snapshot.ref.update({ status: 'blocked', stage: 'configuration_required', errorCode: 'TRAINING_RUNNER_NOT_CONFIGURED', updatedAt: FieldValue.serverTimestamp() })
    logger.warn('Training runner is not configured', { jobId: snapshot.id })
    return
  }
  try {
    await snapshot.ref.update({ status: 'running', stage: 'provider_validation', progress: 1, dispatchedAt: FieldValue.serverTimestamp(), updatedAt: FieldValue.serverTimestamp() })
    const client = await new GoogleAuth().getIdTokenClient(url)
    const response = await client.request({ url, method: 'POST', data: { jobId: snapshot.id, ...snapshot.data() } })
    if (response.status < 200 || response.status >= 300) throw new Error(`Runner responded ${response.status}`)
  } catch (error) {
    logger.error('Training dispatch failed', { jobId: snapshot.id, error })
    await snapshot.ref.update({ status: 'failed', stage: 'dispatch_failed', errorCode: 'RUNNER_UNAVAILABLE', updatedAt: FieldValue.serverTimestamp() })
  }
})
