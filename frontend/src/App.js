import { useCallback, useEffect, useRef, useState } from "react";
import "@/App.css";
import axios from "axios";
import { motion, useReducedMotion } from "framer-motion";
import {
  Activity,
  ArrowUpRight,
  Check,
  Copy,
  Cpu,
  Lock,
  LockOpen,
  Loader2,
  MousePointerClick,
  Pencil,
  Radio,
  RotateCcw,
  Terminal,
  Users,
  X,
} from "lucide-react";
import ConfigPanel from "@/ConfigPanel";
import { fmtError } from "@/lib/adminUtils";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const AD_URL = `${process.env.REACT_APP_BACKEND_URL}/api/r?click_id={click_id}`;

const BOOT_LOG = [
  "init telegram_bot .............. ok",
  "transport ............. webhook",
  "handlers ....... start + catchall",
  "postback ....... trafficstars s2s",
];

const useFlash = (value) => {
  const prev = useRef(value);
  const [flash, setFlash] = useState(false);
  useEffect(() => {
    if (prev.current !== undefined && value !== undefined && prev.current !== value) {
      setFlash(true);
      const t = setTimeout(() => setFlash(false), 750);
      prev.current = value;
      return () => clearTimeout(t);
    }
    prev.current = value;
  }, [value]);
  return flash;
};

const Corner = ({ className }) => (
  <span
    aria-hidden
    className={`pointer-events-none absolute h-2.5 w-2.5 border-emerald-500/40 ${className}`}
  />
);

