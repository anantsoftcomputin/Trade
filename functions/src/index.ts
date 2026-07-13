import { initializeApp } from 'firebase-admin/app'
import { FieldValue, getFirestore, Timestamp } from 'firebase-admin/firestore'
import { HttpsError, onCall } from 'firebase-functions/v2/https'
import { onDocumentCreated } from 'firebase-functions/v2/firestore'
import { onSchedule } from 'firebase-functions/v2/scheduler'
import { defineString } from 'firebase-functions/params'
import { logger } from 'firebase-functions'
import { z } from 'zod'
import { GoogleAuth } from 'google-auth-library'

initializeApp()
const db = getFirestore()
const runnerUrl = defineString('TRAINING_RUNNER_URL', { default: '' })
const geminiModel = defineString('GEMINI_MODEL', { default: 'gemini-2.5-flash' })
const region = 'asia-south1'
const projectId = 'trade-56777'

const trainingInput = z.object({
  symbol: z.string().trim().toUpperCase().regex(/^[A-Z0-9&-]{1,24}$/),
  exchange: z.enum(['NSE', 'BSE']), historyYears: z.number().int().min(3).max(30),
  timeframe: z.literal('1d'),
})
const signalInput = z.object({ modelId: z.string().min(10).max(40), capital: z.number().min(1_000).max(1_000_000_000).default(100_000), riskPct: z.number().min(.1).max(5).default(1) })
const advisorInput = z.object({ modelId: z.string().min(10).max(40), question: z.string().trim().min(2).max(1200), conversationId: z.string().min(10).max(40).optional() })

async function runner(path: string, data: unknown, timeout = 540_000) {
  const url = runnerUrl.value()
  if (!url) throw new HttpsError('failed-precondition', 'Training runner is not configured.')
  const target = `${url}${path}`
  const client = await new GoogleAuth().getIdTokenClient(url)
  const response = await client.request({ url: target, method: 'POST', data, timeout })
  return response.data as Record<string, unknown>
}

async function enforceRateLimit(uid: string, action: string, limit: number, windowMs: number) {
  const ref = db.collection('rateLimits').doc(`${uid}_${action}`)
  await db.runTransaction(async transaction => {
    const snapshot = await transaction.get(ref)
    const now = Date.now(), value = snapshot.data(), windowStart = value?.windowStart?.toMillis?.() ?? 0
    if (!value || now - windowStart >= windowMs) {
      transaction.set(ref, { ownerId: uid, action, count: 1, windowStart: Timestamp.fromMillis(now), updatedAt: FieldValue.serverTimestamp() })
      return
    }
    if (Number(value.count) >= limit) throw new HttpsError('resource-exhausted', 'Please wait before making more requests.')
    transaction.update(ref, { count: FieldValue.increment(1), updatedAt: FieldValue.serverTimestamp() })
  })
}

export const startTraining = onCall({ region, timeoutSeconds: 30, memory: '256MiB', maxInstances: 20 }, async request => {
  if (!request.auth) throw new HttpsError('unauthenticated', 'Sign in before starting a training job.')
  const parsed = trainingInput.safeParse(request.data)
  if (!parsed.success) throw new HttpsError('invalid-argument', 'Only valid daily NSE/BSE research jobs are currently supported.')
  const uid = request.auth.uid
  const recent = await db.collection('trainingJobs').where('ownerId', '==', uid).where('createdAt', '>=', new Date(Date.now() - 60_000)).limit(3).get()
  if (recent.size >= 3) throw new HttpsError('resource-exhausted', 'Please wait before starting another training job.')
  const job = db.collection('trainingJobs').doc()
  const now = FieldValue.serverTimestamp()
  await db.runTransaction(async tx => {
    tx.create(job, { ...parsed.data, ownerId: uid, status: 'queued', stage: 'awaiting_runner', progress: 0, createdAt: now, updatedAt: now, schemaVersion: 2 })
    tx.create(db.collection('auditEvents').doc(), { ownerId: uid, actorId: uid, action: 'training.requested', targetId: job.id, createdAt: now, metadata: { symbol: parsed.data.symbol, exchange: parsed.data.exchange } })
  })
  return { jobId: job.id, status: 'queued' }
})

