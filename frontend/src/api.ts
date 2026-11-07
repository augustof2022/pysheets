import type { WorkbookInfo } from "./types";

interface BootConfig {
  token?: string | null;
  desktop?: boolean;
}

const cfg: BootConfig = (window as unknown as { __PYSHEETS__?: BootConfig }).__PYSHEETS__ || {};
export const TOKEN = cfg.token || null;
export const DESKTOP = !!cfg.desktop;

export class RpcError extends Error {
  info?: WorkbookInfo | null;
  code?: string;
  constructor(msg: string, info?: WorkbookInfo | null, code?: string) {
    super(msg);
    this.info = info;
    this.code = code;
  }
}

function headers(extra: Record<string, string> = {}): Record<string, string> {
  const h: Record<string, string> = { "Content-Type": "application/json", ...extra };
  if (TOKEN) h["X-PySheets-Token"] = TOKEN;
  return h;
}

export interface RpcResponse<T> {
  result: T;
  info?: WorkbookInfo;
}

export async function rpc<T = unknown>(method: string, params: Record<string, unknown> = {}): Promise<RpcResponse<T>> {
  const res = await fetch("api/rpc", { method: "POST", headers: headers(), body: JSON.stringify({ method, params }) });
  if (res.status === 401) throw new RpcError("Not authorized — restart PySheets.");
  const body = await res.json();
  if (!body.ok) throw new RpcError(body.error || "Something went wrong", body.info, body.code);
  return { result: body.result as T, info: body.info };
}

export async function progress(): Promise<{ active: boolean; label?: string; done?: number; total?: number | null; query?: number | null }> {
  const res = await fetch("api/progress", { headers: headers() });
  return res.json();
}

export function upload(file: File, onProgress?: (fraction: number) => void): Promise<string> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `api/upload?name=${encodeURIComponent(file.name)}`);
    if (TOKEN) xhr.setRequestHeader("X-PySheets-Token", TOKEN);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      try {
        const body = JSON.parse(xhr.responseText);
        if (body.ok) resolve(body.path);
        else reject(new RpcError(body.error || "Upload failed"));
      } catch {
        reject(new RpcError("Upload failed"));
      }
    };
    xhr.onerror = () => reject(new RpcError("Upload failed"));
    xhr.send(file);
  });
}

export function downloadUrl(path: string): string {
  const q = new URLSearchParams({ path });
  if (TOKEN) q.set("token", TOKEN);
  return `api/download?${q.toString()}`;
}
