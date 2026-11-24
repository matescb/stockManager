/** Shared vi.mock factories for the box intake / audit DOM tests. */
import { vi } from "vitest";

export const apiGet = vi.fn();
export const apiPost = vi.fn();

export class FakeApiError extends Error {
  status: number;
  userMessage: string;
  constructor(status: number, msg = "api error") {
    super(msg);
    this.status = status;
    this.userMessage = msg;
  }
}

export const RAW_A = "[)>\x1e06\x1d1PLM358\x1dQ100\x1d1TAAA\x1e\x04";
export const RAW_B = "[)>\x1e06\x1d1PLM358\x1dQ50\x1d1TBBB\x1e\x04";
/** Mouser label as a wedge reader typed it: separators lost, and no 1P. */
export const RAW_NO_1P = "[)>06K4048970914K027PC201,C202,C503Q10011K0925716424LJP1VMurata";