export const dispatchTraining = onDocumentCreated({ document: 'trainingJobs/{jobId}', region, timeoutSeconds: 540, memory: '512MiB', maxInstances: 4, retry: false }, async event => {
  const snapshot = event.data
  if (!snapshot) return
  if (!runnerUrl.value()) {
    await snapshot.ref.update({ status: 'blocked', stage: 'configuration_required', errorCode: 'TRAINING_RUNNER_NOT_CONFIGURED', updatedAt: FieldValue.serverTimestamp() })
    return
  }
  try {
    await snapshot.ref.update({ status: 'running', stage: 'provider_validation', progress: 1, dispatchedAt: FieldValue.serverTimestamp(), updatedAt: FieldValue.serverTimestamp() })
    await runner('/', { jobId: snapshot.id, ...snapshot.data() })
  } catch (error) {
    logger.error('Training dispatch failed', { jobId: snapshot.id, error })
    const latest = (await snapshot.ref.get()).data()
    if (latest?.status !== 'failed') await snapshot.ref.update({ status: 'failed', stage: 'dispatch_failed', errorCode: 'RUNNER_UNAVAILABLE', updatedAt: FieldValue.serverTimestamp() })
  }
})

export const generateSignal = onCall({ region, timeoutSeconds: 120, memory: '256MiB', maxInstances: 10 }, async request => {
  if (!request.auth) throw new HttpsError('unauthenticated', 'Sign in first.')
  await enforceRateLimit(request.auth.uid, 'signal', 5, 60_000)
  const parsed = signalInput.safeParse(request.data)
  if (!parsed.success) throw new HttpsError('invalid-argument', 'Invalid signal request.')
  const model = await db.collection('models').doc(parsed.data.modelId).get()
  if (!model.exists || model.data()?.ownerId !== request.auth.uid) throw new HttpsError('not-found', 'Model not found.')
  try {
    return await runner('/signal', { ownerId: request.auth.uid, ...parsed.data }, 120_000)
  } catch (error) {
    logger.error('Signal generation failed', { modelId: parsed.data.modelId, error })
    throw new HttpsError('failed-precondition', 'A verified signal could not be generated.')
  }
})

export const createPaperTradeFromSignal = onCall({ region, timeoutSeconds: 30, memory: '256MiB' }, async request => {
  if (!request.auth) throw new HttpsError('unauthenticated', 'Sign in first.')
  const signalId = z.string().min(10).max(40).safeParse(request.data?.signalId)
  if (!signalId.success) throw new HttpsError('invalid-argument', 'Invalid signal.')
  const signal = await db.collection('signals').doc(signalId.data).get()
  const value = signal.data()
  if (!signal.exists || value?.ownerId !== request.auth.uid) throw new HttpsError('not-found', 'Signal not found.')
  if (value?.action !== 'BUY' || !value.quantity) throw new HttpsError('failed-precondition', 'Only a validated BUY signal can enter paper trading.')
  const duplicate = await db.collection('paperTrades').where('ownerId', '==', request.auth.uid).where('signalId', '==', signal.id).limit(1).get()
  if (!duplicate.empty) return { tradeId: duplicate.docs[0].id, status: duplicate.docs[0].data().status }
  const trade = db.collection('paperTrades').doc()
  await trade.create({
    ownerId: request.auth.uid, signalId: signal.id, modelId: value.modelId, modelVersion: value.modelVersion,
    symbol: value.symbol, exchange: value.exchange, entry: value.entry, stop: value.stop, target: value.target,
    quantity: value.quantity, confidence: value.confidence, status: 'planned', source: 'advisor',
    createdAt: FieldValue.serverTimestamp(), updatedAt: FieldValue.serverTimestamp(),
  })
  return { tradeId: trade.id, status: 'planned' }
})

