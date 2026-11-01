import { describe, expect, it } from 'vitest'
import { isId, parseRoute } from './route'

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

describe('isId', () => {
  it('accepts a uuid', () => {
    expect(isId('0f8fad5b-d9cb-469f-a165-70867728950e')).toBe(true)
  })

  it('rejects anything that could change an API path', () => {
    for (const v of ['../demo', 'a/b', 'x?y=1', '', null, undefined]) {
      expect(isId(v)).toBe(false)
    }
  })
})
