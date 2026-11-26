import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { RoomStatusBoard } from '../components/RoomStatusBoard';
import { MovementLogsTable } from '../components/MovementLogsTable';
import type { ActivePersonState, MovementLog } from '../types/workzone';

describe('RoomStatusBoard', () => {
  it('shows live ticking away timer only for OUTSIDE persons and marks overdue', () => {
    const nowMs = Date.parse('2026-11-26T10:20:00Z');
    const persons: ActivePersonState[] = [
      {
        person_id: '001',
        display_name: 'Student 1',
        current_zone: 'OUTSIDE',
        last_exit_time: '2026-11-26T10:00:00Z',
        last_entry_time: '2026-11-26T09:00:00Z',
        last_exit_log_id: 'log-exit-1',
        last_camera_id: 'cam_1',
        away_alert_sent: false,
        updated_at: '2026-11-26T10:00:00Z',
      },
      {
        person_id: '002',
        display_name: 'Student 2',
        current_zone: 'IN_ROOM_2',
        last_exit_time: null,
        last_entry_time: '2026-11-26T09:05:00Z',
        last_exit_log_id: null,
        last_camera_id: 'cam_2',
        away_alert_sent: false,
        updated_at: '2026-11-26T09:10:00Z',
      },
    ];

    render(<RoomStatusBoard persons={persons} nowMs={nowMs} awayThresholdSeconds={900} />);

    expect(screen.getByTestId('away-timer-001')).toHaveTextContent('20:00');
    expect(screen.getByText('OVERDUE')).toBeInTheDocument();
    expect(screen.getByText(/Student 2 \(002\)/)).toBeInTheDocument();
    expect(screen.getByText(/Internal shift logged/i)).toBeInTheDocument();
  });
});

describe('MovementLogsTable RBAC', () => {
  const sampleLogs: MovementLog[] = [
    {
      log_id: 'log-12345678',
      event_id: 'evt-1',
      timestamp: '2026-11-26T10:05:00Z',
      person_id: '001',
      display_name: 'Student 1',
      camera_id: 'cam_1',
      event_type: 'CAMPUS_RETURN',
      from_zone: 'OUTSIDE',
      to_zone: 'IN_ROOM_1',
      away_duration_seconds: 300,
      snapshot_s3_key: 'snapshots/001_cam_1.jpg',
      confidence: 0.94,
      verification_status: 'AUTO_MATCHED',
      verified_by: null,
      remarks: null,
      paired_exit_log_id: 'log-exit',
      audit_history: [],
    },
  ];

  it('shows Verify/Flag/Adjust buttons for Authorities role and hides them for Viewers', () => {
    const noop = vi.fn();
    const { rerender } = render(
      <MovementLogsTable
        logs={sampleLogs}
        userRole="Authorities"
        statusFilter="ALL"
        personFilter=""
        onStatusFilterChange={noop}
        onPersonFilterChange={noop}
        onAction={noop}
        onPreviewSnapshot={noop}
        onExportCsv={noop}
      />,
    );
    expect(screen.getByRole('button', { name: /Verify/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Flag/i })).toBeInTheDocument();

    rerender(
      <MovementLogsTable
        logs={sampleLogs}
        userRole="Viewers"
        statusFilter="ALL"
        personFilter=""
        onStatusFilterChange={noop}
        onPersonFilterChange={noop}
        onAction={noop}
        onPreviewSnapshot={noop}
        onExportCsv={noop}
      />,
    );
    expect(screen.queryByRole('button', { name: /Verify/i })).not.toBeInTheDocument();
    expect(screen.getByText(/Read-only \(Viewer\)/i)).toBeInTheDocument();
  });
});