const Cell = ({ icon: Icon, label, value, sub, testid, onReset, unlocked, metric }) => {
  const flash = useFlash(value);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const confirmTimerRef = useRef(null);

  useEffect(() => () => clearTimeout(confirmTimerRef.current), []);

  const handleReset = async (e) => {
    e.stopPropagation();
    if (!confirming) {
      setConfirming(true);
      confirmTimerRef.current = setTimeout(() => setConfirming(false), 4000);
      return;
    }
    clearTimeout(confirmTimerRef.current);
    setBusy(true);
    try {
      await onReset(metric);
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  };

  return (
    <div
      data-testid={testid}
      className="group relative border border-zinc-800 bg-zinc-900/40 p-4 backdrop-blur-md transition-colors duration-150 hover:border-emerald-500/50 sm:p-5"
    >
      <div className="flex items-center justify-between">
        <span className="text-[10px] uppercase tracking-[0.2em] text-zinc-500">{label}</span>
        <div className="flex items-center gap-2.5">
          {unlocked && onReset && (
            <button
              data-testid={`${testid}-reset`}
              onClick={handleReset}
              disabled={busy}
              title="reset this metric to zero"
              className={`flex items-center gap-1 rounded-full border px-2 py-0.5 text-[9px] font-semibold uppercase tracking-[0.15em] transition-colors duration-150 focus:outline-none ${
                confirming
                  ? "border-rose-500/70 bg-rose-500/10 text-rose-300"
                  : "border-zinc-700 bg-zinc-800/60 text-zinc-300 hover:border-cyan-400/70 hover:text-cyan-300"
              }`}
            >
              {busy ? (
                <Loader2 size={11} strokeWidth={2} className="animate-spin" />
              ) : (
                <>
                  <RotateCcw size={10} strokeWidth={2} />
                  {confirming ? "confirm?" : "reset"}
                </>
              )}
            </button>
          )}
          <Icon
            size={13}
            strokeWidth={1.5}
            className="text-zinc-600 transition-colors duration-150 group-hover:text-emerald-400"
          />
        </div>
      </div>
      <div
        className={`mt-3 text-3xl font-extrabold tracking-tighter transition-colors duration-300 sm:mt-7 sm:text-5xl ${
          flash ? "text-cyan-300 text-glow-cyan" : "text-zinc-50"
        }`}
      >
        {value ?? "--"}
      </div>
      {sub && <div className="mt-1 text-[10px] uppercase tracking-[0.15em] text-zinc-600">{sub}</div>}
      <Corner className="bottom-0 right-0 border-b border-r opacity-0 transition-opacity duration-150 group-hover:opacity-100" />
    </div>
  );
};

const Row = ({ children, delay, reduced }) => (
  <motion.div
    initial={reduced ? false : { opacity: 0, y: 10 }}
    animate={{ opacity: 1, y: 0 }}
    transition={{ duration: 0.35, delay: reduced ? 0 : delay, ease: [0.22, 1, 0.36, 1] }}
  >
    {children}
  </motion.div>
);

const Home = () => {
  const [status, setStatus] = useState(null);
  const [settings, setSettings] = useState(null);
  const [error, setError] = useState(false);
  const [copied, setCopied] = useState(false);
  const [latency, setLatency] = useState(null);
  const reduced = useReducedMotion();

  const [token, setToken] = useState(() => sessionStorage.getItem("adminToken") || "");
  const [unlockOpen, setUnlockOpen] = useState(false);
  const [pin, setPin] = useState("");
  const [authErr, setAuthErr] = useState("");
  const [authBusy, setAuthBusy] = useState(false);

  const [editAcc, setEditAcc] = useState(false);
  const [accVal, setAccVal] = useState("");
  const [accErr, setAccErr] = useState("");
  const [accBusy, setAccBusy] = useState(false);
  const [notice, setNotice] = useState("");

  const loadStatus = useCallback(async () => {
    const t0 = performance.now();
    try {
      const { data } = await axios.get(`${API}/bot/status`);
      setLatency(Math.round(performance.now() - t0));
      setStatus(data);
      setError(false);
    } catch (e) {
      setError(true);
    }
  }, []);

  useEffect(() => {
    loadStatus();
    axios
      .get(`${API}/settings`)
      .then(({ data }) => setSettings(data))
      .catch(() => {});
    const t = setInterval(loadStatus, 15000);
    return () => clearInterval(t);
  }, [loadStatus]);

  const lock = () => {
    sessionStorage.removeItem("adminToken");
    setToken("");
    setEditAcc(false);
    setUnlockOpen(false);
  };

  const submitPin = async (e) => {
    e?.preventDefault();
    setAuthBusy(true);
    setAuthErr("");
    try {
      const { data } = await axios.post(`${API}/admin/unlock`, { pin });
      sessionStorage.setItem("adminToken", data.token);
      setToken(data.token);
      setPin("");
      setUnlockOpen(false);
      setNotice("");
    } catch (err) {
      setAuthErr(fmtError(err));
    }
    setAuthBusy(false);
  };

  const saveSettings = async (patch) => {
    try {
      const { data } = await axios.put(`${API}/settings`, patch, {
        headers: { Authorization: `Bearer ${token}` },
      });
      setSettings(data);
      loadStatus();
      return data;
    } catch (err) {
      if (err?.response?.status === 401) {
        lock();
        setNotice(fmtError(err));
        setUnlockOpen(true);
      }
      throw err;
    }
  };

  const [activateBusy, setActivateBusy] = useState(false);
  const [activateMsg, setActivateMsg] = useState("");

  const activateHere = async () => {
    setActivateBusy(true);
    setActivateMsg("");
    try {
      await axios.post(`${API}/admin/telegram/activate`, {}, { headers: { Authorization: `Bearer ${token}` } });
      setActivateMsg("this environment is now live");
      loadStatus();
    } catch (err) {
      if (err?.response?.status === 401) {
        lock();
        setNotice(fmtError(err));
        setUnlockOpen(true);
      } else {
        setActivateMsg(fmtError(err));
      }
    }
    setActivateBusy(false);
    setTimeout(() => setActivateMsg(""), 5000);
  };

  const changePin = async (newPin) => {
    try {
      await axios.post(
        `${API}/admin/pin`,
        { new_pin: newPin },
        { headers: { Authorization: `Bearer ${token}` } },
      );
    } catch (err) {
      if (err?.response?.status === 401) {
        lock();
        setNotice(fmtError(err));
        setUnlockOpen(true);
      }
      throw err;
    }
  };

  const broadcast = async (message, file) => {
    const form = new FormData();
    form.append("message", message);
    if (file) form.append("file", file);
    try {
      const { data } = await axios.post(`${API}/admin/broadcast`, form, {
        headers: { Authorization: `Bearer ${token}` },
      });
      return data;
    } catch (err) {
      if (err?.response?.status === 401) {
        lock();
        setNotice(fmtError(err));
        setUnlockOpen(true);
      }
      throw err;
    }
  };

  const broadcastStatus = async () => {
    const { data } = await axios.get(`${API}/admin/broadcast/latest`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    return data;
  };

  const broadcastHistory = async () => {
    const { data } = await axios.get(`${API}/admin/broadcast/history`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    return data;
  };

  const uploadWelcomeImage = async (file) => {
    const form = new FormData();
    form.append("file", file);
    try {
      const { data } = await axios.post(`${API}/admin/welcome-image`, form, {
        headers: { Authorization: `Bearer ${token}` },
      });
      setSettings(data);
      return data;
    } catch (err) {
      if (err?.response?.status === 401) {
        lock();
        setNotice(fmtError(err));
        setUnlockOpen(true);
      }
      throw err;
    }
  };

  const removeWelcomeImage = async () => {
    try {
      const { data } = await axios.delete(`${API}/admin/welcome-image`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      setSettings(data);
      return data;
    } catch (err) {
      if (err?.response?.status === 401) {
        lock();
        setNotice(fmtError(err));
        setUnlockOpen(true);
      }
      throw err;
    }
  };

  const resetStat = async (metric) => {
    try {
      const { data } = await axios.post(
        `${API}/admin/stats/reset`,
        { metric },
        { headers: { Authorization: `Bearer ${token}` } },
      );
      setStatus(data);
    } catch (err) {
      if (err?.response?.status === 401) {
        lock();
        setUnlockOpen(true);
      }
      setNotice(fmtError(err));
    }
  };

  const openAccountEdit = () => {
    if (!token) {
      setUnlockOpen(true);
      return;
    }
    setAccVal(settings?.official_username || "");
    setAccErr("");
    setEditAcc(true);
  };

  const saveAccount = async () => {
    setAccBusy(true);
    setAccErr("");
    try {
      await saveSettings({ official_username: accVal });
      setEditAcc(false);
    } catch (err) {
      setAccErr(fmtError(err));
    }
    setAccBusy(false);
  };

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(AD_URL);
    } catch (e) {
      /* clipboard blocked — the value is selectable */
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1800);
  };

  const online = !error && status?.online;
  const notLiveHere = !error && status && !status.is_live_here;
  const failed = status?.postbacks_failed ?? 0;
  const healthy = failed === 0;
  const handle = settings?.official_username ? `@${settings.official_username}` : "--";

  return (
    <div
      data-testid="bot-status-page"
      className="fx-scanlines fx-grid relative min-h-screen bg-zinc-950 font-mono text-zinc-300 antialiased"
    >
      <div className="pointer-events-none fixed left-0 top-0 h-[45vh] w-full bg-[radial-gradient(ellipse_60%_100%_at_15%_0%,rgba(52,211,153,0.10),transparent_70%)]" />

      <div className="pointer-events-none fixed right-0 top-0 z-20 hidden h-full w-10 items-center justify-center border-l border-zinc-900 xl:flex">
        <span className="rotate-180 whitespace-nowrap text-[10px] uppercase tracking-[0.4em] text-zinc-700 [writing-mode:vertical-rl]">
          trafficstars &nbsp;·&nbsp; conversion relay &nbsp;·&nbsp; v1
        </span>
      </div>

      <div className="relative z-10 max-w-6xl px-4 py-7 sm:px-10 sm:py-10">
        {/* ── top telemetry strip ───────────────────────────── */}
        <Row delay={0.02} reduced={reduced}>
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2 border-b border-zinc-800 pb-3 text-[10px] uppercase tracking-[0.2em] text-zinc-600">
            <span className="flex items-center gap-2 text-zinc-400">
              <Cpu size={12} strokeWidth={1.5} className="text-emerald-400" />
              node/emergent
            </span>
            <span>proto :: webhook</span>
            <span>
              lat ::{" "}
              <span className={latency !== null ? "text-emerald-400" : ""}>
                {latency !== null ? `${latency}ms` : "--"}
              </span>
            </span>

            <div className="ml-auto flex flex-wrap items-center gap-2">
              {token && notLiveHere && (
                <button
                  data-testid="activate-webhook-btn"
                  onClick={activateHere}
                  disabled={activateBusy}
                  className="flex items-center gap-2 border border-amber-500/40 bg-amber-500/10 px-2.5 py-1 tracking-[0.18em] text-amber-400 transition-colors duration-150 hover:bg-amber-500/20 disabled:opacity-50 focus:outline-none focus:ring-1 focus:ring-amber-500"
                >
                  {activateBusy ? (
                    <Loader2 size={11} strokeWidth={2} className="animate-spin" />
                  ) : (
                    <Radio size={11} strokeWidth={1.5} />
                  )}
                  [ make this the live bot ]
                </button>
              )}
              <button
                data-testid="lock-chip"
                onClick={() => (token ? lock() : setUnlockOpen((v) => !v))}
                className={`flex items-center gap-2 border px-2.5 py-1 tracking-[0.18em] transition-colors duration-150 focus:outline-none focus:ring-1 focus:ring-cyan-500 ${
                  token
                    ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-400"
                    : "border-zinc-700 text-zinc-400 hover:border-amber-400 hover:text-amber-400"
                }`}
              >
                {token ? <LockOpen size={11} strokeWidth={1.5} /> : <Lock size={11} strokeWidth={1.5} />}
                {token ? "[ unlocked ]" : "[ locked ]"}
              </button>
              <span
                data-testid="bot-status-pill"
                className={`flex items-center gap-2 border px-2.5 py-1 tracking-[0.18em] ${
                  online
                    ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-400 text-glow-emerald"
                    : notLiveHere
                    ? "border-amber-500/30 bg-amber-500/10 text-amber-400"
                    : "border-rose-500/30 bg-rose-500/10 text-rose-400 text-glow-rose"
                }`}
              >
                <span
                  className={`h-2 w-2 ${
                    online ? "bg-emerald-400 fx-pulse" : notLiveHere ? "bg-amber-400" : "bg-rose-500"
                  }`}
                />
                {error
                  ? "[ link down ]"
                  : online
                  ? "[ system online ]"
                  : notLiveHere
                  ? "[ not live here ]"
                  : "[ offline ]"}
              </span>
              {activateMsg && (
                <span
                  data-testid="activate-webhook-status"
                  className={`text-[10px] ${
                    activateMsg.startsWith("this") ? "text-emerald-400" : "text-rose-400"
                  }`}
                >
                  {activateMsg}
                </span>
              )}
            </div>
          </div>
        </Row>

        {/* ── auth notice ───────────────────────────────────── */}
        {notice && (
          <div
            data-testid="auth-notice"
            className="mt-3 flex items-center gap-3 border border-rose-500/40 bg-rose-500/10 p-3 text-[11px] text-rose-300"
          >
            <span className="text-[10px] uppercase tracking-[0.2em] text-rose-400">
              {"// session"}
            </span>
            <span className="flex-1">{notice}</span>
            <button
              data-testid="auth-notice-close"
              onClick={() => setNotice("")}
              className="text-rose-400 transition-colors duration-150 hover:text-rose-200 focus:outline-none focus:ring-1 focus:ring-cyan-500"
            >
              <X size={12} strokeWidth={1.5} />
            </button>
          </div>
        )}

        {/* ── pin entry ─────────────────────────────────────── */}
        {unlockOpen && !token && (
          <form
            onSubmit={submitPin}
            data-testid="unlock-form"
            className="mt-3 flex flex-wrap items-center gap-3 border border-amber-500/30 bg-zinc-900/60 p-3 backdrop-blur-md"
          >
            <span className="text-[10px] uppercase tracking-[0.2em] text-amber-400">
              {"// enter panel pin"}
            </span>
            <input
              data-testid="pin-input"
              type="password"
              autoFocus
              value={pin}
              onChange={(e) => setPin(e.target.value)}
              placeholder="••••"
              className="w-36 border border-zinc-800 bg-zinc-950/80 px-3 py-1.5 font-mono text-xs tracking-[0.3em] text-zinc-100 outline-none transition-colors duration-150 focus:border-cyan-500/60 focus:ring-1 focus:ring-cyan-500"
            />
            <button
              data-testid="unlock-submit"
              type="submit"
              disabled={authBusy || pin.length < 4}
              className="flex items-center gap-2 border border-emerald-500/50 bg-emerald-500/10 px-3 py-1.5 text-[10px] uppercase tracking-[0.18em] text-emerald-400 transition-colors duration-150 hover:bg-emerald-500/20 disabled:border-zinc-800 disabled:bg-transparent disabled:text-zinc-600 focus:outline-none focus:ring-1 focus:ring-cyan-500"
            >
              {authBusy ? (
                <Loader2 size={12} strokeWidth={2} className="animate-spin" />
              ) : (
                <LockOpen size={12} strokeWidth={1.5} />
              )}
              unlock
            </button>
            <button
              type="button"
              data-testid="unlock-cancel"
              onClick={() => {
                setUnlockOpen(false);
                setAuthErr("");
              }}
              className="border border-zinc-800 p-1.5 text-zinc-500 transition-colors duration-150 hover:text-rose-400 focus:outline-none focus:ring-1 focus:ring-cyan-500"
            >
              <X size={12} strokeWidth={1.5} />
            </button>
            {authErr && (
              <span data-testid="unlock-error" className="text-[11px] text-rose-400">
                {authErr}
              </span>
            )}
          </form>
        )}

        {/* ── hero ──────────────────────────────────────────── */}
        <Row delay={0.08} reduced={reduced}>
          <div className="relative mt-9">
            <p className="flex items-center gap-2 text-[10px] uppercase tracking-[0.28em] text-emerald-400/70">
              <Terminal size={12} strokeWidth={1.5} />
              telegram redirect node
            </p>
            <h1
              data-testid="bot-username"
              className="mt-3 break-all text-2xl font-bold uppercase tracking-tighter text-zinc-50 sm:text-4xl"
            >
              {status?.bot_username ? `@${status.bot_username}` : "@--------"}
              <span className="fx-cursor ml-1 text-emerald-400">_</span>
            </h1>
            <p className="mt-4 max-w-xl text-sm leading-relaxed text-zinc-400">
              Every <span className="text-emerald-400">/start</span> from TrafficStars traffic is
              logged as a conversion, mirrored to their S2S endpoint, and answered instantly with one
              bilingual payload and one button.
            </p>
          </div>
        </Row>

        {/* ── boot log ──────────────────────────────────────── */}
        <Row delay={0.14} reduced={reduced}>
          <div className="relative mt-8 overflow-hidden border border-zinc-800 bg-zinc-900/40 p-4 backdrop-blur-md">
            {!reduced && (
              <span className="fx-sweep pointer-events-none absolute left-0 top-0 h-px w-full bg-gradient-to-r from-transparent via-emerald-400/50 to-transparent" />
            )}
            <div className="space-y-1.5 text-[11px] leading-relaxed">
              {BOOT_LOG.map((line, i) => (
                <motion.p
                  key={line}
                  initial={reduced ? false : { opacity: 0, x: -6 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ duration: 0.25, delay: reduced ? 0 : 0.3 + i * 0.09 }}
                  className="text-zinc-500"
                >
                  <span className="text-emerald-500/70">{">"}</span> {line}
                </motion.p>
              ))}
              <motion.p
                initial={reduced ? false : { opacity: 0, x: -6 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ duration: 0.25, delay: reduced ? 0 : 0.72 }}
                className="text-zinc-300"
              >
                <span className="text-emerald-500/70">{">"}</span> awaiting /start events
                <span className="fx-cursor ml-1 text-emerald-400">▊</span>
              </motion.p>
            </div>
          </div>
        </Row>

        {/* ── destination account (editable) ────────────────── */}
        <Row delay={0.2} reduced={reduced}>
          <div className="mt-4 border border-zinc-800 bg-zinc-900/40 backdrop-blur-md">
            {editAcc ? (
              <div className="p-4">
                <div className="flex flex-wrap items-center gap-3">
                  <span className="text-[10px] uppercase tracking-[0.2em] text-cyan-400">
                    {"// telegram account"}
                  </span>
                  <div className="flex flex-1 items-center border border-zinc-800 bg-zinc-950/80 focus-within:border-cyan-500/60">
                    <span className="pl-3 text-sm text-zinc-500">@</span>
                    <input
                      data-testid="account-input"
                      autoFocus
                      value={accVal}
                      onChange={(e) => setAccVal(e.target.value)}
                      onKeyDown={(e) => e.key === "Enter" && saveAccount()}
                      placeholder="your_username"
                      className="w-full bg-transparent px-2 py-2 font-mono text-sm text-zinc-100 outline-none"
                    />
                  </div>
                  <button
                    data-testid="account-save"
                    onClick={saveAccount}
                    disabled={accBusy || accVal.trim().length < 5}
                    className="flex items-center gap-2 border border-emerald-500/50 bg-emerald-500/10 px-3 py-2 text-[10px] uppercase tracking-[0.18em] text-emerald-400 transition-colors duration-150 hover:bg-emerald-500/20 disabled:border-zinc-800 disabled:bg-transparent disabled:text-zinc-600 focus:outline-none focus:ring-1 focus:ring-cyan-500"
                  >
                    {accBusy ? (
                      <Loader2 size={12} strokeWidth={2} className="animate-spin" />
                    ) : (
                      <Check size={12} strokeWidth={2} />
                    )}
                    save
                  </button>
                  <button
                    data-testid="account-cancel"
                    onClick={() => setEditAcc(false)}
                    className="border border-zinc-800 p-2 text-zinc-500 transition-colors duration-150 hover:text-rose-400 focus:outline-none focus:ring-1 focus:ring-cyan-500"
                  >
                    <X size={12} strokeWidth={1.5} />
                  </button>
                </div>
                {accErr && (
                  <p data-testid="account-error" className="mt-2 text-[11px] text-rose-400">
                    {accErr}
                  </p>
                )}
                <p className="mt-2 text-[11px] text-zinc-600">
                  Username only — the link is rebuilt automatically as{" "}
                  <span className="text-zinc-400">t.me/{accVal || "username"}</span>
                </p>
              </div>
            ) : (
              <div className="flex items-center">
                <button
                  data-testid="routes-to-row"
                  onClick={openAccountEdit}
                  className="group flex min-w-0 flex-1 items-center gap-3 p-4 text-left transition-colors duration-150 hover:bg-zinc-800/60 focus:outline-none focus:ring-1 focus:ring-cyan-500"
                >
                  <span className="shrink-0 text-[10px] uppercase tracking-[0.2em] text-zinc-500">
                    routes to
                  </span>
                  <span
                    data-testid="routes-to-value"
                    className="min-w-0 flex-1 truncate text-xs text-cyan-400"
                  >
                    {handle}
                  </span>
                  <span className="flex shrink-0 items-center gap-1.5 text-[10px] uppercase tracking-[0.18em] text-zinc-600 transition-colors duration-150 group-hover:text-emerald-400">
                    <Pencil size={11} strokeWidth={1.5} />
                    {token ? "edit" : "locked"}
                  </span>
                </button>
                <a
                  data-testid="official-link"
                  href={settings?.official_url || "#"}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="border-l border-zinc-800 p-4 text-zinc-600 transition-colors duration-150 hover:text-cyan-400 focus:outline-none focus:ring-1 focus:ring-cyan-500"
                >
                  <ArrowUpRight size={14} strokeWidth={1.5} />
                </a>
              </div>
            )}
          </div>
        </Row>

        {/* ── stats grid ────────────────────────────────────── */}
        <Row delay={0.26} reduced={reduced}>
          <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-3">
            <Cell
              icon={MousePointerClick}
              label="conversions"
              value={status?.total_starts}
              testid="stat-starts"
              onReset={resetStat}
              unlocked={!!token}
              metric="conversions"
            />
            <Cell
              icon={Users}
              label="unique users"
              value={status?.unique_users}
              sub={status?.blocked_users ? `${status.blocked_users} blocked` : null}
              testid="stat-users"
              onReset={resetStat}
              unlocked={!!token}
              metric="unique_users"
            />
            <Cell
              icon={Activity}
              label="today"
              value={status?.starts_today}
              testid="stat-today"
              onReset={resetStat}
              unlocked={!!token}
              metric="daily"
            />
          </div>
        </Row>

        {/* ── postback health ───────────────────────────────── */}
        <Row delay={0.32} reduced={reduced}>
          <div
            data-testid="postback-row"
            className={`mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 border bg-zinc-900/40 p-4 backdrop-blur-md ${
              status?.postback_enabled
                ? healthy
                  ? "border-emerald-500/25"
                  : "border-rose-500/30"
                : "border-zinc-800"
            }`}
          >
            <Radio
              size={13}
              strokeWidth={1.5}
              className={status?.postback_enabled && healthy ? "text-emerald-400" : "text-amber-400"}
            />
            <span className="text-[10px] uppercase tracking-[0.2em] text-zinc-500">
              trafficstars postback
            </span>
            {status?.postback_enabled ? (
              <span className="ml-auto flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
                <span className="text-emerald-400 text-glow-emerald">
                  {status.postbacks_sent} sent
                </span>
                {failed > 0 && <span className="text-rose-500 text-glow-rose">{failed} failed</span>}
                <span className="text-zinc-600">/</span>
                <span className="text-zinc-300">
                  {status.starts_attributed ?? 0}
                  <span className="text-zinc-600">/{status.total_starts ?? 0}</span> attributed
                </span>
              </span>
            ) : (
              <span className="ml-auto text-xs text-amber-400">not configured</span>
            )}
          </div>
        </Row>

        {/* ── welcome payload editor ────────────────────────── */}
        <Row delay={0.36} reduced={reduced}>
          <ConfigPanel
            settings={settings}
            unlocked={!!token}
            onSave={saveSettings}
            onChangePin={changePin}
            onBroadcast={broadcast}
            onBroadcastStatus={broadcastStatus}
            onBroadcastHistory={broadcastHistory}
            welcomeImageUrl={settings?.has_welcome_image ? `${API}/welcome-image?v=${encodeURIComponent(settings.updated_at || "")}` : null}
            onUploadImage={uploadWelcomeImage}
            onRemoveImage={removeWelcomeImage}
            onRequireUnlock={() => setUnlockOpen(true)}
          />
        </Row>

        {/* ── offer url ─────────────────────────────────────── */}
        <Row delay={0.4} reduced={reduced}>
          <div
            data-testid="ad-url-block"
            className="relative mt-4 border border-emerald-500/25 bg-zinc-900/40 p-4 backdrop-blur-md sm:p-5"
          >
            <Corner className="left-0 top-0 border-l border-t" />
            <Corner className="right-0 top-0 border-r border-t" />
            <Corner className="bottom-0 left-0 border-b border-l" />
            <Corner className="bottom-0 right-0 border-b border-r" />

            <div className="flex flex-wrap items-center justify-between gap-3">
              <span className="text-[10px] uppercase tracking-[0.2em] text-zinc-500">
                {"// paste as trafficstars offer url"}
              </span>
              <button
                data-testid="copy-ad-url"
                onClick={copy}
                className={`flex items-center gap-2 border px-3 py-1.5 text-[10px] uppercase tracking-[0.18em] transition-colors duration-150 focus:outline-none focus:ring-1 focus:ring-cyan-500 ${
                  copied
                    ? "border-emerald-400 bg-emerald-500/10 text-emerald-400"
                    : "border-zinc-700 text-zinc-300 hover:border-emerald-400 hover:text-emerald-400"
                }`}
              >
                {copied ? <Check size={12} strokeWidth={2.5} /> : <Copy size={12} strokeWidth={1.5} />}
                {copied ? "copied" : "copy"}
              </button>
            </div>

            <p
              data-testid="ad-url-value"
              className="mt-3 break-all border-l-2 border-emerald-500/40 pl-3 text-[11px] leading-relaxed text-emerald-400 sm:text-xs"
            >
              {AD_URL}
            </p>

            <p className="mt-3 text-[11px] leading-relaxed text-zinc-500">
              Bridge compresses TrafficStars&apos; 127-char click id into a 12-char token —{" "}
              <span className="text-zinc-300">?start=</span> accepts a maximum of 64.
            </p>
          </div>
        </Row>

        {/* ── footer ────────────────────────────────────────── */}
        <Row delay={0.44} reduced={reduced}>
          <div className="mt-6 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-zinc-800 pt-4 text-[10px] uppercase tracking-[0.18em] text-zinc-600">
            <span className="text-emerald-500/60">[ payload ]</span>
            <span className="normal-case tracking-normal">தமிழ் + English</span>
            <span className="text-zinc-800">|</span>
            <span>single inline button</span>
            <span className="text-zinc-800">|</span>
            <span>no menus</span>
            <span className="text-zinc-800">|</span>
            <span>zero commands</span>
          </div>
        </Row>
      </div>
    </div>
  );
};

export default function App() {
  return <Home />;
}
