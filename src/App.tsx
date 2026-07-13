import { useEffect, useMemo, useState } from 'react'
import { Activity, Bot, BrainCircuit, ChevronRight, FlaskConical, LayoutDashboard, LogOut, Menu, MessageSquareText, Plus, RefreshCw, Send, ShieldCheck, X } from 'lucide-react'
import clsx from 'clsx'
import { useAuth } from './lib/auth-context'
import {
  addSignalToPaper, askAdvisor, requestSignal, startTrainingJob, watchConversations,
  watchModels, watchPaperTrades, watchSignals, watchTrainingJobs,
  type Conversation, type PaperTrade, type ResearchModel, type TradingSignal, type TrainingJob,
} from './lib/repository'
import './App.css'

type Page = 'overview'|'models'|'advisor'|'paper'
const nav = [
  { id: 'overview', label: 'Overview', icon: LayoutDashboard },
  { id: 'models', label: 'Model lab', icon: BrainCircuit },
  { id: 'advisor', label: 'AI advisor', icon: MessageSquareText },
  { id: 'paper', label: 'Paper monitor', icon: FlaskConical },
] as const

const money = (value?: number) => value == null ? '—' : `₹${value.toLocaleString('en-IN', { maximumFractionDigits: 2 })}`
const percent = (value?: number) => value == null || !Number.isFinite(value) ? '—' : `${value >= 0 ? '+' : ''}${(value * 100).toFixed(1)}%`
const dateText = (value: unknown) => {
  if (!value) return 'Pending'
  const source = value as { toDate?: () => Date; seconds?: number }
  const date = source.toDate?.() ?? (source.seconds ? new Date(source.seconds * 1000) : new Date(String(value)))
  return Number.isNaN(date.getTime()) ? 'Pending' : date.toLocaleString('en-IN')
}

function App() {
  const { user, logout } = useAuth()
  const [page, setPage] = useState<Page>('overview')
  const [models, setModels] = useState<ResearchModel[]>([])
  const [signals, setSignals] = useState<TradingSignal[]>([])
  const [trades, setTrades] = useState<PaperTrade[]>([])
  const [jobs, setJobs] = useState<TrainingJob[]>([])
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [selectedModelId, setSelectedModelId] = useState('')
  const [trainOpen, setTrainOpen] = useState(false)
  const [mobileNav, setMobileNav] = useState(false)
  const [toast, setToast] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    const unsubscribers = [watchModels(user.uid, setModels), watchSignals(user.uid, setSignals), watchPaperTrades(user.uid, setTrades), watchTrainingJobs(user.uid, setJobs), watchConversations(user.uid, setConversations)]
    return () => unsubscribers.forEach(unsubscribe => unsubscribe())
  }, [user.uid])
  useEffect(() => { if (!selectedModelId && models[0]) setSelectedModelId(models[0].id) }, [models, selectedModelId])
  useEffect(() => { if (!toast) return; const timer = setTimeout(() => setToast(''), 3500); return () => clearTimeout(timer) }, [toast])

  const selectedModel = models.find(model => model.id === selectedModelId)
  const selectedSignal = signals.find(signal => signal.modelId === selectedModelId)
  const selectedConversation = conversations.find(item => item.modelId === selectedModelId)
  const notify = (message: string) => { setError(''); setToast(message) }
  const fail = (reason: unknown) => setError(reason instanceof Error ? reason.message : 'The request failed safely.')

  return <div className="app-shell">
    <aside className={clsx('sidebar', mobileNav && 'open')}>
      <div className="brand"><span className="brand-mark"><Activity size={21}/></span><div>Arth<span>AI</span></div></div>
      <button className="icon-btn mobile-close" onClick={() => setMobileNav(false)}><X size={20}/></button>
      <nav>{nav.map(item => <button key={item.id} className={clsx(page === item.id && 'active')} onClick={() => { setPage(item.id); setMobileNav(false) }}><item.icon size={18}/><span>{item.label}</span>{item.id === 'advisor' && <i>AI</i>}</button>)}</nav>
      <div className="sidebar-status"><div><span className="status-dot"/> Research services online</div><small>Yahoo daily · paper only</small></div>
      <div className="profile"><span>{(user.displayName || user.email || 'U').slice(0, 2).toUpperCase()}</span><div><b>{user.displayName || 'Researcher'}</b><small>{user.email}</small></div><button title="Sign out" onClick={logout}><LogOut size={14}/></button></div>
    </aside>
    <main>
      <header className="topbar">
        <button className="icon-btn menu-btn" onClick={() => setMobileNav(true)}><Menu size={20}/></button>
        <div className="workspace-title"><b>Research workspace</b><span>Cost-aware · walk-forward validated</span></div>
        <div className="market-state"><span className="live-dot"/> End-of-day research</div>
        <button className="primary compact" onClick={() => setTrainOpen(true)}><Plus size={17}/> Train stock</button>
      </header>
      <div className="page-wrap">
        {page === 'overview' && <Overview models={models} signals={signals} trades={trades} jobs={jobs} onTrain={() => setTrainOpen(true)} onOpen={(modelId, target) => { setSelectedModelId(modelId); setPage(target) }}/>}
        {page === 'models' && <Models models={models} jobs={jobs} selected={selectedModelId} onSelect={setSelectedModelId} onTrain={() => setTrainOpen(true)} onAdvisor={modelId => { setSelectedModelId(modelId); setPage('advisor') }}/>}
        {page === 'advisor' && <Advisor models={models} model={selectedModel} signal={selectedSignal} conversation={selectedConversation} onSelect={setSelectedModelId} notify={notify} fail={fail}/>}
        {page === 'paper' && <PaperMonitor trades={trades}/>}
      </div>
    </main>
    {trainOpen && <TrainModal jobs={jobs} onClose={() => setTrainOpen(false)} notify={notify} fail={fail}/>}
    {toast && <div className="toast"><ShieldCheck size={18}/>{toast}</div>}
    {error && <div className="toast error-toast"><X size={18}/>{error}</div>}
  </div>
}

