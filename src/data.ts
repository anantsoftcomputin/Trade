export type Stock = {
  symbol: string
  exchange: 'NSE' | 'BSE'
  name: string
  price: number
  change: number
  sector: string
  model: number
  status: 'Ready' | 'Training' | 'Needs review'
}

export const stocks: Stock[] = [
  { symbol: 'RELIANCE', exchange: 'NSE', name: 'Reliance Industries', price: 2986.4, change: 1.82, sector: 'Energy', model: 87, status: 'Ready' },
  { symbol: 'HDFCBANK', exchange: 'NSE', name: 'HDFC Bank', price: 1682.75, change: -0.42, sector: 'Financials', model: 81, status: 'Ready' },
  { symbol: 'TCS', exchange: 'NSE', name: 'Tata Consultancy Services', price: 3944.2, change: 0.64, sector: 'Technology', model: 76, status: 'Needs review' },
  { symbol: 'INFY', exchange: 'BSE', name: 'Infosys', price: 1824.1, change: 2.16, sector: 'Technology', model: 42, status: 'Training' },
]

export type Candle = { time: string; open: number; high: number; low: number; close: number; volume: number }

export function makeCandles(base = 2400, count = 260): Candle[] {
  const rows: Candle[] = []
  let price = base
  const start = new Date('2025-07-14T00:00:00')
  for (let i = 0; i < count; i++) {
    const d = new Date(start)
    d.setDate(d.getDate() + i)
    if (d.getDay() === 0 || d.getDay() === 6) continue
    const drift = Math.sin(i / 11) * 9 + Math.cos(i / 27) * 5 + 2.15
    const noise = Math.sin(i * 2.31) * 13
    const open = price + Math.sin(i * 1.7) * 8
    const close = Math.max(100, open + drift + noise)
    const high = Math.max(open, close) + 8 + Math.abs(Math.sin(i)) * 14
    const low = Math.min(open, close) - 7 - Math.abs(Math.cos(i * 1.2)) * 12
    rows.push({
      time: d.toISOString().slice(0, 10),
      open: +open.toFixed(2), high: +high.toFixed(2), low: +low.toFixed(2), close: +close.toFixed(2),
      volume: Math.round(2200000 + Math.abs(Math.sin(i / 4)) * 3100000 + (i % 13) * 70000),
    })
    price = close
  }
  return rows
}

export const equityCurve = Array.from({ length: 24 }, (_, i) => ({
  month: new Date(2024, i, 1).toLocaleDateString('en-IN', { month: 'short', year: '2-digit' }),
  strategy: +(100 + i * 2.45 + Math.sin(i * 1.2) * 4.8).toFixed(1),
  hold: +(100 + i * 1.62 + Math.sin(i / 2.1) * 3.1).toFixed(1),
}))

export const regimeRows = [
  { label: 'Bull market', trades: 42, return: '+18.4%', win: '57%', tone: 'good' },
  { label: 'Bear market', trades: 27, return: '+6.2%', win: '44%', tone: 'good' },
  { label: 'Sideways', trades: 33, return: '+2.8%', win: '48%', tone: 'muted' },
  { label: 'High volatility', trades: 19, return: '-1.1%', win: '37%', tone: 'bad' },
]
