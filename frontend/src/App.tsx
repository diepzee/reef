import { BrowserRouter, Route, Routes } from "react-router-dom";

import { AppShell } from "./components/AppShell";
import { IndexProvider } from "./IndexProvider";
import Editor from "./views/Editor";
import ExportView from "./views/ExportView";
import Home from "./views/Home";
import IndexView from "./views/IndexView";
import InviteToReef from "./views/InviteToReef";
import NewPage from "./views/NewPage";
import NewSpace from "./views/NewSpace";
import PageView from "./views/PageView";
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
          <Route path="/spaces/new" element={<NewSpace />} />
          <Route path="/invite" element={<InviteToReef />} />
          <Route path="/s/:space" element={<SpaceView />} />
          <Route path="/s/:space/new" element={<NewPage />} />
          <Route path="/s/:space/p/*" element={<PageView />} />
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
