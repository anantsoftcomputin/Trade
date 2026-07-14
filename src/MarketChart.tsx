import { useEffect, useRef } from 'react'
import { CandlestickSeries, ColorType, createChart, HistogramSeries, LineSeries } from 'lightweight-charts'
import type { MarketCandle } from './lib/repository'

export function MarketChart({ data }: { data: MarketCandle[] }) {
  const host = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!host.current) return
    const chart = createChart(host.current, {
      layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: '#7f8b9d', fontFamily: 'Inter, sans-serif' },
      grid: { vertLines: { color: '#1c2532' }, horzLines: { color: '#1c2532' } },
      rightPriceScale: { borderColor: '#253140' }, timeScale: { borderColor: '#253140', timeVisible: true },
      crosshair: { vertLine: { color: '#68778d', labelBackgroundColor: '#283548' }, horzLine: { color: '#68778d', labelBackgroundColor: '#283548' } },
      width: host.current.clientWidth, height: 390,
    })
    const candle = chart.addSeries(CandlestickSeries, { upColor: '#24c78e', downColor: '#f05d67', wickUpColor: '#24c78e', wickDownColor: '#f05d67', borderVisible: false })
    candle.setData(data.map(({ time, open, high, low, close }) => ({ time, open, high, low, close })))
    const volume = chart.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, priceScaleId: '', color: '#304661' })
    volume.priceScale().applyOptions({ scaleMargins: { top: .82, bottom: 0 } })
    volume.setData(data.map(x => ({ time: x.time, value: x.volume, color: x.close >= x.open ? '#1e705c88' : '#773b4688' })))
    const sma = chart.addSeries(LineSeries, { color: '#f3b654', lineWidth: 2, priceLineVisible: false, lastValueVisible: false })
    sma.setData(data.slice(19).map((x, i) => ({ time: x.time, value: +(data.slice(i, i + 20).reduce((a, b) => a + b.close, 0) / 20).toFixed(2) })))
    chart.timeScale().fitContent()
    const ro = new ResizeObserver(() => chart.applyOptions({ width: host.current?.clientWidth || 800 }))
    ro.observe(host.current)
    return () => { ro.disconnect(); chart.remove() }
  }, [data])
  return <div className="market-chart" ref={host} />
}
