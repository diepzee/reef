/**
 * How to point an AI assistant at this reef.
 *
 * Until this existed the app never mentioned it. Someone could be invited,
 * sign in, read their pages in the browser and never learn that being read
 * and written by an assistant is the entire product — the instructions lived
 * only on the marketing site, which a signed-in member has no reason to
 * revisit.
 *
 * The endpoint is derived from `window.location.origin` rather than written
 * down, so it is correct in local development and cannot drift if the domain
 * moves again (it already has once, from the Railway hostname to
 * reefwith.me, which silently broke every connector configured against the
 * old one).
 */

import { useState } from "react";

const MCP_PATH = "/mcp";

/** One numbered step in the setup list. */
function Step({ n, children }: { n: number; children: React.ReactNode }) {
  return (
    <li className="connect-step">
      <span className="connect-step-n" aria-hidden="true">
        {n}
      </span>
      <span>{children}</span>
    </li>
  );
}

export default function Connect() {
  const endpoint = `${window.location.origin}${MCP_PATH}`;
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(endpoint);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard access is refused in some browsers and on insecure
      // origins. The URL is on screen and selectable either way, so this
      // failing changes nothing the person cannot do by hand.
      setCopied(false);
    }
  }

  return (
    <div>
      <h1>
        Add <span className="reef-name">reef</span> to Claude
      </h1>
      <p className="muted">
        <span className="reef-name">reef</span> is built to be read and written
        by an assistant. Connect it once and Claude can remember with you.
      </p>

      <div className="export-card">
        <div className="export-card-copy">
          <h2>Your endpoint</h2>
          <p>This is the address Claude needs. It is the same on every device.</p>
        </div>
        <div className="connect-url">
          <code>{endpoint}</code>
          <button type="button" className="button" onClick={copy}>
            {copied ? "Copied" : "Copy"}
          </button>
        </div>
      </div>

      <div className="export-card">
        <div className="export-card-copy">
          <h2>In the Claude app</h2>
        </div>
        <ol className="connect-steps">
          <Step n={1}>
            Open <strong>Settings → Connectors</strong>, then{" "}
            <strong>Add custom connector</strong>.
          </Step>
          <Step n={2}>Paste the endpoint above.</Step>
          <Step n={3}>
            Sign in when Claude asks — use the same address you signed in here
            with, or it will not recognise you.
          </Step>
          <Step n={4}>
            Ask Claude to <em>load your reef index</em> to check it worked.
          </Step>
        </ol>
        <p className="muted">
          Works on desktop, web, and the mobile app. Custom connectors need a
          paid Claude plan.
        </p>
      </div>

      <div className="export-card">
        <div className="export-card-copy">
          <h2>If it does not connect</h2>
          <p>
            Two things account for nearly every failure. If you added{" "}
            <span className="reef-name">reef</span> before and it stopped
            working, remove the connector and add it again — an old one may
            point at a previous address. And if sign-in is refused, the address
            you used in Claude is not the one that was invited.
          </p>
        </div>
      </div>

      <div className="export-card">
        <div className="export-card-copy">
          <h2>What Claude can reach</h2>
          <p>
            Everything you can: your personal pages and every cove you belong
            to. Pages your assistant reads are sent to whoever runs it, and
            their privacy terms cover that exchange —{" "}
            <a href="/privacy">the privacy page</a> says what{" "}
            <span className="reef-name">reef</span> itself does and does not do.
          </p>
        </div>
      </div>
    </div>
  );
}
