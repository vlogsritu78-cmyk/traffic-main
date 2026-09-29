import { useEffect, useRef, useState } from "react";
import {
  ChevronDown,
  History,
  KeyRound,
  Loader2,
  Paperclip,
  RotateCcw,
  Save,
  SendHorizontal,
  ShieldCheck,
  X,
} from "lucide-react";
import { DEFAULT_TA, DEFAULT_EN, fmtError } from "@/lib/adminUtils";

const Field = ({ label, hint, children }) => (
  <div>
    <div className="flex items-baseline justify-between gap-3">
      <span className="text-[10px] uppercase tracking-[0.2em] text-zinc-500">{label}</span>
      {hint && <span className="text-[10px] text-zinc-600">{hint}</span>}
    </div>
    <div className="mt-2">{children}</div>
  </div>
);

const boxCls =
  "w-full border border-zinc-800 bg-zinc-950/80 p-3 font-mono text-xs leading-relaxed text-zinc-200 outline-none transition-colors duration-150 focus:border-cyan-500/60 focus:ring-1 focus:ring-cyan-500 disabled:cursor-not-allowed disabled:text-zinc-500";

export default function ConfigPanel({
  settings,
  unlocked,
  onSave,
  onRequireUnlock,
  onChangePin,
  onBroadcast,
  onBroadcastStatus,
  onBroadcastHistory,
  welcomeImageUrl,
  onUploadImage,
  onRemoveImage,
}) {
  const [ta, setTa] = useState(null);
  const [en, setEn] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [newPin, setNewPin] = useState("");
  const [pinMsg, setPinMsg] = useState("");

  const [bMsg, setBMsg] = useState("");
  const [bFile, setBFile] = useState(null);
  const [bBusy, setBBusy] = useState(false);
  const [bResult, setBResult] = useState("");
  const fileInputRef = useRef(null);
  const pollRef = useRef(null);
  const msgTimerRef = useRef(null);

  const [history, setHistory] = useState(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyBusy, setHistoryBusy] = useState(false);
  const [historyErr, setHistoryErr] = useState("");
  const [expandedId, setExpandedId] = useState(null);

  const [wImgFile, setWImgFile] = useState(null);
  const [wImgBusy, setWImgBusy] = useState(false);
  const [wImgMsg, setWImgMsg] = useState("");
  const wImgInputRef = useRef(null);

  useEffect(() => () => {
    clearInterval(pollRef.current);
    clearTimeout(msgTimerRef.current);
  }, []);

  const taVal = ta ?? settings?.welcome_ta ?? "";
  const enVal = en ?? settings?.welcome_en ?? "";
  const dirty = (ta !== null && ta !== settings?.welcome_ta) || (en !== null && en !== settings?.welcome_en);

  const save = async () => {
    setBusy(true);
    setMsg("");
    clearTimeout(msgTimerRef.current);
    try {
      await onSave({ welcome_ta: taVal, welcome_en: enVal });
      setTa(null);
      setEn(null);
      setMsg("saved — live on the bot");
    } catch (e) {
      setMsg(fmtError(e));
    }
    setBusy(false);
    msgTimerRef.current = setTimeout(() => setMsg(""), 4000);
  };

  const savePin = async () => {
    setBusy(true);
    setPinMsg("");
    try {
      await onChangePin(newPin);
      setNewPin("");
      setPinMsg("PIN updated");
    } catch (e) {
      setPinMsg(fmtError(e));
    }
    setBusy(false);
    setTimeout(() => setPinMsg(""), 4000);
  };

  const ERROR_LABELS = {
    blocked_bot: "blocked the bot",
    invalid_chat: "chat not found",
    rate_limited: "rate limited",
    network_timeout: "network timeout",
    other: "other",
  };

  const describeErrors = (errorCounts) => {
    const entries = Object.entries(errorCounts || {}).filter(([, n]) => n > 0);
    if (!entries.length) return "";
    return ` (${entries.map(([k, n]) => `${n} ${ERROR_LABELS[k] || k}`).join(", ")})`;
  };

  const clearFile = () => {
    setBFile(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const pollBroadcast = () => {
    clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const data = await onBroadcastStatus();
        if (!data) return;
        setBResult(
          data.status === "running"
            ? `sending... ${data.sent + data.failed}/${data.total} (${data.failed} failed)`
            : `done \u2014 sent ${data.sent}/${data.total}${
                data.failed ? ` \u2014 ${data.failed} failed${describeErrors(data.error_counts)}` : ""
              }`,
        );
        if (data.status !== "running") clearInterval(pollRef.current);
      } catch (e) {
        clearInterval(pollRef.current);
      }
    }, 3000);
  };

  const sendBroadcast = async () => {
    setBBusy(true);
    setBResult("");
    try {
      const data = await onBroadcast(bMsg.trim(), bFile);
      setBResult(`queued \u2014 sending to ${data.total} users now`);
      setBMsg("");
      clearFile();
      pollBroadcast();
      if (historyOpen) loadHistory();
    } catch (e) {
      setBResult(fmtError(e));
    }
    setBBusy(false);
  };

  const loadHistory = async () => {
    setHistoryBusy(true);
    setHistoryErr("");
    try {
      const data = await onBroadcastHistory();
      setHistory(data);
    } catch (e) {
      setHistory(null);
      setHistoryErr(fmtError(e));
    }
    setHistoryBusy(false);
  };

  const toggleHistory = () => {
    const next = !historyOpen;
    setHistoryOpen(next);
    if (next && history === null) loadHistory();
  };

  const fmtDate = (iso) => {
    if (!iso) return "\u2014";
    try {
      return new Date(iso).toLocaleString();
    } catch {
      return iso;
    }
  };

  const clearWImgFile = () => {
    setWImgFile(null);
    if (wImgInputRef.current) wImgInputRef.current.value = "";
  };

  const uploadImage = async () => {
    if (!wImgFile) return;
    setWImgBusy(true);
    setWImgMsg("");
    try {
      await onUploadImage(wImgFile);
      setWImgMsg("uploaded — live on the bot");
      clearWImgFile();
      setTimeout(() => setWImgMsg(""), 4000);
    } catch (e) {
      setWImgMsg(fmtError(e));
    }
    setWImgBusy(false);
  };

  const removeImage = async () => {
    setWImgBusy(true);
    setWImgMsg("");
    try {
      await onRemoveImage();
      setWImgMsg("image removed");
      setTimeout(() => setWImgMsg(""), 4000);
    } catch (e) {
      setWImgMsg(fmtError(e));
    }
    setWImgBusy(false);
  };

  return (
    <div
      data-testid="config-panel"
      className="mt-4 border border-zinc-800 bg-zinc-900/40 p-4 backdrop-blur-md sm:p-5"
    >
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-zinc-800 pb-3">
        <span className="text-[10px] uppercase tracking-[0.2em] text-zinc-500">
          {"// welcome payload"}
        </span>
        {unlocked ? (
          <span className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.18em] text-emerald-400">
            <ShieldCheck size={12} strokeWidth={1.5} /> editable
          </span>
        ) : (
          <button
            data-testid="config-unlock-hint"
            onClick={onRequireUnlock}
            className="flex items-center gap-1.5 border border-zinc-700 px-2.5 py-1 text-[10px] uppercase tracking-[0.18em] text-amber-400 transition-colors duration-150 hover:border-amber-400 focus:outline-none focus:ring-1 focus:ring-cyan-500"
          >
            <KeyRound size={12} strokeWidth={1.5} /> unlock to edit
          </button>
        )}
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Field label="தமிழ் block" hint={`${taVal.length}/900`}>
          <textarea
            data-testid="welcome-ta-input"
            value={taVal}
            disabled={!unlocked}
            maxLength={900}
            rows={5}
            onChange={(e) => setTa(e.target.value)}
            className={boxCls}
          />
        </Field>
        <Field label="english block" hint={`${enVal.length}/900`}>
          <textarea
            data-testid="welcome-en-input"
            value={enVal}
            disabled={!unlocked}
            maxLength={900}
            rows={5}
            onChange={(e) => setEn(e.target.value)}
            className={boxCls}
          />
        </Field>
      </div>

      <p className="mt-3 text-[11px] leading-relaxed text-zinc-600">
        The header, dividers, trust badges and the{" "}
        <span className="text-zinc-400">{settings?.button_text || "button"}</span> label stay fixed —
        you are editing only the two body blocks.
      </p>

      <div className="mt-4 border-t border-zinc-800 pt-4">
        <span className="text-[10px] uppercase tracking-[0.2em] text-zinc-500">
          {"// advertisement image"}
        </span>
        <p className="mt-1 text-[11px] leading-relaxed text-zinc-600">
          Sent as its own message right before the welcome text on every /start — like an ad
          creative, followed by the bilingual pitch and contact button.
        </p>

        {welcomeImageUrl ? (
          <img
            data-testid="welcome-image-preview"
            src={welcomeImageUrl}
            alt="welcome creative"
            className="mt-3 max-h-48 border border-zinc-800 object-contain"
          />
        ) : (
          <p data-testid="welcome-image-empty" className="mt-3 text-[11px] text-zinc-600">
            no image set — /start sends text only.
          </p>
        )}

        {unlocked && (
          <>
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <input
                ref={wImgInputRef}
                data-testid="welcome-image-file-input"
                type="file"
                accept="image/*"
                onChange={(e) => setWImgFile(e.target.files?.[0] || null)}
                className="hidden"
              />
              <button
                data-testid="welcome-image-attach-btn"
                onClick={() => wImgInputRef.current?.click()}
                className="flex items-center gap-2 border border-zinc-700 px-3 py-1.5 text-[10px] uppercase tracking-[0.18em] text-zinc-400 transition-colors duration-150 hover:border-cyan-500/60 hover:text-cyan-400 focus:outline-none focus:ring-1 focus:ring-cyan-500"
              >
                <Paperclip size={12} strokeWidth={1.5} />
                {wImgFile ? "change image" : "choose image"}
              </button>
              {wImgFile && (
                <span
                  data-testid="welcome-image-file-name"
                  className="flex items-center gap-2 truncate text-[11px] text-zinc-400"
                >
                  {wImgFile.name}
                  <button
                    data-testid="welcome-image-file-remove"
                    onClick={clearWImgFile}
                    className="text-zinc-600 transition-colors duration-150 hover:text-rose-400"
                  >
                    <X size={11} strokeWidth={1.5} />
                  </button>
                </span>
              )}
              <button
                data-testid="welcome-image-upload-btn"
                onClick={uploadImage}
                disabled={wImgBusy || !wImgFile}
                className="flex items-center gap-2 border border-cyan-500/50 bg-cyan-500/10 px-3 py-1.5 text-[10px] uppercase tracking-[0.18em] text-cyan-400 transition-colors duration-150 hover:bg-cyan-500/20 disabled:border-zinc-800 disabled:bg-transparent disabled:text-zinc-600 focus:outline-none focus:ring-1 focus:ring-cyan-500"
              >
                {wImgBusy ? <Loader2 size={12} strokeWidth={2} className="animate-spin" /> : <Save size={12} strokeWidth={1.5} />}
                upload
              </button>
              {welcomeImageUrl && (
                <button
                  data-testid="welcome-image-remove-btn"
                  onClick={removeImage}
                  disabled={wImgBusy}
                  className="ml-auto flex items-center gap-2 border border-zinc-700 px-3 py-1.5 text-[10px] uppercase tracking-[0.18em] text-zinc-400 transition-colors duration-150 hover:border-rose-400 hover:text-rose-400 disabled:text-zinc-600 focus:outline-none focus:ring-1 focus:ring-cyan-500"
                >
                  <X size={12} strokeWidth={1.5} /> remove image
                </button>
              )}
            </div>
            {wImgMsg && (
              <p
                data-testid="welcome-image-status"
                className={`mt-2 text-[11px] ${
                  wImgMsg.startsWith("uploaded") || wImgMsg.startsWith("image removed")
                    ? "text-emerald-400"
                    : "text-rose-400"
                }`}
              >
                {wImgMsg}
              </p>
            )}
          </>
        )}
      </div>

      {unlocked && (
        <>
          <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-zinc-800 pt-4">
            <button
              data-testid="config-save"
              onClick={save}
              disabled={busy || !dirty || !settings}
              className="flex items-center gap-2 border border-emerald-500/50 bg-emerald-500/10 px-3 py-1.5 text-[10px] uppercase tracking-[0.18em] text-emerald-400 transition-colors duration-150 hover:bg-emerald-500/20 disabled:border-zinc-800 disabled:bg-transparent disabled:text-zinc-600 focus:outline-none focus:ring-1 focus:ring-cyan-500"
            >
              {busy ? (
                <Loader2 size={12} strokeWidth={2} className="animate-spin" />
              ) : (
                <Save size={12} strokeWidth={1.5} />
              )}
              save payload
            </button>
            <button
              data-testid="config-reset"
              onClick={() => {
                setTa(DEFAULT_TA);
                setEn(DEFAULT_EN);
              }}
              className="flex items-center gap-2 border border-zinc-700 px-3 py-1.5 text-[10px] uppercase tracking-[0.18em] text-zinc-400 transition-colors duration-150 hover:border-cyan-500/60 hover:text-cyan-400 focus:outline-none focus:ring-1 focus:ring-cyan-500"
            >
              <RotateCcw size={12} strokeWidth={1.5} /> defaults
            </button>
            {msg && (
              <span
                data-testid="config-status"
                className={`text-[11px] ${
                  msg.startsWith("saved") || msg.startsWith("PIN") ? "text-emerald-400" : "text-rose-400"
                }`}
              >
                {msg}
              </span>
            )}
          </div>

          <div className="mt-4 flex flex-wrap items-end gap-3 border-t border-zinc-800 pt-4">
            <Field label="change panel pin" hint="min 4 chars">
              <input
                data-testid="change-pin-input"
                type="password"
                value={newPin}
                onChange={(e) => setNewPin(e.target.value)}
                placeholder="new pin"
                className={`${boxCls} w-44 py-2`}
              />
            </Field>
            <button
              data-testid="change-pin-save"
              onClick={savePin}
              disabled={busy || newPin.trim().length < 4}
              className="flex items-center gap-2 border border-zinc-700 px-3 py-2 text-[10px] uppercase tracking-[0.18em] text-zinc-300 transition-colors duration-150 hover:border-emerald-400 hover:text-emerald-400 disabled:border-zinc-800 disabled:text-zinc-600 focus:outline-none focus:ring-1 focus:ring-cyan-500"
            >
              <KeyRound size={12} strokeWidth={1.5} /> update pin
            </button>
            {pinMsg && (
              <span
                data-testid="change-pin-status"
                className={`text-[11px] ${
                  pinMsg.startsWith("PIN") ? "text-emerald-400" : "text-rose-400"
                }`}
              >
                {pinMsg}
              </span>
            )}
          </div>

          <div className="mt-4 border-t border-zinc-800 pt-4">
            <span className="text-[10px] uppercase tracking-[0.2em] text-zinc-500">
              {"// broadcast to all users"}
            </span>
            <textarea
              data-testid="broadcast-message-input"
              value={bMsg}
              onChange={(e) => setBMsg(e.target.value)}
              placeholder="Type a message to send to everyone who has started the bot..."
              rows={3}
              maxLength={4000}
              className={`${boxCls} mt-2`}
            />
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <input
                ref={fileInputRef}
                data-testid="broadcast-file-input"
                type="file"
                onChange={(e) => setBFile(e.target.files?.[0] || null)}
                className="hidden"
              />
              <button
                data-testid="broadcast-attach-btn"
                onClick={() => fileInputRef.current?.click()}
                className="flex items-center gap-2 border border-zinc-700 px-3 py-1.5 text-[10px] uppercase tracking-[0.18em] text-zinc-400 transition-colors duration-150 hover:border-cyan-500/60 hover:text-cyan-400 focus:outline-none focus:ring-1 focus:ring-cyan-500"
              >
                <Paperclip size={12} strokeWidth={1.5} />
                {bFile ? "change file" : "attach photo / video / file"}
              </button>
              {bFile && (
                <span
                  data-testid="broadcast-file-name"
                  className="flex items-center gap-2 truncate text-[11px] text-zinc-400"
                >
                  {bFile.name}
                  <button
                    data-testid="broadcast-file-remove"
                    onClick={clearFile}
                    className="text-zinc-600 transition-colors duration-150 hover:text-rose-400"
                  >
                    <X size={11} strokeWidth={1.5} />
                  </button>
                </span>
              )}
              <button
                data-testid="broadcast-send"
                onClick={sendBroadcast}
                disabled={bBusy || (!bMsg.trim() && !bFile)}
                className="ml-auto flex items-center gap-2 border border-cyan-500/50 bg-cyan-500/10 px-3 py-1.5 text-[10px] uppercase tracking-[0.18em] text-cyan-400 transition-colors duration-150 hover:bg-cyan-500/20 disabled:border-zinc-800 disabled:bg-transparent disabled:text-zinc-600 focus:outline-none focus:ring-1 focus:ring-cyan-500"
              >
                {bBusy ? (
                  <Loader2 size={12} strokeWidth={2} className="animate-spin" />
                ) : (
                  <SendHorizontal size={12} strokeWidth={1.5} />
                )}
                send to all users
              </button>
            </div>
            {bResult && (
              <p
                data-testid="broadcast-status"
                className={`mt-2 text-[11px] ${
                  bResult.startsWith("queued") || bResult.startsWith("sending") || bResult.startsWith("done")
                    ? "text-emerald-400"
                    : "text-rose-400"
                }`}
              >
                {bResult}
              </p>
            )}

            <div className="mt-4 border-t border-zinc-900 pt-3">
              <button
                data-testid="broadcast-history-toggle"
                onClick={toggleHistory}
                className="flex items-center gap-2 text-[10px] uppercase tracking-[0.18em] text-zinc-500 transition-colors duration-150 hover:text-cyan-400 focus:outline-none"
              >
                <History size={12} strokeWidth={1.5} />
                broadcast history
                <ChevronDown
                  size={12}
                  strokeWidth={1.5}
                  className={`transition-transform duration-150 ${historyOpen ? "rotate-180" : ""}`}
                />
              </button>

              {historyOpen && (
                <div data-testid="broadcast-history-list" className="mt-3 space-y-2">
                  {historyBusy && (
                    <p className="flex items-center gap-2 text-[11px] text-zinc-500">
                      <Loader2 size={12} strokeWidth={2} className="animate-spin" /> loading...
                    </p>
                  )}
                  {!historyBusy && historyErr && (
                    <p data-testid="broadcast-history-error" className="text-[11px] text-rose-400">
                      {historyErr}
                    </p>
                  )}
                  {!historyBusy && !historyErr && history?.length === 0 && (
                    <p className="text-[11px] text-zinc-600">no broadcasts sent yet.</p>
                  )}
                  {!historyBusy &&
                    history?.map((job) => (
                      <div
                        key={job.id}
                        data-testid={`broadcast-history-row-${job.id}`}
                        className="border border-zinc-800 bg-zinc-950/60 p-2.5 text-[11px]"
                      >
                        <button
                          onClick={() => setExpandedId(expandedId === job.id ? null : job.id)}
                          className="flex w-full flex-col gap-1.5 text-left sm:flex-row sm:items-center sm:justify-between sm:gap-3"
                        >
                          <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                            <span className="truncate text-zinc-300">
                              {job.text_preview || (job.media_filename ? `[file] ${job.media_filename}` : "(empty)")}
                            </span>
                            <span className="text-zinc-600">{fmtDate(job.created_at)}</span>
                          </span>
                          <span
                            className={`shrink-0 uppercase tracking-[0.15em] ${
                              job.status === "running" ? "text-amber-400" : "text-emerald-400"
                            }`}
                          >
                            {`${job.status === "running" ? "sending" : "done"} \u00b7 ${job.sent}/${job.total}${
                              job.failed ? ` \u00b7 ${job.failed} failed` : ""
                            }`}
                          </span>
                        </button>
                        {expandedId === job.id && (
                          <div className="mt-2 border-t border-zinc-900 pt-2 text-zinc-500">
                            {job.failed ? (
                              <span>failure breakdown{describeErrors(job.error_counts)}</span>
                            ) : (
                              <span>no failures</span>
                            )}
                            {job.completed_at && <span className="ml-3">completed {fmtDate(job.completed_at)}</span>}
                          </div>
                        )}
                      </div>
                    ))}
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
