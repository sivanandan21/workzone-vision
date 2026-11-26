import React from 'react';
import { AlertTriangle, Camera, Clock, DoorOpen, ShieldCheck, User } from 'lucide-react';
import type { ActivePersonState, ZoneState } from '../types/workzone';

interface RoomStatusBoardProps {
  persons: ActivePersonState[];
  nowMs: number;
  awayThresholdSeconds: number;
}

export function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const hrs = Math.floor(total / 3600);
  const mins = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  const mm = String(mins).padStart(2, '0');
  const ss = String(secs).padStart(2, '0');
  return hrs > 0 ? `${String(hrs).padStart(2, '0')}:${mm}:${ss}` : `${mm}:${ss}`;
}

export const RoomStatusBoard: React.FC<RoomStatusBoardProps> = ({
  persons,
  nowMs,
  awayThresholdSeconds,
}) => {
  const byZone = (zone: ZoneState) => persons.filter((p) => p.current_zone === zone);
  const outside = byZone('OUTSIDE');
  const room1 = byZone('IN_ROOM_1');
  const room2 = byZone('IN_ROOM_2');

  return (
    <section aria-label="2-Room Nested Live Status" className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-base font-semibold text-white">Nested-Zone Live Occupancy & Away Timers</h2>
          <p className="text-xs text-slate-400">
            Room 2 is nested inside Room 1 (no outside door). Away timer runs only when exiting Room 1 via Camera 1.
          </p>
        </div>
        <div className="flex items-center gap-3 text-xs">
          <span className="rounded-full bg-slate-800 px-3 py-1 text-slate-300">
            Outside: <strong className="text-amber-400">{outside.length}</strong>
          </span>
          <span className="rounded-full bg-slate-800 px-3 py-1 text-slate-300">
            Room 1: <strong className="text-emerald-400">{room1.length}</strong>
          </span>
          <span className="rounded-full bg-slate-800 px-3 py-1 text-slate-300">
            Room 2: <strong className="text-sky-400">{room2.length}</strong>
          </span>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-11 lg:items-stretch">
        {/* OUTSIDE CAMPUS */}
        <div className="rounded-lg border border-amber-500/30 bg-amber-950/20 p-3 lg:col-span-3">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs font-semibold uppercase tracking-wider text-amber-300">
              Outside Campus
            </span>
            <Clock className="h-4 w-4 text-amber-400" />
          </div>
          {outside.length === 0 ? (
            <p className="py-4 text-center text-xs text-slate-500">Nobody currently outside</p>
          ) : (
            <div className="space-y-2">
              {outside.map((p) => {
                const exitMs = p.last_exit_time ? Date.parse(p.last_exit_time) : null;
                const elapsedSec = exitMs ? Math.max(0, (nowMs - exitMs) / 1000) : 0;
                const overdue = exitMs !== null && elapsedSec > awayThresholdSeconds;
                return (
                  <div
                    key={p.person_id}
                    className={`rounded-md border p-2.5 ${
                      overdue
                        ? 'border-rose-500/70 bg-rose-950/50'
                        : 'border-amber-500/30 bg-slate-900/80'
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="flex items-center gap-1.5 text-sm font-medium text-white">
                        <User className="h-3.5 w-3.5 text-amber-400" />
                        {p.display_name} ({p.person_id})
                      </span>
                      {overdue && (
                        <span className="inline-flex items-center gap-1 rounded bg-rose-500/20 px-1.5 py-0.5 text-[10px] font-semibold text-rose-300">
                          <AlertTriangle className="h-3 w-3" /> OVERDUE
                        </span>
                      )}
                    </div>
                    <div className="mt-1 flex items-center justify-between text-xs">
                      <span className="text-slate-400">Away Timer</span>
                      <span
                        data-testid={`away-timer-${p.person_id}`}
                        className={`font-mono font-semibold ${overdue ? 'text-rose-300' : 'text-amber-300'}`}
                      >
                        {exitMs ? formatDuration(elapsedSec) : '00:00'}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* CAMERA 1 GATEWAY */}
        <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-slate-700 bg-slate-950/50 p-2 text-center lg:col-span-1">
          <Camera className="mb-1 h-4 w-4 text-emerald-400" />
          <span className="text-[11px] font-semibold text-slate-300">Cam 1</span>
          <span className="text-[10px] text-slate-500">Main Door</span>
          <DoorOpen className="mt-1 h-4 w-4 text-slate-400" />
        </div>

        {/* ROOM 1 OUTER */}
        <div className="rounded-lg border border-emerald-500/30 bg-emerald-950/15 p-3 lg:col-span-3">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs font-semibold uppercase tracking-wider text-emerald-300">
              Room 1 (Outer)
            </span>
            <ShieldCheck className="h-4 w-4 text-emerald-400" />
          </div>
          {room1.length === 0 ? (
            <p className="py-4 text-center text-xs text-slate-500">No targets in Room 1</p>
          ) : (
            <div className="space-y-2">
              {room1.map((p) => (
                <div
                  key={p.person_id}
                  className="rounded-md border border-emerald-500/30 bg-slate-900/80 p-2.5"
                >
                  <div className="flex items-center justify-between">
                    <span className="flex items-center gap-1.5 text-sm font-medium text-white">
                      <User className="h-3.5 w-3.5 text-emerald-400" />
                      {p.display_name} ({p.person_id})
                    </span>
                    <span className="rounded bg-emerald-500/20 px-1.5 py-0.5 text-[10px] font-medium text-emerald-300">
                      Timer Stopped
                    </span>
                  </div>
                  <p className="mt-1 text-[11px] text-slate-400">
                    Last seen via {p.last_camera_id || 'cam_1'}
                  </p>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* CAMERA 2 GATEWAY */}
        <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-slate-700 bg-slate-950/50 p-2 text-center lg:col-span-1">
          <Camera className="mb-1 h-4 w-4 text-sky-400" />
          <span className="text-[11px] font-semibold text-slate-300">Cam 2</span>
          <span className="text-[10px] text-slate-500">Conn. Door</span>
          <DoorOpen className="mt-1 h-4 w-4 text-slate-400" />
        </div>

        {/* ROOM 2 INNER */}
        <div className="rounded-lg border border-sky-500/30 bg-sky-950/15 p-3 lg:col-span-3">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs font-semibold uppercase tracking-wider text-sky-300">
              Room 2 (Inner)
            </span>
            <ShieldCheck className="h-4 w-4 text-sky-400" />
          </div>
          {room2.length === 0 ? (
            <p className="py-4 text-center text-xs text-slate-500">No targets in Room 2</p>
          ) : (
            <div className="space-y-2">
              {room2.map((p) => (
                <div
                  key={p.person_id}
                  className="rounded-md border border-sky-500/30 bg-slate-900/80 p-2.5"
                >
                  <div className="flex items-center justify-between">
                    <span className="flex items-center gap-1.5 text-sm font-medium text-white">
                      <User className="h-3.5 w-3.5 text-sky-400" />
                      {p.display_name} ({p.person_id})
                    </span>
                    <span className="rounded bg-sky-500/20 px-1.5 py-0.5 text-[10px] font-medium text-sky-300">
                      Inside Inner Room
                    </span>
                  </div>
                  <p className="mt-1 text-[11px] text-slate-400">
                    Internal shift logged (no away timer)
                  </p>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </section>
  );
};
