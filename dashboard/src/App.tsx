import { useCallback, useEffect, useState } from 'react';
import { Play, RefreshCw, Shield, ShieldAlert } from 'lucide-react';
import {
  adjustMovementTimestamp,
  exportLogsCsv,
  fetchActiveState,
  fetchMovementLogs,
  flagMovementLog,
  runDemoScenario,
  verifyMovementLog,
} from './api/client';
import { isCognitoMode, signInWithCognitoPassword } from './auth/cognitoAuth';
import { AuthorityActionModal } from './components/AuthorityActionModal';
import { MovementLogsTable } from './components/MovementLogsTable';
import { RoomStatusBoard } from './components/RoomStatusBoard';
import { SnapshotPreviewModal } from './components/SnapshotPreviewModal';
import type { ActivePersonState, AuthSession, MovementLog, UserRole } from './types/workzone';

export function App() {
  const [session, setSession] = useState<AuthSession>({
    username: 'authority@workzone.local',
    role: 'Authorities',
  });
  const [loginUsername, setLoginUsername] = useState('');
  const [loginPassword, setLoginPassword] = useState('');
  const [authError, setAuthError] = useState<string | null>(null);

  const [persons, setPersons] = useState<ActivePersonState[]>([]);
  const [logs, setLogs] = useState<MovementLog[]>([]);
  const [awayThreshold, setAwayThreshold] = useState<number>(900);
  const [clockOffsetMs, setClockOffsetMs] = useState<number>(0);
  const [nowMs, setNowMs] = useState<number>(Date.now());
  const [statusFilter, setStatusFilter] = useState<string>('ALL');
  const [personFilter, setPersonFilter] = useState<string>('');
  const [loading, setLoading] = useState<boolean>(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const [modalState, setModalState] = useState<{
    mode: 'verify' | 'flag' | 'adjust' | 'assign';
    log: MovementLog;
  } | null>(null);
  const [snapshotPreview, setSnapshotPreview] = useState<{ url: string; title: string } | null>(null);

  const refreshData = useCallback(async () => {
    setLoading(true);
    setErrorMsg(null);
    try {
      const [stateRes, logsRes] = await Promise.all([
        fetchActiveState(session),
        fetchMovementLogs(session, {
          person_id: personFilter.trim() || undefined,
          verification_status: statusFilter,
        }),
      ]);
      setPersons(stateRes.persons);
      setAwayThreshold(stateRes.away_alert_threshold_seconds);
      setClockOffsetMs(Date.parse(stateRes.server_time) - Date.now());
      setLogs(logsRes);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Unable to reach backend API');
    } finally {
      setLoading(false);
    }
  }, [session, personFilter, statusFilter]);

  useEffect(() => {
    if (isCognitoMode() && !session.idToken) {
      return;
    }
    void refreshData();
    const poll = setInterval(() => void refreshData(), 4000);
    return () => clearInterval(poll);
  }, [refreshData, session.idToken]);

  useEffect(() => {
    const tick = setInterval(() => setNowMs(Date.now() + clockOffsetMs), 1000);
    return () => clearInterval(tick);
  }, [clockOffsetMs]);

  const handleActionSubmit = async (data: {
    remarks: string;
    newTimestamp?: string;
    assignPersonId?: string;
    assignDisplayName?: string;
  }) => {
    if (!modalState) return;
    const { mode, log } = modalState;
    if (mode === 'verify' || mode === 'assign') {
      await verifyMovementLog(session, log.log_id, {
        remarks: data.remarks || undefined,
        assign_person_id: data.assignPersonId,
        assign_display_name: data.assignDisplayName,
      });
    } else if (mode === 'flag') {
      await flagMovementLog(session, log.log_id, data.remarks);
    } else if (mode === 'adjust' && data.newTimestamp) {
      await adjustMovementTimestamp(session, log.log_id, data.newTimestamp, data.remarks);
    }
    await refreshData();
  };

  const handleExportCsv = async () => {
    const csv = await exportLogsCsv(session, {
      person_id: personFilter.trim() || undefined,
      verification_status: statusFilter,
    });
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'workzone_movement_logs.csv';
    a.click();
    URL.revokeObjectURL(url);
  };

  if (isCognitoMode() && !session.idToken) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-950 p-4">
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            setAuthError(null);
            try {
              const s = await signInWithCognitoPassword(loginUsername, loginPassword);
              setSession(s);
            } catch (err) {
              setAuthError(err instanceof Error ? err.message : 'Login failed');
            }
          }}
          className="w-full max-w-sm space-y-4 rounded-xl border border-slate-800 bg-slate-900 p-6"
        >
          <h1 className="text-lg font-bold text-white">WorkZone Vision Sign In</h1>
          <input
            type="email"
            placeholder="Authority Email"
            value={loginUsername}
            onChange={(e) => setLoginUsername(e.target.value)}
            className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-white"
            required
          />
          <input
            type="password"
            placeholder="Password"
            value={loginPassword}
            onChange={(e) => setLoginPassword(e.target.value)}
            className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-white"
            required
          />
          {authError && <p className="text-xs text-rose-400">{authError}</p>}
          <button
            type="submit"
            className="w-full rounded-lg bg-sky-600 py-2 text-xs font-semibold text-white hover:bg-sky-500"
          >
            Sign In via Amazon Cognito
          </button>
        </form>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="border-b border-slate-800 bg-slate-900/80 px-4 py-3 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2.5">
            <Shield className="h-6 w-6 text-sky-400" />
            <div>
              <h1 className="text-base font-bold text-white">
                WorkZone Vision — Authority Verification
              </h1>
              <p className="text-[11px] text-slate-400">
                VEXDET RF-DETR ONNX · 2-Camera Nested Zone & Away-Duration Monitor
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {!isCognitoMode() && (
              <select
                aria-label="RBAC Role Switcher"
                value={session.role}
                onChange={(e) =>
                  setSession({ ...session, role: e.target.value as UserRole })
                }
                className="rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-200"
              >
                <option value="Authorities">Role: Authorities (Can Verify/Edit)</option>
                <option value="Viewers">Role: Viewers (Read-Only)</option>
              </select>
            )}

            <button
              type="button"
              onClick={async () => {
                setLoading(true);
                setErrorMsg(null);
                try {
                  await runDemoScenario(session);
                  await refreshData();
                } catch (err) {
                  setErrorMsg(err instanceof Error ? err.message : 'Demo scenario failed');
                  setLoading(false);
                }
              }}
              className="inline-flex items-center gap-1.5 rounded-lg border border-sky-500/40 bg-sky-600/20 px-3 py-1.5 text-xs font-medium text-sky-200 hover:bg-sky-600/30"
            >
              <Play className="h-3.5 w-3.5" />
              Run Demo
            </button>

            <button
              type="button"
              onClick={() => void refreshData()}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-800 px-3 py-1.5 text-xs font-medium text-slate-200 hover:bg-slate-700"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
              Refresh
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl space-y-5 p-4">
        {errorMsg && (
          <div className="flex items-center gap-2 rounded-lg border border-rose-500/40 bg-rose-950/40 px-4 py-2.5 text-xs text-rose-200">
            <ShieldAlert className="h-4 w-4 text-rose-400" />
            <span>{errorMsg}</span>
          </div>
        )}

        <RoomStatusBoard
          persons={persons}
          nowMs={nowMs}
          awayThresholdSeconds={awayThreshold}
        />

        <MovementLogsTable
          logs={logs}
          userRole={session.role}
          statusFilter={statusFilter}
          personFilter={personFilter}
          onStatusFilterChange={setStatusFilter}
          onPersonFilterChange={setPersonFilter}
          onAction={(mode, log) => setModalState({ mode, log })}
          onPreviewSnapshot={(url, title) => setSnapshotPreview({ url, title })}
          onExportCsv={() => void handleExportCsv()}
        />
      </main>

      {modalState && (
        <AuthorityActionModal
          mode={modalState.mode}
          log={modalState.log}
          onClose={() => setModalState(null)}
          onSubmit={handleActionSubmit}
        />
      )}

      {snapshotPreview && (
        <SnapshotPreviewModal
          url={snapshotPreview.url}
          title={snapshotPreview.title}
          onClose={() => setSnapshotPreview(null)}
        />
      )}
    </div>
  );
}

export default App;
