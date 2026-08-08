import { BrowserRouter, Link, Route, Routes } from "react-router-dom";

import reefIcon from "../public/reef.svg";
import Editor from "./views/Editor";
import Home from "./views/Home";
import NewPage from "./views/NewPage";
import NewSpace from "./views/NewSpace";
import PageView from "./views/PageView";
import SpaceView from "./views/SpaceView";

export default function App() {
  return (
    <BrowserRouter basename="/app">
      <header className="app-header">
        <Link to="/" className="app-header-link">
          <img
            src={reefIcon}
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
        <Route path="/s/:space/new" element={<NewPage />} />
        <Route path="/s/:space/p/*" element={<PageView />} />
        <Route path="/s/:space/e/*" element={<Editor />} />
      </Routes>
    </BrowserRouter>
  );
}
