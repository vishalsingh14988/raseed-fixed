import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Bell, Check, ChevronDown, ClipboardList, FileText, Home, Search,
  ShieldCheck, Settings, Upload, Camera, X, Clock3, Trash2, ExternalLink,
  Plus, RefreshCw, Pencil, Moon, Sun, Heart, FileCheck2, LogOut, UserRound, LogIn, Mail
} from "lucide-react";
import "./styles.css";

const API = (import.meta.env.VITE_API_URL || "https://billproof-starter.onrender.com").replace(/\/+$/, "");
const FILTERS = ["All Documents", "Active Warranties", "Expiring Soon", "Expired", "No Warranty"];
const NAV = [
  [Home, "Home"], [FileText, "My Bills"], [ShieldCheck, "Warranty Tracker"],
  [Bell, "Reminders"], [Settings, "Settings"]
];

// Sign-in is remembered with a token kept in this browser, because the
// login cookie alone is unreliable on phones: the website and the API are
// different sites, and many mobile browsers block cross-site cookies,
// which made signed-in users flip back to "Guest" after a refresh.
const TOKEN_KEY = "raseed_token";
function storedToken() {
  try { return localStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; }
}
function captureLoginToken() {
  // After Google sign-in the API returns the browser to this site with
  // the token in the URL fragment; store it and clean the address bar.
  if (!window.location.hash.includes("raseed_token=")) return;
  const match = window.location.hash.match(/raseed_token=([^&]+)/);
  if (match) {
    try { localStorage.setItem(TOKEN_KEY, decodeURIComponent(match[1])); } catch { /* private mode */ }
  }
  window.history.replaceState(null, "", window.location.pathname + window.location.search);
}
// The last confirmed sign-in answer is also remembered, so a returning
// visitor sees their own greeting instantly on reload instead of a
// "checking" state flipping into place a second later. The fresh check
// still runs on every load and overwrites this if anything changed.
const AUTH_CACHE_KEY = "raseed_auth_cache";
function readAuthCache() {
  try {
    const raw = localStorage.getItem(AUTH_CACHE_KEY);
    if (!raw) return null;
    const d = JSON.parse(raw);
    return d && typeof d === "object" && "connected" in d ? d : null;
  } catch { return null; }
}
function writeAuthCache(d) {
  try {
    if (d) localStorage.setItem(AUTH_CACHE_KEY, JSON.stringify({
      connected: !!d.connected, auth_configured: !!d.auth_configured,
      email: d.email || null, profile: d.profile || null,
      profile_complete: !!d.profile_complete,
    }));
  } catch { /* ignore */ }
}
async function apiFetch(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  const token = storedToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  return fetch(`${API}${path}`, { ...options, headers, credentials: "include" });
}
function formatDate(value) {
  if (!value) return "—";
  const d = new Date(`${value}T00:00:00`);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
}
function money(value) {
  if (value === null || value === undefined || value === "") return "—";
  return new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(value);
}
function Status({ status }) {
  const cls = status === "Active" ? "active" : status === "Expiring Soon" ? "soon" : status === "Expired" ? "expired" : "none";
  return <span className={`status ${cls}`}>{status || "No Warranty"}</span>;
}

