import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { api, type FileItem, type Job, type ResultPage, type S3Connection } from './api'

type ConnectionForm = {
  access_key_id: string
  secret_access_key: string
  session_token: string
  region: string
  bucket: string
}

const terminal = new Set(['SUCCESS', 'FAILED', 'CANCELLED'])

function formatBytes(bytes: number): string {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  return `${(bytes / 1024 ** exponent).toFixed(exponent ? 1 : 0)} ${units[exponent]}`
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : 'Something went wrong.'
}

export default function App() {
  const [connectionForm, setConnectionForm] = useState<ConnectionForm>({
    access_key_id: '', secret_access_key: '', session_token: '', region: 'ap-southeast-2', bucket: '',
  })
  const [connection, setConnection] = useState<S3Connection | null>(null)
  const [selectedFile, setSelectedFile] = useState<FileItem | null>(null)
  const [columns, setColumns] = useState<string[]>([])
  const [selectedColumns, setSelectedColumns] = useState<string[]>([])
  const [requestText, setRequestText] = useState('Find email addresses')
  const [replacement, setReplacement] = useState('REDACTED')
  const [operation, setOperation] = useState('auto')
  const [job, setJob] = useState<Job | null>(null)
  const [results, setResults] = useState<ResultPage | null>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)

  const canSubmit = Boolean(connection && selectedFile && selectedColumns.length && requestText.trim() && !busy)
  const fileCountLabel = useMemo(() => connection ? `${connection.files.length} supported file${connection.files.length === 1 ? '' : 's'} found` : '', [connection])

  useEffect(() => {
    if (!job || terminal.has(job.status)) {
      if (job?.status === 'SUCCESS') void loadResults(1)
      return
    }
    const timer = window.setInterval(() => {
      void api.getJob(job.id).then(setJob).catch((error) => setNotice(errorText(error)))
    }, 1500)
    return () => window.clearInterval(timer)
  }, [job?.id, job?.status]) // eslint-disable-line react-hooks/exhaustive-deps

  async function connect(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setNotice(null)
    try {
      const next = await api.connectS3(connectionForm)
      setConnection(next)
      setConnectionForm((form) => ({ ...form, secret_access_key: '', session_token: '' }))
      setSelectedFile(null)
      setColumns([])
      setSelectedColumns([])
      setJob(null)
      setResults(null)
      setNotice(`Connected to ${next.bucket}. ${next.files.length} CSV or Excel file(s) are available.`)
    } catch (error) {
      setNotice(errorText(error))
    } finally {
      setBusy(false)
    }
  }

  async function chooseFile(key: string) {
    const file = connection?.files.find((item) => item.key === key) || null
    setSelectedFile(file)
    setColumns([])
    setSelectedColumns([])
    setResults(null)
    if (!connection || !file) return
    setBusy(true)
    setNotice(null)
    try {
      const response = await api.inspectSource(connection.connection_id, file.key)
      setColumns(response.columns)
      setSelectedColumns(response.columns.length === 1 ? response.columns : [])
      setNotice(`Loaded ${response.columns.length} column name(s). Choose the fields to transform.`)
    } catch (error) {
      setNotice(errorText(error))
    } finally {
      setBusy(false)
    }
  }

  function toggleColumn(column: string) {
    setSelectedColumns((current) => current.includes(column) ? current.filter((value) => value !== column) : [...current, column])
  }

  async function submitJob(event: FormEvent) {
    event.preventDefault()
    if (!connection || !selectedFile) return
    setBusy(true)
    setNotice(null)
    try {
      const next = await api.createJob({
        connection_id: connection.connection_id,
        source_key: selectedFile.key,
        selected_columns: selectedColumns,
        natural_language_request: requestText,
        replacement_value: replacement,
        requested_operation: operation,
      })
      setJob(next)
      setResults(null)
      setNotice('Job queued. You can safely leave this page open while it runs.')
    } catch (error) {
      setNotice(errorText(error))
    } finally {
      setBusy(false)
    }
  }

  async function cancelJob() {
    if (!job) return
    setBusy(true)
    try {
      setJob(await api.cancelJob(job.id))
    } catch (error) {
      setNotice(errorText(error))
    } finally {
      setBusy(false)
    }
  }

  async function loadResults(page: number) {
    if (!job) return
    try {
      setResults(await api.getResults(job.id, page))
    } catch (error) {
      setNotice(errorText(error))
    }
  }

  return (
    <main>
      <header className="hero">
        <div className="eyebrow">DISTRIBUTED DATA CLEANING</div>
        <h1>Regex Data Processor</h1>
        <p>Connect a bucket you control, describe the change in plain language, and process large tabular files asynchronously.</p>
      </header>

      {notice && <div className="notice" role="status">{notice}</div>}

      <section className="card" aria-labelledby="connection-title">
        <div className="section-heading"><span>1</span><div><h2 id="connection-title">Connect Amazon S3</h2><p>Credentials are validated once, encrypted in short-lived storage, and never saved in a job.</p></div></div>
        <form className="form-grid" onSubmit={connect}>
          <label>AWS access key ID<input required autoComplete="off" value={connectionForm.access_key_id} onChange={(event) => setConnectionForm({ ...connectionForm, access_key_id: event.target.value })} /></label>
          <label>AWS secret access key<input required type="password" autoComplete="new-password" value={connectionForm.secret_access_key} onChange={(event) => setConnectionForm({ ...connectionForm, secret_access_key: event.target.value })} /></label>
          <label>Session token <small>optional</small><input type="password" autoComplete="new-password" value={connectionForm.session_token} onChange={(event) => setConnectionForm({ ...connectionForm, session_token: event.target.value })} /></label>
          <label>Region <small>optional</small><input placeholder="ap-southeast-2" value={connectionForm.region} onChange={(event) => setConnectionForm({ ...connectionForm, region: event.target.value })} /></label>
          <label className="wide">Bucket name<input required placeholder="my-private-data-bucket" value={connectionForm.bucket} onChange={(event) => setConnectionForm({ ...connectionForm, bucket: event.target.value })} /></label>
          <div className="button-wrap"><button disabled={busy}>{busy ? 'Working…' : 'Connect and browse files'}</button></div>
        </form>
      </section>

      {connection && <section className="card" aria-labelledby="job-title">
        <div className="section-heading"><span>2</span><div><h2 id="job-title">Configure transformation</h2><p>{fileCountLabel}. Only CSV, XLSX and XLS objects are shown.</p></div></div>
        <form onSubmit={submitJob}>
          <div className="form-grid">
            <label className="wide">Source file
              <select required value={selectedFile?.key || ''} onChange={(event) => void chooseFile(event.target.value)}>
                <option value="">Choose a file…</option>
                {connection.files.map((file) => <option key={file.key} value={file.key}>{file.key} ({formatBytes(file.size)})</option>)}
              </select>
            </label>
            <label className="wide">Describe the pattern or transformation
              <input required value={requestText} onChange={(event) => setRequestText(event.target.value)} placeholder="Find email addresses" />
            </label>
            <label>Mode
              <select value={operation} onChange={(event) => setOperation(event.target.value)}>
                <option value="auto">Auto - resolve from description</option>
                <option value="replace">Regex replacement</option>
                <option value="normalize_whitespace">Normalize whitespace</option>
                <option value="lowercase">Lowercase</option>
                <option value="uppercase">Uppercase</option>
                <option value="trim">Trim</option>
              </select>
            </label>
            <label>Replacement value <small>for replacement mode</small><input value={replacement} onChange={(event) => setReplacement(event.target.value)} placeholder="REDACTED" /></label>
          </div>

          <fieldset disabled={!columns.length || busy}>
            <legend>Target columns</legend>
            {!columns.length ? <p className="muted">Select a source file to load its header.</p> : <div className="check-grid">{columns.map((column) => <label className="check" key={column}><input type="checkbox" checked={selectedColumns.includes(column)} onChange={() => toggleColumn(column)} />{column}</label>)}</div>}
          </fieldset>
          <div className="actions"><button disabled={!canSubmit}>{busy ? 'Working…' : 'Run asynchronous job'}</button></div>
        </form>
      </section>}

      {job && <section className="card job-card" aria-live="polite">
        <div className="job-top"><div><div className={`status ${job.status.toLowerCase()}`}>{job.status}</div><h2>{job.source.key}</h2><p>{job.total_rows === null ? 'Preparing dataset…' : `${job.processed_rows.toLocaleString()} / ${job.total_rows.toLocaleString()} rows`}</p></div>{!terminal.has(job.status) && <button className="secondary" disabled={busy} onClick={() => void cancelJob()}>Cancel job</button>}</div>
        <div className="progress-track"><div className="progress-fill" style={{ width: `${job.progress}%` }} /></div><strong>{job.progress}%</strong>
        {job.regex_pattern && <p className="rule">Resolved regex: <code>{job.regex_pattern}</code></p>}
        {job.resolved_operation && <p className="muted">Applied operation: {job.resolved_operation.replaceAll('_', ' ')}</p>}
        {job.error && <p className="error">{job.error}</p>}
      </section>}

      {results && <section className="card" aria-labelledby="results-title">
        <div className="results-title"><div><h2 id="results-title">Processed data</h2><p>Showing {results.preview_rows.toLocaleString()} row{results.preview_rows === 1 ? '' : 's'}{results.is_bounded_preview ? ` of ${results.total_rows?.toLocaleString()} total rows as a bounded browser preview.` : '.'}</p></div><div className="pager"><button className="secondary" disabled={!results.has_previous} onClick={() => void loadResults(results.page - 1)}>Previous</button><span>Page {results.page}</span><button className="secondary" disabled={!results.has_next} onClick={() => void loadResults(results.page + 1)}>Next</button></div></div>
        <div className="table-wrap"><table><thead><tr>{results.columns.map((column) => <th key={column}>{column}</th>)}</tr></thead><tbody>{results.rows.map((row, index) => <tr key={index}>{results.columns.map((column) => <td key={column}>{row[column] === null || row[column] === undefined ? '' : String(row[column])}</td>)}</tr>)}</tbody></table></div>
      </section>}
    </main>
  )
}
