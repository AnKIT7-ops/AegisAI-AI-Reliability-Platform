import { describe, expect, it } from 'vitest'
import { parseRoute } from './route'

describe('parseRoute', () => {
  it('treats an empty hash as the overview', () => {
    expect(parseRoute('').path).toBe('/')
    expect(parseRoute('#').path).toBe('/')
  })

  it('reads the path', () => {
    expect(parseRoute('#/incidents/abc').path).toBe('/incidents/abc')
  })

  it('reads the query', () => {
    const route = parseRoute('#/evaluations/compare?a=1&b=2')
    expect(route.path).toBe('/evaluations/compare')
    expect(route.query.get('a')).toBe('1')
    expect(route.query.get('b')).toBe('2')
  })
})