function plain(value: unknown): unknown {
  if (value instanceof Timestamp) return value.toDate().toISOString()
  if (Array.isArray(value)) return value.map(plain)
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value as Record<string, unknown>).map(([key, item]) => [key, plain(item)]))
  return value
}

async function advisorTool(uid: string, modelId: string, name: string, args: Record<string, unknown>) {
  const modelSnapshot = await db.collection('models').doc(modelId).get()
  const model = modelSnapshot.data()
  if (!modelSnapshot.exists || model?.ownerId !== uid) return { error: 'MODEL_NOT_FOUND' }
  const latestQuery = () => db.collection('signals').where('ownerId', '==', uid).where('modelId', '==', modelId).orderBy('createdAt', 'desc').limit(1).get()
  if (name === 'get_model_card') return plain({ modelId, ...model })
  if (name === 'get_backtest_evidence') return plain({ metrics: model.metrics, gates: model.gates, releaseStatus: model.releaseStatus, dataQuality: model.dataQuality })
  if (name === 'get_latest_signal') {
    const result = await latestQuery(); return result.empty ? { error: 'NO_SIGNAL' } : plain({ signalId: result.docs[0].id, ...result.docs[0].data() })
  }
  if (name === 'get_signal_history') {
    const limit = Math.max(1, Math.min(20, Number(args.limit ?? 10)))
    const result = await db.collection('signals').where('ownerId', '==', uid).where('modelId', '==', modelId).orderBy('createdAt', 'desc').limit(limit).get()
    return plain(result.docs.map(item => ({ signalId: item.id, ...item.data() })))
  }
  if (name === 'calculate_position_size') {
    const result = await latestQuery()
    if (result.empty) return { error: 'NO_SIGNAL' }
    const signal = result.docs[0].data(), capital = Math.max(1_000, Number(args.capital ?? 100_000)), riskPct = Math.max(.1, Math.min(5, Number(args.riskPct ?? 1)))
    const perShare = Math.abs(Number(signal.entry) - Number(signal.stop))
    const quantity = Math.floor(Math.min(capital / Number(signal.entry), capital * riskPct / 100 / Math.max(perShare, .01)))
    return { capital, riskPct, quantity, maximumRisk: quantity * perShare, entry: signal.entry, stop: signal.stop }
  }
  return { error: 'UNKNOWN_TOOL' }
}

const toolDeclarations = [
  { name: 'get_model_card', description: 'Get the selected trained model, release gates and immutable model card', parameters: { type: 'OBJECT', properties: {} } },
  { name: 'get_backtest_evidence', description: 'Get cost-adjusted locked-test metrics and approval gates', parameters: { type: 'OBJECT', properties: {} } },
  { name: 'get_latest_signal', description: 'Get the latest server-generated BUY SELL HOLD or ABSTAIN signal', parameters: { type: 'OBJECT', properties: {} } },
  { name: 'get_signal_history', description: 'Get recent signals for the selected model', parameters: { type: 'OBJECT', properties: { limit: { type: 'INTEGER', minimum: 1, maximum: 20 } } } },
  { name: 'calculate_position_size', description: 'Calculate risk-limited paper quantity from the latest signal', parameters: { type: 'OBJECT', properties: { capital: { type: 'NUMBER' }, riskPct: { type: 'NUMBER' } }, required: ['capital', 'riskPct'] } },
]