function Overview({ models, signals, trades, jobs, onTrain, onOpen }: { models: ResearchModel[]; signals: TradingSignal[]; trades: PaperTrade[]; jobs: TrainingJob[]; onTrain: () => void; onOpen: (id: string, page: Page) => void }) {
  const activeJobs = jobs.filter(job => ['queued', 'running'].includes(job.status))
  const approved = models.filter(model => model.releaseStatus === 'paper_approved')
  const openTrades = trades.filter(trade => trade.status === 'open')
  return <>
    <section className="welcome"><div><span className="eyebrow">RESEARCH CONTROL CENTER</span><h1>Your evidence-backed stock models</h1><p>Daily Yahoo research data · NIFTY benchmark · statutory costs · locked unseen tests.</p></div><button className="primary" onClick={onTrain}><BrainCircuit size={17}/> New training run</button></section>
    <section className="metric-grid summary-metrics">
      <Metric label="Trained models" value={String(models.length)} detail={`${approved.length} paper-approved`}/>
      <Metric label="Latest signals" value={String(signals.length)} detail="Server generated"/>
      <Metric label="Open paper trades" value={String(openTrades.length)} detail="Marked after daily signals"/>
      <Metric label="Active jobs" value={String(activeJobs.length)} detail="Cloud Run walk-forward"/>
    </section>
    <div className="section-head"><div><h2>Model fleet</h2><p>Real Firestore model registry</p></div></div>
    {!models.length ? <Empty title="No trained models yet" detail="Start with an NSE or BSE symbol. Training performs GA search and a locked test before registration." action="Train first stock" onAction={onTrain}/> : <section className="stock-grid real-model-grid">{models.slice(0, 8).map(model => {
      const signal = signals.find(item => item.modelId === model.id)
      return <button className="stock-card" key={model.id} onClick={() => onOpen(model.id, 'models')}><div className="stock-top"><span className="stock-avatar">{model.symbol.slice(0, 2)}</span><div><b>{model.symbol}</b><span>{model.exchange} · Daily</span></div><em className={model.releaseStatus === 'paper_approved' ? 'ready' : 'review'}>{model.releaseStatus === 'paper_approved' ? 'Paper ready' : 'Rejected'}</em></div><div className="stock-price"><strong>{signal?.action ?? 'NO SIGNAL'}</strong><span className={signal?.action === 'BUY' ? 'good' : signal?.action === 'SELL' ? 'bad' : ''}>{signal ? `${signal.confidence}%` : 'Awaiting run'}</span></div><div className="quality"><span>Locked net return</span><b>{percent(model.metrics.net_return)}</b><div><i style={{ width: `${Math.max(0, Math.min(100, (model.metrics.profit_factor || 0) * 40))}%` }}/></div></div></button>
    })}</section>}
    {!!activeJobs.length && <section className="panel job-panel"><div className="panel-head"><div><span className="eyebrow">ACTIVE TRAINING</span><h2>Cloud research jobs</h2></div></div>{activeJobs.map(job => <div className="job-row" key={job.id}><div><b>{job.symbol}</b><span>{job.stage.replaceAll('_', ' ')}</span></div><div className="progress-bar"><i style={{ width: `${job.progress}%` }}/></div><strong>{job.progress}%</strong></div>)}</section>}
  </>
}

