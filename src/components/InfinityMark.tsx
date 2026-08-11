export interface InfinityMarkProps {
  className?: string;
  lit?: boolean;
  active?: boolean;
}

/**
 * The Infinity Code brand mark: a single-stroke lemniscate that crosses over
 * itself in the middle (a true figure-eight, not two adjacent loops). Vector,
 * so it stays crisp at any size and themes with currentColor. The viewBox is
 * cropped tight to the glyph so it fills its box.
 */
export default function InfinityMark({
  className = "w-9 h-auto",
  lit = false,
  active = false,
}: InfinityMarkProps): JSX.Element {
  return (
    <svg
      className={`infinity-mark ${lit ? "infinity-mark--lit" : ""} ${active ? "infinity-mark--active" : ""} ${className}`}
      viewBox="1 7 22 10"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path className="infinity-mark__base" d="M9.828 9.172a4 4 0 1 0 0 5.656a10 10 0 0 0 2.172 -2.828a10 10 0 0 1 2.172 -2.828a4 4 0 1 1 0 5.656a10 10 0 0 1 -2.172 -2.828a10 10 0 0 0 -2.172 -2.828" />
      <path className="infinity-mark__beam" pathLength="100" d="M9.828 9.172a4 4 0 1 0 0 5.656a10 10 0 0 0 2.172 -2.828a10 10 0 0 1 2.172 -2.828a4 4 0 1 1 0 5.656a10 10 0 0 1 -2.172 -2.828a10 10 0 0 0 -2.172 -2.828" />
    </svg>
  );
}
