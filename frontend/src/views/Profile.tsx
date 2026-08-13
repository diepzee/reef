/**
 * "Profile": who reef knows you as, and the one thing you can change about it.
 *
 * The name and address are shown but not editable here. The address is the
 * identity an invitation was bound against — changing it is not a profile
 * edit, it is a re-binding — and the display name is currently set by
 * whoever invited you. Showing them read-only is honest about that; a
 * disabled input pretending otherwise would not be.
 *
 * The picture is downscaled in the browser before it is sent. A photo
 * straight off a phone is several megabytes and the endpoint caps the stored
 * bytes well below that, so resizing here is the difference between "choose
 * a picture" and "choose a picture, then be told it was too big".
 */

import { useRef, useState } from "react";

import { ApiError, apiSend } from "../api";
import { Avatar } from "../components/Avatar";
import { useMe } from "../useMe";

/** Longest edge, in pixels, of a stored avatar. */
const MAX_EDGE = 512;

/** What the file picker will offer, matching the endpoint's allowed types. */
const ACCEPT = "image/png,image/jpeg,image/webp,image/gif";

/**
 * Draw `file` into a centre-cropped square no larger than `MAX_EDGE` and
 * return it as a mime type plus base64 payload, ready for the API.
 *
 * Centre-cropped rather than letterboxed because every surface that shows an
 * avatar shows it in a circle; fitting the whole image inside that circle
 * would shrink the face and leave transparent corners.
 */
async function squareEncode(file: File): Promise<{ mime: string; data: string }> {
  const bitmap = await createImageBitmap(file);
  const edge = Math.min(bitmap.width, bitmap.height, MAX_EDGE);
  const canvas = document.createElement("canvas");
  canvas.width = edge;
  canvas.height = edge;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("no 2d context");

  const source = Math.min(bitmap.width, bitmap.height);
  context.drawImage(
    bitmap,
    (bitmap.width - source) / 2,
    (bitmap.height - source) / 2,
    source,
    source,
    0,
    0,
    edge,
    edge,
  );
  bitmap.close();

  // toDataURL always succeeds for png; webp is preferred when the browser
  // encodes it, which it signals by returning a data URL with that type.
  const encoded = canvas.toDataURL("image/webp", 0.9);
  const usable = encoded.startsWith("data:image/webp")
    ? encoded
    : canvas.toDataURL("image/png");
  // "data:image/webp;base64,AAAA…" — sliced rather than split so the parts
  // are plain strings, not possibly-absent array entries.
  const comma = usable.indexOf(",");
  const header = usable.slice(0, comma);
  return {
    mime: header.slice("data:".length, header.indexOf(";")),
    data: usable.slice(comma + 1),
  };
}

export default function Profile() {
  const { me, setAvatar: onChange } = useMe();
  const picker = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function choose(file: File | undefined) {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const { mime, data } = await squareEncode(file);
      const result = await apiSend<{ avatar: string | null }>(
        "PUT",
        "/api/me/avatar",
        { mime, data },
      );
      onChange(result.avatar);
    } catch (failure) {
      setError(
        failure instanceof ApiError
          ? failure.detail || failure.message
          : "that picture could not be read",
      );
    } finally {
      setBusy(false);
      // Let the same file be chosen again after a failure.
      if (picker.current) picker.current.value = "";
    }
  }

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      await apiSend<{ avatar: null }>("DELETE", "/api/me/avatar");
      onChange(null);
    } catch {
      setError("that picture could not be removed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <h1>Profile</h1>

      <div className="profile-head">
        <Avatar name={me?.display_name ?? ""} src={me?.avatar} size="md" />
        <div>
          <div className="profile-name">{me?.display_name ?? ""}</div>
          <div className="muted">{me?.email ?? ""}</div>
        </div>
      </div>

      <h2 className="profile-section">Picture</h2>
      <p className="muted">
        Shown next to your name, and to the people you share a cove with. It
        is resized to a {MAX_EDGE}px square before it is stored.
      </p>
      {error && <div className="notice">{error}</div>}
      <input
        ref={picker}
        type="file"
        accept={ACCEPT}
        className="profile-file"
        onChange={(event) => choose(event.target.files?.[0])}
      />
      <div className="ed-toolbar">
        <button
          type="button"
          className="ed-save"
          disabled={busy}
          onClick={() => picker.current?.click()}
        >
          {me?.avatar ? "Change picture" : "Choose a picture"}
        </button>
        {me?.avatar && (
          <button type="button" disabled={busy} onClick={remove}>
            Remove
          </button>
        )}
      </div>

      <h2 className="profile-section">Name and address</h2>
      <p className="muted">
        reef knows you by the address your invitation was sent to, and by the
        name whoever invited you gave. Neither can be changed here yet.
      </p>
    </div>
  );
}
