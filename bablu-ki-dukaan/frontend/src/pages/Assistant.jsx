import { useEffect, useRef, useState } from "react";
import { api } from "../api";

const ACCEPT = ".png,.jpg,.jpeg,.webp,.pdf,.docx,.xlsx,.xlsm,.csv,.tsv,.txt,.md,.json,.mp3,.wav,.m4a,.ogg,.webm,.flac,.mp4";
const KIND_ICON = { image: "🖼️", audio: "🎙️", pdf: "📄", docx: "📝", sheet: "📊", text: "📃" };
const QUICK = [
  "Aaj ki bikri kitni hui?",
  "Kaunsa saaman khatam hone wala hai?",
  "5 kg sugar bechi Sharma ji ko, 100 rupaye abhi diye",
  "Mujhe employees ka register banana hai",
];
const STATUS_TEXT = {
  confirmed: "✅ Saved",
  cancelled: "✋ Cancelled",
  expired: "⌛ Expired - ask me again",
  superseded: "↪ Replaced by a newer suggestion",
  failed: "⚠️ Could not save - nothing changed",
};

const kindOf = (f) => {
  const n = f.name.toLowerCase();
  if (/\.(png|jpe?g|webp)$/.test(n)) return "image";
  if (/\.(mp3|wav|m4a|ogg|webm|flac|mp4)$/.test(n)) return "audio";
  if (n.endsWith(".pdf")) return "pdf";
  if (n.endsWith(".docx")) return "docx";
  if (/\.(xlsx|xlsm|csv|tsv)$/.test(n)) return "sheet";
  return "text";
};

// tiny safe renderer: **bold**, "- bullets", line breaks (no HTML injection possible)
function Rich({ text }) {
  return text.split("\n").map((line, i) => {
    if (!line.trim()) return <div key={i} className="rich-gap" />;
    const bullet = /^\s*[-•*]\s+/.test(line);
    const parts = line.replace(/^\s*[-•*]\s+/, "").split(/\*\*(.+?)\*\*/g);
    return (
      <div key={i} className={bullet ? "rich-li" : "rich-line"}>
        {bullet && "• "}
        {parts.map((p, j) => (j % 2 ? <b key={j}>{p}</b> : p))}
      </div>
    );
  });
}

function Proposal({ p, busy, onAct }) {
  const pending = p.status === "pending";
  return (
    <div className={`proposal st-${p.status}`}>
      <div className="proposal-head">{pending ? "📋 Please check, then confirm" : STATUS_TEXT[p.status] || p.status}</div>
      <ul>
        {p.actions.map((a, i) => (
          <li key={i} className={a.danger ? "danger" : ""}><Rich text={a.summary} /></li>
        ))}
      </ul>
      {pending && (
        <div className="proposal-btns">
          <button className="btn btn-leaf" disabled={busy} onClick={() => onAct(p, "confirm")}>✅ Confirm</button>
          <button className="btn btn-ghost" disabled={busy} onClick={() => onAct(p, "cancel")}>✖ Cancel</button>
        </div>
      )}
      {p.status === "failed" && p.result?.[0] && <div className="proposal-why">{p.result[0]}</div>}
    </div>
  );
}

