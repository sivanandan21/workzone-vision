import React, { useState } from 'react';
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock3,
  Download,
  Flag,
  Image as ImageIcon,
  UserCheck,
} from 'lucide-react';
import type { MovementLog, UserRole, VerificationStatus } from '../types/workzone';
import { resolveSnapshotUrl } from '../api/client';
import { formatDuration } from './RoomStatusBoard';

interface MovementLogsTableProps {
  logs: MovementLog[];
  userRole: UserRole;
  statusFilter: string;
  personFilter: string;
  onStatusFilterChange: (value: string) => void;
  onPersonFilterChange: (value: string) => void;
  onAction: (action: 'verify' | 'flag' | 'adjust' | 'assign', log: MovementLog) => void;
  onPreviewSnapshot: (url: string, title: string) => void;
  onExportCsv: () => void;
}

const STATUS_BADGE: Record<VerificationStatus, string> = {
  AUTO_MATCHED: 'bg-sky-500/20 text-sky-300 border-sky-500/40',
  PENDING_REVIEW: 'bg-amber-500/20 text-amber-300 border-amber-500/40',
  VERIFIED: 'bg-emerald-500/20 text-emerald-300 border-emerald-500/40',
  FLAGGED: 'bg-rose-500/20 text-rose-300 border-rose-500/40',
};

