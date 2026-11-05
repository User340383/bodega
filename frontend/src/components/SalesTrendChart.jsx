import { useState } from 'react'
import { axisMaximum, chartNumber, tickLabel } from './chartValues'

const WIDTH = 640
const HEIGHT = 240
const LEFT = 52
const RIGHT = 12
const TOP = 14
const BOTTOM = 34
const PLOT_WIDTH = WIDTH - LEFT - RIGHT
const PLOT_HEIGHT = HEIGHT - TOP - BOTTOM

function exactAverage(point) {
  if (point.movingAverage === null) return 'Not enough dates'
  return `${point.movingAverageRounded ? '≈ ' : ''}${point.movingAverage}`
}

function describePoint(point) {
  const sales = point.observed ? `${point.quantity} sold` : 'not sold, counted as 0'
  const average = point.movingAverage === null ? '' : `; 7-date average ${exactAverage(point)}`
  const skipped = point.calendarDaysSkipped ? `; ${point.calendarDaysSkipped} unlogged calendar days before this date` : ''
  return `${point.date}: ${sales}${average}${skipped}`
}

/** Null when any value cannot be positioned; the exact table is then the only view. */
function buildGeometry(series) {
  const sales = series.map(point => chartNumber(point.quantity))
  const averages = series.map(point => (point.movingAverage === null ? null : chartNumber(point.movingAverage)))
  const unplottable = sales.includes(null) || series.some((point, index) => point.movingAverage !== null && averages[index] === null)
  if (unplottable) return null
  const top = axisMaximum(Math.max(...sales, ...averages.filter(value => value !== null)))
  if (top === null) return null
  const band = PLOT_WIDTH / series.length
  const barWidth = Math.min(24, Math.max(3, band - 2))
  const y = value => TOP + PLOT_HEIGHT * (1 - value / top)
  const center = index => LEFT + band * (index + 0.5)
  const linePoints = averages.map((value, index) => (value === null ? null : `${center(index).toFixed(1)},${y(value).toFixed(1)}`)).filter(Boolean)
  return { sales, averages, top, band, barWidth, y, center, linePoints }
}

function barPath(x, width, top, bottom) {
  const radius = Math.min(4, width / 2, bottom - top)
  return `M${x} ${bottom}V${top + radius}Q${x} ${top} ${x + radius} ${top}H${x + width - radius}Q${x + width} ${top} ${x + width} ${top + radius}V${bottom}Z`
}

export default function SalesTrendChart({ itemName, series }) {
  const [active, setActive] = useState(null)
  const geometry = buildGeometry(series)
  const hasAverage = series.some(point => point.movingAverage !== null)
  const hasAbsence = series.some(point => !point.observed)
  const hasGap = series.some(point => point.calendarDaysSkipped > 0)
  const labelEvery = Math.max(1, Math.ceil(series.length / 4))
  const baseline = TOP + PLOT_HEIGHT
  const lastAverage = geometry && geometry.linePoints.length ? geometry.averages.length - 1 : null

  return <div className="trend-chart">
    {geometry ? <>
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={`${itemName}: confirmed sales${hasAverage ? ' and 7-logged-date moving average' : ''} across ${series.length} logged sales dates. Exact values are in the table below.`} onMouseLeave={() => setActive(null)}>
        {[0, 0.5, 1].map(fraction => {
          const value = geometry.top * fraction
          return <g key={fraction}>
            <line className="chart-grid" x1={LEFT} x2={WIDTH - RIGHT} y1={geometry.y(value)} y2={geometry.y(value)} />
            <text className="chart-text" x={LEFT - 8} y={geometry.y(value) + 4} textAnchor="end">{tickLabel(value)}</text>
          </g>
        })}
        {series.map((point, index) => {
          const x = geometry.center(index)
          const showLabel = index === series.length - 1 || (index % labelEvery === 0 && series.length - 1 - index >= labelEvery / 2)
          return <g key={point.date}>
            {active === index && <rect className="chart-active" x={x - geometry.band / 2} y={TOP} width={geometry.band} height={PLOT_HEIGHT} />}
            {point.calendarDaysSkipped > 0 && <line className="chart-gap" data-testid="calendar-gap" x1={x - geometry.band / 2} x2={x - geometry.band / 2} y1={baseline - 10} y2={baseline + 6} />}
            {point.observed
              ? <path className="chart-bar" data-testid="sales-bar" d={barPath(x - geometry.barWidth / 2, geometry.barWidth, Math.min(geometry.y(geometry.sales[index]), baseline - 1), baseline)} />
              : <circle className="chart-absent" data-testid="absent-marker" cx={x} cy={baseline - 5} r="4" />}
            {showLabel && <text className="chart-text" x={Math.min(x, WIDTH - RIGHT - 18)} y={HEIGHT - 12} textAnchor="middle">{point.date.slice(5)}</text>}
          </g>
        })}
        {geometry.linePoints.length > 1 && <polyline className="chart-average" data-testid="average-line" points={geometry.linePoints.join(' ')} />}
        {lastAverage !== null && <circle className="chart-average-end" data-testid="average-end" cx={geometry.center(lastAverage)} cy={geometry.y(geometry.averages[lastAverage])} r="4" />}
        {series.map((point, index) => <rect key={point.date} className="chart-hit" x={geometry.center(index) - geometry.band / 2} y={TOP} width={geometry.band} height={PLOT_HEIGHT + BOTTOM} onMouseEnter={() => setActive(index)} onClick={() => setActive(index)}>
          <title>{describePoint(point)}</title>
        </rect>)}
      </svg>
      <p className="chart-readout" aria-hidden="true">{active === null ? 'Hover or tap a date for exact values.' : describePoint(series[active])}</p>
      <ul className="chart-legend">
        <li><span className="key key-bar" aria-hidden="true" />Confirmed sales</li>
        {hasAverage && <li><span className="key key-line" aria-hidden="true" />Average of the latest 7 logged dates</li>}
        {hasAbsence && <li><span className="key key-absent" aria-hidden="true" />Date logged, item not sold (counted as 0)</li>}
        {hasGap && <li><span className="key key-gap" aria-hidden="true" />Calendar dates not logged (unknown, not zero)</li>}
      </ul>
    </> : <p className="chart-unavailable">These values are too large or too small to plot accurately. The table below has the exact figures.</p>}
    <details>
      <summary>Exact daily values for {itemName}</summary>
      <div className="table-scroll"><table>
        <caption className="sr-only">Daily values for {itemName}</caption>
        <thead><tr><th>Logged date</th><th>Confirmed sales</th><th>7-date average</th><th>Unlogged days before</th></tr></thead>
        <tbody>{series.map(point => <tr key={point.date}>
          <td>{point.date}</td>
          <td>{point.observed ? point.quantity : '0 (not sold this date)'}</td>
          <td>{exactAverage(point)}</td>
          <td>{point.calendarDaysSkipped}</td>
        </tr>)}</tbody>
      </table></div>
    </details>
  </div>
}
