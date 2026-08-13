/**
 * The account row at the foot of the sidebar, and the menu behind it.
 *
 * Everything that is about *the person* rather than about a cove lives here:
 * their profile, taking their data out, and signing out. Export used to sit
 * in the nav between Index and the cove list, which read as a third way to
 * browse; it is an account action, so it belongs behind the name.
 *
 * The trigger is the avatar and name together — a bigger target than either
 * alone, and the whole row is what reads as "you" in the pane.
 *
 * Rendered in two places, because the desktop sidebar does not exist below
 * 900px and everything here was unreachable on a phone — including signing
 * out. In the sidebar it sits last and opens upwards; in the mobile header
 * it sits in the top corner and opens downwards, hence `placement`.
 */

import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { apiSend } from "../api";
import type { Me } from "../types";
import { Avatar } from "./Avatar";

export function AccountMenu({
  me,
  placement = "up",
}: {
  me: Me | null;
  /** Which way the popup opens; "down" for the mobile header. */
  placement?: "up" | "down";
}) {
  const [open, setOpen] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  const wrapper = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);

  // One listener pair for the whole menu, attached only while it is open, so
  // a closed menu costs nothing. `pointerdown` rather than `click`: closing
  // on the way down matches every native menu, and it fires before a link
  // inside the menu would navigate.
  useEffect(() => {
    if (!open) return;

    function onPointerDown(event: PointerEvent) {
      if (!wrapper.current?.contains(event.target as Node)) setOpen(false);
    }

    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      setOpen(false);
      // Escape should leave focus where the reader can carry on from, which
      // is the control they opened the menu with.
      trigger.current?.focus();
    }

    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  async function handleSignOut() {
    setSigningOut(true);
    try {
      // The backend hands back a WorkOS logout URL when it knows the
      // upstream AuthKit session id; navigating there ends that session
      // too. Without it, only reef's cookie is gone and the next login
      // redirect would silently sign the user right back in.
      const result = await apiSend<{ ok: boolean; logout_url?: string }>(
        "POST",
        "/api/auth/logout",
      );
      window.location.href = result.logout_url ?? "/app/signed-out";
    } catch {
      setSigningOut(false);
      setOpen(false);
    }
  }

  return (
    <div className={`acct acct-${placement}`} ref={wrapper}>
      <button
        type="button"
        ref={trigger}
        className="acct-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((wasOpen) => !wasOpen)}
      >
        {me && <Avatar name={me.display_name} size="sm" src={me.avatar} />}
        <span className="side-me-name">{me?.display_name ?? ""}</span>
        <svg
          className={`acct-caret ${open ? "open" : ""}`}
          width="10"
          height="10"
          viewBox="0 0 12 12"
          aria-hidden="true"
        >
          <path d="M3 4.5 6 7.5 9 4.5" />
        </svg>
      </button>

      {open && (
        <div className="acct-menu" role="menu">
          <Link
            to="/profile"
            role="menuitem"
            className="acct-item"
            onClick={() => setOpen(false)}
          >
            Profile
          </Link>
          <Link
            to="/export"
            role="menuitem"
            className="acct-item"
            onClick={() => setOpen(false)}
          >
            Export
          </Link>
          <div className="acct-sep" role="separator" />
          <button
            type="button"
            role="menuitem"
            className="acct-item"
            disabled={signingOut}
            onClick={handleSignOut}
          >
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}
