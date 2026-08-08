import { BrowserRouter, Link, Route, Routes } from "react-router-dom";

import Home from "./views/Home";
import NewSpace from "./views/NewSpace";
import SpaceView from "./views/SpaceView";

export default function App() {
  return (
    <BrowserRouter basename="/app">
      <header className="app-header">
        <Link to="/" className="app-header-link">
          <img
            src="./public/reef.svg"
            alt="rif"
            className="app-header-icon"
            width="26"
            height="26"
          />
          <span className="app-header-wordmark">rif</span>
        </Link>
      </header>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/spaces/new" element={<NewSpace />} />
        <Route path="/s/:space" element={<SpaceView />} />
        <Route path="/s/:space/new" element={<p>new page</p>} />
        <Route path="/s/:space/p/*" element={<p>page</p>} />
        <Route path="/s/:space/e/*" element={<p>editor</p>} />
      </Routes>
    </BrowserRouter>
  );
}
