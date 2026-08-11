/**
 * The post-logout landing page. Deliberately outside AppShell: it must not
 * fetch `/api/me`, whose 401 handling would bounce straight back into
 * `/api/auth/login` and undo the sign-out.
 */

import { useLocation } from "react-router-dom";

import { FrondGlyph } from "../components/ReefMark";

export default function SignedOut() {
  const location = useLocation();
  const deleted = new URLSearchParams(location.search).get("deleted") === "1";

  return (
    <main className="signed-out">
      <div className="lockup-c" aria-label="reef">
        <FrondGlyph color="var(--accent)" size={26} />
        <span>reef</span>
      </div>
      <h1>{deleted ? "Your data was deleted" : "Signed out"}</h1>
      <p>
        {deleted
          ? "Your reef account and private data are gone. Shared coves remain with their other members."
          : "Your session has ended on this device."}
      </p>
      <a className="button" href={deleted ? "/" : "/api/auth/login"}>
        {deleted ? "Return home" : "Sign in"}
      </a>
    </main>
  );
}
