import { useEffect, useState } from "react";
import { api, rupees } from "../api";
import { Chip, Empty } from "../components/Common.jsx";

const CFG = {
  sale: {
    label: "Sales return", icon: "🧾",
    intro: "A customer brings a product back to the shop.",
    billsPath: "/sales/", listPath: "/sale-returns/", lineKey: "sale_item", billKey: "sale",
    party: "customer_name", priceKey: "price", priceWord: "sold at", paidKey: "amount_received",
    listTitle: "Past sales returns",
  },
  purchase: {
    label: "Purchase return", icon: "🚚",
    intro: "You send a product back to the supplier.",
    billsPath: "/purchases/", listPath: "/purchase-returns/", lineKey: "purchase_item", billKey: "purchase",
    party: "supplier_name", priceKey: "cost_price", priceWord: "bought at", paidKey: "amount_paid",
    listTitle: "Past purchase returns",
  },
};
const REASONS = ["Damaged", "Expired", "Wrong item delivered", "Poor quality", "Short weight", "Extra / not needed"];

const left = (l) => Math.max(Number(l.quantity) - Number(l.returned_quantity), 0);
const clean = (n) => Number(Number(n).toFixed(3));

function ReturnPanel({ kind }) {
  const c = CFG[kind];
  const [bills, setBills] = useState([]);
  const [rows, setRows] = useState([]);
  const [billId, setBillId] = useState("");
  const [lineId, setLineId] = useState("");
  const [qty, setQty] = useState("");
  const [reason, setReason] = useState("");
  const [msg, setMsg] = useState({ text: "", bad: false });

  const load = () => Promise.all([api.get(c.billsPath), api.get(c.listPath)]).then(([b, r]) => { setBills(b); setRows(r); });
  useEffect(() => { load(); }, []);

  const open = bills.filter((b) => b.lines.some((l) => left(l) > 0)).slice(0, 150);
  const bill = open.find((b) => b.id === billId);
  const line = bill?.lines.find((l) => l.id === lineId);

  // what will happen to the money: same rule the server uses
  let outcome = null;
  if (bill && line && Number(qty) > 0) {
    const amt = Number(qty) * Number(line[c.priceKey]);
    const diff = Number(bill.total_amount) - Number(bill.returned_amount) - amt - Number(bill[c.paidKey]);
    const pending = Math.max(diff, 0);
    const refund = Math.max(-diff, 0);
    outcome = { amt, pending, refund };
  }

  async function submit(e) {
    e.preventDefault();
    setMsg({ text: "", bad: false });
    try {
      await api.post(c.listPath, {
        [c.lineKey]: lineId,
        quantity: qty,
        ...(kind === "purchase" ? { reason } : {}),
      });
      setBillId(""); setLineId(""); setQty(""); setReason("");
      setMsg({ text: kind === "sale" ? "Return saved. Stock is back on the shelf. ✅" : "Return saved. Stock was reduced. ✅", bad: false });
      load();
    } catch (err) {
      setMsg({ text: err.message, bad: true });
    }
  }

  return (
    <div className="grid side flip">
      <form className="card form" onSubmit={submit}>
        <div className="card-head"><h2>{c.icon} New {c.label.toLowerCase()} ↩️</h2></div>
        <p className="hint">{c.intro}</p>

        <label>Which bill?
          <select required value={billId} onChange={(e) => { setBillId(e.target.value); setLineId(""); setQty(""); }}>
            <option value="">Choose a bill…</option>
            {open.map((b) => (
              <option key={b.id} value={b.id}>
                {b.id} · {b[c.party]} · {rupees(b.total_amount)} · {b.date}
              </option>
            ))}
          </select>
        </label>

        {bill && (
          <div className="ret-lines" role="radiogroup" aria-label="Product to return">
            {bill.lines.map((l) => (
              <button type="button" key={l.id} disabled={left(l) <= 0} role="radio" aria-checked={lineId === l.id}
                className={`ret-line ${lineId === l.id ? "on" : ""}`} onClick={() => { setLineId(l.id); setQty(""); }}>
                <b>{l.item_name}</b>
                <small>
                  {clean(l.quantity)} {l.unit} {c.priceWord} {rupees(l[c.priceKey])} · already returned {clean(l.returned_quantity)} ·{" "}
                  {left(l) > 0 ? <>can return <b>{clean(left(l))}</b></> : "fully returned"}
                </small>
              </button>
            ))}
          </div>
        )}

        {line && (
          <label>Quantity to return ({line.unit})
            <input required type="number" step="0.001" min="0.001" max={left(line)} value={qty} onChange={(e) => setQty(e.target.value)} />
          </label>
        )}

        {line && kind === "purchase" && (
          <label>Reason for returning
            <input required minLength={3} maxLength={200} list="reason-list" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Damaged packets" />
            <datalist id="reason-list">{REASONS.map((r) => <option key={r} value={r} />)}</datalist>
          </label>
        )}

        {outcome && (
          <div className="bill ret-outcome">
            <div><span>{kind === "sale" ? "Value given back to customer" : "Value sent back to supplier"}</span><b>{rupees(outcome.amt)}</b></div>
            {kind === "sale" && outcome.pending > 0 && <div className="owe"><span>Customer will still have to pay</span><b>{rupees(outcome.pending)}</b></div>}
            {kind === "sale" && outcome.refund > 0 && <div className="back"><span>Shop must return to customer</span><b>{rupees(outcome.refund)}</b></div>}
            {kind === "purchase" && outcome.pending > 0 && <div className="owe"><span>Shop will still owe the supplier</span><b>{rupees(outcome.pending)}</b></div>}
            {kind === "purchase" && outcome.refund > 0 && <div className="back"><span>Supplier must return to shop</span><b>{rupees(outcome.refund)}</b></div>}
          </div>
        )}

        <button className="btn btn-tomato" type="submit" disabled={!line}>Save {c.label.toLowerCase()}</button>
        {msg.text && <p className={`note ${msg.bad ? "bad" : "good"}`}>{msg.text}</p>}
        {open.length === 0 && <p className="hint">No bill has anything left to return yet.</p>}
      </form>

      <section className="card">
        <div className="card-head"><h2>{c.listTitle}</h2></div>
        {rows.length === 0 ? <Empty emoji="↩️" title="No returns yet" hint="Returned products will be listed here." /> : (
          <ul className="bills">
            {rows.map((r) => (
              <li key={r.id}>
                <div className="bill-top"><b>{r.id} · {r.item_name}</b><span>{r.date}</span></div>
                <div className="bill-items">
                  {clean(r.quantity)} {r.unit} from {r[c.billKey]} · {r[c.party]}
                </div>
                {kind === "purchase" && <div className="ret-reason">Reason: {r.reason}</div>}
                <div className="bill-bottom">
                  <b>{rupees(r.amount)}</b>
                  <Chip tone={kind === "sale" ? "rose" : "leaf"}>{kind === "sale" ? "stock back in" : "stock sent out"}</Chip>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

export default function Returns() {
  const [kind, setKind] = useState("sale");
  return (
    <>
      <div className="filters ret-switch">
        {Object.entries(CFG).map(([k, v]) => (
          <button key={k} className={`tab ${kind === k ? "on" : ""}`} onClick={() => setKind(k)}>
            {v.icon} {v.label}
          </button>
        ))}
      </div>
      <ReturnPanel key={kind} kind={kind} />
    </>
  );
}
