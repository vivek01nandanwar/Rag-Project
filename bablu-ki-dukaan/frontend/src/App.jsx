import { useEffect, useState } from "react";
import Shutter from "./components/Shutter.jsx";
import Pillar from "./components/Pillar.jsx";
import Sticker from "./components/Sticker.jsx";
import { api, session } from "./api";
import Dashboard from "./pages/Dashboard.jsx";
import Stock from "./pages/Stock.jsx";
import Trade from "./pages/Trade.jsx";
import Dues from "./pages/Dues.jsx";
import Transactions from "./pages/Transactions.jsx";
import Assistant from "./pages/Assistant.jsx";
import Extras from "./pages/Extras.jsx";
import Returns from "./pages/Returns.jsx";
import Login from "./pages/Login.jsx";

const TABS = [
  { id: "home", label: "Home", emoji: "🏠" },
  { id: "sales", label: "Sales", emoji: "🧾" },
  { id: "purchases", label: "Purchases", emoji: "🚚" },
  { id: "returns", label: "Returns", emoji: "↩️" },
  { id: "stock", label: "Stock", emoji: "📦" },
  { id: "dues", label: "Udhaar & Refunds", emoji: "💸" },
  { id: "assistant", label: "Benji-AI👄", emoji: "👩🏽" },
  { id: "recent", label: "Recent 50", emoji: "🕘" },
  { id: "extras", label: "Extra registers", emoji: "🗂️" },
];

export default function App() {
  const [tab, setTab] = useState("home");
  const [user, setUser] = useState(null);
  const [checking, setChecking] = useState(!!session.token);

  useEffect(() => {
    session.onExpired(() => setUser(null));        // any 401 sends you back to the login screen
    if (session.token) {
      api.get("/auth/me/").then(setUser).catch(() => session.clear()).finally(() => setChecking(false));
    }
  }, []);

  function logout() {
    api.post("/auth/logout/").catch(() => {});
    session.clear();
    setUser(null);
    setTab("home");
  }

  return (
    <div className="shopfront">
      <Pillar />

      <div className="inside">
        <header>
          <Shutter>
            <div className="stickers">
              <Sticker tilt={-10} tone="sun" size={46}>🍎</Sticker>
              <Sticker tilt={8} tone="mint" size={40}>🥛</Sticker>
            </div>
            <div className="sign">
              <h1>Bablu ki dukaan</h1>
              <p>General Store &nbsp;•&nbsp; Roz ka saaman, apna bharosa</p>
            </div>
            <div className="stickers">
              <Sticker tilt={9} tone="rose" size={40}>🧺</Sticker>
              <Sticker tilt={-8} tone="sky" size={46}>🍚</Sticker>
            </div>
          </Shutter>

          {user && (
            <nav className="tabs" aria-label="Main">
              {TABS.map((t) => (
                <button key={t.id} className={`tab ${tab === t.id ? "on" : ""}`} onClick={() => setTab(t.id)}>
                  <span>{t.emoji}</span> {t.label}
                </button>
              ))}
              <button className="tab logout" onClick={logout} title="Log out">🔒 Logout ({user.username})</button>
            </nav>
          )}
        </header>

        <main className="page">
          {checking && <p className="loading">Checking your login…</p>}
          {!checking && !user && <Login onLogin={setUser} />}
          {!checking && user && (
            <>
              {tab === "home" && <Dashboard go={setTab} />}
              {tab === "sales" && <Trade kind="sale" />}
              {tab === "purchases" && <Trade kind="purchase" />}
              {tab === "returns" && <Returns />}
              {tab === "stock" && <Stock />}
              {tab === "dues" && <Dues />}
              {tab === "assistant" && <Assistant />}
              {tab === "recent" && <Transactions />}
              {tab === "extras" && <Extras />}
            </>
          )}
        </main>

        <footer className="foot">🏪 Bablu ki dukaan · General Store</footer>
      </div>

      <Pillar />
    </div>
  );
}
