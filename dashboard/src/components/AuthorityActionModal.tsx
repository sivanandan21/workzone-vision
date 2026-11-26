import React, { useState } from 'react';
import { X } from 'lucide-react';
import type { MovementLog } from '../types/workzone';

interface AuthorityActionModalProps {
  mode: 'verify' | 'flag' | 'adjust' | 'assign';
  log: MovementLog;
  onClose: () => void;
  onSubmit: (data: {
    remarks: string;
    newTimestamp?: string;
    assignPersonId?: string;
    assignDisplayName?: string;
  }) => Promise<void>;
}

export const AuthorityActionModal: React.FC<AuthorityActionModalProps> = ({
  mode,
  log,
  onClose,
  onSubmit,
}) => {
  const [remarks, setRemarks] = useState(
    mode === 'verify' ? 'Verified against camera snapshot' : '',
  );
  const [newTimestamp, setNewTimestamp] = useState(log.timestamp);
  const [assignPersonId, setAssignPersonId] = useState('001');
  const [assignDisplayName, setAssignDisplayName] = useState('Student 1');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const titleMap = {
    verify: 'Verify Movement Crossing',
    flag: 'Flag Movement Anomaly',
    adjust: 'Adjust Crossing Timestamp',
    assign: 'Assign Identity & Verify',
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if ((mode === 'flag' || mode === 'adjust') && !remarks.trim()) {
      setError('Audit remarks are required.');
      return;
    }
    setSubmitting(true);
    try {
      await onSubmit({
        remarks: remarks.trim(),
        newTimestamp: mode === 'adjust' ? newTimestamp : undefined,
        assignPersonId: mode === 'assign' ? assignPersonId.trim() : undefined,
        assignDisplayName: mode === 'assign' ? assignDisplayName.trim() : undefined,
      });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Action failed');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
      <div className="w-full max-w-md rounded-xl border border-slate-700 bg-slate-900 p-5 shadow-2xl">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-base font-semibold text-white">{titleMap[mode]}</h3>
          <button type="button" onClick={onClose} className="text-slate-400 hover:text-white">
            <X className="h-4 w-4" />
          </button>
        </div>

        <p className="mb-4 text-xs text-slate-400">
          Log <span className="font-mono text-slate-200">{log.log_id.slice(0, 8)}</span> ·{' '}
          {log.from_zone} &rarr; {log.to_zone}
        </p>

        <form onSubmit={handleSubmit} className="space-y-3">
          {mode === 'assign' && (
            <>
              <div>
                <label className="mb-1 block text-xs font-medium text-slate-300">
                  Target Student ID
                </label>
                <input
                  type="text"
                  value={assignPersonId}
                  onChange={(e) => setAssignPersonId(e.target.value)}
                  className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-white"
                  required
                />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-slate-300">
                  Display Name
                </label>
                <input
                  type="text"
                  value={assignDisplayName}
                  onChange={(e) => setAssignDisplayName(e.target.value)}
                  className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-white"
                  required
                />
              </div>
            </>
          )}

          {mode === 'adjust' && (
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-300">
                Corrected UTC Timestamp (ISO-8601)
              </label>
              <input
                type="text"
                value={newTimestamp}
                onChange={(e) => setNewTimestamp(e.target.value)}
                className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-xs text-white"
                required
              />
            </div>
          )}

          <div>
            <label className="mb-1 block text-xs font-medium text-slate-300">
              Audit Remarks {mode === 'flag' || mode === 'adjust' ? '(Required)' : '(Optional)'}
            </label>
            <textarea
              rows={3}
              value={remarks}
              onChange={(e) => setRemarks(e.target.value)}
              placeholder="Enter reason for audit trail..."
              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-white"
            />
          </div>

          {error && <p className="text-xs text-rose-400">{error}</p>}

          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="rounded-lg bg-sky-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-sky-500 disabled:opacity-50"
            >
              {submitting ? 'Saving...' : 'Confirm'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
