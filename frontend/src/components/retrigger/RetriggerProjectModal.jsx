import { useState, useEffect } from 'react'
import { X } from 'lucide-react'

export default function RetriggerProjectModal({ project, onClose, onSave }) {
  const [formData, setFormData] = useState({
    project_name: '',
    s3_bucket: '',
    dynamodb_table: '',
    aws_region: 'us-east-1',
    batch_size: 10,
    batch_sleep_secs: 45,
    destination_bucket: '',
    cloudwatch_log_group: '',
    payload_filename: 'api_payload.json',
    log_lookback_seconds: 604800,
  })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (project) {
      setFormData({
        project_name: project.project_name || '',
        s3_bucket: project.s3_bucket || '',
        dynamodb_table: project.dynamodb_table || '',
        aws_region: project.aws_region || 'us-east-1',
        batch_size: project.batch_size || 10,
        batch_sleep_secs: project.batch_sleep_secs || 45,
        destination_bucket: project.destination_bucket || '',
        cloudwatch_log_group: project.cloudwatch_log_group || '',
        payload_filename: project.payload_filename || 'api_payload.json',
        log_lookback_seconds: project.log_lookback_seconds || 604800,
      })
    }
  }, [project])

  async function handleSubmit(e) {
    e.preventDefault()
    setSaving(true)
    setError(null)

    try {
      await onSave(formData)
      onClose()
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  function handleChange(field, value) {
    setFormData((prev) => ({ ...prev, [field]: value }))
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-surface rounded-2xl shadow-2xl max-w-2xl w-full max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b border-border">
          <div>
            <h3 className="text-text-primary font-bold text-lg">
              {project ? 'Edit Bulk Ops Project' : 'New Bulk Ops Project'}
            </h3>
            <p className="text-text-muted text-sm mt-1">
              Configure S3, DynamoDB, and optional fetch settings (payload / PDF / logs)
            </p>
          </div>
          <button
            onClick={onClose}
            className="w-8 h-8 rounded-lg flex items-center justify-center hover:bg-background transition-colors"
          >
            <X size={18} className="text-text-muted" />
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="p-6">
          {error && (
            <div className="bg-danger-bg border border-danger/20 rounded-xl p-4 mb-6">
              <p className="text-danger text-sm font-medium">{error}</p>
            </div>
          )}

          <div className="space-y-5">
            {/* Project Name */}
            <div>
              <label className="block text-text-secondary text-sm font-medium mb-2">
                Project Name *
              </label>
              <input
                type="text"
                value={formData.project_name}
                onChange={(e) => handleChange('project_name', e.target.value)}
                placeholder="e.g., GE Freight Invoices"
                className="w-full text-sm"
                required
              />
            </div>

            {/* S3 Bucket */}
            <div>
              <label className="block text-text-secondary text-sm font-medium mb-2">
                S3 Bucket *
              </label>
              <input
                type="text"
                value={formData.s3_bucket}
                onChange={(e) => handleChange('s3_bucket', e.target.value)}
                placeholder="e.g., my-invoice-bucket"
                className="w-full text-sm font-mono"
                required
              />
              <p className="text-text-muted text-xs mt-1">
                The S3 bucket where invoice PDFs are stored
              </p>
            </div>

            {/* DynamoDB Table */}
            <div>
              <label className="block text-text-secondary text-sm font-medium mb-2">
                DynamoDB Table *
              </label>
              <input
                type="text"
                value={formData.dynamodb_table}
                onChange={(e) => handleChange('dynamodb_table', e.target.value)}
                placeholder="e.g., invoice-processing-table"
                className="w-full text-sm font-mono"
                required
              />
              <p className="text-text-muted text-xs mt-1">
                The DynamoDB table storing processed invoice records
              </p>
            </div>

            {/* AWS Region */}
            <div>
              <label className="block text-text-secondary text-sm font-medium mb-2">
                AWS Region *
              </label>
              <select
                value={formData.aws_region}
                onChange={(e) => handleChange('aws_region', e.target.value)}
                className="w-full text-sm"
                required
              >
                <option value="us-east-1">US East (N. Virginia) - us-east-1</option>
                <option value="us-east-2">US East (Ohio) - us-east-2</option>
                <option value="us-west-1">US West (N. California) - us-west-1</option>
                <option value="us-west-2">US West (Oregon) - us-west-2</option>
                <option value="ap-south-1">Asia Pacific (Mumbai) - ap-south-1</option>
                <option value="ap-southeast-1">Asia Pacific (Singapore) - ap-southeast-1</option>
                <option value="ap-southeast-2">Asia Pacific (Sydney) - ap-southeast-2</option>
                <option value="eu-central-1">Europe (Frankfurt) - eu-central-1</option>
                <option value="eu-west-1">Europe (Ireland) - eu-west-1</option>
              </select>
            </div>


            <div>
              <label className="block text-text-secondary text-sm font-medium mb-2">
                Destination Bucket (fetch PDF / payload)
              </label>
              <input
                type="text"
                value={formData.destination_bucket}
                onChange={(e) => handleChange('destination_bucket', e.target.value)}
                placeholder="e.g., nam-pando-prod-delicato-bucket"
                className="w-full text-sm font-mono"
              />
              <p className="text-text-muted text-xs mt-1">
                Optional. Overrides bucket in s3_path / output_path when downloading.
              </p>
            </div>

            <div>
              <label className="block text-text-secondary text-sm font-medium mb-2">
                CloudWatch Log Group (Batch or Lambda)
              </label>
              <input
                type="text"
                value={formData.cloudwatch_log_group}
                onChange={(e) => handleChange('cloudwatch_log_group', e.target.value)}
                placeholder="e.g., /aws/batch/... or /aws/lambda/..."
                className="w-full text-sm font-mono"
              />
              <p className="text-text-muted text-xs mt-1">
                Required for Fetch logs. Prefer Batch group when it exists.
              </p>
            </div>

            <div>
              <label className="block text-text-secondary text-sm font-medium mb-2">
                Payload filename
              </label>
              <input
                type="text"
                value={formData.payload_filename}
                onChange={(e) => handleChange('payload_filename', e.target.value)}
                placeholder="api_payload.json"
                className="w-full text-sm font-mono"
              />
            </div>

            <div className="grid grid-cols-2 gap-4">
              {/* Batch Size */}
              <div>
                <label className="block text-text-secondary text-sm font-medium mb-2">
                  Batch Size
                </label>
                <input
                  type="number"
                  min="1"
                  max="100"
                  value={formData.batch_size}
                  onChange={(e) => handleChange('batch_size', parseInt(e.target.value, 10))}
                  className="w-full text-sm"
                  required
                />
                <p className="text-text-muted text-xs mt-1">
                  Files per batch (1-100)
                </p>
              </div>

              {/* Batch Sleep */}
              <div>
                <label className="block text-text-secondary text-sm font-medium mb-2">
                  Batch Sleep (seconds)
                </label>
                <input
                  type="number"
                  min="0"
                  max="300"
                  value={formData.batch_sleep_secs}
                  onChange={(e) => handleChange('batch_sleep_secs', parseInt(e.target.value, 10))}
                  className="w-full text-sm"
                  required
                />
                <p className="text-text-muted text-xs mt-1">
                  Delay between batches (0-300s)
                </p>
              </div>
            </div>
          </div>

          {/* Actions */}
          <div className="flex items-center justify-end gap-3 mt-8">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-sm font-medium rounded-xl transition-colors"
              style={{ background: '#F5F5F7', color: '#8B8B99' }}
              onMouseEnter={(e) => { e.currentTarget.style.background = '#EBEBEF' }}
              onMouseLeave={(e) => { e.currentTarget.style.background = '#F5F5F7' }}
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={saving}
              className="px-6 py-2 text-white rounded-xl text-sm font-semibold transition-colors shadow-sm disabled:opacity-50 disabled:cursor-not-allowed"
              style={{ background: '#6C5CE7' }}
              onMouseEnter={(e) => !saving && (e.currentTarget.style.background = '#5A4BD1')}
              onMouseLeave={(e) => (e.currentTarget.style.background = '#6C5CE7')}
            >
              {saving ? 'Saving...' : project ? 'Update Project' : 'Create Project'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
