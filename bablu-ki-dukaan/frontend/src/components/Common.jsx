import { rupees } from "../api";

export function Empty({ emoji = "🧺", title, hint }) {
  return (
    <div className="empty">
      <div className="empty-emoji">{emoji}</div>
      <strong>{title}</strong>
      {hint && <p>{hint}</p>}
    </div>
  );
}

export function Stat({ emoji, label, value, tone = "sky", money = true }) {
  return (
    <div className={`stat stat-${tone}`}>
      <span className="stat-emoji">{emoji}</span>
      <div>
        <div className="stat-label">{label}</div>
        <div className="stat-value">{money ? rupees(value) : value}</div>
      </div>
    </div>
  );
}

export function Chip({ tone, children }) {
  return <span className={`chip chip-${tone}`}>{children}</span>;
}

export function SettleBlock({ label, max, onSettle }) {
  // small inline form: amount (default = full remaining) + button
  return (
    <form
      className="settle"
      onSubmit={(e) => {
        e.preventDefault();
        onSettle(Number(new FormData(e.target).get("amount")));
      }}
    >
      <input name="amount" type="number" step="0.01" min="0.01" max={max} defaultValue={max} />
      <button className="btn btn-leaf" type="submit">{label}</button>
    </form>
  );
}
