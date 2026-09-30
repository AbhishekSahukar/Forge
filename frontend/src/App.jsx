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

function useStreamedAction() {
  // Shared streaming-run state so the picker's Investigate/Fix buttons
  // reuse exactly the same SSE-consuming logic the old free-text form used —
  // only how the request text gets built changes, not how it's executed.
  const [loading, setLoading] = useState(false);
  const [events, setEvents] = useState([]);
  const [finalReport, setFinalReport] = useState(null);
  const [error, setError] = useState("");

  async function run(requestText) {
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
          setEvents((prev) => [...prev, event.text].filter(Boolean));
        }
      });
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return { loading, events, finalReport, error, run };
}

function IssuePickerPanel() {
  const [step, setStep] = useState("account"); // account | repo | issue | action

  const [accountQuery, setAccountQuery] = useState("");
  const [accounts, setAccounts] = useState([]);
  const [searching, setSearching] = useState(false);
  const [selectedAccount, setSelectedAccount] = useState(null);

  const [repos, setRepos] = useState([]);
  const [loadingRepos, setLoadingRepos] = useState(false);
  const [selectedRepo, setSelectedRepo] = useState(null);

  const [issues, setIssues] = useState([]);
  const [loadingIssues, setLoadingIssues] = useState(false);
  const [selectedIssue, setSelectedIssue] = useState(null);

  const [listError, setListError] = useState("");
  const action = useStreamedAction();

  async function handleSearchAccounts(e) {
    e.preventDefault();
    setListError("");
    setSearching(true);
    try {
      setAccounts(await api.searchGithubAccounts(accountQuery));
    } catch (err) {
      setListError(err.message);
    } finally {
      setSearching(false);
    }
  }

  async function selectAccount(acct) {
    setSelectedAccount(acct);
    setStep("repo");
    setListError("");
    setLoadingRepos(true);
    try {
      setRepos(await api.listGithubRepos(acct.login));
    } catch (err) {
      setListError(err.message);
    } finally {
      setLoadingRepos(false);
    }
  }

  async function selectRepo(repo) {
    setSelectedRepo(repo);
    setStep("issue");
    setListError("");
    setLoadingIssues(true);
    try {
      setIssues(await api.listGithubIssues(selectedAccount.login, repo.name));
    } catch (err) {
      setListError(err.message);
    } finally {
      setLoadingIssues(false);
    }
  }

  function selectIssue(issue) {
    setSelectedIssue(issue);
    setStep("action");
  }

  function goBackTo(target) {
    setStep(target);
    if (target === "account") {
      setSelectedAccount(null);
      setRepos([]);
      setSelectedRepo(null);
      setIssues([]);
      setSelectedIssue(null);
    } else if (target === "repo") {
      setSelectedRepo(null);
      setIssues([]);
      setSelectedIssue(null);
    } else if (target === "issue") {
      setSelectedIssue(null);
    }
  }

  function runAction(kind) {
    const owner = selectedAccount.login;
    const repo = selectedRepo.name;
    const text =
      kind === "fix"
        ? `Fix the bug in issue #${selectedIssue.number} in ${owner}/${repo}`
        : `Investigate issue #${selectedIssue.number} in ${owner}/${repo}`;
    action.run(text);
  }

  return (
    <div className="card">
      <div className="breadcrumb">
        <span className={`crumb ${step === "account" ? "active" : ""}`} onClick={() => goBackTo("account")}>
          Account
        </span>
        {selectedAccount && (
          <>
            <span className="crumb-sep">/</span>
            <span className={`crumb ${step === "repo" ? "active" : ""}`} onClick={() => goBackTo("repo")}>
              {selectedAccount.login}
            </span>
          </>
        )}
        {selectedRepo && (
          <>
            <span className="crumb-sep">/</span>
            <span className={`crumb ${step === "issue" ? "active" : ""}`} onClick={() => goBackTo("issue")}>
              {selectedRepo.name}
            </span>
          </>
        )}
        {selectedIssue && (
          <>
            <span className="crumb-sep">/</span>
            <span className="crumb active">#{selectedIssue.number}</span>
          </>
        )}
      </div>

      {listError && <div className="error">{listError}</div>}

      {step === "account" && (
        <>
          <h2>Find a GitHub account</h2>
          <form onSubmit={handleSearchAccounts}>
            <input
              placeholder="Username or org"
              value={accountQuery}
              onChange={(e) => setAccountQuery(e.target.value)}
            />
            <button type="submit" disabled={searching || !accountQuery.trim()}>
              {searching ? "Searching..." : "Search"}
            </button>
          </form>
          <div className="picker-list">
            {accounts.map((a) => (
              <div key={a.login} className="picker-item" onClick={() => selectAccount(a)}>
                {a.avatar_url && <img src={a.avatar_url} alt="" className="picker-avatar" />}
                <span>{a.login}</span>
              </div>
            ))}
          </div>
        </>
      )}

      {step === "repo" && (
        <>
          <h2>Select a repository</h2>
          {loadingRepos ? (
            <p className="muted">Loading repos...</p>
          ) : (
            <div className="picker-list">
              {repos.map((r) => (
                <div key={r.full_name || r.name} className="picker-item" onClick={() => selectRepo(r)}>
                  <strong>{r.name}</strong>
                  {r.description && <p className="muted">{r.description}</p>}
                </div>
              ))}
              {repos.length === 0 && <p className="muted">No repositories found.</p>}
            </div>
          )}
        </>
      )}

      {step === "issue" && (
        <>
          <h2>Select an issue</h2>
          {loadingIssues ? (
            <p className="muted">Loading issues...</p>
          ) : (
            <div className="picker-list">
              {issues.map((iss) => (
                <div key={iss.number} className="picker-item" onClick={() => selectIssue(iss)}>
                  <span className="badge">#{iss.number}</span> {iss.title}
                </div>
              ))}
              {issues.length === 0 && <p className="muted">No open issues found.</p>}
            </div>
          )}
        </>
      )}

      {step === "action" && selectedIssue && (
        <>
          <h2>
            #{selectedIssue.number} — {selectedIssue.title}
          </h2>
          <p className="muted">
            {selectedAccount.login}/{selectedRepo.name}
          </p>
          <div className="actions">
            <button onClick={() => runAction("investigate")} disabled={action.loading}>
              {action.loading ? "Working..." : "Investigate"}
            </button>
            <button className="approve" onClick={() => runAction("fix")} disabled={action.loading}>
              {action.loading ? "Working..." : "Fix"}
            </button>
          </div>
          {action.error && <div className="error">{action.error}</div>}
          {action.events.length > 0 && (
            <div className="activity-feed">
              {action.events.map((text, i) => (
                <div key={i} className="activity-line">
                  <span className="activity-dot" />
                  {text}
                </div>
              ))}
            </div>
          )}
          {action.finalReport && (
            <div className="result-block">
              <div className="badge">Completed</div>
              <pre>{action.finalReport}</pre>
            </div>
          )}
        </>
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
  // switching tabs doesn't wipe the picker's state. Refetch every
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
          Find & Fix
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
            the picker's in-progress streaming state or result. */}
        <div style={{ display: tab === "request" ? "block" : "none" }}>
          <IssuePickerPanel />
        </div>
        <div style={{ display: tab === "proposals" ? "block" : "none" }}>
          <ProposalsPanel active={tab === "proposals"} />
        </div>
      </main>
    </div>
  );
}