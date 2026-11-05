import React, { useState } from "react";
import { Meta } from "./api";
import { ControlTower, Demand, Inventory, Suppliers } from "./screens1";
import { Assistant, Planner, Simulator } from "./screens2";
import { useApi } from "./ui";

const SCREENS = [["tower", "Control Tower"], ["demand", "Demand Intelligence"], ["inventory", "Inventory"], ["suppliers", "Supplier Intelligence"], ["planner", "Replenishment Planner"], ["simulator", "Scenario Simulator"], ["assistant", "AI Assistant"]] as const;

export function App() {
  const [screen, setScreen] = useState<string>("tower"); const [ctx, setCtx] = useState<any>({});
  const meta = useApi<Meta>("/meta");
  const go = (s: string, c?: any) => { setScreen(s); if (c) setCtx(c); window.scrollTo(0, 0); };
  return (
    <div className="app">
      <nav className="side" aria-label="Main"><h1>ORBIT</h1><small>Retail supply-chain intelligence</small>
        {SCREENS.map(([id, label]) => <button key={id} aria-current={screen === id ? "page" : undefined} onClick={() => go(id)}>{label}</button>)}</nav>
      <main>
        {meta.data && meta.data.data_kind !== "REAL_M5" && <div className="banner" role="note"><b>Simulated data.</b> {meta.data.disclaimer} As of {meta.data.as_of}.</div>}
        {meta.error && <div className="state err" role="alert">{meta.error}</div>}
        {screen === "tower" && <ControlTower go={go} />}{screen === "demand" && <Demand ctx={ctx} />}{screen === "inventory" && <Inventory go={go} />}
        {screen === "suppliers" && <Suppliers />}{screen === "planner" && <Planner ctx={ctx} />}{screen === "simulator" && <Simulator />}{screen === "assistant" && <Assistant ctx={ctx} />}
      </main>
    </div>
  );
}
