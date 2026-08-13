import React from "react";
import { avatarColor, initialOf } from "./avatarColor";

interface AvatarProps {
  /** Display name for the avatar. */
  name: string;
  /** Avatar size: "md" (26px default), "sm" (20px), or "lg" (38px). */
  size?: "md" | "sm" | "lg";
  /** Picture URL; falls back to the coloured initial when absent. */
  src?: string | null;
}

/**
 * Circular avatar: the person's picture when they have one, otherwise their
 * initial on a colour derived from their name.
 */
export function Avatar({
  name,
  size = "md",
  src,
}: AvatarProps): React.ReactNode {
  const className = `avatar ${size === "md" ? "" : `avatar-${size}`}`.trim();
  if (src) {
    return <img className={className} src={src} alt={name} title={name} />;
  }
  return (
    <div
      className={className}
      style={{ backgroundColor: avatarColor(name) }}
      title={name}
    >
      {initialOf(name)}
    </div>
  );
}

/**
 * One face in a stack: who it is, and their picture if they have one.
 *
 * Deliberately not `Member` from `types.ts` — a stack only ever needs these
 * two fields, and taking the wire type would tie every caller to a roster.
 */
export interface Face {
  /** Display name, used for the initial, the colour, and the tooltip. */
  name: string;
  /** Picture URL, or null/absent to fall back to the coloured initial. */
  src?: string | null;
}

interface AvatarStackProps {
  /** The people to show, in order. */
  people: Face[];
  /** Maximum avatars to show before adding a +N chip (default 4). */
  max?: number;
  /** Avatar size for every avatar in the stack (default "md"). */
  size?: "md" | "sm";
  /** Click handler for the entire stack. */
  onClick?: () => void;
  /** Accessible label for screen readers (default "Members"). */
  ariaLabel?: string;
}

/**
 * Overlapped avatar stack with +N chip for overflow.
 */
export function AvatarStack({
  people,
  max = 4,
  size = "md",
  onClick,
  ariaLabel = "Members",
}: AvatarStackProps): React.ReactNode {
  const shown = people.slice(0, max);
  const overflow = people.length - max;
  const stackClassName = `avatar-stack ${size === "sm" ? "avatar-stack-sm" : ""}`.trim();
  const moreClassName = `avatar-more ${size === "sm" ? "avatar-more-sm" : ""}`.trim();

  const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Enter" || e.key === " ") {
      if (e.key === " ") {
        e.preventDefault();
      }
      onClick?.();
    }
  };

  const stackProps = onClick
    ? {
        role: "button" as const,
        tabIndex: 0,
        "aria-label": ariaLabel,
        onKeyDown: handleKeyDown,
      }
    : {};

  return (
    <div className={stackClassName} onClick={onClick} {...stackProps}>
      {/* Index as key, not name: two members may share a display name, and
          the roster's order is stable (server-sorted, re-fetched whole). */}
      {shown.map((person, index) => (
        <Avatar key={index} name={person.name} src={person.src} size={size} />
      ))}
      {overflow > 0 && (
        <div className={moreClassName} title={`${overflow} more`}>
          +{overflow}
        </div>
      )}
    </div>
  );
}
