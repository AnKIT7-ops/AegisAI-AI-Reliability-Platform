export function percent(ratio: number): string {
  return `${(ratio * 100).toFixed(1)}%`
}

export function seconds(value: number): string {
  return value < 1 ? `${Math.round(value * 1000)} ms` : `${value.toFixed(1)} s`
}

export function fileSize(bytes: number): string {
  return bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(1)} KB`
}
