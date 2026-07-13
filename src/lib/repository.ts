import { collection, onSnapshot, orderBy, query, where, type Unsubscribe } from 'firebase/firestore'
import { httpsCallable } from 'firebase/functions'
import { db, functions } from './firebase'

export type TrainingJob = {
  id: string; symbol: string; exchange: 'NSE'|'BSE'; status: string; progress: number; stage: string
  modelId?: string; releaseStatus?: string; errorCode?: string; metrics?: Record<string, number>; createdAt?: unknown
}

export type ResearchModel = {
  id: string; symbol: string; exchange: 'NSE'|'BSE'; architecture: string; releaseStatus: 'paper_approved'|'rejected'
  dataQuality: string; productionEligible: false; metrics: Record<string, number>; gates: Record<string, boolean>
  selectedFeatures: string[]; candidate: { lookback: number; threshold: number; stop_atr: number; target_atr: number }
  trainedAt?: unknown
}

export type TradingSignal = {
  id: string; modelId: string; symbol: string; exchange: 'NSE'|'BSE'; action: 'BUY'|'SELL'|'HOLD'|'ABSTAIN'
  entry: number; stop: number; target: number; quantity: number; confidence: number; expectedReturn: number
  predictedVolatility: number; reason: string; dataQuality: string; releaseStatus: string
  evidence: Record<string, number>; asOf?: unknown; createdAt?: unknown
}

export type PaperTrade = {
  id: string; signalId: string; modelId: string; symbol: string; exchange: string; entry: number; stop: number
  target: number; quantity: number; confidence: number; status: 'planned'|'open'|'closed'|'cancelled'
  lastPrice?: number; exit?: number; pnl?: number; outcome?: string; createdAt?: unknown
}

export type Conversation = { id: string; modelId: string; messages: Array<{ role: 'user'|'assistant'; text: string; createdAt: string }>; updatedAt?: unknown }

function watchOwnerCollection<T extends { id: string }>(name: string, uid: string, dateField: string, callback: (items: T[]) => void): Unsubscribe {
  if (!db) return () => undefined
  const statement = query(collection(db, name), where('ownerId', '==', uid), orderBy(dateField, 'desc'))
  return onSnapshot(statement, snapshot => callback(snapshot.docs.map(item => ({ id: item.id, ...item.data() } as T))), error => console.error(`${name} subscription failed`, error))
}

export async function startTrainingJob(input: { symbol: string; exchange: 'NSE'|'BSE'; historyYears: number; timeframe: '1d' }) {
  if (!functions) throw new Error('Firebase Functions is not configured')
  const call = httpsCallable<typeof input, { jobId: string; status: string }>(functions, 'startTraining')
  return (await call(input)).data
}

export const watchTrainingJobs = (uid: string, callback: (items: TrainingJob[]) => void) => watchOwnerCollection<TrainingJob>('trainingJobs', uid, 'createdAt', callback)
export const watchModels = (uid: string, callback: (items: ResearchModel[]) => void) => watchOwnerCollection<ResearchModel>('models', uid, 'trainedAt', callback)
export const watchSignals = (uid: string, callback: (items: TradingSignal[]) => void) => watchOwnerCollection<TradingSignal>('signals', uid, 'createdAt', callback)
export const watchPaperTrades = (uid: string, callback: (items: PaperTrade[]) => void) => watchOwnerCollection<PaperTrade>('paperTrades', uid, 'createdAt', callback)
export const watchConversations = (uid: string, callback: (items: Conversation[]) => void) => watchOwnerCollection<Conversation>('conversations', uid, 'updatedAt', callback)

export async function requestSignal(input: { modelId: string; capital: number; riskPct: number }) {
  if (!functions) throw new Error('Firebase Functions is not configured')
  const call = httpsCallable<typeof input, TradingSignal>(functions, 'generateSignal')
  return (await call(input)).data
}

export async function askAdvisor(input: { modelId: string; question: string; conversationId?: string }) {
  if (!functions) throw new Error('Firebase Functions is not configured')
  const call = httpsCallable<typeof input, { conversationId: string; answer: string }>(functions, 'askAdvisor')
  return (await call(input)).data
}

export async function addSignalToPaper(signalId: string) {
  if (!functions) throw new Error('Firebase Functions is not configured')
  const call = httpsCallable<{ signalId: string }, { tradeId: string; status: string }>(functions, 'createPaperTradeFromSignal')
  return (await call({ signalId })).data
}
