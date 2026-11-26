import type { AuthSession, MovementLog, StateResponse } from '../types/workzone';

const RAW_API_BASE = import.meta.env.VITE_API_BASE_URL;
const API_BASE = (RAW_API_BASE !== undefined ? RAW_API_BASE : 'http://localhost:8000').replace(/\/$/, '');

function buildHeaders(session: AuthSession, includeJson = true): Record<string, string> {
  const headers: Record<string, string> = {};
  if (includeJson) {
    headers['Content-Type'] = 'application/json';
  }
  if (session.idToken) {
    headers['Authorization'] = `Bearer ${session.idToken}`;
  } else {
    // Local mock server RBAC headers
    headers['X-Local-Actor'] = session.username;
    headers['X-Local-Groups'] = session.role;
  }
  return headers;
}

async function handleResponse<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    const body = await resp.text();
    throw new Error(body || `Request failed with HTTP ${resp.status}`);
  }
  return (await resp.json()) as T;
}

export async function fetchActiveState(session: AuthSession): Promise<StateResponse> {
  const resp = await fetch(`${API_BASE}/api/state`, {
    headers: buildHeaders(session, false),
  });
  return handleResponse<StateResponse>(resp);
}

export async function fetchMovementLogs(
  session: AuthSession,
  filters?: { person_id?: string; verification_status?: string; event_type?: string },
): Promise<MovementLog[]> {
  const params = new URLSearchParams();
  if (filters?.person_id) params.set('person_id', filters.person_id);
  if (filters?.verification_status && filters.verification_status !== 'ALL') {
    params.set('verification_status', filters.verification_status);
  }
  if (filters?.event_type && filters.event_type !== 'ALL') {
    params.set('event_type', filters.event_type);
  }
  const qs = params.toString();
  const resp = await fetch(`${API_BASE}/api/logs${qs ? `?${qs}` : ''}`, {
    headers: buildHeaders(session, false),
  });
  const data = await handleResponse<{ logs: MovementLog[] }>(resp);
  return data.logs;
}

export async function verifyMovementLog(
  session: AuthSession,
  logId: string,
  payload: { remarks?: string; assign_person_id?: string; assign_display_name?: string },
): Promise<MovementLog> {
  const resp = await fetch(`${API_BASE}/api/logs/${logId}/verify`, {
    method: 'POST',
    headers: buildHeaders(session),
    body: JSON.stringify(payload),
  });
  const data = await handleResponse<{ log: MovementLog }>(resp);
  return data.log;
}

export async function flagMovementLog(
  session: AuthSession,
  logId: string,
  remarks: string,
): Promise<MovementLog> {
  const resp = await fetch(`${API_BASE}/api/logs/${logId}/flag`, {
    method: 'POST',
    headers: buildHeaders(session),
    body: JSON.stringify({ remarks }),
  });
  const data = await handleResponse<{ log: MovementLog }>(resp);
  return data.log;
}

export async function adjustMovementTimestamp(
  session: AuthSession,
  logId: string,
  newTimestamp: string,
  remarks: string,
): Promise<MovementLog> {
  const resp = await fetch(`${API_BASE}/api/logs/${logId}/adjust`, {
    method: 'PATCH',
    headers: buildHeaders(session),
    body: JSON.stringify({ new_timestamp: newTimestamp, remarks }),
  });
  const data = await handleResponse<{ log: MovementLog }>(resp);
  return data.log;
}

export type ReportFormat = 'pdf' | 'xlsx';

export type ReportDuration =
  | 'today'
  | 'last_24h'
  | 'last_7d'
  | 'last_30d'
  | 'all'
  | 'custom';

export interface ReportExportOptions {
  format: ReportFormat;
  duration: ReportDuration;
  startDate?: string;
  endDate?: string;
}

export async function downloadReport(
  session: AuthSession,
  options: ReportExportOptions,
): Promise<{ blob: Blob; filename: string }> {
  const params = new URLSearchParams();
  params.set('format', options.format);
  params.set('duration', options.duration);
  params.set('tz_offset_minutes', String(-new Date().getTimezoneOffset()));
  if (options.duration === 'custom') {
    if (options.startDate) params.set('start_date', options.startDate);
    if (options.endDate) params.set('end_date', options.endDate);
  }
  const resp = await fetch(`${API_BASE}/api/logs/export?${params.toString()}`, {
    headers: buildHeaders(session, false),
  });
  if (!resp.ok) {
    const errText = typeof resp.text === 'function' ? await resp.text() : '';
    throw new Error(errText || `Export failed with HTTP ${resp.status}`);
  }
  const blob = await resp.blob();
  const disposition = resp.headers?.get?.('Content-Disposition') ?? '';
  const match = disposition.match(/filename="?([^"]+)"?/i);
  const dateStamp = new Date().toISOString().slice(0, 10);
  const filename =
    match?.[1] || `workzone_report_${options.duration}_${dateStamp}.${options.format}`;
  return { blob, filename };
}

export async function exportLogsCsv(
  session: AuthSession,
  filters?: { person_id?: string; verification_status?: string; event_type?: string },
): Promise<string> {
  const params = new URLSearchParams();
  if (filters?.person_id) params.set('person_id', filters.person_id);
  if (filters?.verification_status && filters.verification_status !== 'ALL') {
    params.set('verification_status', filters.verification_status);
  }
  if (filters?.event_type && filters.event_type !== 'ALL') {
    params.set('event_type', filters.event_type);
  }
  const qs = params.toString();
  const resp = await fetch(`${API_BASE}/api/logs/export${qs ? `?${qs}` : ''}`, {
    headers: buildHeaders(session, false),
  });
  if (!resp.ok) {
    throw new Error(`Export failed with HTTP ${resp.status}`);
  }
  return resp.text();
}

export async function runDemoScenario(session: AuthSession): Promise<{ events_ingested: number }> {
  const resp = await fetch(`${API_BASE}/api/simulator/run-demo`, {
    method: 'POST',
    headers: buildHeaders(session, false),
  });
  return handleResponse<{ events_ingested: number }>(resp);
}

export interface CameraStreamInfo {
  live: boolean;
  frames_received: number;
  last_frame_age_sec: number | null;
}

export type StreamStatusResponse = Record<'cam_1' | 'cam_2', CameraStreamInfo>;

export async function fetchStreamStatus(): Promise<StreamStatusResponse> {
  const url = API_BASE ? `${API_BASE}/api/streams/status` : '/api/streams/status';
  const resp = await fetch(url);
  return handleResponse<StreamStatusResponse>(resp);
}

export function getStreamMjpegUrl(cameraId: string): string {
  return API_BASE ? `${API_BASE}/api/streams/${cameraId}/mjpeg` : `/api/streams/${cameraId}/mjpeg`;
}

export function getStreamSnapshotUrl(cameraId: string): string {
  return API_BASE ? `${API_BASE}/api/streams/${cameraId}/snapshot` : `/api/streams/${cameraId}/snapshot`;
}

export function resolveSnapshotUrl(urlOrKey?: string | null): string | null {
  if (!urlOrKey) return null;
  if (urlOrKey.startsWith('http://') || urlOrKey.startsWith('https://') || urlOrKey.startsWith('data:')) {
    return urlOrKey;
  }
  const normalized = urlOrKey.replace(/^\/+/, '');
  return API_BASE ? `${API_BASE}/${normalized}` : `/${normalized}`;
}
