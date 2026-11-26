import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import App from '../App';

describe('WorkZone Vision Simple Monitor UI', () => {
  it('renders Simple Monitor in STANDBY when no cameras connected, then switches to LIVE when active', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        cam_1: { live: true, frames_received: 5, last_frame_age_sec: 0.1 },
        cam_2: { live: false, frames_received: 0, last_frame_age_sec: null },
      }),
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);

    expect(screen.getByText(/WorkZone Vision/i)).toBeInTheDocument();
    expect(screen.getByText(/Simple Monitor/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Generate Report/i })).toBeInTheDocument();
    expect(screen.getByRole('switch', { name: /Live Feed/i })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByText('Camera 1 (Room 1 Main Entry/Exit)')).toBeInTheDocument();
    expect(screen.getByText('Camera 2 (Room 2 Connecting Door)')).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText('LIVE')).toBeInTheDocument();
      expect(screen.getByText('STANDBY')).toBeInTheDocument();
    });

    vi.unstubAllGlobals();
  });

  it('switches streams between MJPEG and snapshots when toggling Live Feed', () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    render(<App />);
    const toggle = screen.getByRole('switch', { name: /Live Feed/i });
    const cam1Img = screen.getByAltText('Camera 1 (Room 1 Main Entry/Exit)') as HTMLImageElement;
    expect(cam1Img.src).toContain('/api/streams/cam_1/mjpeg');

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-checked', 'false');
    expect(screen.getAllByText('PAUSED')).toHaveLength(2);
    expect(cam1Img.src).toContain('/api/streams/cam_1/snapshot');
    vi.unstubAllGlobals();
  });

  it('triggers CSV download when clicking Generate Report', async () => {
    const fetchMock = vi.fn(async (url: string) => {
      if (url.includes('/api/streams/status')) {
        return {
          ok: true,
          json: async () => ({
            cam_1: { live: false, frames_received: 0, last_frame_age_sec: null },
            cam_2: { live: false, frames_received: 0, last_frame_age_sec: null },
          }),
        };
      }
      return {
        ok: true,
        text: async () => 'log_id,timestamp,person_id\n',
      };
    });
    vi.stubGlobal('fetch', fetchMock);
    const createUrlMock = vi.fn(() => 'blob:mock-csv');
    const revokeUrlMock = vi.fn();
    Object.defineProperty(URL, 'createObjectURL', { value: createUrlMock, configurable: true });
    Object.defineProperty(URL, 'revokeObjectURL', { value: revokeUrlMock, configurable: true });
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: /Generate Report/i }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining('/api/logs/export'),
        expect.any(Object),
      );
      expect(createUrlMock).toHaveBeenCalled();
      expect(clickSpy).toHaveBeenCalled();
      expect(revokeUrlMock).toHaveBeenCalledWith('blob:mock-csv');
    });

    clickSpy.mockRestore();
    vi.unstubAllGlobals();
  });
});