async function geminiAdvisor(uid: string, modelId: string, question: string): Promise<string> {
  const contents: Array<Record<string, unknown>> = [{ role: 'user', parts: [{ text: question }] }]
  const auth = new GoogleAuth({ scopes: ['https://www.googleapis.com/auth/cloud-platform'] })
  const accessToken = await auth.getAccessToken()
  const endpoint = `https://aiplatform.googleapis.com/v1/projects/${projectId}/locations/global/publishers/google/models/${geminiModel.value()}:generateContent`
  for (let turn = 0; turn < 4; turn++) {
    const response = await fetch(endpoint, { method: 'POST', headers: { Authorization: `Bearer ${accessToken}`, 'Content-Type': 'application/json' }, body: JSON.stringify({
      systemInstruction: { parts: [{ text: 'You are ArthAI, a cautious Indian equity research assistant. You must use the supplied tools for every factual model, signal, price, metric or quantity claim. Never invent values. Clearly say research-only, mention the data timestamp, distinguish paper trading from live trading, and explain ABSTAIN or failed gates directly. Be concise.' }] },
      contents, tools: [{ functionDeclarations: toolDeclarations }],
      toolConfig: { functionCallingConfig: { mode: turn === 0 ? 'ANY' : 'AUTO' } },
      generationConfig: { temperature: .15, maxOutputTokens: 900 },
    }) })
    if (!response.ok) throw new Error(`VERTEX_${response.status}`)
    const result = await response.json() as { candidates?: Array<{ content?: { role?: string; parts?: Array<{ text?: string; functionCall?: { name: string; args?: Record<string, unknown> } }> } }> }
    const content = result.candidates?.[0]?.content
    if (!content?.parts) throw new Error('VERTEX_EMPTY_RESPONSE')
    contents.push({ role: 'model', parts: content.parts })
    const calls = content.parts.filter(part => part.functionCall).map(part => part.functionCall!)
    if (!calls.length) return content.parts.map(part => part.text ?? '').join('').trim()
    const responses = await Promise.all(calls.map(async call => ({ functionResponse: { name: call.name, response: await advisorTool(uid, modelId, call.name, call.args ?? {}) } })))
    contents.push({ role: 'user', parts: responses })
  }
  throw new Error('VERTEX_TOOL_LOOP_LIMIT')
}

export const askAdvisor = onCall({ region, timeoutSeconds: 120, memory: '512MiB', maxInstances: 10 }, async request => {
  if (!request.auth) throw new HttpsError('unauthenticated', 'Sign in first.')
  await enforceRateLimit(request.auth.uid, 'advisor', 10, 60_000)
  const parsed = advisorInput.safeParse(request.data)
  if (!parsed.success) throw new HttpsError('invalid-argument', 'Invalid advisor question.')
  const uid = request.auth.uid
  try {
    const answer = await geminiAdvisor(uid, parsed.data.modelId, parsed.data.question)
    const conversation = parsed.data.conversationId ? db.collection('conversations').doc(parsed.data.conversationId) : db.collection('conversations').doc()
    const existing = await conversation.get()
    const messages = [
      { role: 'user', text: parsed.data.question, createdAt: new Date().toISOString() },
      { role: 'assistant', text: answer, createdAt: new Date().toISOString() },
    ]
    if (existing.exists && existing.data()?.ownerId === uid) await conversation.update({ messages: FieldValue.arrayUnion(...messages), updatedAt: FieldValue.serverTimestamp() })
    else if (!existing.exists) await conversation.create({ ownerId: uid, modelId: parsed.data.modelId, messages, createdAt: FieldValue.serverTimestamp(), updatedAt: FieldValue.serverTimestamp() })
    else throw new HttpsError('permission-denied', 'Conversation not found.')
    return { conversationId: conversation.id, answer }
  } catch (error) {
    logger.error('Advisor failed', { modelId: parsed.data.modelId, error })
    if (error instanceof HttpsError) throw error
    throw new HttpsError('unavailable', 'The grounded advisor is temporarily unavailable.')
  }
})

export const generateDailySignals = onSchedule({ region, schedule: '30 18 * * 1-5', timeZone: 'Asia/Kolkata', timeoutSeconds: 540, memory: '256MiB', retryCount: 0 }, async () => {
  try { await runner('/daily', {}, 540_000) }
  catch (error) { logger.error('Daily signal generation failed', { error }) }
})
