import { useEffect, useState } from "react";
import * as api from "./api";
import "./App.css";

function LoginScreen({ onLogin }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const data = await api.login(username, password);
      onLogin(data.role);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="centered">
      <form className="card login-card" onSubmit={handleSubmit}>
        <h1>Forge</h1>
        <p className="subtitle">AI Software Engineering Team</p>
        <label>
          Username
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoFocus />
        </label>
        <label>
          Password
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {error && <div className="error">{error}</div>}
        <button type="submit" disabled={loading}>
          {loading ? "Signing in..." : "Sign in"}
        </button>
        <p className="hint">
          Seed users: admin/admin123, developer/dev123, reviewer/review123, viewer/view123
        </p>
      </form>
    </div>
  );
}

function RequestPanel() {
  const [requestText, setRequestText] = useState("");
  const [loading, setLoading] = useState(false);
  const [events, setEvents] = useState([]);
  const [finalReport, setFinalReport] = useState(null);
  const [error, setError] = useState("");

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    setEvents([]);
    setFinalReport(null);
    setLoading(true);
    try {
      await api.streamTriage(requestText, (event) => {
        if (event.kind === "final") {
          setFinalReport(event.text);
        } else if (event.state === "failed") {
          setError(event.text);
        } else {
          // routing / working status — append to the live activity feed
          setEvents((prev) => [...prev, event.text].filter(Boolean));
        }
      });
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="card">
      <h2>New Request</h2>
      <p className="muted">
        "Investigate ..." routes to the Knowledge Agent. "Fix / implement / resolve ..." routes
        to the Engineering Agent, which produces a proposal — nothing is written to GitHub from
        this screen.
      </p>
      <form onSubmit={handleSubmit}>
        <textarea
          rows={3}
          placeholder="Fix the bug in issue #14 in owner/repo"
          value={requestText}
          onChange={(e) => setRequestText(e.target.value)}
        />
        <button type="submit" disabled={loading || !requestText.trim()}>
          {loading ? "Working..." : "Send"}
        </button>
      </form>
      {error && <div className="error">{error}</div>}
      {events.length > 0 && (
        <div className="activity-feed">
          {events.map((text, i) => (
            <div key={i} className="activity-line">
              <span className="activity-dot" />
              {text}
            </div>
          ))}
        </div>
      )}
      {finalReport && (
        <div className="result-block">
          <div className="badge">Completed</div>
          <pre>{finalReport}</pre>
        </div>
      )}
    </div>
  );
}

