import { useEffect, useMemo, useState } from "react";
import { api, rupees } from "../api";
import { Chip, Empty } from "../components/Common.jsx";

// One screen that works for both Sales and Purchases.
const CFG = {
  sale: {
    title: "New sale 🧾", listTitle: "Past sales", path: "/sales/", partyPath: "/customers/",
    partyKey: "customer", partyLabel: "Customer", partyNone: "Walk-in customer",
    priceKey: "price", itemPrice: "selling_price", priceLabel: "Sold at ₹",
    paidKey: "amount_received", paidLabel: "Money received ₹",
    pendKey: "pending_from_customer", pendText: "Customer will pay later",
    refKey: "refund_due_to_customer", refText: "Shop must return to customer",
    nameKey: "customer_name", btn: "Save sale",
  },
  purchase: {
    title: "New purchase 🚚", listTitle: "Past purchases", path: "/purchases/", partyPath: "/suppliers/",
    partyKey: "supplier", partyLabel: "Supplier", partyNone: "No supplier",
    priceKey: "cost_price", itemPrice: "cost_price", priceLabel: "Bought at ₹",
    paidKey: "amount_paid", paidLabel: "Money paid ₹",
    pendKey: "pending_to_supplier", pendText: "Shop must pay supplier later",
    refKey: "refund_due_from_supplier", refText: "Supplier must return to shop",
    nameKey: "supplier_name", btn: "Save purchase",
  },
};

const emptyLine = { item: "", quantity: "", price: "" };

