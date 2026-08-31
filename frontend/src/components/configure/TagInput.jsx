import { useState } from 'react'
import { X } from 'lucide-react'

const isValidEmail = (email) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)

export default function TagInput({ tags, onChange, placeholder = 'Add email and press Enter' }) {
  const [inputValue, setInputValue] = useState('')
  const [error, setError] = useState('')

  const addTag = (value) => {
    const trimmed = value.trim().replace(/,$/, '')
    if (!trimmed) return
    if (!isValidEmail(trimmed)) {
      setError(`"${trimmed}" is not a valid email address`)
      return
    }
    if (tags.includes(trimmed)) {
      setError('This email is already added')
      return
    }
    onChange([...tags, trimmed])
    setInputValue('')
    setError('')
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault()
      addTag(inputValue)
    } else if (e.key === 'Backspace' && !inputValue && tags.length > 0) {
      onChange(tags.slice(0, -1))
      setError('')
    }
  }

  const removeTag = (tag) => {
    onChange(tags.filter((t) => t !== tag))
    setError('')
  }

  return (
    <div>
      <div
        className={`min-h-[42px] flex flex-wrap gap-1.5 p-2 bg-white border-[1.5px] rounded-lg transition-all focus-within:border-pando-green focus-within:shadow-[0_0_0_3px_rgba(0,61,49,0.10)] ${
          error ? 'border-danger' : 'border-border'
        }`}
      >
        {tags.map((tag) => (
          <span key={tag} className="inline-flex items-center gap-1 px-2.5 py-0.5 bg-pando-green-50 text-pando-green border border-pando-green-200 rounded-full text-xs font-medium">
            {tag}
            <button type="button" onClick={() => removeTag(tag)} className="text-pando-green/60 hover:text-pando-green transition-colors">
              <X size={10} />
            </button>
          </span>
        ))}
        <input
          type="text"
          value={inputValue}
          onChange={(e) => { setInputValue(e.target.value); setError('') }}
          onKeyDown={handleKeyDown}
          onBlur={() => inputValue && addTag(inputValue)}
          placeholder={tags.length === 0 ? placeholder : ''}
          className="flex-1 min-w-[160px] text-sm"
          style={{ background: 'transparent', border: 'none', boxShadow: 'none', padding: '2px 4px', outline: 'none' }}
        />
      </div>
      {error && <p className="text-danger text-xs mt-1">{error}</p>}
    </div>
  )
}
