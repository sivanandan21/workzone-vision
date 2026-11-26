export type ZoneState = 'OUTSIDE' | 'IN_ROOM_1' | 'IN_ROOM_2';

export type VerificationStatus =
  | 'AUTO_MATCHED'
  | 'PENDING_REVIEW'
  | 'VERIFIED'
  | 'FLAGGED';

export type UserRole = 'Authorities' | 'Viewers';

export interface AuthSession {
  username: string;
  role: UserRole;
  idToken?: string;
}

export interface ActivePersonState {
  person_id: string;
  display_name: string;
  current_zone: ZoneState;
  last_exit_time: string | null;
  last_entry_time: string | null;
  last_exit_log_id: string | null;
  last_camera_id: string | null;
  away_alert_sent: boolean;
  updated_at: string;
}

export interface AuditEntry {
  action: string;
  actor: string;
  timestamp: string;
  remarks?: string | null;
  previous_values?: Record<string, unknown>;
  new_values?: Record<string, unknown>;
}

export interface MovementLog {
  log_id: string;
  event_id: string;
  timestamp: string;
  person_id: string;
  display_name: string;
  camera_id: string;
  event_type: string;
  from_zone: ZoneState;
  to_zone: ZoneState;
  away_duration_seconds: number | null;
  snapshot_s3_key: string | null;
  snapshot_url?: string | null;
  confidence: number;
  verification_status: VerificationStatus;
  verified_by: string | null;
  remarks: string | null;
  paired_exit_log_id: string | null;
  audit_history: AuditEntry[];
}

export interface StateResponse {
  server_time: string;
  away_alert_threshold_seconds: number;
  persons: ActivePersonState[];
}