function ProposalDetail({ proposal, onBack, onChanged }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function handleApprove() {
    setBusy(true);
    setError("");
    try {
      const updated = await api.approveProposal(proposal.id);
      onChanged(updated);
    } catch (err) {
      setError(err.message);
      // The approve endpoint's HTTP error message and what's actually
      // stored on the proposal (full step-by-step log, in Forge's
      // case) can diverge — refetch so the displayed proposal reflects
      // the real, persisted state (status: failed, full error) rather
      // than the stale "pending" object still held from before this
      // attempt.
      try {
        const refreshed = await api.getProposal(proposal.id);
        onChanged(refreshed);
      } catch {
        // If even the refetch fails, the local `error` state above is
        // still shown — not silently losing the failure either way.
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleReject() {
    setBusy(true);
    setError("");
    try {
      const updated = await api.rejectProposal(proposal.id);
      onChanged(updated);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  const reviewClass =
    proposal.review_verdict === "APPROVED"
      ? "verdict-approved"
      : proposal.review_verdict === "CHANGES_REQUIRED"
      ? "verdict-changes"
      : "verdict-none";

  return (
    <div className="card">
      <button className="link-button" onClick={onBack}>
        &larr; Back to proposals
      </button>
      <h2>{proposal.pr_title}</h2>
      <div className={`status-pill status-${proposal.status}`}>{proposal.status}</div>

      <div className="detail-grid">
        <div>
          <strong>Repository</strong>
          <p>
            {proposal.owner}/{proposal.repo}
          </p>
        </div>
        <div>
          <strong>Branch</strong>
          <p>{proposal.branch_name}</p>
        </div>
      </div>

      <strong>PR description</strong>
      <p>{proposal.pr_body}</p>

      <strong>Files changed</strong>
      <ul>
        {proposal.files.map((f) => (
          <li key={f}>{f}</li>
        ))}
      </ul>

      <div className={`review-box ${reviewClass}`}>
        <strong>Review Agent verdict: {proposal.review_verdict || "not reviewed"}</strong>
        {proposal.review_reasoning && <p>{proposal.review_reasoning}</p>}
        {proposal.review_concerns?.length > 0 && (
          <ul>
            {proposal.review_concerns.map((c, i) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
        )}
      </div>

      {proposal.error && <div className="error">Execution error: {proposal.error}</div>}
      {proposal.result && (
        <div className="result-block">
          <strong>Result</strong>
          <pre>{JSON.stringify(proposal.result, null, 2)}</pre>
        </div>
      )}

      {(proposal.status === "pending" || proposal.status === "failed") && (
        <div className="actions">
          <button className="approve" onClick={handleApprove} disabled={busy}>
            {busy
              ? "Working..."
              : proposal.status === "failed"
              ? "Retry approve (writes to GitHub)"
              : "Approve (writes to GitHub)"}
          </button>
          <button className="reject" onClick={handleReject} disabled={busy}>
            {busy ? "Working..." : "Reject"}
          </button>
        </div>
      )}
      {error && <div className="error">{error}</div>}
    </div>
  );
}

function ProposalsPanel({ active }) {
  const [proposals, setProposals] = useState([]);
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function refresh() {
    setLoading(true);
    setError("");
    try {
      const data = await api.listProposals();
      setProposals(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  // Panels now stay mounted permanently (see App's <main> below) so
  // switching tabs doesn't wipe RequestPanel's state. Refetch every
  // time this tab becomes the active one, not just on first mount —
  // otherwise proposals created after the initial load would never
  // show up without a full page refresh.
  useEffect(() => {
    if (active) refresh();
  }, [active]);

  async function openProposal(id) {
    try {
      const detail = await api.getProposal(id);
      setSelected(detail);
    } catch (err) {
      setError(err.message);
    }
  }

  if (selected) {
    return (
      <ProposalDetail
        proposal={selected}
        onBack={() => {
          setSelected(null);
          refresh();
        }}
        onChanged={(updated) => setSelected(updated)}
      />
    );
  }

  return (
    <div className="card">
      <div className="panel-header">
        <h2>Proposals</h2>
        <button className="link-button" onClick={refresh}>
          Refresh
        </button>
      </div>
      {error && <div className="error">{error}</div>}
      {loading ? (
        <p className="muted">Loading...</p>
      ) : proposals.length === 0 ? (
        <p className="muted">No proposals yet — send a "fix ..." request first.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Status</th>
              <th>Repo</th>
              <th>Branch</th>
              <th>PR title</th>
            </tr>
          </thead>
          <tbody>
            {proposals.map((p) => (
              <tr key={p.id} onClick={() => openProposal(p.id)} className="clickable-row">
                <td>
                  <span className={`status-pill status-${p.status}`}>{p.status}</span>
                </td>
                <td>
                  {p.owner}/{p.repo}
                </td>
                <td>{p.branch_name}</td>
                <td>{p.pr_title}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export default function App() {
  const [session, setSession] = useState(() => api.getStoredSession());
  const [tab, setTab] = useState("request");

  if (!session) {
    return <LoginScreen onLogin={(role) => setSession({ role })} />;
  }

  return (
    <div className="app-shell">
      <header>
        <h1>Forge</h1>
        <div className="header-right">
          <span className="role-badge">{session.role}</span>
          <button
            className="link-button"
            onClick={() => {
              api.logout();
              setSession(null);
            }}
          >
            Sign out
          </button>
        </div>
      </header>

      <nav>
        <button className={tab === "request" ? "active" : ""} onClick={() => setTab("request")}>
          New Request
        </button>
        <button
          className={tab === "proposals" ? "active" : ""}
          onClick={() => setTab("proposals")}
        >
          Proposals
        </button>
      </nav>

      <main>
        {/* Both panels stay mounted (hidden via CSS instead of
            conditionally rendered) so switching tabs doesn't wipe
            RequestPanel's in-progress streaming state or result. */}
        <div style={{ display: tab === "request" ? "block" : "none" }}>
          <RequestPanel />
        </div>
        <div style={{ display: tab === "proposals" ? "block" : "none" }}>
          <ProposalsPanel active={tab === "proposals"} />
        </div>
      </main>
    </div>
  );
}