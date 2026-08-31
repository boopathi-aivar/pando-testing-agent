import { useState } from 'react'
import { Copy, Check } from 'lucide-react'

function JsonNode({ data, depth }) {
  const [collapsed, setCollapsed] = useState(depth > 1)

  if (data === null) return <span style={{ color: '#6B7280' }}>null</span>
  if (typeof data === 'boolean') return <span style={{ color: '#7C3AED' }}>{String(data)}</span>
  if (typeof data === 'number') return <span style={{ color: '#B45309' }}>{data}</span>
  if (typeof data === 'string') return <span style={{ color: '#065F46' }}>"{data}"</span>

  if (Array.isArray(data)) {
    if (collapsed) {
      return (
        <span className="cursor-pointer hover:underline" style={{ color: '#6B7280' }} onClick={() => setCollapsed(false)}>
          [...{data.length} items]
        </span>
      )
    }
    return (
      <>
        <span className="cursor-pointer text-text-secondary" onClick={() => setCollapsed(true)}>[</span>
        <div style={{ marginLeft: 16 }}>
          {data.map((item, i) => (
            <div key={i}>
              <JsonNode data={item} depth={depth + 1} />
              {i < data.length - 1 && <span className="text-text-muted">,</span>}
            </div>
          ))}
        </div>
        <span className="text-text-secondary">]</span>
      </>
    )
  }

  if (typeof data === 'object') {
    const keys = Object.keys(data)
    if (collapsed) {
      return (
        <span className="cursor-pointer hover:underline" style={{ color: '#6B7280' }} onClick={() => setCollapsed(false)}>
          {'{'}...{keys.length} keys{'}'}
        </span>
      )
    }
    return (
      <>
        <span className="cursor-pointer text-text-secondary" onClick={() => setCollapsed(true)}>{'{'}</span>
        <div style={{ marginLeft: 16 }}>
          {keys.map((key, i) => (
            <div key={key}>
              <span style={{ color: '#6C5CE7' }}>"{key}"</span>
              <span className="text-text-secondary">: </span>
              <JsonNode data={data[key]} depth={depth + 1} />
              {i < keys.length - 1 && <span className="text-text-muted">,</span>}
            </div>
          ))}
        </div>
        <span className="text-text-secondary">{'}'}</span>
      </>
    )
  }

  return <span className="text-text-secondary">{String(data)}</span>
}

export default function JsonViewer({ data }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = () => {
    navigator.clipboard.writeText(JSON.stringify(data, null, 2))
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="relative bg-pando-green-50 border border-pando-green-100 rounded-xl p-4 overflow-auto max-h-80" style={{ fontFamily: 'monospace', fontSize: 13, lineHeight: 1.6 }}>
      <button
        onClick={handleCopy}
        className="absolute top-3 right-3 flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-white border border-border text-text-secondary hover:text-pando-green hover:border-pando-green transition-colors text-xs shadow-sm"
      >
        {copied ? <><Check size={12} className="text-success" /> Copied!</> : <><Copy size={12} /> Copy</>}
      </button>
      <JsonNode data={data} depth={0} />
    </div>
  )
}
