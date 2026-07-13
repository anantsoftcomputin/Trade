import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { createUserWithEmailAndPassword, GoogleAuthProvider, onAuthStateChanged, sendPasswordResetEmail, signInWithEmailAndPassword, signInWithPopup, signOut, updateProfile, type User } from 'firebase/auth'
import { Activity, ArrowRight, BarChart3, BrainCircuit, CircleUserRound, Eye, EyeOff, LockKeyhole, Mail, ShieldCheck } from 'lucide-react'
import { auth } from './firebase'
import { AuthContext } from './auth-context'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    const currentAuth = auth
    return currentAuth ? onAuthStateChanged(currentAuth, next => { setUser(next); setLoading(false) }) : void setLoading(false)
  }, [])
  const value = useMemo(() => {
    const currentAuth = auth
    return user && currentAuth ? { user, logout: () => signOut(currentAuth) } : null
  }, [user])
  if (loading) return <div className="auth-loading"><span><Activity size={25}/></span><b>Securing your workspace…</b></div>
  if (!auth) return <div className="auth-loading"><b>Firebase configuration is missing.</b></div>
  if (!value) return <AuthScreen />
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

function readableError(error: unknown) {
  const code = typeof error === 'object' && error && 'code' in error ? String(error.code) : ''
  if (code.includes('invalid-credential')) return 'The email or password is incorrect.'
  if (code.includes('email-already-in-use')) return 'An account already exists for this email.'
  if (code.includes('weak-password')) return 'Use at least 8 characters for your password.'
  if (code.includes('popup-closed')) return 'Google sign-in was cancelled.'
  if (code.includes('operation-not-allowed')) return 'This sign-in method must be enabled in Firebase Authentication.'
  return 'Sign-in could not be completed. Please try again.'
}

function AuthScreen() {
  const [mode, setMode] = useState<'signin'|'signup'>('signin')
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [show, setShow] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const submit = async () => {
    if (!auth || !email || !password) return setMessage('Enter your email and password.')
    setBusy(true); setMessage('')
    try {
      if (mode === 'signup') {
        const result = await createUserWithEmailAndPassword(auth, email.trim(), password)
        if (name.trim()) await updateProfile(result.user, { displayName: name.trim() })
      } else await signInWithEmailAndPassword(auth, email.trim(), password)
    } catch (e) { setMessage(readableError(e)) } finally { setBusy(false) }
  }
  const google = async () => {
    if (!auth) return
    setBusy(true); setMessage('')
    try { await signInWithPopup(auth, new GoogleAuthProvider()) } catch (e) { setMessage(readableError(e)) } finally { setBusy(false) }
  }
  const reset = async () => {
    if (!auth || !email) return setMessage('Enter your email first, then choose reset password.')
    try { await sendPasswordResetEmail(auth, email.trim()); setMessage('Password reset email sent.') } catch (e) { setMessage(readableError(e)) }
  }
  return <div className="auth-page"><section className="auth-story"><div className="auth-brand"><span><Activity size={22}/></span>Arth<i>AI</i></div><div className="auth-copy"><span className="eyebrow">MARKET INTELLIGENCE, WITH EVIDENCE</span><h1>Research the signal.<br/>Respect the risk.</h1><p>Train, validate and question stock models in one disciplined workspace built for Indian markets.</p><div className="auth-points"><div><BrainCircuit/><span><b>Models that can abstain</b><small>Walk-forward tested, regime-aware and cost-adjusted.</small></span></div><div><BarChart3/><span><b>Evidence behind every signal</b><small>Inspect assumptions, stability and downside before acting.</small></span></div><div><ShieldCheck/><span><b>Paper trading comes first</b><small>Live automation remains locked behind explicit risk gates.</small></span></div></div></div><small>Research software · Not investment advice</small></section><section className="auth-form-wrap"><div className="auth-form"><span className="auth-lock"><LockKeyhole size={20}/></span><h2>{mode==='signin'?'Welcome back':'Create your workspace'}</h2><p>{mode==='signin'?'Sign in to continue to ArthAI.':'Start with a secure personal research workspace.'}</p>{mode==='signup'&&<label>Full name<div><input value={name} onChange={e=>setName(e.target.value)} autoComplete="name" placeholder="Jigar Desai"/></div></label>}<label>Email<div><Mail size={16}/><input value={email} onChange={e=>setEmail(e.target.value)} type="email" autoComplete="email" placeholder="you@example.com"/></div></label><label>Password<div><LockKeyhole size={16}/><input value={password} onChange={e=>setPassword(e.target.value)} type={show?'text':'password'} autoComplete={mode==='signin'?'current-password':'new-password'} placeholder="At least 8 characters"/><button onClick={()=>setShow(!show)}>{show?<EyeOff size={16}/>:<Eye size={16}/>}</button></div></label>{mode==='signin'&&<button className="forgot" onClick={reset}>Forgot password?</button>}{message&&<div className="auth-message">{message}</div>}<button className="primary auth-submit" disabled={busy} onClick={submit}>{busy?'Please wait…':mode==='signin'?'Sign in':'Create account'} {!busy&&<ArrowRight size={17}/>}</button><div className="or"><i/>OR<i/></div><button className="google-btn" disabled={busy} onClick={google}><CircleUserRound size={17}/> Continue with Google</button><div className="auth-switch">{mode==='signin'?"New to ArthAI? ":'Already have an account? '}<button onClick={()=>{setMode(mode==='signin'?'signup':'signin');setMessage('')}}>{mode==='signin'?'Create account':'Sign in'}</button></div></div></section></div>
}