function Models({ models, jobs, selected, onSelect, onTrain, onAdvisor }: { models: ResearchModel[]; jobs: TrainingJob[]; selected: string; onSelect: (id: string) => void; onTrain: () => void; onAdvisor: (id: string) => void }) {
  const model = models.find(item => item.id === selected) ?? models[0]
  return <><div className="page-title"><div><span className="eyebrow">MODEL REGISTRY</span><h1>Walk-forward research models</h1><p>Immutable artifacts, cost-adjusted metrics and explicit release gates.</p></div><button className="primary" onClick={onTrain}><Plus size={17}/> New training run</button></div>
    {!model ? <Empty title="The registry is empty" detail={jobs[0]?.status === 'running' ? `${jobs[0].symbol} is currently training at ${jobs[0].progress}%` : 'Train a stock to create the first model card.'} action="Train stock" onAction={onTrain}/> : <div className="registry-layout"><aside className="model-list">{models.map(item => <button className={clsx(item.id === model.id && 'selected')} key={item.id} onClick={() => onSelect(item.id)}><span className="stock-avatar">{item.symbol.slice(0, 2)}</span><div><b>{item.symbol}</b><small>{dateText(item.trainedAt)}</small></div><em className={item.releaseStatus === 'paper_approved' ? 'good' : 'bad'}>{item.releaseStatus === 'paper_approved' ? 'APPROVED' : 'REJECTED'}</em></button>)}</aside><section className="model-detail"><section className="model-hero"><div><span className="eyebrow">RELEASE DECISION</span><h2>{model.releaseStatus === 'paper_approved' ? 'Approved for paper trading' : 'Rejected by validation gates'}</h2><p>{model.symbol} uses {model.architecture}. Yahoo-derived models are never eligible for automated live trading.</p><button className="primary" onClick={() => onAdvisor(model.id)}><Bot size={17}/> Open grounded advisor</button></div><div className="readiness"><span>GATES PASSED</span><strong>{Object.values(model.gates).filter(Boolean).length}<small>/{Object.keys(model.gates).length}</small></strong><small>{model.dataQuality.replaceAll('_', ' ')}</small></div></section>
      <div className="section-head"><div><h2>Locked-test scorecard</h2><p>Includes statutory costs and estimated slippage</p></div></div><section className="metric-grid"><Metric label="Net return" value={percent(model.metrics.net_return)} detail="After costs"/><Metric label="Maximum drawdown" value={percent(model.metrics.max_drawdown)} detail="Limit −25%"/><Metric label="Profit factor" value={(model.metrics.profit_factor ?? 0).toFixed(2)} detail={`${model.metrics.trades ?? 0} trades`}/><Metric label="Profitable months" value={percent(model.metrics.profitable_months)} detail="Locked test"/><Metric label="Win rate" value={percent(model.metrics.win_rate)} detail="Not an approval gate"/><Metric label="Payoff ratio" value={(model.metrics.payoff_ratio ?? 0).toFixed(2)} detail="Average win / loss"/><Metric label="Buy & hold" value={percent(model.metrics.buy_hold_return)} detail="Same test period"/><Metric label="Excess return" value={percent(model.metrics.excess_return)} detail="Versus holding"/></section>
      <section className="two-col model-cols"><div className="panel"><div className="panel-head"><div><span className="eyebrow">APPROVAL GATES</span><h2>Fail-closed decision</h2></div></div><div className="gate-list">{Object.entries(model.gates).map(([name, passed]) => <div key={name}><span>{name.replace(/([A-Z])/g, ' $1')}</span><b className={passed ? 'good' : 'bad'}>{passed ? 'PASS' : 'FAIL'}</b></div>)}</div></div><div className="panel architecture"><div className="panel-head"><div><span className="eyebrow">CHAMPION</span><h2>GA-selected policy</h2></div></div><div className="specs"><div><span>Lookback</span><b>{model.candidate.lookback} days</b></div><div><span>Threshold</span><b>{(model.candidate.threshold * 100).toFixed(0)}%</b></div><div><span>Stop</span><b>{model.candidate.stop_atr.toFixed(1)} ATR</b></div><div><span>Target</span><b>{model.candidate.target_atr.toFixed(1)} ATR</b></div></div><p>{model.selectedFeatures.length} selected point-in-time features. The locked period was excluded from GA selection.</p></div></section></section></div>}
  </>
}

