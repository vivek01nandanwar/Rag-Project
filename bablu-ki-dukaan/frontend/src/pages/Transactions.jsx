import { useEffect, useState } from "react";
import { api, rupees } from "../api";
import { Chip, Empty } from "../components/Common.jsx";

const FILTERS = [
  { id: "all", label: "All" },
  { id: "sale", label: "Sales" },
  { id: "purchase", label: "Purchases" },
  { id: "return", label: "Returns" },
  { id: "payment", label: "Pending settled / refunds" },
];
const ICON = { sale: "🧾", purchase: "🚚", payment: "💸", return: "↩️" };

const when = (iso) =>
  new Date(iso).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });

export default function Transactions() {
  const [rows, setRows] = useState(null);
  const [filter, setFilter] = useState("all");
  const [err, setErr] = useState("");

  const load = () => api.get("/transactions/").then(setRows).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);

  if (err) return <Empty emoji="🔌" title="Cannot load transactions" hint={err} />;
  if (!rows) return <p className="loading">Turning the pages of the ledger…</p>;

  const shown = rows.filter((r) => filter === "all" || r.type === filter);

  return (
    <section className="card">
      <div className="card-head">
        <h2>Recent 50 transactions 🕘</h2>
        <button className="btn btn-ghost" onClick={load}>Refresh</button>
      </div>

      <div className="filters">
        {FILTERS.map((f) => (
          <button key={f.id} className={`tab ${filter === f.id ? "on" : ""}`} onClick={() => setFilter(f.id)}>
            {f.label}
          </button>
        ))}
      </div>

      {shown.length === 0 ? (
        <Empty title="No transactions yet" hint="Sales, purchases, returns and settlements will appear here." />
      ) : (
        <ul className="tx-list">
          {shown.map((r) => (
            <li key={r.id} className={`tx tx-${r.type}`}>
              <span className="tx-icon">{ICON[r.type]}</span>

              <div className="tx-main">
                <div className="tx-title">
                  <b>{r.title}</b> <small>{r.id}</small>
                </div>
                <div className="tx-party">{r.party}</div>
                <div className="tx-summary">{r.summary}</div>
                <div className="tx-chips">
                  {(r.type === "sale" || r.type === "purchase") && <Chip tone="leaf">{r.type === "sale" ? "received" : "paid"} {rupees(r.settled)}</Chip>}
                  <Chip tone={Number(r.pending) > 0 ? "sun" : "leaf"}>
                    {r.type === "sale" || r.type === "purchase" ? "remaining" : "remaining after this"} {rupees(r.pending)}
                  </Chip>
                  {Number(r.refund) > 0 && <Chip tone="rose">to return {rupees(r.refund)}</Chip>}
                  {r.type === "payment" && <Chip tone={r.flow === "in" ? "leaf" : "rose"}>{r.flow === "in" ? "money in" : "money out"}</Chip>}
                  {r.type === "return" && (
                    <Chip tone={r.flow === "in" ? "leaf" : "rose"}>
                      {r.title === "Sales return" ? "value given back to customer" : "value back from supplier"}
                    </Chip>
                  )}
                </div>
              </div>

              <div className="tx-side">
                <b className={`tx-amount ${r.flow}`}>{rupees(r.amount)}</b>
                <small>{when(r.created_at)}</small>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
