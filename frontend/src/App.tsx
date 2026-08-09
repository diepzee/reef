import { BrowserRouter, Route, Routes } from "react-router-dom";

import { AppShell } from "./components/AppShell";
import { IndexProvider } from "./IndexProvider";
import Editor from "./views/Editor";
import Home from "./views/Home";
import NewPage from "./views/NewPage";
import NewSpace from "./views/NewSpace";
import PageView from "./views/PageView";
import SignedOut from "./views/SignedOut";
import SpaceView from "./views/SpaceView";

/** Everything that requires a session: shell chrome plus the inner routes. */
function AuthedApp() {
  return (
    <IndexProvider>
      <AppShell>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/spaces/new" element={<NewSpace />} />
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
        <Route path="*" element={<AuthedApp />} />
      </Routes>
    </BrowserRouter>
  );
}
