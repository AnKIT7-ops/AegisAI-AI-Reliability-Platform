import type { ReactNode } from 'react'
import { metricLabel, metricValue, words } from '../format'
import type { MetricRow } from '../types'

export function Readout({
  label,
  value,
  note,
}: {
  label: string
  value: ReactNode
  note?: ReactNode
}) {
  return (
    <div className="readout">
      <dt>{label}</dt>
      <dd>
        <span className="readout-value">{value}</span>
        {note && <span className="readout-note">{note}</span>}
      </dd>
    </div>
  )
}

const GLYPHS = { improved: '▲', regressed: '▼', same: '=' }

/** The measured value against the gate's limit, which is the line on the bar. */
export function MetricBar({
  name,
  value,
  limit,
  missed,
}: {
  name: string
  value: number
  limit: number | undefined
  missed: boolean
}) {
  // Without a limit, a value above 1 has no scale to draw it against.
  const hasScale = limit !== undefined || value <= 1
  const top = limit === undefined || limit <= 1 ? 1 : limit * 1.25
  const position = (amount: number) => `${Math.min(amount / top, 1) * 100}%`
  return (
    <li className={missed ? 'metric metric-missed' : 'metric'}>
      <span className="metric-name">{metricLabel(name)}</span>
      <span className="metric-value">
        {metricValue(name, value)}
        {limit !== undefined && (
          <span className="meta"> limit {metricValue(name, limit)}</span>
        )}
        {missed && <strong> Missed</strong>}
      </span>
      {hasScale && (
        <span className="metric-bar" aria-hidden="true">
          <span className="metric-fill" style={{ width: position(value) }} />
          {limit !== undefined && (
            <span className="metric-limit" style={{ left: position(limit) }} />
          )}
        </span>
      )}
    </li>
  )
}

export function MetricDeltas({
  rows,
  baselineLabel,
  candidateLabel,
}: {
  rows: MetricRow[]
  baselineLabel: string
  candidateLabel: string
}) {
  return (
    <div className="table-scroll">
      <table>
        <caption className="sr-only">Metrics compared</caption>
        <thead>
          <tr>
            <th scope="col">Metric</th>
            <th scope="col" className="number">
              {baselineLabel}
            </th>
            <th scope="col" className="number">
              {candidateLabel}
            </th>
            <th scope="col" className="number">
              Change
            </th>
            <th scope="col">Direction</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.name} className={`delta-${row.direction}`}>
              <td>{metricLabel(row.name)}</td>
              <td className="number">{metricValue(row.name, row.baseline)}</td>
              <td className="number">{metricValue(row.name, row.candidate)}</td>
              <td className="number">
                {row.delta > 0 ? '+' : ''}
                {metricValue(row.name, row.delta)}
              </td>
              <td>
                <span aria-hidden="true">{GLYPHS[row.direction]}</span>{' '}
                {words(row.direction)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
