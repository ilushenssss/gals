/** Иконки интерфейса: inline-SVG с обводкой `currentColor`, без внешних наборов. */
const PATHS = {
  back: "m15 18-6-6 6-6",
  next: "m9 18 6-6-6-6",
  down: "m6 9 6 6 6-6",
  plus: "M12 5v14M5 12h14",
  close: "M18 6 6 18M6 6l12 12",
  check: "M20 6 9 17l-5-5",
  search: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14ZM20 20l-3.5-3.5",
  warn: "M12 3 2 21h20L12 3ZM12 10v5M12 18v.5",
  info: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18ZM12 11v6M12 7.5V8",
  error: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18ZM12 8v5M12 16v.5",
  upload: "M12 16V4M7 9l5-5 5 5M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3",
  download: "M12 4v12M7 11l5 5 5-5M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3",
  map: "M9 4 3 6v14l6-2 6 2 6-2V4l-6 2-6-2ZM9 4v14M15 6v14",
  plane: "M3 13l7-2 4-7 2 1-2 6 5 1 2-2 1 1-2 3 1 3-1 1-2-2-5 1 1 6-2 1-3-7-7-1z",
  file: "M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8l-5-5ZM14 3v5h5",
  pen: "M4 20h4L19 9l-4-4L4 16v4Z",
  list: "M5 3h14a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1ZM8 8h8M8 12h8M8 16h5",
  sun: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8ZM12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4",
  moon: "M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5Z",
  auto: "M12 3a9 9 0 1 0 0 18V3Z M12 3a9 9 0 0 1 0 18",
  shield: "M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6l-8-3Z",
  logo: "M4 18 L4 6 L9 18 L9 6 L14 18 L14 6 L20 18",
} as const

export type IconName = keyof typeof PATHS

export function Icon({ name, size = 16, className }: { name: IconName; size?: number; className?: string }) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d={PATHS[name]} />
    </svg>
  )
}
