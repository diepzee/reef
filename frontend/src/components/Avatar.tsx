import React from "react";
import { avatarColor, initialOf } from "./avatarColor";

interface AvatarProps {
  /** Display name for the avatar. */
  name: string;
  /** Avatar size: "md" (26px default) or "sm" (20px). */
  size?: "md" | "sm";
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
  const className = `avatar ${size === "sm" ? "avatar-sm" : ""}`.trim();
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

interface AvatarStackProps {
  /** Array of display names. */
  names: string[];
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
  names,
  max = 4,
  size = "md",
  onClick,
  ariaLabel = "Members",
}: AvatarStackProps): React.ReactNode {
  const shown = names.slice(0, max);
  const overflow = names.length - max;
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
      {shown.map((name) => (
        <Avatar key={name} name={name} size={size} />
      ))}
      {overflow > 0 && (
        <div className={moreClassName} title={`${overflow} more`}>
          +{overflow}
        </div>
      )}
    </div>
  );
}
