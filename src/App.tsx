import { useEffect, useMemo, useState } from 'react'
import { Activity, BarChart3, Bell, Bot, BrainCircuit, CandlestickChart, ChevronDown, ChevronRight, CircleHelp, Database, FlaskConical, History, LayoutDashboard, LogOut, Menu, MessageSquareText, Moon, Plus, Search, Settings, ShieldCheck, Sparkles, TrendingDown, TrendingUp, X, Zap } from 'lucide-react'
import { Area, AreaChart, CartesianGrid, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import clsx from 'clsx'
import { MarketChart } from './MarketChart'
import { equityCurve, makeCandles, regimeRows, stocks, type Stock } from './data'
import { useAuth } from './lib/auth-context'
import { createPaperTrade, startTrainingJob, watchTrainingJobs } from './lib/repository'
import './App.css'

type Page = 'overview' | 'market' | 'models' | 'advisor' | 'backtests'

const nav = [
  { id: 'overview', label: 'Overview', icon: LayoutDashboard },
  { id: 'market', label: 'Market lab', icon: CandlestickChart },
  { id: 'models', label: 'Model lab', icon: BrainCircuit },
  { id: 'advisor', label: 'AI advisor', icon: MessageSquareText },
  { id: 'backtests', label: 'Backtests', icon: FlaskConical },
] as const

function Metric({ label, value, detail, tone = 'plain' }: { label: string; value: string; detail: string; tone?: 'plain' | 'good' | 'bad' }) {
  return <div className="metric"><div className="metric-head"><span>{label}</span><CircleHelp size={13} /></div><strong className={tone}>{value}</strong><small>{detail}</small></div>
}

function StockAvatar({ stock }: { stock: Stock }) {
  return <span className="stock-avatar">{stock.symbol.slice(0, 2)}</span>
}

function App() {
  const { user, logout } = useAuth()
  const [page, setPage] = useState<Page>('overview')
  const [stock, setStock] = useState(stocks[0])
  const [watch] = useState(stocks)
  const [searchOpen, setSearchOpen] = useState(false)
  const [trainOpen, setTrainOpen] = useState(false)
  const [training, setTraining] = useState(false)
  const [progress, setProgress] = useState(0)
  const [activeJobId, setActiveJobId] = useState('')
  const [mobileNav, setMobileNav] = useState(false)
  const [toast, setToast] = useState('')
  const [actionError, setActionError] = useState('')
  const candles = useMemo(() => makeCandles(stock.price * .8), [stock])

  useEffect(() => watchTrainingJobs(user.uid, jobs => {
    if (!activeJobId) return
    const job = jobs.find(item => item.id === activeJobId)
    if (!job) return
    setProgress(job.progress || 0)
    if (job.status === 'blocked' || job.status === 'failed') {
      setTraining(false)
      setActionError(job.status === 'blocked' ? 'Training runner and licensed market-data provider must be configured before this job can continue.' : (job.error || 'The server-side training job failed.'))
    }
    if (job.status === 'completed') {
      setTraining(false); setTrainOpen(false); setToast(`${job.symbol} validation report is ready`); setActiveJobId('')
    }
  }), [user.uid, activeJobId])
  useEffect(() => { if (!toast) return; const t = setTimeout(() => setToast(''), 3500); return () => clearTimeout(t) }, [toast])

  const beginTraining = async () => {
    setActionError('')
    try {
      const job = await startTrainingJob({ symbol: stock.symbol, exchange: stock.exchange, historyYears: 8, timeframe: '1d' })
      setActiveJobId(job.jobId)
      setTraining(true)
      setToast(`Training job ${job.jobId.slice(0, 8)} queued securely`)
    } catch (error) { setActionError(error instanceof Error ? error.message : 'Training could not be started') }
  }
  const addPaperTrade = async () => {
    setActionError('')
    try {
      await createPaperTrade(user.uid, { symbol: stock.symbol, exchange: stock.exchange, entry: 3014, stop: 2918, target: 3216, quantity: 33, confidence: 78, modelVersion: '2.4.1' })
      setToast('Paper trade saved to Firestore and your journal')
    } catch (error) { setActionError(error instanceof Error ? error.message : 'Paper trade could not be saved') }
  }

  const go = (id: Page) => { setPage(id); setMobileNav(false) }
  return <div className="app-shell">
    <aside className={clsx('sidebar', mobileNav && 'open')}>
      <div className="brand"><span className="brand-mark"><Activity size={21} /></span><div>Arth<span>AI</span></div></div>
      <button className="icon-btn mobile-close" onClick={() => setMobileNav(false)}><X size={20} /></button>
      <nav>{nav.map(item => <button key={item.id} className={clsx(page === item.id && 'active')} onClick={() => go(item.id)}><item.icon size={18} /><span>{item.label}</span>{item.id === 'advisor' && <i>AI</i>}</button>)}</nav>
      <div className="nav-section">WORKSPACE</div>
      <nav className="secondary"><button><Database size={18} /><span>Data sources</span></button><button><History size={18} /><span>Trade journal</span></button><button><Settings size={18} /><span>Settings</span></button></nav>
      <div className="sidebar-status"><div><span className="status-dot" /> Firebase connected</div><small>Market feed · demo dataset</small></div>
      <div className="profile"><span>{(user.displayName || user.email || 'U').split(/\s|@/).map(x=>x[0]).join('').slice(0,2).toUpperCase()}</span><div><b>{user.displayName || user.email?.split('@')[0] || 'Researcher'}</b><small>{user.email || 'Research workspace'}</small></div><button title="Sign out" onClick={logout}><LogOut size={14}/></button></div>
    </aside>

    <main>
      <header className="topbar">
        <button className="icon-btn menu-btn" onClick={() => setMobileNav(true)}><Menu size={20} /></button>
        <button className="global-search" onClick={() => setSearchOpen(true)}><Search size={17} /><span>Search any NSE or BSE stock…</span><kbd>⌘ K</kbd></button>
        <div className="market-state"><span className="live-dot" /> NSE closed <small>· Opens 09:15</small></div>
        <button className="icon-btn"><Moon size={18} /></button><button className="icon-btn notification"><Bell size={18} /><i /></button>
        <button className="primary compact" onClick={() => setTrainOpen(true)}><Plus size={17} /> Train stock</button>
      </header>

      <div className="page-wrap">
        {page === 'overview' && <Overview stock={stock} candles={candles} onPage={go} watch={watch} onStock={setStock} />}
        {page === 'market' && <MarketLab stock={stock} candles={candles} onAdvisor={() => go('advisor')} />}
        {page === 'models' && <ModelLab stock={stock} onTrain={() => setTrainOpen(true)} />}
        {page === 'advisor' && <Advisor stock={stock} onPaper={addPaperTrade} />}
        {page === 'backtests' && <Backtests />}
      </div>
    </main>

    {searchOpen && <div className="modal-layer" onMouseDown={() => setSearchOpen(false)}><div className="search-modal" onMouseDown={e => e.stopPropagation()}><div className="search-input"><Search size={20} /><input autoFocus placeholder="Search symbol or company" /><kbd>ESC</kbd></div><small>RECENT & TRAINED</small>{watch.map(s => <button key={s.symbol} onClick={() => { setStock(s); setSearchOpen(false); go('market') }}><StockAvatar stock={s} /><div><b>{s.symbol}</b><span>{s.name}</span></div><em>{s.exchange}</em><strong className={s.change > 0 ? 'good' : 'bad'}>{s.change > 0 ? '+' : ''}{s.change}%</strong></button>)}</div></div>}
    {trainOpen && <TrainModal stock={stock} training={training} progress={progress} error={actionError} onClose={() => !training && setTrainOpen(false)} onStart={beginTraining} />}
    {toast && <div className="toast"><ShieldCheck size={18} />{toast}</div>}
    {actionError && !trainOpen && <div className="toast error-toast"><X size={18} />{actionError}</div>}
  </div>
}

function Overview({ stock, candles, onPage, watch, onStock }: { stock: Stock; candles: ReturnType<typeof makeCandles>; onPage: (p: Page) => void; watch: Stock[]; onStock: (s: Stock) => void }) {
  return <>
    <section className="welcome"><div><span className="eyebrow">MONDAY, 13 JULY</span><h1>Good evening, Jigar.</h1><p>Your models are healthy. One needs attention before the next market session.</p></div><button className="primary" onClick={() => onPage('advisor')}><Sparkles size={17} /> Ask AI advisor</button></section>
    <section className="pulse-card">
      <div className="pulse-title"><span className="pulse-icon"><Zap size={19} /></span><div><span className="eyebrow">TODAY'S MODEL PULSE</span><h2>RELIANCE setup is strengthening</h2><p>Momentum and volume alignment improved after the latest close.</p></div></div>
      <div className="signal-summary"><span>WATCHLIST SIGNAL</span><strong>Potential breakout</strong><small>Wait for ₹3,012 confirmation</small></div>
      <button className="ghost" onClick={() => onPage('advisor')}>View analysis <ChevronRight size={16} /></button>
    </section>
    <div className="section-head"><div><h2>Your model fleet</h2><p>Live status across trained stocks</p></div><button className="text-btn" onClick={() => onPage('models')}>View model lab <ChevronRight size={15} /></button></div>
    <section className="stock-grid">{watch.map(s => <button className={clsx('stock-card', stock.symbol === s.symbol && 'selected')} key={s.symbol} onClick={() => onStock(s)}><div className="stock-top"><StockAvatar stock={s} /><div><b>{s.symbol}</b><span>{s.exchange} · {s.sector}</span></div><em className={s.status === 'Ready' ? 'ready' : s.status === 'Training' ? 'training' : 'review'}>{s.status}</em></div><div className="stock-price"><strong>₹{s.price.toLocaleString('en-IN')}</strong><span className={s.change >= 0 ? 'good' : 'bad'}>{s.change >= 0 ? <TrendingUp size={14}/> : <TrendingDown size={14}/>} {Math.abs(s.change)}%</span></div><div className="quality"><span>Model quality</span><b>{s.model}/100</b><div><i style={{ width: `${s.model}%` }} /></div></div></button>)}</section>
    <section className="dashboard-grid">
      <div className="panel chart-panel"><div className="panel-head"><div><span className="eyebrow">SELECTED MODEL</span><h2>{stock.symbol} <small>{stock.exchange}</small></h2></div><div className="range"><button>1M</button><button>3M</button><button className="active">1Y</button><button>All</button></div></div><MarketChart data={candles} /><div className="chart-legend"><span><i className="candle-legend" /> Price</span><span><i className="sma-legend" /> 20-day SMA</span><span><i className="buy-legend" /> Model signals</span></div></div>
      <div className="panel score-panel"><div className="panel-head"><div><span className="eyebrow">DEPLOYMENT READINESS</span><h2>Robust, with one caveat</h2></div><span className="score-ring">87</span></div><div className="gate good"><ShieldCheck size={18}/><div><b>8 of 9 gates passed</b><span>Eligible for paper trading</span></div></div><div className="checklist"><div><span>Out-of-sample return</span><b className="good">+21.8%</b></div><div><span>Max drawdown</span><b>-8.4%</b></div><div><span>Profit factor</span><b>1.72</b></div><div><span>Profitable months</span><b>67%</b></div><div className="warning"><span>High-volatility regime</span><b>Review</b></div></div><button className="secondary-btn" onClick={() => onPage('models')}>Open validation report</button></div>
    </section>
    <section className="panel performance"><div className="panel-head"><div><span className="eyebrow">OUT-OF-SAMPLE PERFORMANCE</span><h2>Strategy vs buy & hold</h2><p>Includes brokerage, STT, taxes and estimated slippage</p></div><div className="perf-key"><span><i className="strategy-dot"/>Strategy <b>+62.1%</b></span><span><i className="hold-dot"/>Buy & hold <b>+38.7%</b></span></div></div><div className="equity-chart"><ResponsiveContainer width="100%" height="100%"><AreaChart data={equityCurve}><defs><linearGradient id="teal" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#24c78e" stopOpacity=".28"/><stop offset="1" stopColor="#24c78e" stopOpacity="0"/></linearGradient></defs><CartesianGrid stroke="#1c2532" vertical={false}/><XAxis dataKey="month" tick={{ fill:'#718096', fontSize:11 }} axisLine={false} tickLine={false}/><YAxis hide/><Tooltip contentStyle={{ background:'#111b28', border:'1px solid #29394b', borderRadius:10 }}/><Area type="monotone" dataKey="strategy" stroke="#24c78e" strokeWidth={2} fill="url(#teal)"/><Line type="monotone" dataKey="hold" stroke="#7b8da7" strokeWidth={1.5} dot={false}/></AreaChart></ResponsiveContainer></div></section>
  </>
}

function MarketLab({ stock, candles, onAdvisor }: { stock: Stock; candles: ReturnType<typeof makeCandles>; onAdvisor: () => void }) {
  return <><div className="page-title"><div><span className="eyebrow">MARKET LAB / {stock.exchange}</span><h1>{stock.name}</h1><p>{stock.symbol} · Equity · {stock.sector}</p></div><div className="headline-price"><strong>₹{stock.price.toLocaleString('en-IN')}</strong><span className="good"><TrendingUp size={15}/> +{stock.change}% today</span></div></div><div className="toolbar"><div className="range"><button>1D</button><button>1W</button><button>1M</button><button className="active">1Y</button><button>5Y</button><button>MAX</button></div><div><button className="tool-btn"><BarChart3 size={15}/> Indicators <ChevronDown size={14}/></button><button className="tool-btn">Adjusted data</button></div></div><section className="panel market-main"><MarketChart data={candles}/></section><section className="metrics-row"><Metric label="RSI (14)" value="62.4" detail="Neutral–bullish"/><Metric label="MACD" value="+12.8" detail="Above signal" tone="good"/><Metric label="ATR (14)" value="₹48.20" detail="1.61% volatility"/><Metric label="Relative strength" value="1.18×" detail="vs NIFTY 50" tone="good"/><Metric label="Delivery" value="54.2%" detail="Above 20D average" tone="good"/></section><section className="two-col"><div className="panel"><div className="panel-head"><div><span className="eyebrow">TECHNICAL MAP</span><h2>Key price levels</h2></div></div><div className="levels"><div><span>Resistance 2</span><b>₹3,126</b></div><div><span>Resistance 1</span><b>₹3,012</b></div><div className="current"><span>Current price</span><b>₹2,986</b></div><div><span>Support 1</span><b>₹2,924</b></div><div><span>Support 2</span><b>₹2,862</b></div></div></div><div className="panel insight"><div className="panel-head"><div><span className="eyebrow">MODEL INTERPRETATION</span><h2>What matters right now</h2></div><Sparkles size={20}/></div><p>Price is compressing below resistance while delivery volume rises. A close above ₹3,012 with volume over 1.4× the 20-day average would confirm the setup.</p><div className="factor-tags"><span>Volume expansion +</span><span>Market regime +</span><span>Event risk −</span></div><button className="primary" onClick={onAdvisor}>Discuss with advisor</button></div></section></>
}

function ModelLab({ stock, onTrain }: { stock: Stock; onTrain: () => void }) {
 return <><div className="page-title"><div><span className="eyebrow">MODEL LAB</span><h1>{stock.symbol} intelligence</h1><p>ANN ensemble · GA-optimized · Version 2.4.1</p></div><button className="primary" onClick={onTrain}><BrainCircuit size={17}/> New training run</button></div><section className="model-hero"><div><span className="eyebrow">RELEASE DECISION</span><h2>Paper-trading approved</h2><p>The champion model passes 8 of 9 deployment gates on truly unseen data. Keep high-volatility exposure capped until the next retrain.</p><div className="model-actions"><button className="primary"><ShieldCheck size={17}/> Deploy to paper</button><button className="secondary-btn">Compare candidates</button></div></div><div className="readiness"><span>READINESS</span><strong>87<small>/100</small></strong><i><em style={{width:'87%'}} /></i><small>Minimum required: 75</small></div></section><div className="section-head"><div><h2>Validation scorecard</h2><p>Locked test set · Apr–Jun 2026 · 121 trades</p></div><span className="verified"><ShieldCheck size={15}/> Leakage checks passed</span></div><section className="metric-grid"><Metric label="Net return" value="+21.8%" detail="After all costs" tone="good"/><Metric label="Maximum drawdown" value="−8.4%" detail="Limit: −12%"/><Metric label="Profit factor" value="1.72" detail="Gross profit / loss" tone="good"/><Metric label="Win rate" value="47.9%" detail="Not used as approval gate"/><Metric label="Avg win / loss" value="2.14×" detail="Healthy payoff ratio" tone="good"/><Metric label="Worst loss streak" value="6" detail="Within expected range"/></section><section className="two-col model-cols"><div className="panel"><div className="panel-head"><div><span className="eyebrow">MARKET REGIMES</span><h2>Performance consistency</h2></div></div><div className="regime-table"><div className="table-head"><span>Regime</span><span>Trades</span><span>Return</span><span>Win rate</span></div>{regimeRows.map(r=><div key={r.label}><b>{r.label}</b><span>{r.trades}</span><strong className={r.tone}>{r.return}</strong><span>{r.win}</span></div>)}</div></div><div className="panel architecture"><div className="panel-head"><div><span className="eyebrow">CHAMPION ARCHITECTURE</span><h2>Hybrid temporal ensemble</h2></div></div><div className="arch-flow"><span>132 features</span><ChevronRight/><span>TCN + GRU</span><ChevronRight/><span>Attention</span><ChevronRight/><span>3 heads</span></div><p>The genetic algorithm selects features, lookback windows, thresholds and risk parameters. It never sees the locked test set.</p><div className="specs"><div><span>GA generations</span><b>48</b></div><div><span>Walk-forward folds</span><b>8</b></div><div><span>Parameters</span><b>1.8M</b></div><div><span>Training window</span><b>8 years</b></div></div></div></section></>
}

function Advisor({ stock, onPaper }: { stock: Stock; onPaper: () => void }) {
 const [question,setQuestion]=useState(''); const [asked,setAsked]=useState(false)
 return <div className="advisor-layout"><section className="advisor-main"><div className="advisor-head"><span className="ai-orb"><Bot size={24}/></span><div><h1>AI trading advisor</h1><p>Grounded in your validated {stock.symbol} model and latest synced data</p></div><span className="data-fresh"><i/> Data as of 12 Jul, 15:30</span></div><div className="conversation"><div className="user-bubble">Is there a high-quality setup for {stock.symbol} right now?</div><div className="ai-answer"><div className="answer-title"><span className="buy-badge">WATCH → BUY</span><span>Conditional signal</span><strong>78% model confidence</strong></div><h2>{stock.symbol} — breakout setup</h2><p>The setup is constructive, but confirmation has not occurred. Avoid entering before a daily close above resistance.</p><div className="trade-ticket"><div><span>ENTRY</span><b>₹3,014–₹3,028</b><small>After close above ₹3,012</small></div><div><span>STOP</span><b>₹2,918</b><small>3.2% initial risk</small></div><div><span>TARGET</span><b>₹3,216</b><small>2.08 reward / risk</small></div><div><span>QUANTITY</span><b>33 shares</b><small>For ₹1 lakh, 1% risk</small></div></div><div className="thesis"><h3>Why the model likes it</h3><ul><li><i className="positive"/>Price compression below a tested resistance zone</li><li><i className="positive"/>Delivery volume is 18% above its 20-day mean</li><li><i className="positive"/>Relative strength vs NIFTY is improving</li><li><i className="negative"/>Earnings event within 9 trading sessions</li></ul></div><div className="risk-note"><ShieldCheck size={18}/><p><b>Risk check</b>This is a research signal, not a guarantee. Slippage above 0.25% reduces expected profit factor to 1.46.</p></div><div className="answer-actions"><button className="primary" onClick={onPaper}>Add paper trade</button><button className="secondary-btn">See backtest evidence</button></div></div>{asked && <div className="ai-answer compact-answer"><div className="typing"><i/><i/><i/></div> I’ll evaluate that against the locked model report, market regime, costs and your risk limits.</div>}</div><div className="composer"><div className="quick-prompts"><button onClick={()=>setQuestion('What could invalidate this setup?')}>What could invalidate this?</button><button onClick={()=>setQuestion('Show performance in bear markets')}>Bear-market evidence</button><button onClick={()=>setQuestion('Calculate quantity for ₹50,000')}>Size for ₹50,000</button></div><form onSubmit={e=>{e.preventDefault(); if(question.trim()){setAsked(true);setQuestion('')}}}><Sparkles size={18}/><input value={question} onChange={e=>setQuestion(e.target.value)} placeholder={`Ask about ${stock.symbol}, risk, or model evidence…`}/><button><ChevronRight size={19}/></button></form><small>ArthAI can be wrong. Verify important decisions independently.</small></div></section><aside className="context-panel"><span className="eyebrow">ANSWER CONTEXT</span><h3>What the advisor used</h3>{['Validated model v2.4.1','Daily OHLCV · 8 years','NIFTY & sector regime','Corporate events calendar','Your 1% risk limit'].map((x,i)=><div key={x}><ShieldCheck size={15}/><span>{x}</span>{i===1&&<em>Fresh</em>}</div>)}<hr/><h3>Model limitations</h3><p>No live order-book or reliable news sentiment is connected. Intraday answers are disabled.</p><button className="text-btn">View full model card <ChevronRight size={15}/></button></aside></div>
}

function Backtests() { return <><div className="page-title"><div><span className="eyebrow">RESEARCH ARCHIVE</span><h1>Backtests</h1><p>Reproducible experiments with immutable datasets and cost assumptions</p></div><button className="primary"><Plus size={17}/> New backtest</button></div><section className="panel backtest-list"><div className="backtest-head"><span>EXPERIMENT</span><span>PERIOD</span><span>NET RETURN</span><span>MAX DD</span><span>PROFIT FACTOR</span><span>STATUS</span></div>{[['RELIANCE · Hybrid v2.4','2018–2026','+94.2%','−11.8%','1.68','Champion'],['HDFCBANK · GRU v1.9','2017–2026','+71.6%','−14.2%','1.44','Approved'],['TCS · Momentum v3.1','2019–2026','+38.1%','−18.9%','1.21','Rejected'],['INFY · GA search #184','2020–2026','Running','—','—','Training']].map(r=><button key={r[0]}>{r.map((x,j)=><span key={x} className={j===2&&x.startsWith('+')?'good':''}>{j===0&&<FlaskConical size={16}/>} {x}</span>)}<ChevronRight size={16}/></button>)}</section><div className="empty-note"><ShieldCheck size={19}/><div><b>Every result is reproducible</b><span>Data version, code hash, random seeds, costs and feature definitions are saved with each run.</span></div></div></> }

function TrainModal({ stock, training, progress, error, onClose, onStart }: { stock: Stock; training: boolean; progress: number; error: string; onClose: () => void; onStart: () => void }) {
 return <div className="modal-layer"><div className="train-modal"><button className="icon-btn modal-x" onClick={onClose}><X size={19}/></button><span className="modal-icon"><BrainCircuit size={25}/></span><span className="eyebrow">NEW TRAINING RUN</span><h2>Build intelligence for a stock</h2><p>The pipeline creates an authenticated, auditable server job and locks an unseen test period.</p>{!training ? <><label>Stock or symbol<div className="fake-input"><Search size={17}/><b>{stock.symbol}</b><span>{stock.name}</span><em>{stock.exchange}</em></div></label><div className="form-grid"><label>History<select defaultValue="8"><option value="8">8 years</option><option>5 years</option><option>Maximum available</option></select></label><label>Timeframe<select><option>Daily</option><option>15 minute</option><option>Hourly</option></select></label></div><div className="pipeline"><span>1<br/><small>Validate data</small></span><i/><span>2<br/><small>GA search</small></span><i/><span>3<br/><small>Walk-forward</small></span><i/><span>4<br/><small>Test & gate</small></span></div><div className="legal-note"><ShieldCheck size={17}/>Only licensed provider data is accepted. Jobs fail closed when the provider or training runner is not configured.</div>{error&&<div className="auth-message">{error}</div>}<button className="primary full" onClick={onStart}>Start training pipeline <ChevronRight size={17}/></button></> : <div className="training-state"><div className="brain-pulse"><BrainCircuit size={38}/></div><h3>{progress < 20 ? 'Validating historical candles' : progress < 55 ? 'Evolving model candidates' : progress < 85 ? 'Running walk-forward folds' : 'Locking validation report'}</h3><p>Authenticated server job · Processing continues securely in the cloud</p><div className="progress-bar"><i style={{width:`${progress}%`}}/></div><b>{progress}%</b><div className="training-log"><span className="done">✓ Authenticated job accepted</span><span className={progress>25?'done':''}>{progress>25?'✓':'·'} Provider and leakage validation</span><span className={progress>55?'done':''}>{progress>55?'✓':'·'} Population fitness search</span><span className={progress>82?'done':''}>{progress>82?'✓':'·'} Unseen market evaluation</span></div></div>}</div></div>
}

export default App
