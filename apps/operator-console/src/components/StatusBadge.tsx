export function StatusBadge({ label, tone }: { label: string; tone: string }) {
  return <span className={`dashboard-status dashboard-status-${tone.toLowerCase()}`}>{label}</span>;
}
