import React from 'react';
import { X } from 'lucide-react';

interface SnapshotPreviewModalProps {
  url: string;
  title: string;
  onClose: () => void;
}

export const SnapshotPreviewModal: React.FC<SnapshotPreviewModalProps> = ({
  url,
  title,
  onClose,
}) => (
  <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4">
    <div className="w-full max-w-lg rounded-xl border border-slate-700 bg-slate-900 p-4 shadow-2xl">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-white">{title}</h3>
        <button type="button" onClick={onClose} className="text-slate-400 hover:text-white">
          <X className="h-4 w-4" />
        </button>
      </div>
      <div className="flex justify-center overflow-hidden rounded-lg border border-slate-800 bg-black">
        <img src={url} alt={title} className="max-h-[420px] w-auto object-contain" />
      </div>
    </div>
  </div>
);
