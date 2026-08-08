import React from "react";
import { avatarColor, initialOf } from "./avatarColor";

interface AvatarProps {
  /** Display name for the avatar. */
  name: string;
  /** Avatar size: "md" (26px default) or "sm" (20px). */
  size?: "md" | "sm";
}

/**
 * Circular avatar with initial and deterministic color.
 */
export function Avatar({ name, size = "md" }: AvatarProps): React.ReactNode {
  const className = `avatar ${size === "sm" ? "avatar-sm" : ""}`.trim();
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
  /** Click handler for the entire stack. */
  onClick?: () => void;
}

/**
 * Overlapped avatar stack with +N chip for overflow.
 */
export function AvatarStack({
  names,
  max = 4,
  onClick,
}: AvatarStackProps): React.ReactNode {
  const shown = names.slice(0, max);
  const overflow = names.length - max;

  return (
    <div className="avatar-stack" onClick={onClick}>
      {shown.map((name) => (
        <Avatar key={name} name={name} size="md" />
      ))}
      {overflow > 0 && (
        <div className="avatar-more" title={`${overflow} more`}>
          +{overflow}
        </div>
      )}
    </div>
  );
}
