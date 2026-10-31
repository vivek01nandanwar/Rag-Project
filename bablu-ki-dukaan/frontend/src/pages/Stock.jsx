import { useEffect, useRef, useState } from "react";
import { api, rupees } from "../api";
import { Chip, Empty } from "../components/Common.jsx";

const UNITS = ["kg", "g", "litre", "packet", "piece", "dozen"];
const blank = {
  name: "", aliases: "", emoji: "📦", unit: "kg", selling_price: "", cost_price: "",
  low_stock_threshold: 5, category: "", is_active: true,
};

export default function Stock() {
  const [items, setItems] = useState([]);
  const [cats, setCats] = useState([]);
  const [form, setForm] = useState(blank);
  const [editing, setEditing] = useState(null);      // the product being edited, or null for "new product"
  const [msg, setMsg] = useState({ text: "", bad: false });
  const [q, setQ] = useState("");
  const [catFilter, setCatFilter] = useState("all");  // "all" | "none" | category id
  const formRef = useRef(null);

  const load = () => Promise.all([api.get("/items/"), api.get("/categories/")]).then(([i, c]) => { setItems(i); setCats(c); });
  useEffect(() => { load(); }, []);

  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  // type a category: reuse it if it exists, create it if it is new, empty = no category
  async function categoryId(name) {
    const n = name.trim();
    if (!n) return null;
    const found = cats.find((c) => c.name.toLowerCase() === n.toLowerCase());
    if (found) return found.id;
    const created = await api.post("/categories/", { name: n });
    return created.id;
  }

  function startEdit(item) {
    setEditing(item);
    setForm({
      name: item.name, aliases: item.aliases || "", emoji: item.emoji || "📦", unit: item.unit,
      selling_price: item.selling_price, cost_price: item.cost_price, low_stock_threshold: item.low_stock_threshold,
      category: item.category_name || "", is_active: item.is_active,
    });
    setMsg({ text: "", bad: false });
    setTimeout(() => formRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
  }

  function reset() {
    setEditing(null);
    setForm(blank);
  }

  async function save(e) {
    e.preventDefault();
    setMsg({ text: "", bad: false });
    try {
      const payload = {
        name: form.name.trim(), aliases: form.aliases, emoji: form.emoji, unit: form.unit,
        selling_price: form.selling_price, cost_price: form.cost_price, low_stock_threshold: form.low_stock_threshold,
        category: await categoryId(form.category),
      };
      if (editing) {
        await api.patch(`/items/${editing.id}/`, { ...payload, is_active: form.is_active });
        setMsg({ text: `${payload.name} updated ✅`, bad: false });
      } else {
        await api.post("/items/", payload);
        setMsg({ text: "Product added. Add stock from the Purchases tab.", bad: false });
      }
      reset();
      load();
    } catch (err) {
      setMsg({ text: err.message, bad: true });
    }
  }

  async function recount(item) {
    const v = window.prompt(`Counted stock of ${item.name} (${item.unit})?`, item.current_stock);
    if (v === null) return;
    try { await api.post(`/items/${item.id}/adjust/`, { new_quantity: v, note: "Shelf count" }); load(); }
    catch (err) { setMsg({ text: err.message, bad: true }); }
  }

  const shown = items.filter((i) => {
    const text = (i.name + " " + i.aliases).toLowerCase().includes(q.toLowerCase());
    const cat = catFilter === "all" || (catFilter === "none" ? !i.category : i.category === catFilter);
    return text && cat;
  });

  return (
    <div className="grid side">
      <section className="card">
        <div className="card-head stock-head">
          <h2>Available stock 📦</h2>
          <div className="stock-tools">
            <select className="cat-filter" value={catFilter} onChange={(e) => setCatFilter(e.target.value)} aria-label="Filter by category">
              <option value="all">All categories</option>
              {cats.map((c) => <option key={c.id} value={c.id}>{c.emoji} {c.name}</option>)}
              <option value="none">No category</option>
            </select>
            <input className="search" placeholder="Search ghee, chai, sabun…" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
        </div>

        {shown.length === 0 ? <Empty title="No products found" hint="Change the filter, or add a product on the right." /> : (
          <div className="scroll">
            <table>
              <thead><tr><th>Product</th><th>In stock</th><th>Sell</th><th>Buy</th><th></th></tr></thead>
              <tbody>
                {shown.map((i) => (
                  <tr key={i.id} className={editing?.id === i.id ? "row-editing" : ""}>
                    <td>
                      <span className="pemoji">{i.emoji}</span> <b>{i.name}</b>{" "}
                      {!i.is_active && <Chip tone="sun">hidden</Chip>}
                      <br /><small>{i.id} · {i.category_name || "No category"}</small>
                    </td>
                    <td>
                      {Number(i.current_stock)} {i.unit}{" "}
                      {i.is_low_stock && <Chip tone="rose">low</Chip>}
                    </td>
                    <td>{rupees(i.selling_price)}</td>
                    <td>{rupees(i.cost_price)}</td>
                    <td className="row-actions">
                      <button className="btn btn-ghost" onClick={() => recount(i)}>Recount</button>
                      <button className="btn btn-ghost" onClick={() => startEdit(i)}>✏️ Edit</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <form className="card form" onSubmit={save} ref={formRef}>
        <div className="card-head"><h2>{editing ? `Edit product ✏️` : "New product 🏷️"}</h2></div>
        {editing && <p className="hint">Editing <b>{editing.name}</b> ({editing.id}). Stock changes through Purchases, Sales or Recount.</p>}

        <label>Name<input required value={form.name} onChange={set("name")} placeholder="e.g. Ghee" /></label>
        <label>Other names<input value={form.aliases} onChange={set("aliases")} placeholder="ghi, desi ghee" /></label>

        <label>Category
          <input list="category-list" value={form.category} onChange={set("category")} placeholder="Pick one or type a new one" />
          <datalist id="category-list">{cats.map((c) => <option key={c.id} value={c.name} />)}</datalist>
        </label>

        <div className="row">
          <label>Sticker<input value={form.emoji} onChange={set("emoji")} maxLength={4} /></label>
          <label>Unit
            <select value={form.unit} onChange={set("unit")} disabled={editing && Number(editing.current_stock) !== 0}
              title={editing && Number(editing.current_stock) !== 0 ? "Unit cannot change while the product has stock" : ""}>
              {UNITS.map((u) => <option key={u}>{u}</option>)}
            </select>
          </label>
        </div>
        <div className="row">
          <label>Selling price ₹<input required type="number" step="0.01" min="0" value={form.selling_price} onChange={set("selling_price")} /></label>
          <label>Buying price ₹<input required type="number" step="0.01" min="0" value={form.cost_price} onChange={set("cost_price")} /></label>
        </div>
        <label>Warn me when stock is below<input type="number" step="0.001" min="0" value={form.low_stock_threshold} onChange={set("low_stock_threshold")} /></label>

        {editing && (
          <label className="check">
            <input type="checkbox" checked={form.is_active} onChange={(e) => setForm({ ...form, is_active: e.target.checked })} />
            Show this product in sales / purchase lists
          </label>
        )}

        <div className="form-btns">
          <button className="btn btn-sun" type="submit">{editing ? "Update product" : "Save product"}</button>
          {editing && <button className="btn btn-ghost" type="button" onClick={() => { reset(); setMsg({ text: "", bad: false }); }}>Cancel</button>}
        </div>
        {msg.text && <p className={`note ${msg.bad ? "bad" : "good"}`}>{msg.text}</p>}
      </form>
    </div>
  );
}
