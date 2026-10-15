import { CHANNEL_LABEL } from "../lib/format";
import type { Channel } from "../lib/types";

const COLOR: Record<Channel, string> = { whatsapp: "var(--wa)", instagram: "var(--ig)", messenger: "var(--fb)" };
const LETTER: Record<Channel, string> = { whatsapp: "W", instagram: "I", messenger: "M" };

/** Small square mark for a channel, with the name for screen readers (or shown with `label`). */
export function ChannelMark({ channel, label = false }: { channel: Channel | null; label?: boolean }) {
  if (!channel) return null;
  return (
    <span className="inline-flex items-center gap-1.5 text-xs font-medium text-ink-2">
      <span
        className="narrow inline-flex size-4 items-center justify-center rounded-[4px] text-[10px] font-bold text-white"
        style={{ background: COLOR[channel] }}
        aria-hidden
      >
        {LETTER[channel]}
      </span>
      {label ? CHANNEL_LABEL[channel] : <span className="sr-only">{CHANNEL_LABEL[channel]}</span>}
    </span>
  );
}