export const MovementLogsTable: React.FC<MovementLogsTableProps> = ({
  logs,
  userRole,
  statusFilter,
  personFilter,
  onStatusFilterChange,
  onPersonFilterChange,
  onAction,
  onPreviewSnapshot,
  onExportCsv,
}) => {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const canEdit = userRole === 'Authorities';

  return (
    <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-white">Movement & Away-Duration Verification Log</h2>
          <p className="text-xs text-slate-400">
            Verify crossings, resolve unknown/reconciled tracks, adjust timestamps, and inspect S3 snapshot crops.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <input
            type="text"
            value={personFilter}
            onChange={(e) => onPersonFilterChange(e.target.value)}
            placeholder="Filter Person ID (e.g. 001)"
            className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-1.5 text-xs text-slate-100 placeholder-slate-500 focus:border-sky-500 focus:outline-none"
          />

          <select
            aria-label="Filter verification status"
            value={statusFilter}
            onChange={(e) => onStatusFilterChange(e.target.value)}
            className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-1.5 text-xs text-slate-100 focus:border-sky-500 focus:outline-none"
          >
            <option value="ALL">All Statuses</option>
            <option value="PENDING_REVIEW">Pending Review</option>
            <option value="AUTO_MATCHED">Auto Matched</option>
            <option value="VERIFIED">Verified</option>
            <option value="FLAGGED">Flagged</option>
          </select>

          <button
            type="button"
            onClick={onExportCsv}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-800 px-3 py-1.5 text-xs font-medium text-slate-200 hover:bg-slate-700"
          >
            <Download className="h-3.5 w-3.5" />
            Export CSV
          </button>
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-left text-xs">
          <thead>
            <tr className="border-b border-slate-800 text-slate-400">
              <th className="py-2 pr-2">Audit</th>
              <th className="py-2 pr-3">Timestamp (UTC)</th>
              <th className="py-2 pr-3">Person</th>
              <th className="py-2 pr-3">Transition</th>
              <th className="py-2 pr-3">Away Duration</th>
              <th className="py-2 pr-3">Snapshot</th>
              <th className="py-2 pr-3">Status</th>
              <th className="py-2 text-right">Authority Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/70">
            {logs.length === 0 ? (
              <tr>
                <td colSpan={8} className="py-8 text-center text-slate-500">
                  No movement logs match the current filter.
                </td>
              </tr>
            ) : (
              logs.map((log) => {
                const snapUrl = resolveSnapshotUrl(log.snapshot_url || log.snapshot_s3_key);
                const isExpanded = expandedId === log.log_id;
                return (
                  <React.Fragment key={log.log_id}>
                    <tr className="hover:bg-slate-800/40">
                      <td className="py-2.5 pr-2">
                        <button
                          type="button"
                          onClick={() => setExpandedId(isExpanded ? null : log.log_id)}
                          className="rounded p-1 text-slate-400 hover:bg-slate-800 hover:text-white"
                          title="Toggle audit trail"
                        >
                          {isExpanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                        </button>
                      </td>
                      <td className="whitespace-nowrap py-2.5 pr-3 font-mono text-slate-300">
                        {new Date(log.timestamp).toLocaleString()}
                      </td>
                      <td className="py-2.5 pr-3">
                        <div className="font-medium text-white">{log.display_name}</div>
                        <div className="text-[11px] text-slate-400">
                          ID: {log.person_id} · {(log.confidence * 100).toFixed(0)}% · {log.camera_id}
                        </div>
                      </td>
                      <td className="py-2.5 pr-3">
                        <div className="font-medium text-slate-200">{log.event_type}</div>
                        <div className="text-[11px] text-slate-400">
                          {log.from_zone} &rarr; {log.to_zone}
                        </div>
                      </td>
                      <td className="whitespace-nowrap py-2.5 pr-3 font-mono">
                        {log.away_duration_seconds !== null ? (
                          <span className="font-semibold text-amber-300">
                            {formatDuration(log.away_duration_seconds)}
                          </span>
                        ) : (
                          <span className="text-slate-500">—</span>
                        )}
                      </td>
                      <td className="py-2.5 pr-3">
                        {snapUrl ? (
                          <button
                            type="button"
                            onClick={() =>
                              onPreviewSnapshot(
                                snapUrl,
                                `${log.display_name} (${log.person_id}) · ${log.event_type}`,
                              )
                            }
                            className="inline-flex items-center gap-1 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-[11px] text-sky-300 hover:border-sky-500"
                          >
                            <ImageIcon className="h-3 w-3" /> View Crop
                          </button>
                        ) : (
                          <span className="text-slate-600">No crop</span>
                        )}
                      </td>
                      <td className="py-2.5 pr-3">
                        <span
                          className={`inline-block rounded-full border px-2 py-0.5 text-[10px] font-semibold ${STATUS_BADGE[log.verification_status]}`}
                        >
                          {log.verification_status}
                        </span>
                        {log.remarks && (
                          <div className="mt-1 max-w-[200px] truncate text-[10px] text-slate-400" title={log.remarks}>
                            {log.remarks}
                          </div>
                        )}
                      </td>
                      <td className="whitespace-nowrap py-2.5 text-right">
                        {canEdit ? (
                          <div className="inline-flex items-center gap-1">
                            {log.person_id === 'UNKNOWN_TRACK' && (
                              <button
                                type="button"
                                onClick={() => onAction('assign', log)}
                                className="inline-flex items-center gap-1 rounded bg-sky-600/20 px-2 py-1 text-[11px] font-medium text-sky-300 hover:bg-sky-600/40"
                              >
                                <UserCheck className="h-3 w-3" /> Assign
                              </button>
                            )}
                            <button
                              type="button"
                              onClick={() => onAction('verify', log)}
                              className="inline-flex items-center gap-1 rounded bg-emerald-600/20 px-2 py-1 text-[11px] font-medium text-emerald-300 hover:bg-emerald-600/40"
                            >
                              <CheckCircle2 className="h-3 w-3" /> Verify
                            </button>
                            <button
                              type="button"
                              onClick={() => onAction('flag', log)}
                              className="inline-flex items-center gap-1 rounded bg-rose-600/20 px-2 py-1 text-[11px] font-medium text-rose-300 hover:bg-rose-600/40"
                            >
                              <Flag className="h-3 w-3" /> Flag
                            </button>
                            <button
                              type="button"
                              onClick={() => onAction('adjust', log)}
                              className="inline-flex items-center gap-1 rounded bg-amber-600/20 px-2 py-1 text-[11px] font-medium text-amber-300 hover:bg-amber-600/40"
                            >
                              <Clock3 className="h-3 w-3" /> Adjust
                            </button>
                          </div>
                        ) : (
                          <span className="text-[11px] text-slate-500">Read-only (Viewer)</span>
                        )}
                      </td>
                    </tr>
                    {isExpanded && (
                      <tr className="bg-slate-950/60">
                        <td colSpan={8} className="px-6 py-3">
                          <div className="text-[11px] font-semibold text-slate-300">Audit Trail</div>
                          {log.audit_history.length === 0 ? (
                            <p className="mt-1 text-[11px] text-slate-500">No authority modifications recorded yet.</p>
                          ) : (
                            <ul className="mt-1.5 space-y-1">
                              {log.audit_history.map((entry, idx) => (
                                <li key={idx} className="text-[11px] text-slate-400">
                                  <span className="font-semibold text-slate-200">{entry.action}</span> by{' '}
                                  <span className="text-sky-300">{entry.actor}</span> at{' '}
                                  <span className="font-mono">{entry.timestamp}</span>
                                  {entry.remarks ? ` — "${entry.remarks}"` : ''}
                                </li>
                              ))}
                            </ul>
                          )}
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
};
