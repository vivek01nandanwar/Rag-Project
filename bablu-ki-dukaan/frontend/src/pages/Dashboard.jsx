import { useEffect, useState } from "react";
import { api, rupees } from "../api";
import { Chip, Empty, Stat } from "../components/Common.jsx";

export default function Dashboard({ go }) {
  const [d, setD] = useState(null);
  const [year, setYear] = useState("");
  const [err, setErr] = useState("");

  useEffect(() => {
    api.get(`/dashboard/${year ? `?year=${year}` : ""}`).then(setD).catch((e) => setErr(e.message));
  }, [year]);

  if (err) return <Empty emoji="🔌" title="Cannot reach the server" hint={`Start Django on port 8000. (${err})`} />;
  if (!d) return <p className="loading">Opening the shutters…</p>;

  const p = d.profit;
  const loss = Number(p.total_profit) < 0;

  return (
    <>
      <section className="grid stats three">
        <Stat emoji="🧾" label="Today's sales" value={d.today_sales} tone="leaf" />
        <Stat emoji="🚚" label="Today's purchases" value={d.today_purchases} tone="sky" />
        <Stat emoji="📦" label="Stock worth (at cost)" value={d.stock_value} tone="sun" />
      </section>

      <section className="card profit">
        <div className="card-head">
          <h2>Total Profit 📈</h2>
          <label className="year-pick">Year
            <select value={p.year} onChange={(e) => setYear(e.target.value)}>
              {d.years.map((y) => <option key={y} value={y}>{y}</option>)}
            </select>
          </label>
        </div>

        <div className="profit-top">
          <div className={`profit-amount ${loss ? "loss" : ""}`}>{rupees(p.total_profit)}</div>
          <small>{loss ? "loss" : "profit"} in {p.year}</small>
        </div>

        <div className="profit-eq">
          <div className="pe pe-in"><span>Total sales</span><b>{rupees(p.total_sales)}</b></div>
          <i>−</i>
          <div className="pe pe-out"><span>Sales returns</span><b>{rupees(p.sales_returns)}</b></div>
          <i>−</i>
          <div className="pe pe-out"><span>Buying price of sold products</span><b>{rupees(p.buying_cost)}</b></div>
          <i>=</i>
          <div className={`pe pe-res ${loss ? "loss" : ""}`}><span>Total profit</span><b>{rupees(p.total_profit)}</b></div>
        </div>
      </section>

      <section className="card">
        <div className="card-head">
          <h2>Pending money 💸</h2>
          <button className="btn btn-ghost" onClick={() => go("dues")}>Open all</button>
        </div>
        <div className="grid four">
          <div className="due due-in"><span>Customers will pay us</span><b>{rupees(d.pending_from_customers)}</b></div>
          <div className="due due-out"><span>We must return to customers</span><b>{rupees(d.refund_due_to_customers)}</b></div>
          <div className="due due-out"><span>We must pay suppliers</span><b>{rupees(d.pending_to_suppliers)}</b></div>
          <div className="due due-in"><span>Suppliers must return to us</span><b>{rupees(d.refund_due_from_suppliers)}</b></div>
        </div>
      </section>

      <div className="grid two">
        <section className="card">
          <div className="card-head"><h2>Running low ⚠️</h2></div>
          {d.low_stock.length === 0 ? (
            <Empty emoji="🎉" title="All shelves are well stocked" />
          ) : (
            <ul className="plain">
              {d.low_stock.map((i) => (
                <li key={i.id}>
                  <span>{i.emoji} {i.name}</span>
                  <Chip tone="rose">{Number(i.current_stock)} {i.unit} left</Chip>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="card">
          <div className="card-head"><h2>Latest sales 🧾</h2></div>
          {d.recent_sales.length === 0 ? (
            <Empty title="No sales yet" hint="Your first sale will show up here." />
          ) : (
            <ul className="plain">
              {d.recent_sales.map((s) => (
                <li key={s.id}>
                  <span>{s.customer_name} <small>{s.id} · {s.date}</small></span>
                  <b>{rupees(s.total_amount)}</b>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </>
  );
}
