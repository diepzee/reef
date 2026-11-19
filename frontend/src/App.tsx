import { BrowserRouter, Route, Routes } from "react-router-dom";

import { AppShell } from "./components/AppShell";
import { IndexProvider } from "./IndexProvider";
import Editor from "./views/Editor";
import ExportView from "./views/ExportView";
import Home from "./views/Home";
import IndexView from "./views/IndexView";
import Connect from "./views/Connect";
import InviteToReef from "./views/InviteToReef";
import NewEngram from "./views/NewEngram";
import NewSpace from "./views/NewSpace";
import EngramView from "./views/EngramView";
import Profile from "./views/Profile";
import { Gallery } from "./views/Gallery";
import SignedOut from "./views/SignedOut";
import SpaceView from "./views/SpaceView";

/** Everything that requires a session: shell chrome plus the inner routes. */
function AuthedApp() {
  return (
    <IndexProvider>
      <AppShell>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/index" element={<IndexView />} />
          <Route path="/export" element={<ExportView />} />
          <Route path="/profile" element={<Profile />} />
          <Route path="/spaces/new" element={<NewSpace />} />
          <Route path="/invite" element={<InviteToReef />} />
          <Route path="/connect" element={<Connect />} />
          <Route path="/s/:space" element={<SpaceView />} />
          <Route path="/s/:space/new" element={<NewEngram />} />
          <Route path="/s/:space/engram/*" element={<EngramView />} />
          <Route path="/s/:space/e/*" element={<Editor />} />
        </Routes>
      </AppShell>
    </IndexProvider>
  );
}

export default function App() {
  return (
    <BrowserRouter basename="/app">
      <Routes>
        <Route path="/signed-out" element={<SignedOut />} />
        {/* Dev-only tuning surface — Bun inlines NODE_ENV, so prod builds tree-shake this. */}
        {process.env.NODE_ENV !== "production" && <Route path="/gallery" element={<Gallery />} />}
        <Route path="*" element={<AuthedApp />} />
      </Routes>
    </BrowserRouter>
  );
}
