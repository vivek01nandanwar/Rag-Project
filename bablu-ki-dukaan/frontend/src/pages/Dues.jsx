import { useEffect, useState } from "react";
import { api, rupees } from "../api";
import { Empty, SettleBlock } from "../components/Common.jsx";

export default function Dues() {
  const [d, setD] = useState(null);
  const [msg, setMsg] = useState("");
  const load = () => api.get("/dues/").then(setD);
  useEffect(() => { load(); }, []);

  async function settle(body) {
    setMsg("");
    try { await api.post("/payments/", body); load(); }
    catch (e) { setMsg(e.message); }
  }

  if (!d) return <p className="loading">Opening the ledger…</p>;

  const Section = ({ emoji, title, hint, rows, name, field, label, make }) => (
    <section className="card">
      <div className="card-head"><h2>{emoji} {title}</h2></div>
      <p className="hint">{hint}</p>
      {rows.length === 0 ? <Empty emoji="✨" title="Nothing pending" /> : (
        <ul className="bills">
          {rows.map((r) => (
            <li key={r.id}>
              <div className="bill-top"><b>{r[name]}</b><span>{r.id} · {r.date}</span></div>
              <div className="bill-items">Bill {rupees(r.total_amount)}</div>
              <div className="bill-bottom">
                <b className="big">{rupees(r[field])}</b>
                <SettleBlock label={label} max={r[field]} onSettle={(amount) => settle(make(r, amount))} />
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );

  return (
    <>
      {msg && <p className="note bad">{msg}</p>}
      <div className="grid two">
        <Section emoji="🧑‍🤝‍🧑" title="Customers still have to pay us" hint="Udhaar: money the shop will receive."
          rows={d.customers_owe_shop} name="customer_name" field="pending_from_customer" label="Got it"
          make={(r, amount) => ({ sale: r.id, direction: "in", amount })} />
        <Section emoji="↩️" title="We must return to customers" hint="Extra money a customer paid, to be given back."
          rows={d.shop_owes_customers} name="customer_name" field="refund_due_to_customer" label="Returned"
          make={(r, amount) => ({ sale: r.id, direction: "out", amount })} />
        <Section emoji="🚚" title="We still have to pay suppliers" hint="Purchase bills not fully paid yet."
          rows={d.shop_owes_suppliers} name="supplier_name" field="pending_to_supplier" label="Paid"
          make={(r, amount) => ({ purchase: r.id, direction: "out", amount })} />
        <Section emoji="🔁" title="Suppliers must return to us" hint="Extra money we paid a supplier."
          rows={d.suppliers_owe_shop} name="supplier_name" field="refund_due_from_supplier" label="Got back"
          make={(r, amount) => ({ purchase: r.id, direction: "in", amount })} />
      </div>
    </>
  );
}