function Advisor({ models, model, signal, conversation, onSelect, notify, fail }: { models: ResearchModel[]; model?: ResearchModel; signal?: TradingSignal; conversation?: Conversation; onSelect: (id: string) => void; notify: (s: string) => void; fail: (e: unknown) => void }) {
  const [question, setQuestion] = useState('')
  const [busy, setBusy] = useState(false)
  const [localMessages, setLocalMessages] = useState<Array<{ role: 'user'|'assistant'; text: string }>>([])
  const messages = useMemo(() => conversation?.messages ?? localMessages, [conversation, localMessages])
  useEffect(() => setLocalMessages([]), [model?.id])
  const ask = async (text = question) => {
    if (!model || !text.trim() || busy) return
    setBusy(true); setQuestion(''); setLocalMessages(previous => [...previous, { role: 'user', text }])
    try {
      const result = await askAdvisor({ modelId: model.id, question: text, conversationId: conversation?.id })
      setLocalMessages(previous => [...previous, { role: 'assistant', text: result.answer }])
    } catch (error) { fail(error) } finally { setBusy(false) }
  }
  const refresh = async () => {
    if (!model) return
    setBusy(true); try { await requestSignal({ modelId: model.id, capital: 100_000, riskPct: 1 }); notify('Fresh daily signal generated') } catch (error) { fail(error) } finally { setBusy(false) }
  }
  const paper = async () => {
    if (!signal) return
    try { await addSignalToPaper(signal.id); notify('Signal added to paper monitoring') } catch (error) { fail(error) }
  }
  if (!model) return <Empty title="Train a stock before opening the advisor" detail="The advisor only discusses registered models and server-generated evidence." action="Open Model Lab" onAction={() => undefined}/>
  return <div className="advisor-layout real-advisor"><section className="advisor-main"><div className="advisor-head"><span className="ai-orb"><Bot size={24}/></span><div><h1>Grounded research advisor</h1><p>Gemini can only answer through server tools for the selected model.</p></div><select value={model.id} onChange={event => onSelect(event.target.value)}>{models.map(item => <option value={item.id} key={item.id}>{item.symbol} · {item.releaseStatus.replace('_', ' ')}</option>)}</select></div>
    {signal ? <section className="ai-answer signal-card"><div className="answer-title"><span className={clsx('buy-badge', signal.action === 'SELL' && 'sell', signal.action === 'ABSTAIN' && 'abstain')}>{signal.action}</span><span>{signal.symbol} · {signal.dataQuality.replaceAll('_', ' ')}</span><strong>{signal.confidence}% confidence</strong></div><h2>{signal.reason}</h2><div className="trade-ticket"><div><span>ENTRY</span><b>{money(signal.entry)}</b></div><div><span>STOP</span><b>{money(signal.stop)}</b></div><div><span>TARGET</span><b>{money(signal.target)}</b></div><div><span>QUANTITY</span><b>{signal.quantity || '—'}</b></div></div><div className="risk-note"><ShieldCheck size={18}/><p><b>As of {dateText(signal.asOf)}</b>Research-only signal. No live order will be placed.</p></div><div className="answer-actions"><button className="secondary-btn" onClick={refresh} disabled={busy}><RefreshCw size={16}/> Refresh signal</button>{signal.action === 'BUY' && <button className="primary" onClick={paper}>Add paper trade</button>}</div></section> : <section className="panel no-signal"><h2>No signal generated yet</h2><p>Generate an end-of-day signal from this registered model.</p><button className="primary" onClick={refresh} disabled={busy}><RefreshCw size={16}/> Generate signal</button></section>}
    <div className="conversation real-conversation">{messages.map((message, index) => <div key={`${index}-${message.text.slice(0, 10)}`} className={message.role === 'user' ? 'user-bubble' : 'ai-answer compact-answer'}>{message.text}</div>)}{busy && <div className="ai-answer compact-answer"><div className="typing"><i/><i/><i/></div>Checking the model registry and server evidence…</div>}</div>
    <div className="composer"><div className="quick-prompts"><button onClick={() => ask('Explain the latest signal and what could invalidate it.')}>Explain signal</button><button onClick={() => ask('Show performance and drawdown in bear markets.')}>Bear-market evidence</button><button onClick={() => ask('Calculate quantity for ₹2 lakh at 1% risk.')}>Size ₹2 lakh</button></div><form onSubmit={event => { event.preventDefault(); void ask() }}><MessageSquareText size={18}/><input value={question} onChange={event => setQuestion(event.target.value)} placeholder={`Ask about ${model.symbol}, risk or evidence…`}/><button disabled={busy || !question.trim()}><Send size={18}/></button></form><small>Server-grounded research only. Verify important decisions independently.</small></div></section><aside className="context-panel"><span className="eyebrow">SELECTED MODEL</span><h3>{model.symbol} · {model.exchange}</h3><div><ShieldCheck size={15}/><span>{model.architecture}</span></div><div><ShieldCheck size={15}/><span>{model.selectedFeatures.length} selected features</span></div><div><ShieldCheck size={15}/><span>{model.releaseStatus.replace('_', ' ')}</span></div><hr/><h3>Limitations</h3><p>Daily Yahoo research data is not a live exchange feed. Intraday answers and automated orders remain disabled.</p></aside></div>
}

