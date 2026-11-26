import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import App from '../App';

describe('WorkZone Vision Simple Monitor UI', () => {
  it('renders exact Simple Monitor header and both live camera cards', () => {
    render(<App />);

    expect(screen.getByText(/WorkZone Vision/i)).toBeInTheDocument();
    expect(screen.getByText(/Simple Monitor/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Generate Report/i })).toBeInTheDocument();
    expect(screen.getByRole('switch', { name: /Live Feed/i })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByText('Live Camera Detection')).toBeInTheDocument();
    expect(screen.getByText('Camera 1 (Room 1 Main Entry/Exit)')).toBeInTheDocument();
    expect(screen.getByText('Camera 2 (Room 2 Connecting Door)')).toBeInTheDocument();
    expect(screen.getAllByText('LIVE')).toHaveLength(2);
  });

  it('switches streams between LIVE MJPEG and PAUSED snapshots when toggling Live Feed', () => {
    render(<App />);
    const toggle = screen.getByRole('switch', { name: /Live Feed/i });
    const cam1Img = screen.getByAltText('Camera 1 (Room 1 Main Entry/Exit)') as HTMLImageElement;
    expect(cam1Img.src).toContain('/api/streams/cam_1/mjpeg');

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-checked', 'false');
    expect(screen.getAllByText('PAUSED')).toHaveLength(2);
    expect(cam1Img.src).toContain('/api/streams/cam_1/snapshot');
  });

  it('triggers CSV download when clicking Generate Report', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      text: async () => 'log_id,timestamp,person_id\n',
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