export default function Trade({ kind }) {
  const c = CFG[kind];
  const [items, setItems] = useState([]);
  const [parties, setParties] = useState([]);
  const [rows, setRows] = useState([]);
  const [party, setParty] = useState("");
  const [lines, setLines] = useState([{ ...emptyLine }]);
  const [paid, setPaid] = useState("");
  const [msg, setMsg] = useState({ text: "", bad: false });
  const [adding, setAdding] = useState(false);               // the "new customer / supplier" panel
  const [np, setNp] = useState({ name: "", phone: "", address: "" });
  const [addMsg, setAddMsg] = useState("");

  const load = () => {
    api.get("/items/").then(setItems);
    api.get(c.partyPath).then(setParties);
    api.get(c.path).then(setRows);
  };
  useEffect(() => { setParty(""); setLines([{ ...emptyLine }]); setPaid(""); setMsg({ text: "", bad: false }); setAdding(false); setAddMsg(""); load(); }, [kind]);

  const byId = useMemo(() => Object.fromEntries(items.map((i) => [String(i.id), i])), [items]);

  const setLine = (idx, patch) =>
    setLines(lines.map((l, i) => {
      if (i !== idx) return l;
      const next = { ...l, ...patch };
      if (patch.item !== undefined && byId[patch.item]) next.price = byId[patch.item][c.itemPrice]; // auto-fill price
      return next;
    }));

  const total = lines.reduce((s, l) => s + (Number(l.quantity) || 0) * (Number(l.price) || 0), 0);
  const diff = total - (Number(paid) || 0);
  const pending = Math.max(diff, 0);
  const refund = Math.max(-diff, 0);

  async function addParty() {
    setAddMsg("");
    const name = np.name.trim();
    const phone = np.phone.trim();
    if (!name) return setAddMsg("Please enter the name.");
    if (phone && !/^[0-9+\-\s]{6,20}$/.test(phone)) return setAddMsg("Phone number looks wrong (digits, +, - and spaces only).");
    if (parties.some((p) => p.name.toLowerCase() === name.toLowerCase() && (p.phone || "") === phone))
      return setAddMsg(`${name} is already in the list. Choose them from the dropdown.`);
    try {
      const created = await api.post(c.partyPath, { name, phone, address: np.address.trim() });
      setParties((list) => [...list, created].sort((a, b) => a.name.localeCompare(b.name)));
      setParty(created.id);                       // select the new person straight away
      setNp({ name: "", phone: "", address: "" });
      setAdding(false);
      setMsg({ text: `${c.partyLabel} “${created.name}” added and selected ✅`, bad: false });
    } catch (err) { setAddMsg(err.message); }
  }

  async function save(e) {
    e.preventDefault();
    setMsg({ text: "", bad: false });
    try {
      await api.post(c.path, {
        [c.partyKey]: party || null,
        [c.paidKey]: paid || 0,
        ...(kind === "sale" ? { payment_mode: pending > 0 && !paid ? "udhaar" : "cash" } : {}),
        lines: lines.filter((l) => l.item).map((l) => ({ item: l.item, quantity: l.quantity, [c.priceKey]: l.price })),
      });
      setLines([{ ...emptyLine }]); setPaid(""); setParty("");
      setMsg({ text: "Saved. Stock has been updated. ✅", bad: false });
      load();
    } catch (err) { setMsg({ text: err.message, bad: true }); }
  }

  return (
    <div className="grid side flip">
      <form className="card form" onSubmit={save}>
        <div className="card-head"><h2>{c.title}</h2></div>

        <div className="party-row">
          <label>{c.partyLabel}
            <select value={party} onChange={(e) => setParty(e.target.value)}>
              <option value="">{c.partyNone}</option>
              {parties.map((p) => <option key={p.id} value={p.id}>{p.name}{p.phone ? ` · ${p.phone}` : ""}</option>)}
            </select>
          </label>
          <button type="button" className="btn btn-ghost" onClick={() => { setAdding((a) => !a); setAddMsg(""); }}>
            {adding ? "✕ Close" : `＋ New ${c.partyLabel.toLowerCase()}`}
          </button>
        </div>

        {adding && (
          <div className="new-party" onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addParty(); } }}>
            <b>New {c.partyLabel.toLowerCase()} details</b>
            <label>Name *
              <input value={np.name} onChange={(e) => setNp({ ...np, name: e.target.value })} placeholder="e.g. Ramesh bhai" maxLength={120} autoFocus />
            </label>
            <div className="row">
              <label>Phone
                <input value={np.phone} inputMode="tel" onChange={(e) => setNp({ ...np, phone: e.target.value })} placeholder="98XXXXXXXX" maxLength={20} />
              </label>
              <label>Address
                <input value={np.address} onChange={(e) => setNp({ ...np, address: e.target.value })} placeholder="Street / area" maxLength={200} />
              </label>
            </div>
            {addMsg && <p className="note bad">{addMsg}</p>}
            <div className="form-btns">
              <button type="button" className="btn btn-sun" onClick={addParty}>Save {c.partyLabel.toLowerCase()}</button>
              <button type="button" className="btn btn-ghost" onClick={() => { setAdding(false); setAddMsg(""); }}>Cancel</button>
            </div>
          </div>
        )}

        {lines.map((l, idx) => (
          <div className="line" key={idx}>
            <select required value={l.item} onChange={(e) => setLine(idx, { item: e.target.value })}>
              <option value="">Choose product…</option>
              {items.map((i) => <option key={i.id} value={i.id}>{i.emoji} {i.name} ({Number(i.current_stock)} {i.unit})</option>)}
            </select>
            <input required type="number" step="0.001" min="0.001" placeholder={byId[l.item] ? `Qty (${byId[l.item].unit})` : "Qty"} value={l.quantity} onChange={(e) => setLine(idx, { quantity: e.target.value })} />
            <input required type="number" step="0.01" min="0" placeholder={c.priceLabel} title={c.priceLabel} value={l.price} onChange={(e) => setLine(idx, { price: e.target.value })} />
            {lines.length > 1 && <button type="button" className="x" aria-label="Remove line" onClick={() => setLines(lines.filter((_, i) => i !== idx))}>✕</button>}
          </div>
        ))}
        <button type="button" className="btn btn-ghost" onClick={() => setLines([...lines, { ...emptyLine }])}>+ Add another product</button>

        <div className="bill">
          <div><span>Bill total</span><b>{rupees(total)}</b></div>
          <label className="paid">{c.paidLabel}<input type="number" step="0.01" min="0" value={paid} onChange={(e) => setPaid(e.target.value)} placeholder="0" /></label>
          {pending > 0 && <div className="owe"><span>{c.pendText}</span><b>{rupees(pending)}</b></div>}
          {refund > 0 && <div className="back"><span>{c.refText}</span><b>{rupees(refund)}</b></div>}
        </div>

        <button className="btn btn-tomato" type="submit">{c.btn}</button>
        {msg.text && <p className={`note ${msg.bad ? "bad" : "good"}`}>{msg.text}</p>}
      </form>

      <section className="card">
        <div className="card-head"><h2>{c.listTitle}</h2></div>
        {rows.length === 0 ? <Empty title="Nothing here yet" hint="Saved bills will be listed here." /> : (
          <ul className="bills">
            {rows.map((r) => (
              <li key={r.id}>
                <div className="bill-top">
                  <b>{r.id} · {r[c.nameKey]}</b>
                  <span>{r.date}</span>
                </div>
                <div className="bill-items">{r.lines.map((l) => `${l.item_name} ×${Number(l.quantity)}`).join(", ")}</div>
                <div className="bill-bottom">
                  <b>{rupees(r.total_amount)}</b>
                  {Number(r.returned_amount) > 0 && <Chip tone="sky">returned {rupees(r.returned_amount)}</Chip>}
                  <Chip tone="leaf">paid {rupees(r[c.paidKey])}</Chip>
                  {Number(r[c.pendKey]) > 0 && <Chip tone="sun">pending {rupees(r[c.pendKey])}</Chip>}
                  {Number(r[c.refKey]) > 0 && <Chip tone="rose">to return {rupees(r[c.refKey])}</Chip>}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
