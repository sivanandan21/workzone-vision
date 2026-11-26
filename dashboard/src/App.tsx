import { useCallback, useEffect, useState } from 'react';
import { FileText, RefreshCw, Shield } from 'lucide-react';
import {
  exportLogsCsv,
  fetchStreamStatus,
  getStreamMjpegUrl,
  getStreamSnapshotUrl,
  type StreamStatusResponse,
} from './api/client';
import type { AuthSession } from './types/workzone';

const DEFAULT_SESSION: AuthSession = {
  username: 'authority@workzone.local',
  role: 'Authorities',
};

export function App() {
  const [liveFeed, setLiveFeed] = useState<boolean>(true);
  const [streamVersion, setStreamVersion] = useState<number>(() => Date.now());
  const [exporting, setExporting] = useState<boolean>(false);
  const [streamStatus, setStreamStatus] = useState<StreamStatusResponse>({
    cam_1: { live: false, frames_received: 0, last_frame_age_sec: null },
    cam_2: { live: false, frames_received: 0, last_frame_age_sec: null },
  });

  const pollStatus = useCallback(async () => {
    try {
      const status = await fetchStreamStatus();
      setStreamStatus(status);
    } catch {
      // Preserve last known status if offline
    }
  }, []);

  useEffect(() => {
    void pollStatus();
    const timer = setInterval(() => void pollStatus(), 3000);
    return () => clearInterval(timer);
  }, [pollStatus]);

  const handleGenerateReport = async () => {
    setExporting(true);
    try {
      const csv = await exportLogsCsv(DEFAULT_SESSION);
      const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `workzone_report_${new Date().toISOString().slice(0, 10)}.csv`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error('Report generation failed:', err);
    } finally {
      setExporting(false);
    }
  };

  const handleRefresh = () => {
    setStreamVersion(Date.now());
    void pollStatus();
  };

  const cam1Src = liveFeed
    ? `${getStreamMjpegUrl('cam_1')}?v=${streamVersion}`
    : `${getStreamSnapshotUrl('cam_1')}?v=${streamVersion}`;
  const cam2Src = liveFeed
    ? `${getStreamMjpegUrl('cam_2')}?v=${streamVersion}`
    : `${getStreamSnapshotUrl('cam_2')}?v=${streamVersion}`;

  const renderBadge = (camId: 'cam_1' | 'cam_2') => {
    if (!liveFeed) {
      return (
        <span className="inline-flex items-center gap-1 rounded bg-slate-600 px-2 py-0.5 text-[10px] font-bold tracking-wide text-white">
          <span className="h-1.5 w-1.5 rounded-full bg-white" />
          PAUSED
        </span>
      );
    }
    const isCamLive = streamStatus[camId]?.live;
    return (
      <span
        className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-[10px] font-bold tracking-wide text-white ${
          isCamLive ? 'bg-red-600' : 'bg-slate-700'
        }`}
      >
        <span className="h-1.5 w-1.5 rounded-full bg-white" />
        {isCamLive ? 'LIVE' : 'STANDBY'}
      </span>
    );
  };

  return (
    <div className="min-h-screen bg-[#0f1624] text-slate-100">
      {/* Top Header Bar */}
      <header className="border-b border-[#253047] bg-[#161e2e] px-5 py-3">
        <div className="mx-auto flex max-w-[1440px] items-center justify-between gap-4">
          <div className="flex items-center gap-2.5">
            <Shield className="h-5 w-5 text-slate-400" />
            <h1 className="text-sm font-semibold tracking-tight text-white">
              WorkZone Vision <span className="font-normal text-slate-400">— Simple Monitor</span>
            </h1>
          </div>

          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => void handleGenerateReport()}
              disabled={exporting}
              className="inline-flex items-center gap-1.5 rounded-md bg-blue-600 px-3.5 py-1.5 text-xs font-medium text-white transition hover:bg-blue-500 disabled:opacity-60"
            >
              <FileText className="h-3.5 w-3.5" />
              {exporting ? 'Generating...' : 'Generate Report'}
            </button>

            <div className="flex items-center gap-2 pl-1">
              <span className="text-xs font-medium text-slate-300">Live Feed</span>
              <button
                type="button"
                role="switch"
                aria-checked={liveFeed}
                aria-label="Live Feed"
                onClick={() => {
                  setLiveFeed((prev) => !prev);
                  setStreamVersion(Date.now());
                }}
                className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors ${
                  liveFeed ? 'bg-blue-600' : 'bg-slate-600'
                }`}
              >
                <span
                  className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${
                    liveFeed ? 'translate-x-4' : 'translate-x-1'
                  }`}
                />
              </button>
            </div>

            <button
              type="button"
              aria-label="Refresh streams"
              onClick={handleRefresh}
              className="inline-flex h-7 w-7 items-center justify-center rounded-md border border-[#2c3852] bg-[#1c263b] text-slate-300 transition hover:bg-[#24314d] hover:text-white"
            >
              <RefreshCw className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      </header>

      {/* Main Live Camera Detection Panel */}
      <main className="mx-auto max-w-[1440px] p-5">
        <section className="rounded-xl border border-[#2a3650] bg-[#141c2c] p-4 shadow-xl">
          <h2 className="mb-3.5 text-sm font-semibold text-white">Live Camera Detection</h2>

          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            {/* Camera 1 */}
            <div className="overflow-hidden rounded-lg border border-[#2b3752] bg-[#0e1523]">
              <div className="flex items-center justify-between border-b border-[#253047] bg-[#192234] px-3.5 py-2">
                <span className="text-xs font-medium text-slate-200">
                  Camera 1 (Room 1 Main Entry/Exit)
                </span>
                {renderBadge('cam_1')}
              </div>
              <div className="aspect-[16/10] w-full bg-[#0c121e]">
                <img
                  src={cam1Src}
                  alt="Camera 1 (Room 1 Main Entry/Exit)"
                  className="h-full w-full object-cover"
                />
              </div>
            </div>

            {/* Camera 2 */}
            <div className="overflow-hidden rounded-lg border border-[#2b3752] bg-[#0e1523]">
              <div className="flex items-center justify-between border-b border-[#253047] bg-[#192234] px-3.5 py-2">
                <span className="text-xs font-medium text-slate-200">
                  Camera 2 (Room 2 Connecting Door)
                </span>
                {renderBadge('cam_2')}
              </div>
              <div className="aspect-[16/10] w-full bg-[#0c121e]">
                <img
                  src={cam2Src}
                  alt="Camera 2 (Room 2 Connecting Door)"
                  className="h-full w-full object-cover"
                />
              </div>
            </div>
          </div>
        </section>
      </main>
    </div>
  );
}

export default App;
