// Ascension Dial: 6-segment circular form indicator (Infinity Code X).
import { FORM_COLORS, FORM_NAMES } from "../lib/ascensionClient";

interface AscensionDialProps {
  level: number;
  dwellRemaining: number;
  cooldownRemaining: number;
}

const SEGMENTS = 6;
const SEG_DEG = 360 / SEGMENTS;

function conic(): string {
  const stops: string[] = [];
  for (let i = 0; i < SEGMENTS; i += 1) {
    const from = i * SEG_DEG - 90;
    const to = from + SEG_DEG - 4;
    stops.push(`${FORM_COLORS[i]} ${from}deg ${to}deg`);
    if (i < SEGMENTS - 1) {
      const gap = from + SEG_DEG - 4;
      stops.push(`transparent ${gap}deg ${from + SEG_DEG}deg`);
    }
  }
  return `conic-gradient(${stops.join(", ")})`;
}

export default function AscensionDial({
  level,
  dwellRemaining,
  cooldownRemaining,
}: AscensionDialProps): JSX.Element {
  return (
    <div className="asc-dial-wrap" aria-label={`Ascension form: ${FORM_NAMES[level]}`}>
      <div
        className="asc-dial"
        style={{
          background: conic(),
          filter: `drop-shadow(0 0 14px ${FORM_COLORS[level]}66)`,
        }}
      >
        <div className="asc-dial-core">
          <span className="asc-dial-form" style={{ color: FORM_COLORS[level] }}>
            {FORM_NAMES[level]}
          </span>
          <span className="asc-dial-lvl">LEVEL {level} / 5</span>
          <span className="asc-dial-count">
            {dwellRemaining > 0
              ? `dwell ${Math.ceil(dwellRemaining)}s`
              : cooldownRemaining > 0
                ? `cooldown ${Math.ceil(cooldownRemaining)}s`
                : "ready"}
          </span>
        </div>
      </div>
      <div className="asc-dial-legend">
        {FORM_NAMES.map((name, i) => (
          <span
            key={name}
            className={`asc-dial-tag${i === level ? " asc-dial-tag-active" : ""}`}
            style={i === level ? { color: FORM_COLORS[i] } : undefined}
          >
            {name === "Mr X Final" ? "Mr X Final - Locked" : name}
          </span>
        ))}
      </div>
    </div>
  );
}