function PaperMonitor({ trades }: { trades: PaperTrade[] }) {
  const totalPnl = trades.reduce((sum, trade) => sum + (trade.pnl ?? 0), 0)
  return <><div className="page-title"><div><span className="eyebrow">PAPER PORTFOLIO</span><h1>Forward monitoring</h1><p>Planned entries, open positions and closed outcomes marked from daily candles.</p></div><div className={totalPnl >= 0 ? 'headline-price good' : 'headline-price bad'}><strong>{money(totalPnl)}</strong><span>Realized P&amp;L</span></div></div>{!trades.length ? <Empty title="No paper trades" detail="A paper-approved BUY signal can be added from the grounded advisor."/> : <section className="panel paper-table"><div className="paper-head"><span>STOCK</span><span>STATUS</span><span>ENTRY</span><span>LAST / EXIT</span><span>QUANTITY</span><span>P&amp;L</span></div>{trades.map(trade => <div className="paper-row" key={trade.id}><b>{trade.symbol}<small>{trade.exchange}</small></b><em className={trade.status}>{trade.status}</em><span>{money(trade.entry)}</span><span>{money(trade.exit ?? trade.lastPrice)}</span><span>{trade.quantity}</span><strong className={(trade.pnl ?? 0) >= 0 ? 'good' : 'bad'}>{trade.pnl == null ? '—' : money(trade.pnl)}</strong></div>)}</section>}</>
}

