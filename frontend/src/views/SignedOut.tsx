/**
 * The post-logout landing page. Deliberately outside AppShell: it must not
 * fetch `/api/me`, whose 401 handling would bounce straight back into
 * `/api/auth/login` and undo the sign-out.
 */

import { FrondGlyph } from "../components/ReefMark";

export default function SignedOut() {
  return (
    <main className="signed-out">
      <div className="lockup-c" aria-label="reef">
        <FrondGlyph color="var(--accent)" size={26} />
        <span>reef</span>
      </div>
      <h1>Signed out</h1>
      <p>Your session has ended on this device.</p>
      <a className="button" href="/api/auth/login">
        Sign in
      </a>
    </main>
  );
}
