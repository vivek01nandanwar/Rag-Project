import { useEffect, useState } from "react";
import { api } from "../api";
import { Empty } from "../components/Common.jsx";

export default function Extras() {
  const [tables, setTables] = useState(null);
  const [sel, setSel] = useState("");
  const [err, setErr] = useState("");

  useEffect(() => {
    api.get("/assistant/registers/")
      .then((t) => { setTables(t); if (t.length) setSel(t[0].id); })
      .catch((e) => setErr(e.message));
  }, []);

  if (err) return <Empty emoji="🔌" title="Cannot load registers" hint={err} />;
  if (!tables) return <p className="loading">Opening the registers…</p>;
  if (tables.length === 0) {
    return (
      <section className="card">
        <Empty emoji="🗂️" title="No extra registers yet"
          hint="Ask Munshi ji, for example: “Mujhe employees ka register banana hai” or “I want to track daily expenses”." />
      </section>
    );
  }

  const t = tables.find((x) => x.id === sel) || tables[0];
  return (
    <section className="card">
      <div className="card-head"><h2>Extra registers 🗂️</h2></div>
      <div className="filters">
        {tables.map((x) => (
          <button key={x.id} className={`tab ${x.id === t.id ? "on" : ""}`} onClick={() => setSel(x.id)}>
            {x.name} <small>({x.records.length})</small>
          </button>
        ))}
      </div>
      {t.description && <p className="hint">{t.description}</p>}
      {t.records.length === 0 ? (
        <Empty title="No rows yet" hint={`Tell Munshi ji to add a row to ${t.name}.`} />
      ) : (
        <div className="scroll">
          <table>
            <thead><tr><th>ID</th>{t.fields.map((f) => <th key={f.name}>{f.name}</th>)}</tr></thead>
            <tbody>
              {t.records.map((r) => (
                <tr key={r.id}>
                  <td><small>{r.id}</small></td>
                  {t.fields.map((f) => <td key={f.name}>{r[f.name] ?? ""}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
