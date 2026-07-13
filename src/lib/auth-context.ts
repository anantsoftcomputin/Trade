import { createContext, useContext } from 'react'
import type { User } from 'firebase/auth'

export type AuthValue = { user: User; logout: () => Promise<void> }
export const AuthContext = createContext<AuthValue | null>(null)

export function useAuth() {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used inside AuthProvider')
  return value
}
