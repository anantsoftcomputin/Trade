import { addDoc, collection, onSnapshot, orderBy, query, serverTimestamp, where, type Unsubscribe } from 'firebase/firestore'
import { httpsCallable } from 'firebase/functions'
import { db, functions } from './firebase'

export type TrainingJob = { id: string; symbol: string; exchange: string; status: string; progress: number; stage: string; createdAt?: unknown; error?: string }

export async function startTrainingJob(input: { symbol: string; exchange: 'NSE'|'BSE'; historyYears: number; timeframe: '1d'|'1h'|'15m' }) {
  if (!functions) throw new Error('Firebase Functions is not configured')
  const call = httpsCallable<typeof input, { jobId: string; status: string }>(functions, 'startTraining')
  return (await call(input)).data
}

export function watchTrainingJobs(uid: string, callback: (jobs: TrainingJob[]) => void): Unsubscribe {
  if (!db) return () => undefined
  const q = query(collection(db, 'trainingJobs'), where('ownerId', '==', uid), orderBy('createdAt', 'desc'))
  return onSnapshot(q, snapshot => callback(snapshot.docs.map(d => ({ id: d.id, ...d.data() } as TrainingJob))), error => console.error('Training subscription failed', error))
}

export async function createPaperTrade(uid: string, trade: { symbol: string; exchange: string; entry: number; stop: number; target: number; quantity: number; confidence: number; modelVersion: string }) {
  if (!db) throw new Error('Firestore is not configured')
  return addDoc(collection(db, 'paperTrades'), { ...trade, ownerId: uid, status: 'planned', source: 'advisor', createdAt: serverTimestamp(), updatedAt: serverTimestamp() })
}