function App() {
  const [bills, setBills] = useState([]);
  const [query, setQuery] = useState("");
  const [searchFocus, setSearchFocus] = useState(false);
  const [filter, setFilter] = useState("All Documents");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [uploadOpen, setUploadOpen] = useState(false);
  const [selectedBill, setSelectedBill] = useState(null);
  const [notice, setNotice] = useState("");
  const [activeNav, setActiveNav] = useState("Home");
  const [dark, setDark] = useState(false);
  const [remindersEnabled, setRemindersEnabled] = useState(true);
  const [gmailError, setGmailError] = useState("");
  const [gmailStatus, setGmailStatus] = useState(null); // null = not checked yet
  const [gmailImporting, setGmailImporting] = useState(false);
  const [gmailResult, setGmailResult] = useState("");
  const [summaryBills, setSummaryBills] = useState([]);
  const [auth, setAuth] = useState(() => readAuthCache()); // null = first visit, still checking sign-in
  const [profileForm, setProfileForm] = useState({ name: "", purpose: "Personal bills", age: "" });
  const [profileError, setProfileError] = useState("");
  const [profileSaving, setProfileSaving] = useState(false);
  const guest = !!auth && auth.auth_configured && !auth.connected;
  const guestRef = React.useRef(false);
  guestRef.current = guest;
  const gmailPollingRef = React.useRef(false);
  // Until the sign-in check answers, greet neutrally: showing a name
  // here made it look as if the user was signed in, then "flip" to
  // Guest once the real answer arrived.
  const firstName = auth?.profile?.name ? auth.profile.name.split(" ")[0] : guest ? "Guest" : "friend";

  async function loadBills() {
    setLoading(true);
    try {
      const params = new URLSearchParams({ q: query, status: filter });
      const res = await apiFetch(`/api/bills?${params}`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Could not load bills.");
      if (guestRef.current) { setBills([]); setSummaryBills([]); setError(""); return; }
      setBills(Array.isArray(data) ? data : []);
      setError("");
      // Unfiltered list for the dashboard summary — counts must not change
      // just because a search/filter is applied to the list below.
      try {
        const allRes = await apiFetch(`/api/bills`);
        const allData = await allRes.json();
        if (allRes.ok && Array.isArray(allData)) setSummaryBills(allData);
      } catch { /* summary is best-effort */ }
    } catch (e) {
      setError(`${e.message}. Check that the backend is running and its URL is correct.`);
    } finally { setLoading(false); }
  }

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.has("gmail_error")) {
      setGmailError(`Google sign-in did not complete: ${params.get("gmail_error")}`);
      window.history.replaceState({}, "", window.location.pathname);
    }
  }, []);

  useEffect(() => {
    // Is Gmail connected for bill importing? Checked whenever the
    // signed-in user changes; drives the Connect / Import card below.
    if (!auth?.connected) { setGmailStatus(null); return; }
    let cancelled = false;
    (async () => {
      try {
        const r = await apiFetch("/api/gmail/status");
        const d = await r.json();
        if (!cancelled && r.ok) {
          setGmailStatus(d);
          // If a scan is still running on the server (e.g. the page
          // was refreshed mid-scan), reconnect to its progress.
          if (d.connected) {
            try {
              const sr = await apiFetch("/api/gmail/scan/status");
              const sd = await sr.json();
              if (!cancelled && sr.ok && sd.state === "running") pollGmailScan();
            } catch { /* progress resume is best-effort */ }
          }
        }
      } catch { /* leave as unknown; the card still offers Connect */ }
    })();
    return () => { cancelled = true; };
  }, [auth?.connected]);

  useEffect(() => {
    // Who is signed in? Drives the login gate and the first-time questions.
    captureLoginToken();
    let cancelled = false;
    async function checkAuth(attempt) {
      try {
        const r = await apiFetch("/api/auth/me");
        const d = await r.json();
        if (cancelled) return;
        // A saved token the server no longer accepts is cleared, so the
        // site settles into honest Guest mode instead of flip-flopping.
        if (!d?.connected && storedToken()) {
          try { localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(AUTH_CACHE_KEY); } catch { /* ignore */ }
        }
        writeAuthCache(d);
        setAuth(d);
        if (d?.profile?.name) setProfileForm(f => ({ ...f, name: d.profile.name }));
      } catch (err) {
        if (cancelled) return;
        // The free server may be waking up; one retry before giving up,
        // so a slow first answer never throws a signed-in user to Guest.
        if (attempt === 0) { setTimeout(() => checkAuth(1), 2500); return; }
        setAuth({ connected: false, auth_configured: false, profile: null, profile_complete: false });
      }
    }
    checkAuth(0);
    return () => { cancelled = true; };
  }, []);

  async function saveProfile(e) {
    e.preventDefault();
    setProfileError("");
    if (!profileForm.name.trim()) { setProfileError("Please tell us your name."); return; }
    const age = parseInt(profileForm.age, 10);
    if (!age || age < 5 || age > 120) { setProfileError("Please enter your age."); return; }
    setProfileSaving(true);
    try {
      const res = await apiFetch("/api/auth/profile", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: profileForm.name.trim(), purpose: profileForm.purpose, age }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Could not save your details.");
      writeAuthCache(data);
      setAuth(data);
      loadBills();
    } catch (err) {
      setProfileError(err.message);
    } finally { setProfileSaving(false); }
  }

  async function logout() {
    try { localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(AUTH_CACHE_KEY); } catch { /* ignore */ }
    try { await apiFetch("/api/auth/logout", { method: "POST" }); } catch { /* reload anyway */ }
    window.location.reload();
  }

  async function pollGmailScan() {
    // The scan runs on the server in the background; this loop only
    // follows its progress. It keeps running after a page refresh too
    // (the status check below restarts it), so a refresh no longer
    // loses the scan or its result.
    if (gmailPollingRef.current) return;
    gmailPollingRef.current = true;
    setGmailImporting(true);
    let lastImported = -1;
    try {
      for (let i = 0; i < 240; i++) {
        await new Promise(r => setTimeout(r, 3000));
        let s;
        try {
          const r = await apiFetch("/api/gmail/scan/status");
          if (!r.ok) break;
          s = await r.json();
        } catch { continue; }
        if (s.state === "running") {
          setGmailResult(`Scanning your Gmail… checked ${s.checked} email${s.checked === 1 ? "" : "s"}, imported ${s.imported} new bill${s.imported === 1 ? "" : "s"} so far. New bills appear in your list below as they are found — no need to refresh.`);
          if (s.imported !== lastImported) {
            lastImported = s.imported;
            if (s.imported > 0) { setFilter("All Documents"); setQuery(""); await loadBills(); }
          }
          continue;
        }
        if (s.state === "done") {
          setGmailResult(`Checked ${s.checked} emails — imported ${s.imported} new bill${s.imported === 1 ? "" : "s"}${s.skipped ? `, skipped ${s.skipped} (already saved or not bills)` : ""}. ${s.note || ""}`);
          if (s.imported > 0) { setFilter("All Documents"); setQuery(""); await loadBills(); }
        } else if (s.state === "error") {
          if (s.status_code === 401) {
            setGmailStatus({ connected: false, email: null });
            setGmailResult("Your Gmail connection needs to be renewed — please connect Gmail again.");
          } else {
            setGmailResult(s.detail || "Gmail import failed.");
          }
        }
        break;
      }
    } finally {
      gmailPollingRef.current = false;
      setGmailImporting(false);
    }
  }

  async function importFromGmail() {
    setGmailResult("");
    try {
      const res = await apiFetch("/api/gmail/scan", { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (res.status === 401) {
        setGmailStatus({ connected: false, email: null });
        setGmailResult("Your Gmail connection needs to be renewed — please connect Gmail again.");
        return;
      }
      if (!res.ok) throw new Error(data.detail || "Gmail import failed.");
      setGmailResult("Starting your Gmail scan…");
      await pollGmailScan();
    } catch (e) {
      setGmailResult(e.message);
    }
  }

  useEffect(() => {
    const timer = setTimeout(loadBills, 200);
    return () => clearTimeout(timer);
  }, [query, filter]);

  useEffect(() => {
    // Signed-out visitors see an empty vault — bills belong to an account.
    if (guest) { setBills([]); setSummaryBills([]); setLoading(false); }
  }, [guest]);

  function navigate(label) {
    setActiveNav(label);
    if (label === "Home" || label === "My Bills") setFilter("All Documents");
    if (label === "Warranty Tracker") setFilter("Active Warranties");
    if (label === "Reminders") setFilter("Expiring Soon");
  }

  async function uploadFile(file, form) {
    const body = new FormData();
    body.append("file", file);
    body.append("warranty_months", form.warrantyMonths || "0");
    body.append("category", form.category || "Other");
    body.append("product_override", form.product || "");
    body.append("seller_override", form.seller || "");
    body.append("purchase_date_override", form.purchaseDate || "");
    body.append("amount_override", form.amount || "");
    body.append("extracted_text_override", form.extractedText || "");
    const res = await apiFetch("/api/bills", { method: "POST", body });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Upload failed.");
    setNotice("Bill uploaded and saved successfully.");
    setUploadOpen(false); setFilter("All Documents"); setQuery("");
    await loadBills();
  }

  async function removeBill(bill) {
    if (!window.confirm(`Delete "${bill.product}" and its stored file?`)) return;
    try {
      const res = await apiFetch(`/api/bills/${bill.id}`, { method: "DELETE" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Could not delete bill.");
      setSelectedBill(null); setNotice("Bill deleted."); await loadBills();
    } catch (e) { setError(e.message); }
  }

  async function updateBill(bill, fields) {
    const res = await apiFetch(`/api/bills/${bill.id}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(fields)
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Could not update bill.");
    setSelectedBill(data); setNotice("Bill details updated."); await loadBills();
  }

  const counts = useMemo(() => ({
    all: summaryBills.length,
    active: summaryBills.filter(b => b.warranty_status === "Active").length,
    soon: summaryBills.filter(b => b.warranty_status === "Expiring Soon").length,
    expired: summaryBills.filter(b => b.warranty_status === "Expired").length,
    noWarranty: summaryBills.filter(b => b.warranty_status === "No Warranty").length,
    totalSpent: summaryBills.reduce((sum, b) => sum + (Number(b.amount) || 0), 0)
  }), [summaryBills]);

  // Instant client-side filtering on top of the server results, so typing in
  // search visibly filters straight away (the old build appeared not to
  // filter at all while it waited on a cold/slow backend response).
  const visibleBills = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return bills;
    return bills.filter(b => [b.product, b.seller, b.category, b.original_filename]
      .map(v => String(v || "").toLowerCase()).join(" ").includes(needle));
  }, [bills, query]);
  // Google-style live suggestions under the search box, searched across
  // the full vault (summaryBills), not just the currently listed page.
  const searchMatches = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return [];
    const pool = summaryBills.length ? summaryBills : bills;
    return pool.filter(b => [b.product, b.seller, b.category, b.original_filename, b.invoice_no, b.order_no]
      .map(v => String(v || "").toLowerCase()).join(" ").includes(needle)).slice(0, 6);
  }, [summaryBills, bills, query]);
  const pageTitle = activeNav === "Home" ? (auth ? `Good evening, ${firstName}` : "Good evening") : activeNav;
  const pageSubtitle = activeNav === "Home" ? "Every purchase, safely kept in one place." :
    activeNav === "My Bills" ? "Find and manage all your purchase receipts." :
    activeNav === "Warranty Tracker" ? "Know what is covered and what needs attention." :
    activeNav === "Reminders" ? "Bills with warranties that need a little attention." : "Make Raseed work the way you like.";

  return (
    <div className={`app-shell ${dark ? "dark" : ""}`}>
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark"><ClipboardList size={25} strokeWidth={2.1} /></span>
          <span className="brand-name">रसीद</span>
        </div>
        <div className="brand-caption">YOUR PERSONAL BILL VAULT</div>
        <nav className="side-nav">
          {NAV.map(([Icon, label]) => (
            <button key={label} className={`nav-item ${activeNav === label ? "selected" : ""}`} onClick={() => navigate(label)}>
              <Icon size={19} /><span>{label}</span>
            </button>
          ))}
        </nav>
        <div className="sidebar-note">
          <div className="paper-art"><FileText size={36} /><FileCheck2 size={30} /></div>
          <p>Har bill sambhaal ke.<br />Every receipt, remembered.</p>
        </div>
        <div className="sidebar-bottom"><ShieldCheck size={15} /> Your bills, in one safe place</div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <div className="search-wrap">
            <div className="searchbox"><Search size={19} /><input value={query} onChange={e => setQuery(e.target.value)} onFocus={() => setSearchFocus(true)} onBlur={() => setTimeout(() => setSearchFocus(false), 150)} onKeyDown={e => { if (e.key === "Escape") setSearchFocus(false); }} placeholder="Search bills, products or brands..." />
              {query && <button aria-label="Clear search" onClick={() => setQuery("")}><X size={16} /></button>}
            </div>
            {searchFocus && query.trim() && (
              <div className="search-results">
                {searchMatches.length ? searchMatches.map(bill => (
                  <button key={bill.id} className="search-result" onMouseDown={e => { e.preventDefault(); setSelectedBill(bill); setQuery(""); setSearchFocus(false); }}>
                    <span className="sr-thumb"><FileText size={16} /></span>
                    <span className="sr-main"><b>{bill.product || bill.original_filename || "Untitled bill"}</b><small>{bill.seller || bill.category || "Purchase"} · {formatDate(bill.purchase_date)}</small></span>
                    <span className="sr-amount">{money(bill.amount)}</span>
                  </button>
                )) : <div className="search-none">No bills match “{query.trim()}”</div>}
              </div>
            )}
          </div>
          <div className="top-actions">
            <button className="icon-button" title="Warranty reminders" onClick={() => navigate("Reminders")}><Bell size={20} />{remindersEnabled && counts.soon > 0 && <i />}</button>
            {auth === null
              ? <span style={{ display: "inline-block", width: 118, height: 40 }} />
              : guest
              ? <a className="user-chip" href={`${API}/api/auth/google`}><span className="avatar"><LogIn size={15} /></span><span>Login</span></a>
              : <button className="user-chip" onClick={() => navigate("Settings")}><span className="avatar">{firstName.charAt(0).toUpperCase()}</span><span>Hi, {firstName}</span><ChevronDown size={15} /></button>}
          </div>
        </header>

        <section className="welcome">
          <div><div className="eyebrow">NAMASTE{auth ? `, ${firstName.toUpperCase()}` : ""}</div><h1>{pageTitle} <Heart className="heart" size={26} /></h1><p>{pageSubtitle}</p></div>
          {activeNav === "Home" && auth !== null && (guest
            ? <a className="primary-button" href={`${API}/api/auth/google`}><LogIn size={18} /> Login</a>
            : <button className="primary-button" onClick={() => setUploadOpen(true)}><Camera size={18} /> Scan / Upload Bill</button>)}
        </section>

        {notice && <div className="notice"><Check size={17} /><span>{notice}</span><button onClick={() => setNotice("")}><X size={16} /></button></div>}
        {error && <div className="alert-error">{error}<button onClick={() => { setError(""); loadBills(); }}>Try again</button></div>}
        {gmailError && <div className="alert-error">{gmailError}<button onClick={() => setGmailError("")}><X size={16} /></button></div>}

        {activeNav === "Home" && (
          <>
            <section className="summary-grid" aria-label="Your bill summary">
              <div className="summary-card"><small>TOTAL BILLS</small><b>{counts.all}</b><span>saved in your vault</span></div>
              <div className="summary-card"><small>TOTAL SPENT</small><b>{money(counts.totalSpent || null)}</b><span>across saved bills with an amount</span></div>
              <div className="summary-card"><small>ACTIVE WARRANTIES</small><b>{counts.active}</b><span>currently covered</span></div>
              <div className="summary-card"><small>EXPIRING SOON</small><b>{counts.soon}</b><span>ending in the next 30 days</span></div>
            </section>

            <section className="intro-card">
              <div className="intro-icon"><FileCheck2 size={24} /></div>
              <div className="intro-copy"><h2>Your receipts, all together</h2><p>Upload a bill once. Find it whenever you need it.</p></div>
              <button className="soft-button" onClick={() => navigate("My Bills")}>View my bills <ExternalLink size={15} /></button>
              <div className="intro-foot">Keep your purchase records together and review each saved receipt whenever you need it.</div>
            </section>

            {auth?.connected && (
              <section className="intro-card">
                <div className="intro-icon"><Mail size={24} /></div>
                <div className="intro-copy"><h2>Bills straight from your Gmail</h2><p>{gmailStatus?.connected ? `Connected to ${gmailStatus.email}. Import invoice emails from the last year into your vault.` : "Connect Gmail once, and Raseed will find your invoice emails and add them to your vault for you."}</p></div>
                {gmailStatus?.connected
                  ? <button className="soft-button" onClick={importFromGmail} disabled={gmailImporting}>{gmailImporting ? "Scanning your Gmail…" : <><Mail size={15} /> Import bills from Gmail</>}</button>
                  : <a className="soft-button" href={`${API}/api/auth/gmail/connect`}><Mail size={15} /> Connect Gmail</a>}
                <div className="intro-foot">{gmailImporting ? "Progress updates above automatically, and new bills appear in your list as they are found — no need to refresh. Bills already in your vault are skipped automatically." : "Connect with the same Google account you logged in with. Raseed only reads invoice, receipt and order emails — it never sends or deletes mail."}</div>
              </section>
            )}
            {gmailResult && <div className="notice"><Check size={17} /><span>{gmailResult}</span><button onClick={() => setGmailResult("")}><X size={16} /></button></div>}

          </>
        )}

        {activeNav === "Settings" && (
          <section className="panel settings-panel">
            <div className="panel-heading"><div><h2>Preferences</h2><p>Simple controls for your Raseed workspace.</p></div></div>
            <div className="setting-row"><div className="setting-symbol"><Bell size={19} /></div><div className="setting-copy"><b>Warranty reminders</b><span>Show bills whose warranty is expiring soon.</span></div><button className={`toggle ${remindersEnabled ? "on" : ""}`} onClick={() => { setRemindersEnabled(v => !v); setNotice(`Warranty reminders ${remindersEnabled ? "paused" : "enabled"}.`); }} aria-label="Toggle reminders"><span /></button></div>
            <div className="setting-row"><div className="setting-symbol"><Moon size={19} /></div><div className="setting-copy"><b>Appearance</b><span>Switch between light and dark themes.</span></div><button className="soft-button" onClick={() => setDark(v => !v)}>{dark ? <Sun size={16} /> : <Moon size={16} />}{dark ? "Light mode" : "Dark mode"}</button></div>
            <div className="setting-row"><div className="setting-symbol"><ShieldCheck size={19} /></div><div className="setting-copy"><b>File storage</b><span>Uploads are sent to your backend; Cloudinary credentials stay server-side.</span></div></div>
            {auth?.connected && (
              <div className="setting-row"><div className="setting-symbol"><Mail size={19} /></div><div className="setting-copy"><b>Gmail import</b><span>{gmailStatus?.connected ? `Connected (${gmailStatus.email}) — import invoice emails into your vault.` : "Connect Gmail to import invoice emails automatically."}</span></div>{gmailStatus?.connected ? <button className="soft-button" onClick={importFromGmail} disabled={gmailImporting}>{gmailImporting ? "Scanning…" : "Import bills"}</button> : <a className="soft-button" href={`${API}/api/auth/gmail/connect`}>Connect Gmail</a>}</div>
            )}
            {auth?.connected && (
              <div className="setting-row"><div className="setting-symbol"><UserRound size={19} /></div><div className="setting-copy"><b>{auth.profile?.name || "Your account"}</b><span>{auth.email}{auth.profile?.purpose ? ` · ${auth.profile.purpose}` : ""}{auth.profile?.age ? ` · Age ${auth.profile.age}` : ""}</span></div><button className="soft-button" onClick={logout}><LogOut size={16} /> Log out</button></div>
            )}
          </section>
        )}

        {activeNav !== "Settings" && (
          <section className="panel bills-panel">
            <div className="panel-heading">
              <div><div className="eyebrow">{activeNav === "Home" ? "YOUR COLLECTION" : "MANAGE YOUR RECEIPTS"}</div><h2>{activeNav === "Home" ? "Recently added bills" : activeNav === "My Bills" ? "My Bills" : activeNav === "Warranty Tracker" ? "Warranty overview" : "Upcoming warranty reminders"}</h2><p>{activeNav === "Warranty Tracker" ? "Warranty details are shown here, not on the home page." : activeNav === "Reminders" ? "Items marked expiring soon are listed first." : "Open a bill to view or update its details."}</p></div>
            </div>
            <div className="list-toolbar">
              <div className="filter-tabs">{FILTERS.map(f => <button key={f} className={filter === f ? "active" : ""} onClick={() => setFilter(f)}>{f}</button>)}</div>
              <button className="sort-button" onClick={loadBills}><RefreshCw size={15} /> Refresh</button>
            </div>
            {loading ? <div className="empty-state"><RefreshCw className="spin" size={24} /><b>Loading your bills…</b></div> :
              visibleBills.length === 0 ? <div className="empty-state"><div className="empty-icon"><FileText size={27} /></div><b>{guest ? "Login to see your bills" : query ? "No matching bills" : filter !== "All Documents" ? "No bills in this view" : "No bills here yet"}</b><span>{guest ? "Your vault is personal — your bills appear here only after you login." : query ? "Try a different search." : filter !== "All Documents" ? `You have ${counts.all} bill${counts.all === 1 ? "" : "s"} saved, but none match “${filter}” right now.` : "Upload your first receipt and it will appear here."}</span>{guest ? <a className="primary-button" href={`${API}/api/auth/google`}><LogIn size={17} /> Login with Google</a> : filter !== "All Documents" || query ? <button className="primary-button" onClick={() => { setFilter("All Documents"); setQuery(""); }}>Show all documents</button> : <button className="primary-button" onClick={() => setUploadOpen(true)}><Plus size={17} /> Add a bill</button>}</div> :
              <div className="bill-list">
                {visibleBills.map(bill => <button className="bill-row" key={bill.id} onClick={() => setSelectedBill(bill)}>
                  <span className="receipt-thumb"><FileText size={22} /><small>₹</small></span>
                  <span className="bill-main"><b>{bill.product || bill.original_filename || "Untitled bill"}</b><small>{bill.seller || bill.category || "Purchase"} · {formatDate(bill.purchase_date)}</small></span>
                  <span className="bill-meta"><small>AMOUNT</small><b>{money(bill.amount)}</b></span>
                  <span className="bill-meta warranty-info"><small>WARRANTY ENDS</small><b>{bill.warranty_lifetime ? "Never (lifetime)" : formatDate(bill.warranty_end)}</b></span>
                  <Status status={bill.warranty_status} />
                  <span className="row-arrow"><ChevronDown size={17} /></span>
                </button>)}
              </div>
            }
          </section>
        )}
        <footer className="page-footer">रसीद <span>•</span> HAR BILL SAMBHAAL KE</footer>
      </main>

      {auth?.connected && !auth.profile_complete && (
        <div className="modal-backdrop">
          <div className="modal auth-modal">
            <div className="brand auth-brand"><span className="brand-mark"><ClipboardList size={25} strokeWidth={2.1} /></span><span className="brand-name">रसीद</span></div>
            <h2 className="auth-heading">Welcome to Raseed</h2>
            <p className="auth-copy">First time here? Three quick questions and your vault is ready.</p>
            <form onSubmit={saveProfile} className="auth-form">
              <label className="field"><span>Your name</span>
                <input value={profileForm.name} onChange={e => setProfileForm(f => ({ ...f, name: e.target.value }))} placeholder="e.g. Urvi Garg" autoFocus />
              </label>
              <label className="field"><span>What will you use Raseed for?</span>
                <select value={profileForm.purpose} onChange={e => setProfileForm(f => ({ ...f, purpose: e.target.value }))}>
                  <option>Personal bills</option>
                  <option>Family bills</option>
                  <option>Business / shop bills</option>
                  <option>Tracking warranties</option>
                  <option>A bit of everything</option>
                </select>
              </label>
              <label className="field"><span>Your age</span>
                <input type="number" min="5" max="120" value={profileForm.age} onChange={e => setProfileForm(f => ({ ...f, age: e.target.value }))} placeholder="e.g. 21" />
              </label>
              {profileError && <div className="error-box">{profileError}</div>}
              <button className="primary-button auth-submit" type="submit" disabled={profileSaving}>{profileSaving ? "Saving…" : "Start using Raseed"}</button>
            </form>
            <button className="auth-link" onClick={logout}>Sign in with a different account</button>
          </div>
        </div>
      )}
      {uploadOpen && <UploadModal onClose={() => setUploadOpen(false)} onUpload={uploadFile} />}
      {selectedBill && <BillModal bill={selectedBill} onClose={() => setSelectedBill(null)} onDelete={removeBill} onSave={updateBill} />}
    </div>
  );
}

async function runClientOcr(file, onProgress) {
  // OCR in the browser (tesseract.js) so bill reading works even when the
  // backend host has no Tesseract installed. The extracted text is sent to
  // the backend, which parses product / seller / date / amount from it.
  const { createWorker } = await import("tesseract.js");
  const worker = await createWorker("eng", 1, {
    logger: m => { if (m.status === "recognizing text") onProgress(Math.round((m.progress || 0) * 100)); }
  });
  try {
    const { data } = await worker.recognize(file);
    return (data && data.text) || "";
  } finally {
    await worker.terminate();
  }
}

function UploadModal({ onClose, onUpload }) {
  const [file, setFile] = useState(null);
  const [form, setForm] = useState({ product: "", seller: "", purchaseDate: "", amount: "", warrantyMonths: "0", category: "Other", extractedText: "" });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [ocrState, setOcrState] = useState(""); // "", "running", "done", "failed", "pdf"
  const [ocrProgress, setOcrProgress] = useState(0);

  async function handleFile(nextFile) {
    setFile(nextFile); setError("");
    setForm(old => ({ ...old, extractedText: "" }));
    if (!nextFile) { setOcrState(""); return; }
    if (nextFile.type === "application/pdf" || nextFile.name.toLowerCase().endsWith(".pdf")) {
      setOcrState("pdf"); // the backend extracts PDF text / runs OCR server-side
      return;
    }
    if (!nextFile.type.startsWith("image/")) { setOcrState(""); return; }
    setOcrState("running"); setOcrProgress(0);
    try {
      const text = await runClientOcr(nextFile, setOcrProgress);
      if (text && text.trim().length >= 20) {
        setForm(old => ({ ...old, extractedText: text }));
        setOcrState("done");
      } else {
        setOcrState("failed");
      }
    } catch {
      setOcrState("failed"); // upload still works; the backend will try its own OCR
    }
  }
  function change(key, value) { setForm(old => ({ ...old, [key]: value })); }
  async function submit(e) {
    e.preventDefault();
    if (!file) { setError("Please choose a receipt image or PDF first."); return; }
    setSaving(true); setError("");
    try { await onUpload(file, form); }
    catch (err) { setError(err.message); }
    finally { setSaving(false); }
  }
  return <div className="modal-backdrop" onMouseDown={e => e.target === e.currentTarget && onClose()}><form className="modal upload-modal" onSubmit={submit}>
    <div className="modal-head"><div><div className="eyebrow">ADD TO YOUR VAULT</div><h2>Scan / Upload a bill</h2><p>Choose a receipt image or PDF. The backend will extract what it can.</p></div><button type="button" className="icon-button" onClick={onClose}><X size={20} /></button></div>
    <label className="dropzone"><input type="file" accept="image/jpeg,image/png,image/webp,application/pdf,.jpg,.jpeg,.png,.webp,.pdf" onChange={e => { handleFile(e.target.files?.[0] || null); }} /><span className="upload-circle"><Upload size={22} /></span><b>{file ? file.name : "Choose a bill to upload"}</b><small>JPG, PNG, WEBP or PDF · up to 12 MB</small></label>
    {file && <div className="file-selected"><Check size={15} /> Ready to upload · {Math.max(1, Math.round(file.size / 1024))} KB</div>}
    {ocrState === "running" && <div className="file-selected">Reading your bill with OCR… {ocrProgress}%</div>}
    {ocrState === "done" && <div className="file-selected"><Check size={15} /> Bill text read by OCR — details will be filled in automatically. You can still correct them below.</div>}
    {ocrState === "failed" && <div className="file-selected">Couldn’t read text from this image here — the server will try, and you can fill the details in below.</div>}
    {ocrState === "pdf" && <div className="file-selected">PDF selected — its text will be extracted on the server when you upload.</div>}
    <div className="form-grid">
      <label>Product / item<input value={form.product} onChange={e => change("product", e.target.value)} placeholder="e.g. Wireless headphones" /></label>
      <label>Seller / brand<input value={form.seller} onChange={e => change("seller", e.target.value)} placeholder="e.g. Croma" /></label>
      <label>Purchase date<input type="date" value={form.purchaseDate} onChange={e => change("purchaseDate", e.target.value)} /></label>
      <label>Amount (₹)<input type="number" min="0" step="0.01" value={form.amount} onChange={e => change("amount", e.target.value)} placeholder="e.g. 2499" /></label>
      <label>Warranty duration<select value={form.warrantyMonths} onChange={e => change("warrantyMonths", e.target.value)}><option value="0">No warranty / unknown</option><option value="3">3 months</option><option value="6">6 months</option><option value="12">1 year</option><option value="24">2 years</option><option value="36">3 years</option><option value="60">5 years</option><option value="120">10 years</option></select></label>
      <label>Category<select value={form.category} onChange={e => change("category", e.target.value)}>{["Other","Electronics","Home & Kitchen","Fashion","Appliances","Furniture","Personal care","Travel"].map(c => <option key={c}>{c}</option>)}</select></label>
    </div>
    <p className="ocr-note"><ShieldCheck size={15} /> OCR reads your bill (in your browser for photos, on the server for PDFs) and fills in product, seller, date and amount. Check the saved details against your original bill.</p>
    {error && <div className="inline-error">{error}</div>}
    <div className="modal-foot"><button type="button" className="secondary-button" onClick={onClose}>Cancel</button><button type="submit" className="primary-button" disabled={saving}>{saving ? "Uploading…" : <><Upload size={16} /> Upload bill</>}</button></div>
  </form></div>;
}

function BillModal({ bill, onClose, onDelete, onSave }) {
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [form, setForm] = useState({ product: bill.product || "", seller: bill.seller || "", purchase_date: bill.purchase_date || "", amount: bill.amount ?? "", warranty_months: bill.warranty_months ?? 0, category: bill.category || "Other", invoice_no: bill.invoice_no || "", order_no: bill.order_no || "", warranty_lifetime: bill.warranty_lifetime ? 1 : 0 });
  function change(key, value) { setForm(old => ({ ...old, [key]: value })); }
  async function save() {
    setSaving(true); setError("");
    try {
      const payload = { ...form, amount: form.amount === "" ? null : Number(form.amount), warranty_months: Number(form.warranty_months) };
      await onSave(bill, payload); setEditing(false);
    } catch (e) { setError(e.message); }
    finally { setSaving(false); }
  }
  return <div className="modal-backdrop" onMouseDown={e => e.target === e.currentTarget && onClose()}><div className="modal detail-modal">
    <div className="modal-head"><div><div className="eyebrow">YOUR SAVED RECEIPT</div><h2>{editing ? "Edit bill details" : bill.product || "Bill details"}</h2><p>{bill.seller || bill.original_filename || "Purchase record"}</p></div><button className="icon-button" onClick={onClose}><X size={20} /></button></div>
    <div className="detail-status"><Status status={bill.warranty_status} /><span>Added {formatDate((bill.created_at || "").slice(0,10))}</span></div>
    {editing ? <div className="form-grid">
      <label>Product<input value={form.product} onChange={e => change("product", e.target.value)} /></label>
      <label>Seller<input value={form.seller} onChange={e => change("seller", e.target.value)} /></label>
      <label>Purchase date<input type="date" value={form.purchase_date} onChange={e => change("purchase_date", e.target.value)} /></label>
      <label>Amount (₹)<input type="number" min="0" step="0.01" value={form.amount} onChange={e => change("amount", e.target.value)} /></label>
      <label>Warranty months<input type="number" min="0" max="120" value={form.warranty_months} onChange={e => change("warranty_months", e.target.value)} /></label>
      <label>Category<input value={form.category} onChange={e => change("category", e.target.value)} /></label>
      <label>Lifetime warranty<input type="checkbox" checked={!!form.warranty_lifetime} onChange={e => change("warranty_lifetime", e.target.checked ? 1 : 0)} /></label>
      <label>Invoice number<input value={form.invoice_no} onChange={e => change("invoice_no", e.target.value)} placeholder="e.g. AF26GJ317452231" /></label>
      <label>Order number<input value={form.order_no} onChange={e => change("order_no", e.target.value)} placeholder="e.g. FN5954801329" /></label>
    </div> : <div className="detail-grid">
      <div><small>PRODUCT</small><b>{bill.product || "—"}</b></div><div><small>AMOUNT</small><b>{money(bill.amount)}</b></div>
      <div><small>SELLER</small><b>{bill.seller || "—"}</b></div><div><small>PURCHASE DATE</small><b>{formatDate(bill.purchase_date)}</b></div>
      <div><small>WARRANTY DURATION</small><b>{bill.warranty_lifetime ? "Lifetime" : bill.warranty_months ? `${bill.warranty_months} months` : "Not specified"}</b></div><div><small>WARRANTY ENDS</small><b>{bill.warranty_lifetime ? "Never (lifetime)" : formatDate(bill.warranty_end)}</b></div>
      <div><small>INVOICE NUMBER</small><b>{bill.invoice_no || "—"}</b></div><div><small>ORDER NUMBER</small><b>{bill.order_no || "—"}</b></div>
    </div>}
    {bill.cloudinary_url && <a className="file-link" href={bill.cloudinary_url} target="_blank" rel="noreferrer"><ExternalLink size={16} /> Open original bill</a>}
    {error && <div className="inline-error">{error}</div>}
    <div className="modal-foot"><button className="danger-button" onClick={() => onDelete(bill)}><Trash2 size={15} /> Delete</button><span className="modal-spacer" />
      {editing && <button className="secondary-button" onClick={() => setEditing(false)}>Cancel</button>}
      {editing ? <button className="primary-button" disabled={saving} onClick={save}>{saving ? "Saving…" : "Save changes"}</button> : <button className="primary-button" onClick={() => setEditing(true)}><Pencil size={15} /> Edit details</button>}
    </div>
  </div></div>;
}

createRoot(document.getElementById("root")).render(<App />);