function TrainModal({ jobs, onClose, notify, fail }: { jobs: TrainingJob[]; onClose: () => void; notify: (s: string) => void; fail: (e: unknown) => void }) {
  const [symbol, setSymbol] = useState('RELIANCE'), [exchange, setExchange] = useState<'NSE'|'BSE'>('NSE'), [years, setYears] = useState(8), [jobId, setJobId] = useState(''), [submitting, setSubmitting] = useState(false)
  const job = jobs.find(item => item.id === jobId)
  const active = submitting || (job && ['queued', 'running'].includes(job.status))
  const begin = async () => {
    setSubmitting(true)
    try { const result = await startTrainingJob({ symbol: symbol.trim().toUpperCase(), exchange, historyYears: years, timeframe: '1d' }); setJobId(result.jobId); notify(`${symbol.toUpperCase()} training queued`) }
    catch (error) { fail(error) } finally { setSubmitting(false) }
  }
  return <div className="modal-layer"><div className="train-modal"><button className="icon-btn modal-x" onClick={() => !active && onClose()}><X size={19}/></button><span className="modal-icon"><BrainCircuit size={25}/></span><span className="eyebrow">NEW WALK-FORWARD RUN</span><h2>Train a daily stock model</h2><p>Yahoo research candles, NIFTY benchmark, Indian costs, GA selection and a locked unseen test.</p>{job ? <div className="training-state"><div className="brain-pulse"><BrainCircuit size={38}/></div><h3>{job.stage.replaceAll('_', ' ')}</h3><p>{job.status === 'completed' ? `Model ${job.releaseStatus?.replace('_', ' ')}` : job.status === 'failed' ? job.errorCode : 'Processing continues securely in Cloud Run'}</p><div className="progress-bar"><i style={{ width: `${job.progress}%` }}/></div><b>{job.progress}%</b>{['completed', 'failed'].includes(job.status) && <button className="primary full" onClick={onClose}>Close</button>}</div> : <><label>Symbol<input className="real-input" value={symbol} onChange={event => setSymbol(event.target.value.toUpperCase())} maxLength={24} placeholder="RELIANCE"/></label><div className="form-grid"><label>Exchange<select value={exchange} onChange={event => setExchange(event.target.value as 'NSE'|'BSE')}><option>NSE</option><option>BSE</option></select></label><label>History<select value={years} onChange={event => setYears(Number(event.target.value))}><option value={5}>5 years</option><option value={8}>8 years</option><option value={12}>12 years</option><option value={20}>20 years</option></select></label></div><div className="pipeline"><span>1<br/><small>Data + costs</small></span><i/><span>2<br/><small>ANN + GA</small></span><i/><span>3<br/><small>Walk-forward</small></span><i/><span>4<br/><small>Lock &amp; gate</small></span></div><div className="legal-note"><ShieldCheck size={17}/>Yahoo models are research-only and can qualify only for paper trading.</div><button className="primary full" disabled={!symbol.trim() || submitting} onClick={begin}>Start training pipeline <ChevronRight size={17}/></button></>}</div></div>
}

function Metric({ label, value, detail }: { label: string; value: string; detail: string }) { return <div className="metric"><span>{label}</span><strong>{value}</strong><small>{detail}</small></div> }
function Empty({ title, detail, action, onAction }: { title: string; detail: string; action?: string; onAction?: () => void }) { return <section className="panel empty-state"><BrainCircuit size={30}/><h2>{title}</h2><p>{detail}</p>{action && <button className="primary" onClick={onAction}>{action}</button>}</section> }

export default App
