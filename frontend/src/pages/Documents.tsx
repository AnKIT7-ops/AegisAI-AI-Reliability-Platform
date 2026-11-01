import { useCallback, useRef } from 'react'
import type { FormEvent } from 'react'
import { api } from '../api'
import { DemoBanner, Empty, ErrorState, Loading, Tag } from '../components/States'
import { fileSize } from '../format'
import type { DemoDocument, DemoStatus, DocumentRecord } from '../types'
import { useAction, useApi } from '../useApi'

interface Uploaded {
  document: DocumentRecord
  created: boolean
}

function Table({
  caption,
  rows,
  captionHidden = false,
}: {
  caption: string
  rows: DemoDocument[]
  captionHidden?: boolean
}) {
  return (
    <div className="table-scroll">
      <table>
        <caption className={captionHidden ? 'sr-only' : undefined}>
          {caption}
        </caption>
        <thead>
          <tr>
            <th scope="col">File</th>
            <th scope="col" className="number">
              Size
            </th>
            <th scope="col" className="number">
              Chunks
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.filename}>
              <td>{row.filename}</td>
              <td className="number">{fileSize(row.size_bytes)}</td>
              <td className="number">{row.chunks}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function Documents() {
  const documents = useApi<{ documents: DocumentRecord[] }>('/documents')
  const demo = useApi<DemoStatus>('/demo')
  const input = useRef<HTMLInputElement>(null)
  const upload = useAction(
    useCallback((file: File) => {
      const form = new FormData()
      form.append('file', file)
      return api<Uploaded>('/documents/ingest', { method: 'POST', form })
    }, []),
  )

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const file = input.current?.files?.[0]
    if (!file) return
    if (await upload.run(file)) {
      if (input.current) input.current.value = ''
      documents.reload()
    }
  }

  const demoDocuments = demo.data?.loaded ? demo.data.documents : []

  return (
    <>
      <h1>Documents</h1>
      {demoDocuments.length > 0 && <DemoBanner />}
      <form className="upload" onSubmit={submit}>
        <label htmlFor="file">Add a Markdown or text file, up to 1 MB</label>
        <input id="file" ref={input} type="file" accept=".md,.txt" required />
        <button type="submit" disabled={upload.loading}>
          {upload.loading ? 'Adding…' : 'Add document'}
        </button>
        <p className="meta">Adding a document needs Ollama running.</p>
      </form>
      {upload.error && <ErrorState error={upload.error} />}
      {upload.data && (
        <p role="status">
          {upload.data.created
            ? `Added ${upload.data.document.filename}.`
            : `${upload.data.document.filename} was already added.`}
        </p>
      )}

      {documents.loading && documents.data === null && (
        <Loading label="Loading documents…" />
      )}
      {documents.error && (
        <ErrorState error={documents.error} onRetry={documents.reload} />
      )}
      {documents.data &&
        (documents.data.documents.length > 0 ? (
          <Table caption="Your documents" rows={documents.data.documents} />
        ) : (
          <Empty title="No documents added yet">
            <p>Add a file above, and Chat can answer questions from it.</p>
          </Empty>
        ))}

      {demoDocuments.length > 0 && (
        <section aria-labelledby="demo-documents">
          <h2 id="demo-documents">
            Demo documents <Tag>Demo</Tag>
          </h2>
          <p className="meta">
            The documents behind the recorded answers. They are listed here and
            are not in your document index.
          </p>
          <Table caption="Demo documents" rows={demoDocuments} captionHidden />
        </section>
      )}
    </>
  )
}
