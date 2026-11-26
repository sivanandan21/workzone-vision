import { useState, type FormEvent } from 'react';
import { Calendar, Download, FileSpreadsheet, FileText, X } from 'lucide-react';
import type { ReportDuration, ReportExportOptions, ReportFormat } from '../api/client';

interface ReportDownloadModalProps {
  isOpen: boolean;
  exporting: boolean;
  errorMessage: string | null;
  onClose: () => void;
  onDownload: (options: ReportExportOptions) => Promise<void>;
}

const DURATION_OPTIONS: Array<{ value: ReportDuration; label: string; hint: string }> = [
  { value: 'today', label: 'Today', hint: 'From midnight today until now' },
  { value: 'last_24h', label: 'Last 24 Hours', hint: 'Rolling 24-hour activity window' },
  { value: 'last_7d', label: 'Last 7 Days', hint: 'Past week summary & logs' },
  { value: 'last_30d', label: 'Last 30 Days', hint: 'Monthly attendance & away times' },
  { value: 'all', label: 'All Recorded Activity', hint: 'Complete available history' },
  { value: 'custom', label: 'Custom Date Range', hint: 'Pick start and end calendar dates' },
];

function formatDateInput(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

export function ReportDownloadModal({
  isOpen,
  exporting,
  errorMessage,
  onClose,
  onDownload,
}: ReportDownloadModalProps) {
  const [duration, setDuration] = useState<ReportDuration>('last_7d');
  const [format, setFormat] = useState<ReportFormat>('pdf');
  const [startDate, setStartDate] = useState<string>(() => {
    const d = new Date();
    d.setDate(d.getDate() - 7);
    return formatDateInput(d);
  });
  const [endDate, setEndDate] = useState<string>(() => formatDateInput(new Date()));
  const [validationError, setValidationError] = useState<string | null>(null);

  if (!isOpen) return null;

  const selectedDurationMeta =
    DURATION_OPTIONS.find((item) => item.value === duration) ?? DURATION_OPTIONS[2];

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setValidationError(null);

    if (duration === 'custom') {
      if (!startDate || !endDate) {
        setValidationError('Please select both a start date and an end date.');
        return;
      }
      if (startDate > endDate) {
        setValidationError('Start date must be on or before end date.');
        return;
      }
    }

    await onDownload({
      format,
      duration,
      startDate: duration === 'custom' ? startDate : undefined,
      endDate: duration === 'custom' ? endDate : undefined,
    });
  };

  const displayError = validationError || errorMessage;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"
      role="presentation"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="report-modal-title"
        className="w-full max-w-md rounded-xl border border-[#2c3852] bg-[#141c2c] text-slate-100 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-[#253047] px-5 py-3.5">
          <div className="flex items-center gap-2">
            <FileText className="h-4 w-4 text-blue-400" />
            <h2 id="report-modal-title" className="text-sm font-semibold text-white">
              Generate Movement Report
            </h2>
          </div>
          <button
            type="button"
            aria-label="Close report dialog"
            onClick={onClose}
            className="rounded-md p-1 text-slate-400 transition hover:bg-[#1e2940] hover:text-white"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <form onSubmit={(e) => void handleSubmit(e)} className="space-y-4 px-5 py-4">
          {/* Duration selector */}
          <div>
            <label
              htmlFor="report-duration-select"
              className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-slate-300"
            >
              <Calendar className="h-3.5 w-3.5 text-slate-400" />
              Report Duration
            </label>
            <select
              id="report-duration-select"
              value={duration}
              onChange={(e) => {
                setDuration(e.target.value as ReportDuration);
                setValidationError(null);
              }}
              className="w-full rounded-lg border border-[#2c3852] bg-[#0e1523] px-3 py-2 text-xs text-white focus:border-blue-500 focus:outline-none"
            >
              {DURATION_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
            <p className="mt-1 text-[11px] text-slate-400">{selectedDurationMeta.hint}</p>
          </div>

          {/* Custom Date Inputs */}
          {duration === 'custom' && (
            <div className="grid grid-cols-2 gap-3 rounded-lg border border-[#253047] bg-[#0e1523] p-3">
              <div>
                <label
                  htmlFor="report-start-date"
                  className="mb-1 block text-[11px] font-medium text-slate-300"
                >
                  From Date
                </label>
                <input
                  id="report-start-date"
                  type="date"
                  value={startDate}
                  max={endDate || undefined}
                  onChange={(e) => setStartDate(e.target.value)}
                  className="w-full rounded border border-[#2c3852] bg-[#141c2c] px-2.5 py-1.5 text-xs text-white focus:border-blue-500 focus:outline-none"
                />
              </div>
              <div>
                <label
                  htmlFor="report-end-date"
                  className="mb-1 block text-[11px] font-medium text-slate-300"
                >
                  To Date
                </label>
                <input
                  id="report-end-date"
                  type="date"
                  value={endDate}
                  min={startDate || undefined}
                  onChange={(e) => setEndDate(e.target.value)}
                  className="w-full rounded border border-[#2c3852] bg-[#141c2c] px-2.5 py-1.5 text-xs text-white focus:border-blue-500 focus:outline-none"
                />
              </div>
            </div>
          )}

          {/* File Format selector */}
          <div>
            <span className="mb-1.5 block text-xs font-medium text-slate-300">File Format</span>
            <div className="grid grid-cols-2 gap-2.5">
              <button
                type="button"
                aria-pressed={format === 'pdf'}
                onClick={() => setFormat('pdf')}
                className={`flex flex-col items-start gap-1 rounded-lg border p-3 text-left transition ${
                  format === 'pdf'
                    ? 'border-blue-500 bg-blue-600/15 text-white'
                    : 'border-[#2c3852] bg-[#0e1523] text-slate-300 hover:border-slate-500'
                }`}
              >
                <span className="inline-flex items-center gap-1.5 text-xs font-semibold">
                  <FileText className="h-3.5 w-3.5 text-rose-400" />
                  PDF (.pdf)
                </span>
                <span className="text-[11px] text-slate-400">
                  Formatted KPIs, student summary &amp; activity table
                </span>
              </button>

              <button
                type="button"
                aria-pressed={format === 'xlsx'}
                onClick={() => setFormat('xlsx')}
                className={`flex flex-col items-start gap-1 rounded-lg border p-3 text-left transition ${
                  format === 'xlsx'
                    ? 'border-blue-500 bg-blue-600/15 text-white'
                    : 'border-[#2c3852] bg-[#0e1523] text-slate-300 hover:border-slate-500'
                }`}
              >
                <span className="inline-flex items-center gap-1.5 text-xs font-semibold">
                  <FileSpreadsheet className="h-3.5 w-3.5 text-emerald-400" />
                  Excel (.xlsx)
                </span>
                <span className="text-[11px] text-slate-400">
                  2-sheet workbook: Summary &amp; Movement Details
                </span>
              </button>
            </div>
          </div>

          {displayError && (
            <div className="rounded-md border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
              {displayError}
            </div>
          )}

          <div className="flex items-center justify-end gap-2.5 border-t border-[#253047] pt-3">
            <button
              type="button"
              onClick={onClose}
              disabled={exporting}
              className="rounded-md border border-[#2c3852] bg-[#1c263b] px-3.5 py-1.5 text-xs font-medium text-slate-300 transition hover:bg-[#24314d] hover:text-white disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={exporting}
              className="inline-flex items-center gap-1.5 rounded-md bg-blue-600 px-4 py-1.5 text-xs font-medium text-white transition hover:bg-blue-500 disabled:opacity-60"
            >
              <Download className="h-3.5 w-3.5" />
              {exporting ? 'Preparing...' : `Download ${format.toUpperCase()}`}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