export default function Assistant() {
  const [status, setStatus] = useState(null);
  const [sessions, setSessions] = useState([]);
  const [active, setActive] = useState("");
  const [messages, setMessages] = useState([]);
  const [text, setText] = useState("");
  const [files, setFiles] = useState([]);
  const [busy, setBusy] = useState(false);
  const [acting, setActing] = useState("");
  const [err, setErr] = useState("");
  const [rec, setRec] = useState(null);
  const endRef = useRef(null);
  const fileRef = useRef(null);
  const justCreated = useRef("");

  const loadSessions = () => api.get("/assistant/sessions/").then(setSessions).catch(() => {});

  useEffect(() => {
    api.get("/assistant/status/").then(setStatus).catch(() => {});
    loadSessions();
  }, []);

  useEffect(() => {
    if (!active) { setMessages([]); return; }
    if (justCreated.current === active) { justCreated.current = ""; return; }   // we already hold these messages
    api.get(`/assistant/sessions/${active}/`).then((s) => setMessages(s.messages)).catch((e) => setErr(e.message));
  }, [active]);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [messages, busy]);

  function addFiles(list) {
    const next = [...files, ...Array.from(list)];
    const max = status?.limits?.files || 5;
    if (next.length > max) setErr(`You can attach at most ${max} files.`);
    setFiles(next.slice(0, max));
  }

  async function send(e) {
    e?.preventDefault();
    const msg = text.trim();
    if ((!msg && files.length === 0) || busy) return;
    setErr("");
    setBusy(true);
    const sentFiles = files;
    const tempId = "tmp" + Date.now();
    setMessages((m) => [...m, { id: tempId, role: "user", text: msg, attachments: sentFiles.map((f) => ({ name: f.name, kind: kindOf(f) })) }]);
    setText("");
    setFiles([]);

    const fd = new FormData();
    fd.append("message", msg);
    if (active) fd.append("session", active);
    sentFiles.forEach((f) => fd.append("files", f));

    try {
      const r = await api.upload("/assistant/chat/", fd);
      if (!active) { justCreated.current = r.session; setActive(r.session); }
      setMessages((m) => [...m.map((x) => (x.id === tempId ? { ...x, id: r.user_message_id, read: r.read } : x)), r.message]);
      loadSessions();
    } catch (ex) {
      setMessages((m) => m.filter((x) => x.id !== tempId));
      setText(msg);
      setFiles(sentFiles);
      setErr(ex.message);
    } finally {
      setBusy(false);
    }
  }

  async function act(p, kind) {
    setActing(p.id);
    setErr("");
    try {
      const r = await api.post(`/assistant/actions/${p.id}/${kind}/`);
      setMessages((ms) => [...ms.map((m) => (m.proposal?.id === p.id ? { ...m, proposal: r.proposal } : m)), r.message]);
    } catch (ex) {
      setErr(ex.message);
      if (active) api.get(`/assistant/sessions/${active}/`).then((s) => setMessages(s.messages)).catch(() => {});
    } finally {
      setActing("");
    }
  }

  async function toggleMic() {
    if (rec) { rec.stop(); return; }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mr = new MediaRecorder(stream);
      const chunks = [];
      mr.ondataavailable = (e) => chunks.push(e.data);
      mr.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        const type = mr.mimeType || "audio/webm";
        const ext = type.includes("mp4") ? "mp4" : type.includes("ogg") ? "ogg" : "webm";
        setFiles((f) => [...f, new File(chunks, `voice-note.${ext}`, { type })]);
        setRec(null);
      };
      mr.start();
      setRec(mr);
    } catch {
      setErr("Microphone is blocked. Allow it from the browser's address bar and try again.");
    }
  }

  async function removeSession(id) {
    if (!window.confirm("Delete this chat?")) return;
    await api.del(`/assistant/sessions/${id}/`).catch(() => {});
    if (id === active) setActive("");
    loadSessions();
  }

  return (
    <div className="assistant">
      <aside className="card a-rail">
        <button className="btn btn-sun" onClick={() => { setActive(""); setErr(""); }}>＋ New chat</button>
        <ul>
          {sessions.map((s) => (
            <li key={s.id} className={s.id === active ? "on" : ""}>
              <button className="a-sess" onClick={() => setActive(s.id)} title={s.title}>💬 {s.title}</button>
              <button className="x" aria-label="Delete chat" onClick={() => removeSession(s.id)}>✕</button>
            </li>
          ))}
          {sessions.length === 0 && <li className="a-none">No chats yet</li>}
        </ul>
      </aside>

      <section className="card a-main">
        <header className="a-head">
          <span className="a-avatar">🧑‍💼</span>
          <div>
            <h2>Munshi ji</h2>
            <small>Your AI shop assistant · talk, type, or send a bill photo 📸</small>
          </div>
        </header>

        {status && !status.configured && (
          <p className="note bad">The assistant is not set up yet. Add GROQ_API_KEY to backend/.env and restart Django.</p>
        )}

        <div className="a-scroll">
          {messages.length === 0 && (
            <div className="a-welcome">
              <div className="a-big">🛍️</div>
              <h3>Namaste! Main Baddie Benji 👩🏽 hoon.</h3>
              <p>I can add or fix products, record sales, purchases and udhaar, read bills from photos, PDFs, Excel and voice notes, and keep new registers. You always press ✅ before anything is saved.</p>
              <div className="a-quick">
                {QUICK.map((q) => <button key={q} className="tab" onClick={() => setText(q)}>{q}</button>)}
              </div>
            </div>
          )}

          {messages.map((m) => (
            <div key={m.id} className={`a-row ${m.role}`}>
              {m.role === "assistant" && <span className="a-mini">🧑‍💼</span>}
              <div className={`a-bubble ${m.role}`}>
                {m.attachments?.length > 0 && (
                  <div className="a-files">
                    {m.attachments.map((a, i) => <span key={i} className="a-file">{KIND_ICON[a.kind] || "📎"} {a.name}</span>)}
                  </div>
                )}
                {m.text && <Rich text={m.text} />}
                {m.read?.map((r, i) => (
                  <div key={i} className="a-read">{KIND_ICON[r.kind] || "📎"} <b>{r.kind === "audio" ? "Heard" : "Read"}:</b> {r.preview || "(nothing readable)"}</div>
                ))}
                {m.proposal && <Proposal p={m.proposal} busy={acting === m.proposal.id} onAct={act} />}
              </div>
            </div>
          ))}

          {busy && (
            <div className="a-row assistant">
              <span className="a-mini">🧑‍💼</span>
              <div className="a-bubble assistant a-typing"><i /><i /><i /></div>
            </div>
          )}
          <div ref={endRef} />
        </div>

        {err && <p className="note bad">{err}</p>}

        {files.length > 0 && (
          <div className="a-files tray">
            {files.map((f, i) => (
              <span key={i} className="a-file">
                {KIND_ICON[kindOf(f)]} {f.name}
                <button type="button" aria-label="Remove file" onClick={() => setFiles(files.filter((_, j) => j !== i))}>✕</button>
              </span>
            ))}
          </div>
        )}

        <form className="a-input" onSubmit={send}>
          <input ref={fileRef} type="file" multiple accept={ACCEPT} hidden onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }} />
          <button type="button" className="a-icon" title="Attach photo, PDF, Excel, audio…" onClick={() => fileRef.current?.click()}>📎</button>
          <button type="button" className={`a-icon ${rec ? "rec" : ""}`} title={rec ? "Stop recording" : "Record a voice note"} onClick={toggleMic}>{rec ? "⏹️" : "🎙️"}</button>
          <input
            className="a-text" value={text} maxLength={2000} onChange={(e) => setText(e.target.value)}
            placeholder="Type here… e.g. “5 kg ghee becha” or attach a bill"
          />
          <button className="btn btn-tomato" type="submit" disabled={busy}>Send ➤</button>
        </form>
      </section>
    </div>
  );
}
