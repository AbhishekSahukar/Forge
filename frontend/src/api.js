const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";

function getToken() {
  return localStorage.getItem("forge_token");
}

function setToken(token) {
  localStorage.setItem("forge_token", token);
}

function clearToken() {
  localStorage.removeItem("forge_token");
  localStorage.removeItem("forge_role");
}

async function request(path, { method = "GET", body, auth = true } = {}) {
  const headers = {};
  if (body) headers["Content-Type"] = "application/json";
  if (auth) {
    const token = getToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;
  }

  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = data.detail || JSON.stringify(data);
    } catch {
      // response wasn't JSON — keep statusText
    }
    const error = new Error(detail);
    error.status = res.status;
    if (res.status === 401 && auth) {
      // The stored token is invalid/expired. Clear it and reload so the
      // person lands back on the login screen immediately, instead of
      // staying on a panel that looks logged-in but 401s on every call.
      clearToken();
      window.location.reload();
    }
    throw error;
  }

  if (res.status === 204) return null;
  return res.json();
}

export async function login(username, password) {
  // /auth/login expects OAuth2 form-encoded data, not JSON
  const body = new URLSearchParams({ username, password });
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || "Login failed");
  }
  const data = await res.json();
  setToken(data.access_token);
  localStorage.setItem("forge_role", data.role);
  return data;
}

export function logout() {
  clearToken();
}

export function getStoredSession() {
  const token = getToken();
  const role = localStorage.getItem("forge_role");
  return token && role ? { token, role } : null;
}

export function triage(userRequest) {
  return request("/agent/triage", { method: "POST", body: { request: userRequest } });
}

/**
 * Streams a triage request over SSE, calling onEvent(event) for each
 * one as it arrives: {kind: "status", state, text} while working,
 * {kind: "final", text} once. Uses fetch() + a manual ReadableStream
 * reader rather than native EventSource, because EventSource cannot
 * send an Authorization header and this endpoint requires one.
 *
 * Returns a promise that resolves once the stream ends (after the
 * final event or an error) — callers don't need to track completion
 * separately from onEvent.
 */
export async function streamTriage(userRequest, onEvent) {
  const token = getToken();
  const res = await fetch(`${API_BASE}/agent/triage/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ request: userRequest }),
  });

  if (!res.ok) {
    if (res.status === 401) {
      clearToken();
      window.location.reload();
    }
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `Request failed (${res.status})`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE frames are separated by a blank line; each frame's payload
    // line starts with "data: ".
    const frames = buffer.split("\n\n");
    buffer = frames.pop(); // last element may be an incomplete frame — keep it for next read

    for (const frame of frames) {
      const line = frame.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      try {
        const event = JSON.parse(line.slice("data: ".length));
        onEvent(event);
      } catch {
        // Malformed frame — skip rather than crash the whole stream.
      }
    }
  }
}

export function investigate(task) {
  return request("/agent/investigate", { method: "POST", body: { task } });
}

export function searchGithubAccounts(query) {
  return request(`/github/search-accounts?q=${encodeURIComponent(query)}`);
}

export function listGithubRepos(owner) {
  return request(`/github/${encodeURIComponent(owner)}/repos`);
}

export function listGithubIssues(owner, repo) {
  return request(`/github/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/issues`);
}

export function listProposals() {
  return request("/agent/proposals");
}

export function getProposal(id) {
  return request(`/agent/proposals/${id}`);
}

export function approveProposal(id) {
  return request(`/agent/proposals/${id}/approve`, { method: "POST" });
}

export function rejectProposal(id) {
  return request(`/agent/proposals/${id}/reject`, { method: "POST" });
}