// Little round emoji "stickers" that make the shop feel alive.
export default function Sticker({ children, tilt = 0, tone = "sun", size = 56 }) {
  return (
    <span
      className={`sticker sticker-${tone}`}
      style={{ transform: `rotate(${tilt}deg)`, width: size, height: size, fontSize: size * 0.55 }}
      aria-hidden="true"
    >
      {children}
    </span>
  );
}
