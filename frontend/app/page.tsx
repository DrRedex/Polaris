'use client';

/**
 * PolarisDashboard.tsx
 * ---------------------------------------------------------------------------
 * Next.js (App Router) client component. Tailwind CSS + framer-motion + lucide-react.
 *
 *   npm i framer-motion lucide-react
 *
 * Fonts
 *   'Syne', 'DM Sans' and 'JetBrains Mono' are loaded by the <style> block rendered
 *   below and are ALWAYS applied through single-quoted font stacks (see FONT), so
 *   multi-word family names can never be mis-parsed. If you prefer Tailwind classes,
 *   mirror the same quoted stacks in tailwind.config:
 *
 *     theme.extend.fontFamily = {
 *       display: ["'Syne'", "'DM Sans'", 'sans-serif'],
 *       sans:    ["'DM Sans'", 'system-ui', 'sans-serif'],
 *       mono:    ["'JetBrains Mono'", 'ui-monospace', 'monospace'],
 *     }
 *
 * Architecture note (why the wheel and logo live OUTSIDE <AnimatePresence>)
 *   With mode="wait", the outgoing view is unmounted before the incoming one mounts,
 *   so a layoutId pair that lives inside the two views has nothing to morph between.
 *   The role wheel (layoutId="role-wheel") and the logo (layoutId="logo") are therefore
 *   mounted once, above the AnimatePresence boundary, and only change layout when `view`
 *   flips. The Landing and Dashboard chrome fade/slide in and out around them.
 */

import { AnimatePresence, MotionConfig, motion, type TargetAndTransition, type Transition } from 'framer-motion';
import {
  Bell,
  ChevronDown,
  ChevronsLeft,
  ChevronsRight,
  Download,
  Loader2,
  MessageSquare,
  RotateCcw,
  Settings as SettingsIcon,
  X,
} from 'lucide-react';
import {
  Fragment,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type CSSProperties,
  type FormEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
  type ReactNode,
} from 'react';
import {
  downloadBlob,
  getAlerts,
  getComponentMetrics,
  getTimeseries,
  postInvestigation,
  postPdfReport,
  type ComponentMetric,
  type InvestigationResult,
  type RoleKey,
  type Severity,
  type Timeseries,
} from '@/lib/api';

/* ============================================================================
 * Types
 * ========================================================================= */

type View = 'landing' | 'dash';
type Slot = 'center' | 'left' | 'right';

interface RoleConfig {
  key: RoleKey;
  name: string;
  short: string;
  /** Exact brand hex: strokes, fills, glows, hover and active states. */
  accent: string;
  /** Accent used for small text. Identical to `accent` except Manager (contrast on #030712). */
  text: string;
  /** Foreground colour on top of a solid `accent` surface. */
  ink: string;
  /** Wedge start / end angle on the landing wheel (degrees, 0 = right, 90 = down). */
  a0: number;
  a1: number;
  kpiValue: string;
  kpiLabel: string;
  features: string[];
  glow: string;
  soft: string;
  mid: string;
  line: string;
  dim: string;
}

interface Question {
  /** Unique per entry, so the same text asked twice still gets its own React key. */
  id: string;
  text: string;
}
interface Thread {
  id: string;
  title: string;
  when: string;
  questions: Question[];
}
interface Theme {
  accent: string;
  text: string;
  ink: string;
  glow: string;
  soft: string;
  mid: string;
  line: string;
  dim: string;
}

/* ============================================================================
 * Typography (every family is single-quoted)
 * ========================================================================= */

const FONT: Record<'display' | 'body' | 'mono', CSSProperties> = {
  display: { fontFamily: "'Syne', 'DM Sans', sans-serif", fontWeight: 800 },
  body: { fontFamily: "'DM Sans', system-ui, sans-serif" },
  mono: { fontFamily: "'JetBrains Mono', ui-monospace, monospace" },
};

const GLOBAL_CSS = `
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;700&family=JetBrains+Mono:wght@400;500&family=Syne:wght@700;800&display=swap');
.polaris-root button:focus-visible,
.polaris-root input:focus-visible,
.polaris-root [tabindex]:focus-visible { outline: 2px solid #fff; outline-offset: 2px; }
.polaris-root .pl-scroll { scrollbar-width: thin; scrollbar-color: rgba(255,255,255,.18) transparent; }
@keyframes pl-cue { 0%,100% { transform: translateY(0); opacity: .6 } 50% { transform: translateY(6px); opacity: 1 } }
@keyframes pl-pulse { 0%,100% { opacity: .35 } 50% { opacity: 1 } }
@keyframes pl-node { 0%,100% { opacity: .55; transform: scale(1) } 50% { opacity: 1; transform: scale(2) } }
@keyframes pl-edge { 0%,100% { opacity: .35 } 50% { opacity: 1 } }
.polaris-root .pl-cue { animation: pl-cue 1.8s ease-in-out infinite; }
.polaris-root .pl-pulse { animation: pl-pulse 1s ease-in-out infinite; }
.polaris-root .pl-node { transform-box: fill-box; transform-origin: center; transition: fill .45s ease, stroke .45s ease, opacity .45s ease; }
.polaris-root .pl-node[data-lit='true'] { animation: pl-node 1.8s ease-in-out infinite; animation-delay: var(--pl-d, 0s); }
.polaris-root .pl-edge { transition: stroke .45s ease, stroke-width .45s ease, opacity .45s ease; }
.polaris-root .pl-edge[data-lit='true'] { animation: pl-edge 1.8s ease-in-out infinite; animation-delay: var(--pl-d, 0s); }
@media (prefers-reduced-motion: reduce) {
  .polaris-root .pl-cue, .polaris-root .pl-pulse, .polaris-root .pl-node, .polaris-root .pl-edge { animation: none !important; }
}
`;

/* ============================================================================
 * Roles
 * ========================================================================= */

const rgba = (hex: string, a: number): string => {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${n >> 16}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
};

const tint = (accent: string) => ({
  glow: rgba(accent, 0.35),
  soft: rgba(accent, 0.1),
  mid: rgba(accent, 0.22),
  line: rgba(accent, 0.45),
  dim: rgba(accent, 0.5),
});

const ROLES: Record<RoleKey, RoleConfig> = {
  ENG: {
    key: 'ENG',
    name: 'Engineer',
    short: 'ENG',
    accent: '#00F0FF', // Electric Cyan
    text: '#00F0FF',
    ink: '#021014',
    a0: 150,
    a1: 270,
    kpiValue: 'Root cause',
    kpiLabel: 'from the symptom to the first failing line',
    features: [
      'Deep log search: ask in plain language, see the SQL and the rows behind it',
      'Root cause: follow a trace from the symptom to the first failing component',
      'Trace view: every ERROR linked to the request that produced it',
    ],
    ...tint('#00F0FF'),
  },
  LEAD: {
    key: 'LEAD',
    name: 'Team Lead',
    short: 'LEAD',
    accent: '#F59E0B', // Starlight Gold
    text: '#F59E0B',
    ink: '#1A1100',
    a0: 30,
    a1: 150,
    kpiValue: 'Patterns',
    kpiLabel: 'recurring failures by component and owner',
    features: [
      'Failure patterns: errors grouped by component and message template',
      'Regression watch: what changed since the last deploy',
      'Hand-off: assign a finding with its evidence attached',
    ],
    ...tint('#F59E0B'),
  },
  MGR: {
    key: 'MGR',
    name: 'Manager',
    short: 'MGR',
    accent: '#6366F1', // Deep Indigo
    text: '#8B93FF',
    ink: '#FFFFFF',
    a0: 270,
    a1: 390,
    kpiValue: 'Risk',
    kpiLabel: 'ranked by severity and persistence',
    features: [
      'Risk prioritization: impact first, noise last',
      'Plain-language summary: no log reading needed',
      'PDF reports: ready for the review meeting',
    ],
    ...tint('#6366F1'),
  },
};

const ROLE_KEYS: RoleKey[] = ['ENG', 'LEAD', 'MGR'];

const themeOf = (k: RoleKey): Theme => {
  const { accent, text, ink, glow, soft, mid, line, dim } = ROLES[k];
  return { accent, text, ink, glow, soft, mid, line, dim };
};

/* ---- wheel orientation maths ------------------------------------------------
 * Landing: LEAD wedge is centred at 90deg (bottom), ENG at 210deg (left), MGR at 330deg (right).
 * Docked:  the wheel is rotated so the active wedge points straight down (90deg). Its centre sits
 *          on the viewport's top edge, so only the lower half shows, an inverted semi-circle.
 *          The other two wedges are left as slivers in the upper corners of the bowl.
 */
const WEDGE_MID: Record<RoleKey, number> = { LEAD: 90, ENG: 210, MGR: 330 };
const CYCLE: RoleKey[] = ['LEAD', 'ENG', 'MGR']; // clockwise by wedge angle
const nextRole = (k: RoleKey): RoleKey => CYCLE[(CYCLE.indexOf(k) + 1) % 3];
/** Wheel rotation that brings a role's wedge to the bottom, relative to the landing orientation. */
const ROT_FOR: Record<RoleKey, number> = { LEAD: 0, ENG: -120, MGR: 120 };
/** Label nudge inside its (rotated) wedge so it sits in the visible part of the bowl. Docked, the side slivers
 *  span 0..30deg (right) and 150..180deg (left) of the lower half, so the side labels are centred at 15deg / 165deg,
 *  well clear of the 30deg / 150deg dividers. */
const SLOT_DELTA: Record<Slot, number> = { center: 0, left: -45, right: 45 };
/** ...and pushed outward past the hub (radius ~19.4cqw), so the corner labels clear the logo at the top of the dock. */
const SLOT_RADIUS: Record<Slot, string> = { center: '30.87cqw', left: '36cqw', right: '36cqw' };
/** Docked, inactive labels are a touch smaller and get a tighter hit box, so they stay inside their sliver. */
const SIDE_SCALE = 0.6;
const SIDE_BOX = { w: 15, h: 9 }; // cqw; the default box is 21 x 13
const slotOf = (k: RoleKey, active: RoleKey): Slot =>
  k === active ? 'center' : k === nextRole(active) ? 'left' : 'right';

const pt = (r: number, deg: number): [number, number] => [
  230 + r * Math.cos((deg * Math.PI) / 180),
  230 + r * Math.sin((deg * Math.PI) / 180),
];
const f1 = (n: number): string => n.toFixed(1);

const wedgePath = (a0: number, a1: number): string => {
  const g = 1.6;
  const o1 = pt(222, a0 + g);
  const o2 = pt(222, a1 - g);
  const i1 = pt(62, a0 + g);
  const i2 = pt(62, a1 - g);
  return `M${f1(o1[0])} ${f1(o1[1])} A222 222 0 0 1 ${f1(o2[0])} ${f1(o2[1])} L${f1(i2[0])} ${f1(i2[1])} A62 62 0 0 0 ${f1(i1[0])} ${f1(i1[1])} Z`;
};
const WEDGE_D: Record<RoleKey, string> = {
  ENG: wedgePath(ROLES.ENG.a0, ROLES.ENG.a1),
  LEAD: wedgePath(ROLES.LEAD.a0, ROLES.LEAD.a1),
  MGR: wedgePath(ROLES.MGR.a0, ROLES.MGR.a1),
};
const RIM_ARC = (() => {
  const [x1, y1] = pt(224, 60);
  const [x2, y2] = pt(224, 120);
  return `M${f1(x1)} ${f1(y1)} A224 224 0 0 1 ${f1(x2)} ${f1(y2)}`;
})();

const LANDING_SIZE = 'min(460px, 92vw, 60vh)';
/** Dock diameter, shared through a CSS variable set on the root. Leaves 64px each side for Alerts / Settings. */
const DOCK_VAR = 'min(340px, calc(100vw - 128px))';
const DOCK_SIZE = 'var(--pl-dock)';
const DOCK_OFFSET = 'calc(var(--pl-dock) / -2)'; // wheel centre lands exactly on y = 0
const DOCK_HALF = 170;
const SIDEBAR_W = 264;
const DESKTOP_MQ = '(min-width: 768px)';

const LAYOUT_DURATION = 0.9;
const LAYOUT_T: Transition = { duration: LAYOUT_DURATION, ease: [0.22, 1, 0.36, 1] };
const SPIN_T: Transition = { duration: 0.8, ease: [0.34, 1.35, 0.45, 1] };
const FADE = {
  initial: { opacity: 0 },
  animate: { opacity: 1 },
  exit: { opacity: 0 },
  transition: { duration: 0.3 },
} as const;

/* ============================================================================
 * Dashboard data. Everything below the role config comes from the API; these are only starting questions.
 * ========================================================================= */

const INITIAL_THREADS: Thread[] = [
  {
    id: 't1',
    title: 'Slow instance builds',
    when: 'Suggested',
    questions: [
      { id: 't1-q1', text: 'Why are instance builds slow, and which component is responsible?' },
      { id: 't1-q2', text: 'When was build latency at its worst?' },
    ],
  },
  {
    id: 't2',
    title: 'Neutron port timeouts',
    when: 'Suggested',
    questions: [
      { id: 't2-q1', text: 'Are the Neutron port timeouts a cause or an effect?' },
      { id: 't2-q2', text: 'Show every trace that mentions a lock timeout' },
    ],
  },
  {
    id: 't3',
    title: 'Error trend by day',
    when: 'Suggested',
    questions: [
      { id: 't3-q1', text: 'How do ERROR counts change from day to day?' },
      { id: 't3-q2', text: 'Which components logged the most ERROR lines?' },
    ],
  },
  {
    id: 't4',
    title: 'Instances that failed to spawn',
    when: 'Suggested',
    questions: [{ id: 't4-q1', text: 'Which instances failed to spawn, and why?' }],
  },
];

const SEV_COLOR: Record<Severity, string> = { ERROR: '#FF5C8A', WARN: '#FFA45C', INFO: '#9AA6BF' };

/* ---- activity chart (viewBox 760 x 240) ---- */
const CHART_VB_W = 760;
const CHART_VB_H = 240;
const CHART_X0 = 44;
const CHART_W = 700;
const CHART_Y0 = 16;
const CHART_H = 188;
const CHART_TICKS = 4;

/* Investigations run over the full corpus (no time filter); an empty window renders as "full corpus" in the PDF. */
const REPORT_WINDOW: Record<string, string> = {};

/* ============================================================================
 * Constellation background (landing: reactive, dashboard: calm)
 * ========================================================================= */

const VB_W = 1440;
const VB_H = 900;

/** Thirds of the screen as pie sectors around the wheel, matching the wheel's wedges. */
const sectorOf = (x: number, y: number): RoleKey => {
  const a = ((Math.atan2(y - VB_H / 2, x - VB_W / 2) * 180) / Math.PI + 360) % 360;
  if (a >= 30 && a < 150) return 'LEAD';
  if (a >= 150 && a < 270) return 'ENG';
  return 'MGR';
};

const mulberry32 = (seed: number) => {
  let a = seed;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
};

interface CNode {
  id: number;
  x: number;
  y: number;
  r: number;
  sector: RoleKey;
  delay: string;
}
interface CEdge {
  id: string;
  a: CNode;
  b: CNode;
  sector: RoleKey;
  delay: string;
}

/** Deterministic (seeded) so server and client render identical markup. */
const NETWORK = (() => {
  const rand = mulberry32(2026);
  const cols = 13;
  const rows = 8;
  const nodes: CNode[] = [];
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const x = (c + 0.5 + (rand() - 0.5) * 0.8) * (VB_W / cols);
      const y = (r + 0.5 + (rand() - 0.5) * 0.8) * (VB_H / rows);
      nodes.push({
        id: nodes.length,
        x: +x.toFixed(1),
        y: +y.toFixed(1),
        r: +(1.3 + rand() * 1.5).toFixed(2),
        sector: sectorOf(x, y),
        delay: `${(rand() * 1.8).toFixed(2)}s`,
      });
    }
  }
  const edges: CEdge[] = [];
  const seen = new Set<string>();
  for (const n of nodes) {
    nodes
      .filter((m) => m.id !== n.id)
      .map((m) => ({ m, d: Math.hypot(m.x - n.x, m.y - n.y) }))
      .sort((p, q) => p.d - q.d)
      .slice(0, 2)
      .filter((p) => p.d < 230)
      .forEach(({ m }) => {
        const key = n.id < m.id ? `${n.id}-${m.id}` : `${m.id}-${n.id}`;
        if (seen.has(key)) return;
        seen.add(key);
        edges.push({
          id: key,
          a: n,
          b: m,
          sector: sectorOf((n.x + m.x) / 2, (n.y + m.y) / 2),
          delay: `${(rand() * 1.8).toFixed(2)}s`,
        });
      });
  }
  return { nodes, edges };
})();

const SECTOR_POLY: Record<RoleKey, string> = (() => {
  const far = (deg: number) =>
    `${f1(VB_W / 2 + 3000 * Math.cos((deg * Math.PI) / 180))},${f1(VB_H / 2 + 3000 * Math.sin((deg * Math.PI) / 180))}`;
  const out = {} as Record<RoleKey, string>;
  ROLE_KEYS.forEach((k) => {
    const { a0, a1 } = ROLES[k];
    out[k] = `${VB_W / 2},${VB_H / 2} ${far(a0)} ${far((a0 + a1) / 2)} ${far(a1)}`;
  });
  return out;
})();

function Constellation({ litRole }: { litRole: RoleKey | null }) {
  return (
    <svg
      className="pointer-events-none absolute inset-0 h-full w-full"
      viewBox={`0 0 ${VB_W} ${VB_H}`}
      preserveAspectRatio="xMidYMid slice"
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        {ROLE_KEYS.map((k) => (
          <radialGradient key={k} id={`pl-glow-${k}`} gradientUnits="userSpaceOnUse" cx={VB_W / 2} cy={VB_H / 2} r={820}>
            <stop offset="0" stopColor={ROLES[k].accent} stopOpacity="0.34" />
            <stop offset="1" stopColor={ROLES[k].accent} stopOpacity="0" />
          </radialGradient>
        ))}
      </defs>

      {ROLE_KEYS.map((k) => (
        <polygon
          key={k}
          points={SECTOR_POLY[k]}
          fill={`url(#pl-glow-${k})`}
          style={{ opacity: litRole === k ? 1 : 0, transition: 'opacity .45s ease' }}
        />
      ))}

      <g>
        {NETWORK.edges.map((e) => {
          const lit = litRole === e.sector;
          const dim = litRole !== null && !lit;
          return (
            <line
              key={e.id}
              className="pl-edge"
              data-lit={lit}
              x1={e.a.x}
              y1={e.a.y}
              x2={e.b.x}
              y2={e.b.y}
              style={
                {
                  stroke: lit ? ROLES[e.sector].accent : 'rgba(148,163,184,0.2)',
                  strokeWidth: lit ? 1.3 : 0.8,
                  opacity: dim ? 0.35 : 1,
                  '--pl-d': e.delay,
                } as CSSProperties
              }
            />
          );
        })}
      </g>
      <g>
        {NETWORK.nodes.map((n) => {
          const lit = litRole === n.sector;
          const dim = litRole !== null && !lit;
          return (
            <circle
              key={n.id}
              className="pl-node"
              data-lit={lit}
              cx={n.x}
              cy={n.y}
              r={n.r}
              style={
                {
                  fill: lit ? ROLES[n.sector].accent : '#CBD5E1',
                  stroke: lit ? rgba(ROLES[n.sector].accent, 0.4) : 'transparent',
                  strokeWidth: lit ? 5 : 0,
                  opacity: lit ? 1 : dim ? 0.25 : 0.65,
                  '--pl-d': n.delay,
                } as CSSProperties
              }
            />
          );
        })}
      </g>
    </svg>
  );
}

/* ============================================================================
 * Shared glyph
 * ========================================================================= */

function PolarisStar({ className, color }: { className?: string; color: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M12 2 L14.2 9.8 L22 12 L14.2 14.2 L12 22 L9.8 14.2 L2 12 L9.8 9.8 Z"
        fill={color}
        style={{ transition: 'fill .5s' }}
      />
    </svg>
  );
}

/* ============================================================================
 * Shared element 1: the role wheel  (layoutId="role-wheel")
 * Landing: a full 460px wheel centred on screen.
 * Dashboard: the SAME element, scaled to 340px and translated so its centre sits on the top
 * edge of the viewport. Everything below y=0 is the inverted semi-circle dock.
 * Internals are sized in `cqw` (container units) so they track the box through the morph.
 * ========================================================================= */

interface RoleWheelProps {
  view: View;
  role: RoleKey;
  rot: number;
  hoverRole: RoleKey | null;
  onHover: (k: RoleKey | null) => void;
  onPick: (k: RoleKey) => void;
}

function RoleWheel({ view, role, rot, hoverRole, onHover, onPick }: RoleWheelProps) {
  const docked = view === 'dash';
  const active = ROLES[role];
  const hubColor = !docked && hoverRole ? ROLES[hoverRole].text : '#E6EBF5';

  return (
    // `md:pl-[var(--pl-side)]` shifts the flex centre by half the sidebar width (see the root), so the docked
    // wheel lines up with the centre of the main scroll column instead of the viewport.
    <div
      className={`pointer-events-none fixed inset-0 z-40 flex justify-center md:pl-[var(--pl-side)] ${docked ? 'items-start' : 'items-center'}`}
    >
      <motion.div
        layout
        layoutId="role-wheel"
        transition={{ layout: LAYOUT_T }}
        role="group"
        aria-label="Role wheel: Engineer, Manager, Team Lead"
        className="relative aspect-square shrink-0 [container-type:inline-size]"
        style={{ width: docked ? DOCK_SIZE : LANDING_SIZE, marginTop: docked ? DOCK_OFFSET : 0 }}
      >
        {/* dock body: fades in behind the wedges, never fades the wheel itself */}
        <motion.div
          aria-hidden="true"
          initial={false}
          animate={{ opacity: docked ? 1 : 0 }}
          transition={{ duration: 0.6 }}
          className="absolute inset-0 rounded-full border border-white/[0.14] bg-[rgba(6,10,22,0.92)] backdrop-blur-[16px] transition-shadow duration-[600ms]"
          style={{ boxShadow: `0 20px 56px -12px ${active.glow}` }}
        />

        {/* Hit-area clip. Docked, only the lower half of the wheel is on screen, so the wedges may only
            catch clicks there. The clip sits on this NON-rotating wrapper (the dial inside spins), and it is
            applied after the dock transition ends so the wheel's upper half still slides off screen visibly. */}
        <div
          className="absolute inset-0"
          style={{
            clipPath: docked ? 'inset(50% 0 0 0)' : 'none',
            transition: `clip-path 0s linear ${docked ? LAYOUT_DURATION : 0}s`,
          }}
        >
        {/* wedges rotate as one dial */}
        <motion.div className="absolute inset-0" initial={false} animate={{ rotate: rot }} transition={SPIN_T}>
          <svg viewBox="0 0 460 460" className="pointer-events-none h-full w-full overflow-visible" role="presentation">
            {ROLE_KEYS.map((k) => {
              const c = ROLES[k];
              const isActive = docked && k === role;
              const hot = hoverRole === k;
              const lit = isActive || hot;
              return (
                <path
                  key={k}
                  d={WEDGE_D[k]}
                  className="pointer-events-auto"
                  strokeWidth={1.5}
                  fill={
                    docked
                      ? isActive
                        ? c.mid
                        : hot
                          ? c.soft
                          : 'rgba(255,255,255,0.015)'
                      : hot
                        ? c.mid
                        : 'rgba(15,23,42,0.72)'
                  }
                  stroke={lit ? (docked ? c.line : c.accent) : 'rgba(255,255,255,0.14)'}
                  style={{
                    cursor: 'pointer',
                    transition: 'fill .3s, stroke .3s, filter .3s',
                    filter: !docked && hot ? `drop-shadow(0 0 16px ${c.glow})` : 'none',
                  }}
                  onMouseEnter={() => onHover(k)}
                  onMouseLeave={() => onHover(null)}
                  onClick={() => onPick(k)}
                />
              );
            })}
          </svg>
        </motion.div>
        </div>

        {/* static overlay: ring, hub, rim indicator */}
        <svg
          viewBox="0 0 460 460"
          className="pointer-events-none absolute inset-0 h-full w-full"
          aria-hidden="true"
        >
          <motion.circle
            cx={230}
            cy={230}
            r={227}
            fill="none"
            stroke="rgba(255,255,255,0.07)"
            strokeDasharray="2 7"
            initial={false}
            animate={{ opacity: docked ? 0 : 1 }}
            transition={{ duration: 0.5 }}
          />
          <motion.circle
            cx={230}
            cy={230}
            r={108}
            fill="none"
            stroke="rgba(255,255,255,0.1)"
            strokeDasharray="2 5"
            initial={false}
            animate={{ opacity: docked ? 1 : 0 }}
            transition={{ duration: 0.6 }}
          />
          <motion.circle
            cx={230}
            cy={230}
            fill="rgba(3,7,18,0.92)"
            stroke="rgba(255,255,255,0.12)"
            initial={false}
            animate={{ r: docked ? 89 : 54 }}
            transition={SPIN_T}
          />
          <motion.path
            d="M230 206 L236 224 L254 230 L236 236 L230 254 L224 236 L206 230 L224 224 Z"
            initial={false}
            animate={{ opacity: docked ? 0 : 1, fill: hubColor }}
            transition={{ duration: 0.4 }}
          />
          <motion.path
            d={RIM_ARC}
            fill="none"
            strokeWidth={5}
            strokeLinecap="round"
            initial={false}
            animate={{ opacity: docked ? 1 : 0, stroke: active.accent }}
            transition={{ duration: 0.5 }}
          />
          <motion.path
            d="M214 457 L230 436 L246 457 Z"
            initial={false}
            animate={{ opacity: docked ? 1 : 0, fill: active.accent }}
            transition={{ duration: 0.5 }}
          />
        </svg>

        {/* labels orbit the centre; each is counter-rotated to stay upright */}
        {ROLE_KEYS.map((k) => {
          const c = ROLES[k];
          const slot = slotOf(k, role);
          const isActive = docked && slot === 'center';
          const hot = hoverRole === k;
          const angle = WEDGE_MID[k] + rot + (docked ? SLOT_DELTA[slot] : 0);
          const color = docked
            ? isActive || hot
              ? c.text
              : 'rgba(200,210,230,0.62)'
            : hot
              ? c.text
              : '#E6EBF5';
          const scale = docked ? (isActive ? 0.84 : SIDE_SCALE) : 1;
          // The box stays centred on the same point, so snapping its size is invisible.
          const box = docked && !isActive ? SIDE_BOX : { w: 21, h: 13 };
          return (
            <motion.div
              key={k}
              className="absolute left-1/2 top-1/2 h-0 w-0"
              initial={false}
              animate={{ rotate: angle }}
              transition={SPIN_T}
            >
              <motion.div
                className="absolute"
                style={{ left: `${-box.w / 2}cqw`, top: `${-box.h / 2}cqw`, width: `${box.w}cqw`, height: `${box.h}cqw` }}
                initial={false}
                animate={{ rotate: -angle, x: docked ? SLOT_RADIUS[slot] : SLOT_RADIUS.center }}
                transition={SPIN_T}
              >
                <button
                  type="button"
                  className="pointer-events-auto absolute inset-0 flex items-center justify-center whitespace-nowrap rounded-xl"
                  aria-label={docked ? c.name : `Enter as ${c.name}`}
                  aria-pressed={docked ? isActive : undefined}
                  title={c.name}
                  onClick={() => onPick(k)}
                  onMouseEnter={() => onHover(k)}
                  onMouseLeave={() => onHover(null)}
                  onFocus={() => onHover(k)}
                  onBlur={() => onHover(null)}
                >
                  <motion.span
                    style={{ ...FONT.display, fontSize: '6.5cqw', lineHeight: 1 }}
                    initial={false}
                    animate={{ scale, color }}
                    transition={{ duration: 0.45 }}
                  >
                    {c.short}
                  </motion.span>
                  <motion.span
                    aria-hidden="true"
                    className="pointer-events-none absolute inset-x-0 text-center"
                    style={{ ...FONT.body, top: 'calc(50% + 4.3cqw)', fontSize: '2.83cqw', color: '#B4BED3' }}
                    initial={false}
                    animate={{ opacity: docked ? 0 : 1 }}
                    transition={{ duration: 0.3 }}
                  >
                    {c.name}
                  </motion.span>
                </button>
              </motion.div>
            </motion.div>
          );
        })}
      </motion.div>
    </div>
  );
}

/* ============================================================================
 * Shared element 2: the logo  (layoutId="logo"), always top-centre
 * ========================================================================= */

function PolarisLogo({ view, accent, onClick }: { view: View; accent: string; onClick: () => void }) {
  const docked = view === 'dash';
  return (
    <div className="pointer-events-none fixed inset-x-0 top-0 z-50 flex justify-center md:pl-[var(--pl-side)]">
      {/* layout="position": font size and Y are driven by `animate`, so the layout pass only has to carry the
          position. A full `layout` would scale-correct the text and fight the font-size tween. */}
      <motion.div
        layout="position"
        layoutId="logo"
        initial={false}
        animate={{ fontSize: docked ? 18 : 30, y: docked ? 9 : 26 }}
        transition={{ layout: LAYOUT_T, fontSize: LAYOUT_T, y: LAYOUT_T }}
        className={`pointer-events-auto flex select-none items-center gap-[0.4em] ${docked ? 'cursor-pointer' : ''}`}
        {...(docked
          ? {
              role: 'button',
              tabIndex: 0,
              'aria-label': 'Back to role selection',
              onClick,
              onKeyDown: (e: ReactKeyboardEvent) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  onClick();
                }
              },
            }
          : {})}
      >
        <PolarisStar className="h-[0.95em] w-[0.95em]" color={docked ? accent : '#E6EBF5'} />
        <span style={{ ...FONT.display, letterSpacing: '-0.02em', lineHeight: 1 }}>Polaris</span>
      </motion.div>
    </div>
  );
}

/* ============================================================================
 * Landing view chrome
 * ========================================================================= */

interface LandingViewProps {
  hoverRole: RoleKey | null;
  lastHover: RoleKey;
  onHover: (k: RoleKey | null) => void;
  onEnter: (k: RoleKey) => void;
}

const CARD_POS: Record<RoleKey, string> = {
  ENG: 'left-[3%] top-[30%]',
  MGR: 'right-[3%] top-[30%]',
  LEAD: 'bottom-7 left-1/2 -ml-[190px]',
};

function LandingView({ hoverRole, lastHover, onHover, onEnter }: LandingViewProps) {
  const pr = ROLES[lastHover];
  return (
    <motion.div className="pointer-events-none absolute inset-0 z-[45]" {...FADE}>
      <div className="absolute inset-x-0 top-[72px] text-center text-[13px] text-[#9AA6BF]">
        Your North Star for log analytics
      </div>
      <div className="absolute right-8 top-8 hidden text-[13px] text-[#8D99B3] md:block">
        National AI Hackathon 2026 by SchubKI
      </div>

      <div className="pointer-events-auto absolute bottom-7 left-8 flex flex-col gap-2.5">
        <div className="text-[13px] text-[#9AA6BF]">Hover a role to preview it. Select one to enter.</div>
        <div className="flex flex-wrap gap-2">
          {ROLE_KEYS.map((k) => {
            const c = ROLES[k];
            const on = hoverRole === k;
            return (
              <button
                key={k}
                type="button"
                onClick={() => onEnter(k)}
                onMouseEnter={() => onHover(k)}
                onMouseLeave={() => onHover(null)}
                onFocus={() => onHover(k)}
                onBlur={() => onHover(null)}
                className="min-h-11 rounded-full border px-4 font-medium text-[#E6EBF5] transition-colors duration-300"
                style={{
                  borderColor: on ? c.accent : 'rgba(255,255,255,0.16)',
                  background: on ? c.soft : 'rgba(8,12,24,0.7)',
                }}
              >
                {c.name}
              </button>
            );
          })}
        </div>
      </div>

      <motion.div
        className={`absolute z-[7] hidden w-[380px] max-w-[calc(100vw-48px)] md:block ${CARD_POS[lastHover]}`}
        initial={false}
        animate={{ opacity: hoverRole ? 1 : 0, y: hoverRole ? 0 : 10 }}
        transition={{ duration: 0.35 }}
      >
        <div
          className="rounded-[18px] border bg-[rgba(8,12,24,0.8)] px-5 pb-5 pt-[18px] backdrop-blur-[18px]"
          style={{ borderColor: pr.line, boxShadow: `0 18px 60px -14px ${pr.glow}` }}
        >
          <div style={{ ...FONT.display, fontSize: 22, color: pr.text }}>{pr.name}</div>
          <div className="mt-3 flex flex-wrap items-baseline gap-2.5">
            <span style={{ ...FONT.display, fontSize: 34, lineHeight: 1 }}>{pr.kpiValue}</span>
            <span className="text-[13px] text-[#9AA6BF]">{pr.kpiLabel}</span>
          </div>
          <div className="mt-3.5 flex flex-col gap-[9px]">
            {pr.features.map((f) => (
              <div key={f} className="flex items-start gap-2.5 text-[13.5px] text-[#D5DCEB]">
                <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-[1px]" style={{ background: pr.accent }} />
                <span>{f}</span>
              </div>
            ))}
          </div>
        </div>
      </motion.div>
    </motion.div>
  );
}

/* ============================================================================
 * Session state: threads -> question history (accordion tree)
 * ========================================================================= */

/**
 * `enabled` gates the network call (off while the landing view is showing). The effect below is the only place an
 * investigation is fetched: it re-runs when the role or the current question changes, and `runKey` forces a re-run
 * when the same question is selected again. Cleanup aborts the previous request, so stale responses are dropped.
 */
function useSession(role: RoleKey, enabled: boolean) {
  const [threads, setThreads] = useState<Thread[]>(INITIAL_THREADS);
  const [openThreadId, setOpenThreadId] = useState<string | null>('t1');
  const [activeThreadId, setActiveThreadId] = useState('t1');
  const [currentQ, setCurrentQ] = useState<Question>(INITIAL_THREADS[0].questions[0]);
  const [runKey, setRunKey] = useState(0);
  // The settled outcome is tagged with the request it answers; anything that doesn't match the current key is "loading".
  const [settled, setSettled] = useState<{ key: string; result: InvestigationResult | null; error: string | null }>({
    key: '',
    result: null,
    error: null,
  });
  const questionSeq = useRef(0);
  const requestKey = `${role}|${currentQ.id}|${runKey}`;

  useEffect(() => {
    if (!enabled) return;
    const ctrl = new AbortController();
    postInvestigation({ role, question: currentQ.text }, ctrl.signal)
      .then((result) => setSettled({ key: requestKey, result, error: null }))
      .catch((reason: unknown) => {
        if (ctrl.signal.aborted) return;
        setSettled({ key: requestKey, result: null, error: reason instanceof Error ? reason.message : 'Investigation failed.' });
      });
    return () => ctrl.abort();
  }, [enabled, role, currentQ, requestKey]);

  const current = settled.key === requestKey;
  const running = enabled && !current;
  const investigation = current ? settled.result : null;
  const error = current ? settled.error : null;

  const run = useCallback((q: Question, threadId?: string) => {
    if (threadId) setActiveThreadId(threadId);
    setCurrentQ(q);
    setRunKey((k) => k + 1);
  }, []);

  // Opening a thread also makes it the active one, so the next submit lands in the thread on screen.
  const toggleThread = useCallback((id: string) => {
    setOpenThreadId((cur) => (cur === id ? null : id));
    setActiveThreadId(id);
  }, []);

  const submit = useCallback(
    (text: string) => {
      const q: Question = { id: `q-${++questionSeq.current}`, text };
      setThreads((ts) =>
        ts.map((t) => (t.id === activeThreadId ? { ...t, questions: [q, ...t.questions].slice(0, 12) } : t)),
      );
      setOpenThreadId(activeThreadId);
      run(q);
    },
    [activeThreadId, run],
  );

  return { threads, openThreadId, activeThreadId, currentQ, running, investigation, error, run, toggleThread, submit };
}

/* ---- sidebar: inline column on md+, overlay drawer below md ---- */

const subscribeDesktop = (notify: () => void) => {
  const mq = window.matchMedia(DESKTOP_MQ);
  mq.addEventListener('change', notify);
  return () => mq.removeEventListener('change', notify);
};
const getDesktop = () => window.matchMedia(DESKTOP_MQ).matches;
const getDesktopServer = () => true;

function useSidebar() {
  const isDesktop = useSyncExternalStore(subscribeDesktop, getDesktop, getDesktopServer);
  // Two independent flags: the desktop column defaults open, the mobile drawer defaults closed.
  const [leftOpen, setLeftOpen] = useState(true);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const open = isDesktop ? leftOpen : drawerOpen;
  const setOpen = useCallback(
    (next: boolean) => (isDesktop ? setLeftOpen(next) : setDrawerOpen(next)),
    [isDesktop],
  );
  return { isDesktop, leftOpen, open, setOpen };
}
type Sidebar = ReturnType<typeof useSidebar>
type Session = ReturnType<typeof useSession>;

function ThreadAccordion({
  session,
  theme,
  onNavigate,
}: {
  session: Session;
  theme: Theme;
  /** Called after a question is picked (the mobile drawer uses it to close itself). */
  onNavigate?: () => void;
}) {
  const { threads, openThreadId, activeThreadId, currentQ, run, toggleThread } = session;
  return (
    <nav aria-label="Chat threads" className="flex flex-col gap-1">
      {threads.map((t) => {
        const open = openThreadId === t.id;
        const isActive = activeThreadId === t.id;
        return (
          <div key={t.id}>
            <button
              type="button"
              aria-expanded={open}
              aria-controls={`pl-thread-${t.id}`}
              onClick={() => toggleThread(t.id)}
              className="flex w-full items-center gap-2.5 rounded-[10px] border px-3 py-2.5 text-left transition-colors duration-200 hover:bg-white/5"
              style={{
                background: isActive ? theme.soft : 'transparent',
                borderColor: isActive ? theme.line : 'transparent',
              }}
            >
              <MessageSquare size={15} strokeWidth={1.8} className="shrink-0 text-[#8D99B3]" aria-hidden="true" />
              <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                <span className="truncate text-[13.5px] font-medium">{t.title}</span>
                <span className="text-xs text-[#8D99B3]">{t.when}</span>
              </span>
              <motion.span initial={false} animate={{ rotate: open ? 180 : 0 }} transition={{ duration: 0.25 }}>
                <ChevronDown size={16} strokeWidth={2} className="text-[#8D99B3]" aria-hidden="true" />
              </motion.span>
            </button>

            <AnimatePresence initial={false}>
              {open && (
                <motion.div
                  id={`pl-thread-${t.id}`}
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: 'auto', opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.28, ease: [0.3, 0.8, 0.3, 1] }}
                  className="overflow-hidden"
                >
                  <div className="ml-[21px] mt-1 border-l border-white/10 pb-1 pl-2.5">
                    <div className="px-2 pb-1 pt-1 text-[11px] uppercase tracking-[0.08em] text-[#8D99B3]">
                      Question history
                    </div>
                    <ul className="flex flex-col gap-1">
                      {t.questions.map((q) => {
                        const on = isActive && q.id === currentQ.id;
                        return (
                          <li key={q.id}>
                            <button
                              type="button"
                              onClick={() => {
                                run(q, t.id);
                                onNavigate?.();
                              }}
                              aria-current={on ? 'true' : undefined}
                              className="w-full rounded-[9px] border px-2.5 py-2 text-left text-[13px] leading-[1.45] text-[#D5DCEB] transition-colors duration-200 hover:bg-white/5"
                              style={{
                                background: on ? theme.soft : 'transparent',
                                borderColor: on ? theme.line : 'rgba(255,255,255,0.06)',
                              }}
                            >
                              {q.text}
                            </button>
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        );
      })}
    </nav>
  );
}

/* ============================================================================
 * Small dashboard pieces
 * ========================================================================= */

const SQL_RE =
  /('[^']*')|\b(SELECT|FROM|WHERE|AND|BETWEEN|GROUP BY|ORDER BY|DESC|AS|IN|LIKE|LIMIT|count)\b|\b(\d+)\b/gi;

function Sql({ code }: { code: string }) {
  const out: ReactNode[] = [];
  let last = 0;
  let i = 0;
  for (const m of code.matchAll(SQL_RE)) {
    const idx = m.index ?? 0;
    if (idx > last) out.push(<Fragment key={`t${i++}`}>{code.slice(last, idx)}</Fragment>);
    const cls = m[1] ? 'text-[#7FE0B5]' : m[2] ? 'font-medium text-[#C3B5FF]' : 'text-[#FFB38A]';
    out.push(
      <span key={`m${i++}`} className={cls}>
        {m[0]}
      </span>,
    );
    last = idx + m[0].length;
  }
  out.push(<Fragment key={`t${i}`}>{code.slice(last)}</Fragment>);
  return <>{out}</>;
}

interface CiteProps {
  n: number;
  active: boolean;
  theme: Theme;
  onCite: (n: number | null) => void;
}
function Cite({ n, active, theme, onCite }: CiteProps) {
  return (
    <button
      type="button"
      aria-label={`Evidence row ${n}`}
      onClick={() => {
        onCite(n);
        document.getElementById(`evidence-row-${n}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }}
      onMouseEnter={() => onCite(n)}
      onMouseLeave={() => onCite(null)}
      onFocus={() => onCite(n)}
      onBlur={() => onCite(null)}
      className="ml-1 rounded-[5px] border px-1.5 py-px text-[11px] transition-colors duration-200"
      style={{
        ...FONT.mono,
        background: active ? theme.accent : 'transparent',
        color: active ? theme.ink : theme.text,
        borderColor: active ? theme.accent : theme.line,
      }}
    >
      [{n}]
    </button>
  );
}

const CARD = 'rounded-2xl border border-white/[0.08] bg-[rgba(15,23,42,0.7)]';

/* ---- role-scoped dashboard data (chart, components, alerts) ---- */

interface RoleData<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
}

/** Fetches `fetcher(role)` and refetches when the role changes. `fetcher` must be a stable (module-level) function. */
function useRoleData<T>(role: RoleKey, fetcher: (role: RoleKey, signal: AbortSignal) => Promise<T>): RoleData<T> {
  const [settled, setSettled] = useState<{ role: RoleKey | null; data: T | null; error: string | null }>({
    role: null,
    data: null,
    error: null,
  });
  useEffect(() => {
    const ctrl = new AbortController();
    fetcher(role, ctrl.signal)
      .then((data) => setSettled({ role, data, error: null }))
      .catch((reason: unknown) => {
        if (ctrl.signal.aborted) return;
        setSettled({ role, data: null, error: reason instanceof Error ? reason.message : 'Could not load data.' });
      });
    return () => ctrl.abort();
  }, [role, fetcher]);
  const current = settled.role === role;
  return { data: current ? settled.data : null, loading: !current, error: current ? settled.error : null };
}

/** Timestamps arrive zone-less ("2017-05-16T03:00:00") and are treated as UTC everywhere, so labels never shift. */
const tsMs = (ts: string): number => Date.parse(ts.endsWith('Z') ? ts : `${ts}Z`);
const tsLabel = (ms: number): string => new Date(ms).toISOString().slice(5, 16).replace('T', ' ');

/** Smallest "nice" step (1, 1.5, 2, 2.5, 3, 4, 5 x 10^k) whose CHART_TICKS steps cover `max`. */
const niceStep = (max: number): number => {
  const raw = max / CHART_TICKS;
  if (!(raw > 0)) return 1;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const norm = raw / mag;
  return ([1, 1.5, 2, 2.5, 3, 4, 5, 10].find((s) => s >= norm - 1e-9) ?? 10) * mag;
};

const fmtVal = (v: number, unit: string): string =>
  unit === 's' ? `${v < 1 ? v.toFixed(3) : v.toFixed(2)} s` : `${Math.round(v).toLocaleString('en-US')} ${unit}`;

function ChartFrame({ title, children, footer }: { title: string; children: ReactNode; footer?: ReactNode }) {
  return (
    <div className={`min-w-0 flex-[2_1_460px] px-[18px] pb-3.5 pt-4 ${CARD}`}>
      <h2 className="m-0 text-base" style={FONT.display}>
        {title}
      </h2>
      {children}
      {footer && <div className="mt-1.5 text-xs leading-normal text-[#8D99B3]">{footer}</div>}
    </div>
  );
}

function ActivityChart({ theme, data, loading, error }: { theme: Theme } & RoleData<Timeseries>) {
  const [hoverPt, setHoverPt] = useState<number | null>(null);

  const geo = useMemo(() => {
    if (!data || data.points.length === 0) return null;
    const pts = data.points.map((p) => ({ ...p, t: tsMs(p.ts) }));
    const stepMs = Math.max(1, data.step_minutes) * 60_000;
    const t0 = pts[0].t;
    const t1 = pts[pts.length - 1].t + stepMs;
    const step = niceStep(Math.max(data.peak ?? 0, data.threshold ?? 0, data.baseline ?? 0) * 1.08);
    const yMax = step * CHART_TICKS;
    const xOf = (t: number) => CHART_X0 + (CHART_W * (t - t0)) / (t1 - t0);
    const yOf = (v: number) => CHART_Y0 + CHART_H * (1 - v / yMax);
    const halfW = (CHART_W * stepMs) / (t1 - t0) / 2;
    const xy = pts.map((p) => ({ ...p, x: xOf(p.t + stepMs / 2), y: yOf(p.val) }));

    // A jump larger than one bucket is a gap in the corpus: break the line there instead of bridging it.
    const runs: (typeof xy)[] = [];
    xy.forEach((p, i) => {
      if (i === 0 || p.t - xy[i - 1].t > stepMs) runs.push([p]);
      else runs[runs.length - 1].push(p);
    });
    const base = CHART_Y0 + CHART_H;
    const path = (r: typeof xy) => r.map((p, i) => `${i === 0 ? 'M' : 'L'}${f1(p.x)} ${f1(p.y)}`).join(' ');
    const line = runs.map(path).join(' ');
    const area = runs
      .map((r) => `${path(r)} L${f1(r[r.length - 1].x)} ${base} L${f1(r[0].x)} ${base} Z`)
      .join(' ');
    const gaps = runs.slice(1).map((r, i) => {
      const prev = runs[i][runs[i].length - 1];
      return { x: prev.x + halfW, w: r[0].x - halfW - (prev.x + halfW) };
    });
    // Consecutive flagged buckets merge into one band.
    const bands: { x: number; w: number }[] = [];
    xy.forEach((p, i) => {
      if (!p.anomaly) return;
      const contiguous = i > 0 && xy[i - 1].anomaly && p.t - xy[i - 1].t <= stepMs;
      if (contiguous) bands[bands.length - 1].w = p.x + halfW - bands[bands.length - 1].x;
      else bands.push({ x: p.x - halfW, w: 2 * halfW });
    });
    const xTicks = Array.from({ length: 5 }, (_, i) => {
      const t = t0 + ((t1 - t0) * i) / 4;
      const anchor: 'start' | 'end' | 'middle' = i === 0 ? 'start' : i === 4 ? 'end' : 'middle';
      return { x: xOf(t), label: tsLabel(t), anchor };
    });
    const yTicks = Array.from({ length: CHART_TICKS + 1 }, (_, i) => ({ v: step * i, y: yOf(step * i) }));
    return { xy, line, area, gaps, bands, xTicks, yTicks, baseY: data.baseline != null ? yOf(data.baseline) : null };
  }, [data]);

  if (loading) {
    return (
      <ChartFrame title="Loading activity...">
        <div className="pl-pulse mt-3 h-[190px] rounded-[10px] bg-white/[0.05]" aria-busy="true" aria-label="Loading chart" />
      </ChartFrame>
    );
  }
  if (error || !data) {
    return (
      <ChartFrame title="Activity">
        <div role="alert" className="py-6 text-[12.5px] text-[#FF5C8A]">{error ?? 'Chart data unavailable.'}</div>
      </ChartFrame>
    );
  }

  const unitLabel = data.unit === 's' ? 'p95' : data.unit;

  if (!geo) {
    // Manager: the view has no time column, so the card shows one corpus-wide value per component.
    return (
      <ChartFrame title={data.title} footer={data.note}>
        {data.table.length === 0 ? (
          <div className="py-6 text-[12.5px] text-[#8D99B3]">No data returned for this role.</div>
        ) : (
          <table className="mt-3 w-full border-collapse text-[12.5px]">
            <thead>
              <tr className="text-left text-xs text-[#8D99B3]">
                <th className="py-1.5 pr-3 font-normal">Component</th>
                <th className="py-1.5 pr-3 font-normal">{unitLabel}</th>
                <th className="py-1.5 font-normal">Source</th>
              </tr>
            </thead>
            <tbody>
              {data.table.map((r) => (
                <tr key={r.name} className="border-t border-white/[0.08]">
                  <td className="py-2 pr-3 text-[#D5DCEB]" style={FONT.mono}>{r.name}</td>
                  <td className="py-2 pr-3" style={{ ...FONT.mono, color: r.val != null ? theme.text : '#8D99B3' }}>
                    {r.val != null ? fmtVal(r.val, data.unit) : '—'}
                  </td>
                  <td className="py-2 text-[#9AA6BF]">{r.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </ChartFrame>
    );
  }

  const p = hoverPt !== null ? geo.xy[hoverPt] : null;
  const tip = p && {
    left: `${((p.x / CHART_VB_W) * 100).toFixed(2)}%`,
    dotTop: `${((p.y / CHART_VB_H) * 100).toFixed(2)}%`,
    right: p.x < CHART_VB_W * 0.55,
    title: `${tsLabel(p.t)}: ${fmtVal(p.val, data.unit)}${data.unit === 's' ? ' p95' : ''}`,
    ratio: data.baseline ? `${(p.val / data.baseline).toFixed(2)}× baseline` : '',
    note: p.anomaly
      ? `Above the baseline + 3×MAD threshold${data.threshold != null ? ` (${fmtVal(data.threshold, data.unit)})` : ''}.`
      : 'Within the normal range.',
  };
  const peakPt = geo.xy.reduce((a, b) => (b.val > a.val ? b : a));
  const flagged = geo.xy.filter((q) => q.anomaly).length;
  const ariaLabel =
    `${data.title}, ${geo.xy.length} points from ${tsLabel(geo.xy[0].t)} to ${tsLabel(geo.xy[geo.xy.length - 1].t)} UTC.` +
    (data.baseline != null ? ` Baseline ${fmtVal(data.baseline, data.unit)}.` : '') +
    ` Peak ${fmtVal(peakPt.val, data.unit)} at ${tsLabel(peakPt.t)}. ${flagged} point${flagged === 1 ? '' : 's'} flagged as anomalous.`;

  const onMove = (e: ReactMouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const x = ((e.clientX - rect.left) / rect.width) * CHART_VB_W;
    let best = 0;
    geo.xy.forEach((q, i) => {
      if (Math.abs(q.x - x) < Math.abs(geo.xy[best].x - x)) best = i;
    });
    setHoverPt(best);
  };

  return (
    <ChartFrame title={data.title} footer={data.note}>
      <div className="mt-1 flex flex-wrap gap-3.5 text-xs text-[#9AA6BF]">
        <span className="flex items-center gap-1.5">
          <span className="h-0.5 w-3.5 transition-colors duration-500" style={{ background: theme.accent }} />
          {unitLabel}
        </span>
        {data.baseline != null && (
          <span className="flex items-center gap-1.5">
            <span className="h-0 w-3.5 border-t-2 border-dashed border-[#8D99B3]" />
            baseline {fmtVal(data.baseline, data.unit)}
          </span>
        )}
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-3 border border-[#FF5C8A] bg-[rgba(255,92,138,0.28)]" />
          anomaly ({flagged})
        </span>
        {geo.gaps.length > 0 && (
          <span className="flex items-center gap-1.5">
            <span className="h-2.5 w-3 bg-white/[0.08]" />
            no logs
          </span>
        )}
      </div>

      <div className="relative mt-2" onMouseMove={onMove} onMouseLeave={() => setHoverPt(null)}>
        <svg viewBox={`0 0 ${CHART_VB_W} ${CHART_VB_H}`} width="100%" className="block h-auto" role="img" aria-label={ariaLabel}>
          {geo.gaps.map((g) => (
            <rect key={`g${g.x}`} x={g.x} y={CHART_Y0} width={Math.max(0, g.w)} height={CHART_H} fill="rgba(255,255,255,0.04)" />
          ))}
          {geo.bands.map((b) => (
            <rect
              key={`b${b.x}`}
              x={b.x}
              y={CHART_Y0}
              width={b.w}
              height={CHART_H}
              fill="rgba(255,92,138,0.12)"
              stroke="rgba(255,92,138,0.5)"
              strokeDasharray="3 4"
            />
          ))}
          <g stroke="rgba(255,255,255,0.08)">
            {geo.yTicks.map((t) => (
              <line key={t.v} x1={CHART_X0} y1={t.y} x2={CHART_X0 + CHART_W} y2={t.y} />
            ))}
          </g>
          {geo.baseY !== null && (
            <line x1={CHART_X0} y1={geo.baseY} x2={CHART_X0 + CHART_W} y2={geo.baseY} stroke="#8D99B3" strokeWidth="1.2" strokeDasharray="5 5" />
          )}
          <g fontFamily="'JetBrains Mono', monospace" fontSize="11" fill="#8D99B3" textAnchor="end">
            {geo.yTicks.map((t) => (
              <text key={t.v} x={CHART_X0 - 6} y={t.y + 4}>
                {t.v === 0 ? '0' : data.unit === 's' ? `${+t.v.toFixed(3)} s` : Math.round(t.v).toLocaleString('en-US')}
              </text>
            ))}
          </g>
          <g fontFamily="'JetBrains Mono', monospace" fontSize="11" fill="#8D99B3">
            {geo.xTicks.map((t) => (
              <text key={t.x} x={t.x} y={CHART_Y0 + CHART_H + 22} textAnchor={t.anchor}>
                {t.label}
              </text>
            ))}
          </g>
          <path d={geo.area} fill={theme.soft} style={{ transition: 'fill .5s' }} />
          <path d={geo.line} fill="none" stroke={theme.accent} strokeWidth="2.2" strokeLinejoin="round" style={{ transition: 'stroke .5s' }} />
          {geo.xy
            .filter((q) => q.anomaly)
            .map((q) => (
              <circle key={q.ts} cx={q.x} cy={q.y} r="3" fill="#FF5C8A" />
            ))}
        </svg>

        {tip && p && (
          <>
            <div className="pointer-events-none absolute bottom-[8%] top-0 w-px bg-white/35" style={{ left: tip.left }} />
            <div
              className="pointer-events-none absolute -ml-[5px] -mt-[5px] h-2.5 w-2.5 rounded-full border-2 border-[#030712]"
              style={{ left: tip.left, top: tip.dotTop, background: p.anomaly ? '#FF5C8A' : theme.accent }}
            />
            <div
              className="pointer-events-none absolute top-[4%] z-[5] w-[236px] rounded-[10px] border bg-[rgba(3,7,18,0.95)] px-3 py-2.5 shadow-[0_14px_40px_-10px_rgba(0,0,0,0.8)]"
              style={{
                left: tip.left,
                transform: tip.right ? 'translateX(12px)' : 'translateX(calc(-100% - 12px))',
                borderColor: p.anomaly ? 'rgba(255,92,138,0.6)' : 'rgba(255,255,255,0.14)',
              }}
            >
              <div className="text-[12.5px] text-[#E6EBF5]" style={FONT.mono}>{tip.title}</div>
              {tip.ratio && (
                <div className="mt-0.5 text-xs font-medium" style={{ color: p.anomaly ? '#FF5C8A' : '#9AA6BF' }}>
                  {tip.ratio}
                </div>
              )}
              <div className="mt-1 text-[12.5px] leading-[1.45] text-[#D5DCEB]">{tip.note}</div>
            </div>
          </>
        )}
      </div>
    </ChartFrame>
  );
}

function ComponentBars({ theme, data, loading, error }: { theme: Theme } & RoleData<ComponentMetric[]>) {
  const [hover, setHover] = useState<string | null>(null);
  const items = data ?? [];
  const errors = items.reduce((sum, c) => sum + c.n, 0);
  const warnings = items.reduce((sum, c) => sum + c.warnings, 0);
  const max = Math.max(1, ...items.map((c) => c.n + c.warnings));
  const note = items.find((c) => c.name === hover)?.note ?? 'Hover a bar to read what it means.';
  const fmt = (n: number) => n.toLocaleString('en-US');
  return (
    <div className={`flex min-w-0 flex-[1_1_300px] flex-col px-[18px] py-4 ${CARD}`}>
      <h2 className="m-0 text-base" style={FONT.display}>Failures by component</h2>
      <div className="mt-0.5 text-[12.5px] text-[#8D99B3]">
        {loading
          ? 'Loading counts...'
          : error
            ? 'Counts unavailable'
            : `${fmt(errors)} ERROR/CRITICAL and ${fmt(warnings)} WARNING lines, full corpus`}
      </div>
      <div className="mt-2 flex gap-3.5 text-xs text-[#9AA6BF]">
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-3 rounded-[2px]" style={{ background: theme.accent }} />
          ERROR
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-3 rounded-[2px]" style={{ background: theme.dim }} />
          WARNING
        </span>
      </div>
      <div className="mt-2.5 flex flex-col gap-1" aria-busy={loading}>
        {loading &&
          Array.from({ length: 3 }, (_, i) => (
            <div key={i} className="grid min-h-[30px] grid-cols-[112px_minmax(0,1fr)_84px] items-center gap-2.5 px-1.5" aria-hidden="true">
              <span className="pl-pulse h-2.5 rounded-[3px] bg-white/[0.07]" />
              <span className="pl-pulse h-2.5 rounded-[3px] bg-white/[0.07]" style={{ width: `${90 - i * 25}%` }} />
              <span className="pl-pulse h-2.5 rounded-[3px] bg-white/[0.07]" />
            </div>
          ))}
        {error && <div role="alert" className="py-2 text-[12.5px] text-[#FF5C8A]">{error}</div>}
        {!loading && !error && items.length === 0 && (
          <div className="py-2 text-[12.5px] text-[#8D99B3]">No components returned for this role.</div>
        )}
        {items.map((c) => {
          const on = hover === c.name;
          return (
            <div
              key={c.name}
              tabIndex={0}
              aria-label={`${c.name}: ${c.n} ERROR/CRITICAL, ${c.warnings} WARNING lines`}
              onMouseEnter={() => setHover(c.name)}
              onMouseLeave={() => setHover(null)}
              onFocus={() => setHover(c.name)}
              onBlur={() => setHover(null)}
              className="grid min-h-[30px] grid-cols-[112px_minmax(0,1fr)_84px] items-center gap-2.5 rounded-lg px-1.5 transition-colors duration-200"
              style={{ background: on ? 'rgba(255,255,255,0.06)' : 'transparent' }}
            >
              <span className="truncate text-xs text-[#D5DCEB]" style={FONT.mono}>{c.name}</span>
              <span className="flex h-2.5 overflow-hidden rounded-[3px] bg-white/[0.07]">
                {c.n > 0 && (
                  <span
                    className="block h-full min-w-[3px] transition-[width] duration-500"
                    style={{ width: `${((c.n / max) * 100).toFixed(2)}%`, background: theme.accent }}
                  />
                )}
                <span
                  className="block h-full transition-[width,opacity] duration-500"
                  style={{ width: `${((c.warnings / max) * 100).toFixed(2)}%`, background: theme.dim, opacity: on ? 1 : 0.8 }}
                />
              </span>
              <span className="text-right text-xs text-[#B4BED3]" style={FONT.mono}>
                {fmt(c.n)} / {fmt(c.warnings)}
              </span>
            </div>
          );
        })}
      </div>
      <div className="mt-auto min-h-[76px] pt-3 text-[12.5px] leading-normal text-[#D5DCEB]">{note}</div>
    </div>
  );
}

/** Evidence table columns: #, Q, timestamp, trace, severity, component, body. */
const EVIDENCE_COLS = 'grid-cols-[28px_34px_156px_120px_60px_112px_minmax(150px,1fr)]';

function SkeletonLines({ n, pulse }: { n: number; pulse: boolean }) {
  return (
    <div className="flex flex-col gap-2.5" aria-hidden="true">
      {Array.from({ length: n }, (_, i) => (
        <span
          key={i}
          className={`${pulse ? 'pl-pulse ' : ''}block h-3 rounded-[4px] bg-white/[0.07]`}
          style={{ width: `${92 - ((i * 17) % 40)}%` }}
        />
      ))}
    </div>
  );
}

function StatSkeletons({ pulse }: { pulse: boolean }) {
  const cls = `${pulse ? 'pl-pulse ' : ''}block rounded-[5px] bg-white/[0.07]`;
  return (
    <>
      {Array.from({ length: 4 }, (_, i) => (
        <div key={i} className="min-w-0 rounded-[14px] border border-white/[0.08] bg-[rgba(15,23,42,0.7)] px-4 py-3.5" aria-hidden="true">
          <span className={`${cls} h-3 w-24`} />
          <span className={`${cls} mt-3 h-7 w-16`} />
          <span className={`${cls} mt-2.5 h-3 w-32`} />
        </div>
      ))}
    </>
  );
}

/* ============================================================================
 * Dashboard view chrome
 * ========================================================================= */

interface DashboardViewProps {
  role: RoleKey;
  session: Session;
  sidebar: Sidebar;
  showStars: boolean;
  onToggleStars: () => void;
}

type Modal = 'alerts' | 'settings' | null;

function DashboardView({ role, session, sidebar, showStars, onToggleStars }: DashboardViewProps) {
  const T = useMemo(() => themeOf(role), [role]);
  // Only the settled investigation is shown: no prototype fallback while running or after an error.
  const content = session.investigation?.content ?? null;
  const evidence = useMemo(() => session.investigation?.evidence ?? [], [session.investigation]);
  const queries = useMemo(() => session.investigation?.queries ?? [], [session.investigation]);
  const components = useRoleData(role, getComponentMetrics);
  const series = useRoleData(role, getTimeseries);
  const alerts = useRoleData(role, getAlerts);
  const { currentQ, running, submit } = session;
  const failed = !running && session.error !== null;
  const { isDesktop, leftOpen, open: sideOpen, setOpen: setSideOpen } = sidebar;

  const [modal, setModal] = useState<Modal>(null);
  const [explain, setExplain] = useState<number | null>(null);
  const [cite, setCite] = useState<number | null>(null);
  const [hoverQ, setHoverQ] = useState<number | null>(null);
  // The query card to highlight: the hovered Q link or card, else the query behind the hovered evidence row.
  const activeQ = hoverQ ?? evidence.find((r) => r.n === cite)?.q ?? null;
  const [input, setInput] = useState('');
  const [chatActive, setChatActive] = useState(false);
  const [chatFocus, setChatFocus] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [isExporting, setIsExporting] = useState(false);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => () => clearTimeout(toastTimer.current), []);

  // Escape dismisses the open modal first, then the mobile sidebar drawer.
  const drawerShown = !isDesktop && sideOpen;
  useEffect(() => {
    if (!modal && !drawerShown) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      if (modal) setModal(null);
      else setSideOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [modal, drawerShown, setSideOpen]);

  const showToast = useCallback((msg: string) => {
    setToast(msg);
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 3600);
  }, []);

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    const q = input.trim();
    if (!q) return;
    setInput('');
    submit(q);
  };

  const handleExportPDF = useCallback(async () => {
    if (isExporting || !content) return;
    setIsExporting(true);
    try {
      const cites = [...new Set([
        ...content.facts.flatMap((finding) => finding.cites),
        ...content.hyps.flatMap((hypothesis) => hypothesis.cites),
      ])];
      // The backend renders from the payload (it keeps no state) and validates every cite against evidence[].n.
      const blob = await postPdfReport({
        role,
        question: currentQ.text,
        window: REPORT_WINDOW,
        citedRows: cites,
        content,
        evidence,
        queries,
      });
      downloadBlob(blob, `polaris-${role.toLowerCase()}-report.pdf`);
      showToast('Report downloaded successfully.');
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'PDF export failed.');
    } finally {
      setIsExporting(false);
    }
  }, [isExporting, role, currentQ, showToast, content, evidence, queries]);

  const asideBox = isDesktop
    ? { width: sideOpen ? SIDEBAR_W : 0, x: 0 }
    : { width: SIDEBAR_W, x: sideOpen ? 0 : -SIDEBAR_W };
  const asideAnimate: TargetAndTransition = sideOpen
    ? { ...asideBox, visibility: 'visible' }
    : { ...asideBox, transitionEnd: { visibility: 'hidden' } };

  const chatLit = chatActive || chatFocus;
  // The dock is centred on the main column (viewport minus sidebar), whose content is capped at 1180px.
  // The title column is the space left of the dock: half the content width, minus half the dock, minus gutters.
  const titleW = `calc(min(100vw - ${leftOpen ? SIDEBAR_W : 0}px, 1180px) / 2 - ${DOCK_HALF}px - 48px)`;
  // Beside the dock when there is room for a readable column, otherwise below it.
  const rowPad = leftOpen ? 'min-[1400px]:pt-6' : 'min-[1024px]:pt-6';
  const titleCol = leftOpen
    ? 'min-[1400px]:w-[var(--pl-title-w)] min-[1400px]:flex-none'
    : 'min-[1024px]:w-[var(--pl-title-w)] min-[1024px]:flex-none';

  return (
    <motion.div className="absolute inset-0 z-10" {...FADE}>
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0"
        style={{ background: `radial-gradient(ellipse 55% 38% at 50% 0%, ${T.soft}, rgba(0,0,0,0) 70%)` }}
      />

      {/* header: Alerts far left, Settings far right. The logo + dock own the centre. */}
      <header className="absolute inset-x-0 top-0 z-30 flex h-14 items-center justify-between border-b border-white/[0.08] bg-[rgba(3,7,18,0.72)] px-3 backdrop-blur-[14px]">
        <button
          type="button"
          onClick={() => setModal('alerts')}
          aria-label="Open alerts"
          className="flex min-h-11 items-center gap-2 rounded-[10px] px-3 transition-colors duration-200 hover:bg-white/[0.07]"
        >
          <Bell size={20} strokeWidth={1.8} aria-hidden="true" />
          <span className="hidden font-medium sm:inline">Alerts</span>
          <span
            className="flex h-5 min-w-5 items-center justify-center rounded-[10px] px-[5px] text-[11px] font-medium transition-colors duration-500"
            style={{ ...FONT.mono, background: T.accent, color: T.ink }}
            aria-label={alerts.loading ? 'Loading alerts' : alerts.error ? 'Alerts unavailable' : `${alerts.data?.length ?? 0} alerts`}
          >
            {alerts.loading ? <span className="pl-pulse">·</span> : alerts.error ? '!' : (alerts.data?.length ?? 0)}
          </span>
        </button>
        <button
          type="button"
          onClick={() => setModal('settings')}
          aria-label="Open settings"
          className="flex min-h-11 items-center gap-2 rounded-[10px] px-3 transition-colors duration-200 hover:bg-white/[0.07]"
        >
          <span className="hidden font-medium sm:inline">Settings</span>
          <SettingsIcon size={20} strokeWidth={1.8} aria-hidden="true" />
        </button>
      </header>

      <div className="absolute inset-x-0 bottom-0 top-14 flex">
        {/* mobile drawer scrim (below md the sidebar is an overlay, not a column) */}
        <AnimatePresence>
          {drawerShown && (
            <motion.button
              key="sidebar-scrim"
              type="button"
              aria-label="Close chat threads"
              onClick={() => setSideOpen(false)}
              className="absolute inset-0 z-[30] border-0 bg-[rgba(3,7,18,0.55)] md:hidden"
              {...FADE}
            />
          )}
        </AnimatePresence>

        {/* left sidebar: accordion tree. md+: inline column that collapses. Below md: fixed-width drawer that
            slides over the content, starting under the dock. `visibility` flips once the slide has finished,
            so the closed sidebar can't be tabbed into. */}
        <motion.aside
          id="pl-sidebar"
          initial={false}
          animate={asideAnimate}
          transition={{ duration: 0.45, ease: [0.3, 0.8, 0.3, 1] }}
          className="absolute bottom-0 left-0 top-[max(0px,calc(var(--pl-dock)_/_2_-_48px))] z-[35] shrink-0 overflow-hidden border-r border-white/[0.08] bg-[rgba(8,12,24,0.96)] backdrop-blur-[10px] md:relative md:bottom-auto md:left-auto md:top-auto md:z-10 md:bg-[rgba(8,12,24,0.6)]"
        >
          <div className="flex h-full flex-col" style={{ width: SIDEBAR_W }}>
            <div className="flex min-h-14 items-center justify-between py-2.5 pl-4 pr-2">
              <h2 className="m-0 text-[15px]" style={FONT.display}>Chat threads</h2>
              <button
                type="button"
                onClick={() => setSideOpen(false)}
                aria-label="Collapse chat threads"
                className="flex h-11 w-11 items-center justify-center rounded-[10px] transition-colors duration-200 hover:bg-white/[0.07]"
              >
                <ChevronsLeft size={18} strokeWidth={2} aria-hidden="true" />
              </button>
            </div>
            <div className="pl-scroll flex-1 overflow-y-auto px-2.5 pb-4 pt-1">
              <ThreadAccordion
                session={session}
                theme={T}
                onNavigate={() => {
                  if (!isDesktop) setSideOpen(false);
                }}
              />
            </div>
          </div>
        </motion.aside>

        {/* main */}
        <div className="relative min-w-0 flex-1">
          {!sideOpen && (
            <button
              type="button"
              onClick={() => setSideOpen(true)}
              aria-label="Open chat threads"
              aria-controls="pl-sidebar"
              aria-expanded={false}
              className="absolute left-0 top-3 z-20 flex h-12 w-[30px] items-center justify-center rounded-r-[10px] border border-l-0 border-white/10 bg-[rgba(8,12,24,0.85)]"
            >
              <ChevronsRight size={16} strokeWidth={2} aria-hidden="true" />
            </button>
          )}

          <div className="pl-scroll absolute inset-0 overflow-y-auto overflow-x-hidden">
            <div className="mx-auto max-w-[1180px] px-6 pb-[150px]">
              <motion.div
                key={role}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.4 }}
              >
                {/* title row: sits beside the dock when there is room, below it otherwise */}
                <div
                  className={`flex flex-col gap-3 pt-[calc(var(--pl-dock)_/_2_-_38px)] sm:flex-row sm:items-start sm:justify-between sm:gap-6 ${rowPad}`}
                  style={{ '--pl-title-w': titleW } as CSSProperties}
                >
                  <div className={`min-w-0 flex-1 ${titleCol}`}>
                    <h1
                      className="m-0 leading-[1.12] tracking-[-0.02em] [text-wrap:balance]"
                      style={{ ...FONT.display, fontSize: 'clamp(20px, 1.7vw, 26px)' }}
                    >
                      {content?.title ??
                        (running ? 'Running investigation…' : failed ? 'Investigation failed' : `${ROLES[role].name} log investigation`)}
                    </h1>
                    <div className="mt-2 text-[13.5px] transition-colors duration-500" style={{ color: T.text }}>
                      {content?.sub ?? `${ROLES[role].name} view of the OpenStack corpus`}
                    </div>
                  </div>
                  <div className="shrink-0 sm:ml-auto sm:text-right">
                    <div className="text-xs text-[#9AA6BF]" style={FONT.mono}>Full corpus, no time filter</div>
                    <div className="mt-1 text-[12.5px] text-[#8D99B3]">OpenStack logs loaded in SereneDB</div>
                  </div>
                </div>

                {/* A. visual dashboard */}
                <section className="mt-6 flex flex-col gap-3.5">
                  <div className="grid grid-cols-[repeat(auto-fit,minmax(min(220px,100%),1fr))] gap-3">
                    {!content ? <StatSkeletons pulse={running} /> : content.stats.map((c, i) => {
                      const on = explain === i;
                      return (
                        <div
                          key={c.label}
                          tabIndex={0}
                          onMouseEnter={() => setExplain(i)}
                          onMouseLeave={() => setExplain(null)}
                          onFocus={() => setExplain(i)}
                          onBlur={() => setExplain(null)}
                          className="relative min-w-0 rounded-[14px] border bg-[rgba(15,23,42,0.7)] px-4 py-3.5 transition-colors duration-[250ms]"
                          style={{ borderColor: on ? T.line : 'rgba(255,255,255,0.08)' }}
                        >
                          <div className="text-[13px] text-[#9AA6BF]">{c.label}</div>
                          <div className="mt-1.5 flex items-baseline gap-1.5">
                            <span
                              className="min-w-0 whitespace-nowrap"
                              style={{
                                ...FONT.display,
                                fontSize: c.value.length > 6 ? 'clamp(20px, 2vw, 26px)' : 32,
                                lineHeight: 1.1,
                              }}
                            >
                              {c.value}
                            </span>
                            <span className="text-sm text-[#B4BED3]">{c.unit}</span>
                          </div>
                          <div className="mt-1 text-[12.5px] text-[#8D99B3]">{c.note}</div>
                          <AnimatePresence>
                            {on && (
                              // The popover is a DOM child of the card, so hovering it keeps the card hovered. The
                              // old 8px visual gap was dead space belonging to neither, which fired mouseleave on the
                              // way down. The gap is now `pt-2` padding inside the popover's own hit box.
                              <motion.div
                                initial={{ opacity: 0, y: -4 }}
                                animate={{ opacity: 1, y: 0 }}
                                exit={{ opacity: 0 }}
                                transition={{ duration: 0.15 }}
                                className="absolute inset-x-0 top-full z-[25] min-w-[220px] pt-2"
                              >
                                <div
                                  className="rounded-[10px] border bg-[rgba(3,7,18,0.96)] px-3 py-2.5 text-[12.5px] leading-normal text-[#D5DCEB] shadow-[0_14px_40px_-10px_rgba(0,0,0,0.8)]"
                                  style={{ borderColor: T.line }}
                                >
                                  {c.explain}
                                </div>
                              </motion.div>
                            )}
                          </AnimatePresence>
                        </div>
                      );
                    })}
                  </div>

                  <div className="flex flex-wrap items-stretch gap-3">
                    <ActivityChart theme={T} {...series} />
                    <ComponentBars theme={T} {...components} />
                  </div>
                </section>

                {/* B. scroll transition */}
                <div
                  className="mt-7 flex h-28 flex-col items-center justify-end gap-0.5 rounded-b-[40px] pb-3.5 transition-[background] duration-500"
                  style={{ background: `linear-gradient(to bottom, rgba(0,0,0,0), ${T.soft})` }}
                >
                  <span className="text-[13px] transition-colors duration-500" style={{ color: T.text }}>
                    Scroll for the analysis
                  </span>
                  <ChevronDown size={20} strokeWidth={2} className="pl-cue" style={{ color: T.text }} aria-hidden="true" />
                </div>

                {/* C. report */}
                <section className={`mt-2 px-[26px] pb-7 pt-6 ${CARD} rounded-[18px]`}>
                  <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
                    <div className="min-w-0 flex-[1_1_360px]">
                      <h2 className="m-0 text-xl" style={FONT.display}>Analysis</h2>
                      <div className="mt-2 text-[13px] text-[#9AA6BF]">You asked</div>
                      <div className="mt-0.5 max-w-[62ch] text-base font-medium [text-wrap:pretty]">{currentQ.text}</div>
                    </div>
                    <div className="flex items-center gap-2 pt-1 text-[13px] text-[#B4BED3]">
                      <span
                        className={`h-2 w-2 rounded-full ${running ? 'pl-pulse' : ''}`}
                        style={{ background: running ? T.accent : '#7FE0B5' }}
                      />
                      <span>
                        {running ? 'Running investigation' : failed ? 'Investigation failed' : `Answered, ${evidence.length} evidence rows below`}
                      </span>
                    </div>
                  </div>

                  {failed && (
                    <div
                      role="alert"
                      className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-[#FF5C8A]/40 bg-[#FF5C8A]/10 px-3 py-2 text-sm text-[#FFC2D1]"
                    >
                      <span>{session.error}</span>
                      <button
                        type="button"
                        onClick={() => session.run(currentQ)}
                        className="flex min-h-9 items-center gap-1.5 rounded-[9px] border border-[#FF5C8A]/50 px-3 text-[13px] font-medium text-[#FFE0E8] transition-colors hover:bg-[#FF5C8A]/15"
                      >
                        <RotateCcw size={14} strokeWidth={2} aria-hidden="true" />
                        Retry
                      </button>
                    </div>
                  )}

                  <div
                    className="mt-[22px] grid grid-cols-[repeat(auto-fit,minmax(min(260px,100%),1fr))] gap-7"
                    aria-busy={running}
                  >
                    <div>
                      <h3 className="m-0 mb-3 text-[15px]" style={FONT.display}>Observed facts</h3>
                      <div className="flex flex-col gap-3.5">
                        {!content ? <SkeletonLines n={3} pulse={running} /> : content.facts.length === 0 ? <p className="m-0 text-[13px] text-[#8D99B3]">No finding passed verification against the evidence.</p> : content.facts.map((f) => (
                          <p key={f.text} className="m-0 leading-[1.6] text-[#D5DCEB] [text-wrap:pretty]">
                            {f.text}
                            {f.cites.map((n) => (
                              <Cite key={n} n={n} active={cite === n} theme={T} onCite={setCite} />
                            ))}
                          </p>
                        ))}
                      </div>
                    </div>
                    <div>
                      <h3 className="m-0 mb-3 text-[15px]" style={FONT.display}>Hypotheses</h3>
                      <div className="flex flex-col gap-3.5">
                        {!content ? <SkeletonLines n={2} pulse={running} /> : content.hyps.length === 0 ? <p className="m-0 text-[13px] text-[#8D99B3]">No hypothesis passed verification against the evidence.</p> : content.hyps.map((h) => (
                          <p key={h.text} className="m-0 leading-[1.6] text-[#D5DCEB] [text-wrap:pretty]">
                            <strong className="font-bold transition-colors duration-500" style={{ color: T.text }}>
                              {h.tag}.
                            </strong>{' '}
                            {h.text}
                            {h.cites.map((n) => (
                              <Cite key={n} n={n} active={cite === n} theme={T} onCite={setCite} />
                            ))}
                          </p>
                        ))}
                      </div>
                    </div>
                    <div>
                      <h3 className="m-0 mb-3 text-[15px]" style={FONT.display}>Recommended action</h3>
                      <div className="flex flex-col gap-3.5">
                        {!content ? <SkeletonLines n={3} pulse={running} /> : content.actions.length === 0 ? <p className="m-0 text-[13px] text-[#8D99B3]">No recommended action.</p> : content.actions.map((a, i) => (
                          <div key={a} className="flex items-start gap-2.5 leading-[1.6] text-[#D5DCEB]">
                            <span
                              className="w-5 shrink-0 pt-px text-[12.5px] transition-colors duration-500"
                              style={{ ...FONT.mono, color: T.text }}
                            >
                              {i + 1}.
                            </span>
                            <span className="[text-wrap:pretty]">{a}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                </section>

                {/* D. evidence */}
                <section className="mt-7">
                  <h2 className="m-0 text-xl" style={FONT.display}>Evidence and transparency</h2>
                  <div className="mt-1 text-[13px] text-[#9AA6BF]">
                    Every claim above links to a row here, and every row to the query that returned it. Hover a citation to find it.
                  </div>
                  <div className="mt-4 flex flex-wrap items-stretch gap-3">
                    <div className={`min-w-0 flex-[1_1_340px] px-[18px] py-4 ${CARD}`}>
                      <div className="flex items-baseline justify-between gap-3">
                        <h3 className="m-0 text-[15px]" style={FONT.display}>Executed SereneDB queries</h3>
                        {queries.length > 0 && (
                          <span className="text-xs text-[#8D99B3]">
                            {queries.length} {queries.length === 1 ? 'query' : 'queries'}
                          </span>
                        )}
                      </div>
                      <div className="mt-3.5 flex flex-col gap-3.5">
                        {running && <SkeletonLines n={4} pulse />}
                        {!running && queries.length === 0 && (
                          <div className="text-[12.5px] text-[#8D99B3]">No queries executed.</div>
                        )}
                        {queries.map((q, i) => {
                          const qn = i + 1;
                          const on = activeQ === qn;
                          return (
                            <div
                              key={`${qn}-${q.title}`}
                              id={`query-card-${qn}`}
                              onMouseEnter={() => setHoverQ(qn)}
                              onMouseLeave={() => setHoverQ(null)}
                              className="-m-1.5 rounded-[12px] p-1.5 transition-[background-color,box-shadow] duration-200"
                              style={{ background: on ? T.soft : 'transparent', boxShadow: on ? `inset 0 0 0 1px ${T.line}` : 'none' }}
                            >
                              <div className="mb-1.5 flex justify-between gap-3 text-xs text-[#9AA6BF]">
                                <span>
                                  <span className="mr-1.5 transition-colors duration-500" style={{ ...FONT.mono, color: T.text }}>
                                    Q{qn}
                                  </span>
                                  {q.title}
                                </span>
                                <span className="shrink-0" style={FONT.mono}>{q.meta}</span>
                              </div>
                              <pre
                                className="pl-scroll m-0 max-h-[260px] overflow-auto rounded-[10px] border border-white/[0.06] bg-black/[0.38] px-3.5 py-3 text-xs leading-[1.65] text-[#D5DCEB]"
                                style={FONT.mono}
                              >
                                <Sql code={q.sql} />
                              </pre>
                            </div>
                          );
                        })}
                      </div>
                    </div>

                    <div className={`min-w-0 flex-[2.2_1_520px] px-[18px] py-4 ${CARD}`}>
                      <div className="flex items-baseline justify-between gap-3">
                        <h3 className="m-0 text-[15px]" style={FONT.display}>Correlated log rows</h3>
                        <span className="text-xs text-[#8D99B3]">
                          {running ? 'Loading rows...' : `${evidence.length} rows from ${queries.length} ${queries.length === 1 ? 'query' : 'queries'}`}
                        </span>
                      </div>
                      <div className="pl-scroll mt-3 overflow-x-auto">
                        <div className="min-w-[740px] text-[11.5px]" style={FONT.mono}>
                          <div className={`grid ${EVIDENCE_COLS} gap-2.5 border-b border-white/10 px-2 py-1.5 text-xs text-[#8D99B3]`}>
                            <span>#</span><span>Q</span><span>Timestamp</span><span>Trace</span><span>Severity</span><span>Component</span><span>Body</span>
                          </div>
                          {running && (
                            <div className="px-2 py-3">
                              <SkeletonLines n={5} pulse />
                            </div>
                          )}
                          {!running && evidence.length === 0 && (
                            <div className="px-2 py-3 text-[12.5px] text-[#8D99B3]" style={FONT.body}>
                              No evidence rows returned.
                            </div>
                          )}
                          {evidence.map((r) => {
                            const on = cite === r.n;
                            const inQuery = hoverQ !== null && r.q === hoverQ;
                            return (
                              <div
                                key={r.n}
                                id={`evidence-row-${r.n}`}
                                onMouseEnter={() => setCite(r.n)}
                                onMouseLeave={() => setCite(null)}
                                className={`grid ${EVIDENCE_COLS} gap-2.5 rounded-lg px-2 py-[9px] leading-normal transition-[background-color,box-shadow] duration-200`}
                                style={{
                                  background: on ? T.mid : inQuery ? T.soft : 'transparent',
                                  boxShadow: on ? `inset 0 0 0 1px ${T.line}` : 'none',
                                }}
                              >
                                <span className="text-[#8D99B3]">{r.n}</span>
                                <span>
                                  {r.q ? (
                                    <button
                                      type="button"
                                      aria-label={`Show query ${r.q}`}
                                      onClick={() =>
                                        document.getElementById(`query-card-${r.q}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
                                      }
                                      onMouseEnter={() => setHoverQ(r.q ?? null)}
                                      onMouseLeave={() => setHoverQ(null)}
                                      onFocus={() => setHoverQ(r.q ?? null)}
                                      onBlur={() => setHoverQ(null)}
                                      className="rounded-[5px] border px-1 text-[11px] transition-colors duration-200 hover:bg-white/[0.07]"
                                      style={{ color: T.text, borderColor: T.line }}
                                    >
                                      Q{r.q}
                                    </button>
                                  ) : (
                                    <span className="text-[#8D99B3]">—</span>
                                  )}
                                </span>
                                <span className="text-[#B4BED3]">{r.ts || '—'}</span>
                                <span className="truncate text-[#B4BED3]" title={r.trace}>{r.trace}</span>
                                <span className="font-medium" style={{ color: SEV_COLOR[r.sev] }}>{r.sev}</span>
                                <span className="truncate text-[#B4BED3]" title={r.comp}>{r.comp}</span>
                                <span className="break-words text-[#E6EBF5]">{r.body}</span>
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    </div>
                  </div>
                </section>

                {/* E. export */}
                <div className="mt-[22px] flex flex-wrap items-center justify-between gap-x-5 gap-y-3">
                  <div className="max-w-[48ch] text-[13px] text-[#9AA6BF]">
                    The PDF includes the analysis, every executed query and every cited row.
                  </div>
                  <button
                    type="button"
                    onClick={handleExportPDF}
                    disabled={isExporting || !content}
                    aria-busy={isExporting}
                    className="flex min-h-12 items-center gap-2.5 rounded-xl px-[22px] text-[15px] font-bold transition-[background-color,color,opacity] duration-500 disabled:cursor-wait disabled:opacity-80"
                    style={{ background: T.accent, color: T.ink, boxShadow: `0 10px 34px -8px ${T.glow}` }}
                  >
                    {isExporting ? (
                      <Loader2 size={18} strokeWidth={2.2} className="animate-spin" aria-hidden="true" />
                    ) : (
                      <Download size={18} strokeWidth={2.2} aria-hidden="true" />
                    )}
                    {isExporting ? 'Preparing report...' : 'Download report as PDF'}
                  </button>
                </div>
              </motion.div>
            </div>
          </div>

          {/* floating chat bar */}
          <div className="absolute bottom-5 left-1/2 z-[15] w-[min(720px,calc(100%-48px))] -translate-x-1/2">
            <form
              onSubmit={onSubmit}
              onMouseEnter={() => setChatActive(true)}
              onMouseLeave={() => setChatActive(false)}
              className="flex items-center gap-2 rounded-full border py-1.5 pl-5 pr-1.5 backdrop-blur-[14px] transition-[opacity,background-color,border-color,box-shadow] duration-300"
              style={{
                background: chatLit ? 'rgba(0,0,0,0.62)' : 'rgba(0,0,0,0.4)',
                borderColor: chatLit ? T.line : 'rgba(255,255,255,0.1)',
                boxShadow: chatLit ? `0 0 0 1px ${T.line}, 0 10px 40px -8px ${T.glow}` : '0 6px 24px -8px rgba(0,0,0,0.6)',
                opacity: chatLit ? 1 : 0.74,
              }}
            >
              <label htmlFor="polaris-q" className="sr-only">Ask a question about the logs</label>
              <input
                id="polaris-q"
                type="text"
                autoComplete="off"
                placeholder="Ask a question about the logs..."
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onFocus={() => setChatFocus(true)}
                onBlur={() => setChatFocus(false)}
                className="h-11 min-w-0 flex-1 border-0 bg-transparent text-[15px] text-[#F2F5FB] outline-none placeholder:text-[#8D99B3]"
              />
              <button
                type="submit"
                className="min-h-11 rounded-full px-5 font-bold transition-colors duration-500"
                style={{ background: T.accent, color: T.ink }}
              >
                Submit
              </button>
            </form>
          </div>

          <AnimatePresence>
            {toast && (
              <motion.div
                role="status"
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: 8 }}
                transition={{ duration: 0.2 }}
                className="absolute bottom-[84px] left-1/2 z-[16] max-w-[calc(100%-48px)] -translate-x-1/2 rounded-[10px] border bg-[rgba(3,7,18,0.95)] px-4 py-2.5 text-[13px] text-[#E6EBF5] shadow-[0_12px_36px_-8px_rgba(0,0,0,0.8)]"
                style={{ borderColor: T.line }}
              >
                {toast}
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>

      {/* modals */}
      <AnimatePresence>
        {modal && (
          <motion.div
            key={modal}
            className="absolute inset-0 z-[70]"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
          >
            <button
              type="button"
              onClick={() => setModal(null)}
              aria-label={modal === 'alerts' ? 'Close alerts' : 'Close settings'}
              className="absolute inset-0 h-full w-full rounded-none border-0 bg-[rgba(3,7,18,0.55)] backdrop-blur-[4px]"
            />
            {modal === 'alerts' ? (
              <div
                role="dialog"
                aria-modal="true"
                aria-label="Alerts"
                className="absolute left-4 top-[68px] max-h-[calc(100%-90px)] w-[min(420px,calc(100%-32px))] overflow-y-auto rounded-[18px] border bg-[rgba(8,12,24,0.96)] px-[18px] pb-3 pt-[18px]"
                style={{ borderColor: T.line, boxShadow: `0 24px 70px -12px ${T.glow}` }}
              >
                <div className="flex items-center justify-between">
                  <h2 className="m-0 text-lg" style={FONT.display}>Alerts</h2>
                  <button
                    type="button"
                    onClick={() => setModal(null)}
                    aria-label="Close alerts"
                    className="flex h-11 w-11 items-center justify-center rounded-[10px] transition-colors hover:bg-white/[0.07]"
                  >
                    <X size={18} strokeWidth={2} aria-hidden="true" />
                  </button>
                </div>
                <div className="mt-1.5 flex flex-col">
                  <div className="pb-2 text-[12.5px] text-[#9AA6BF]">
                    Derived from the full OpenStack corpus with the {ROLES[role].name} database login.
                  </div>
                  {alerts.loading && (
                    <div className="border-t border-white/[0.08] py-3">
                      <SkeletonLines n={3} pulse />
                    </div>
                  )}
                  {alerts.error && (
                    <div role="alert" className="border-t border-white/[0.08] py-3 text-[13px] text-[#FF5C8A]">
                      {alerts.error}
                    </div>
                  )}
                  {!alerts.loading && !alerts.error && (alerts.data ?? []).length === 0 && (
                    <div className="border-t border-white/[0.08] py-3 text-[13px] text-[#9AA6BF]">No alerts for this role.</div>
                  )}
                  {(alerts.data ?? []).map((a, i) => (
                    <div key={`${i}-${a.title}`} className="flex flex-col gap-1.5 border-t border-white/[0.08] py-3">
                      <div className="flex items-baseline justify-between gap-2.5">
                        <span className="font-medium">{a.title}</span>
                        <span className="text-[11.5px]" style={{ ...FONT.mono, color: SEV_COLOR[a.sev] }}>{a.sev}</span>
                      </div>
                      <div className="text-[13px] text-[#9AA6BF]">
                        {a.detail}
                        {a.time && (
                          <>
                            {' '}
                            <span className="text-[11.5px]" style={FONT.mono}>at {a.time}</span>
                          </>
                        )}
                      </div>
                      <div>
                        <button
                          type="button"
                          onClick={() => {
                            setModal(null);
                            setInput(a.q);
                            showToast('Question added to the chat bar. Select Submit to run it.');
                          }}
                          className="min-h-11 rounded-[10px] border border-white/[0.16] px-3 text-[13px] transition-colors hover:bg-white/[0.07]"
                        >
                          Ask Polaris about this
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <div
                role="dialog"
                aria-modal="true"
                aria-label="Settings"
                className="absolute right-4 top-[68px] w-[min(380px,calc(100%-32px))] rounded-[18px] border bg-[rgba(8,12,24,0.96)] p-[18px]"
                style={{ borderColor: T.line, boxShadow: `0 24px 70px -12px ${T.glow}` }}
              >
                <div className="flex items-center justify-between">
                  <h2 className="m-0 text-lg" style={FONT.display}>Settings</h2>
                  <button
                    type="button"
                    onClick={() => setModal(null)}
                    aria-label="Close settings"
                    className="flex h-11 w-11 items-center justify-center rounded-[10px] transition-colors hover:bg-white/[0.07]"
                  >
                    <X size={18} strokeWidth={2} aria-hidden="true" />
                  </button>
                </div>
                <div className="mt-1.5 flex flex-col">
                  {[
                    { label: 'Constellation background', hint: 'A quiet star network behind the interface.', on: showStars, click: onToggleStars },
                    {
                      label: 'Chat threads',
                      hint: 'Show the thread sidebar. On small screens it opens as a drawer.',
                      on: sideOpen,
                      click: () => {
                        setSideOpen(!sideOpen);
                        // The drawer sits under the modal scrim, so close the modal to reveal it.
                        if (!isDesktop && !sideOpen) setModal(null);
                      },
                    },
                  ].map((t) => (
                    <div key={t.label} className="flex items-center justify-between gap-4 border-t border-white/[0.08] py-3">
                      <div>
                        <div className="font-medium">{t.label}</div>
                        <div className="text-[12.5px] text-[#9AA6BF]">{t.hint}</div>
                      </div>
                      <button
                        type="button"
                        role="switch"
                        aria-checked={t.on}
                        aria-label={t.label}
                        onClick={t.click}
                        className="flex h-11 w-[52px] shrink-0 items-center justify-center"
                      >
                        <span
                          className="relative h-6 w-11 rounded-xl border border-white/20 transition-colors duration-[250ms]"
                          style={{ background: t.on ? T.accent : 'rgba(255,255,255,0.12)' }}
                        >
                          <span
                            className="absolute left-0.5 top-0.5 h-[18px] w-[18px] rounded-full bg-white transition-transform duration-[250ms]"
                            style={{ transform: `translateX(${t.on ? 20 : 0}px)` }}
                          />
                        </span>
                      </button>
                    </div>
                  ))}
                </div>
                <div className="mt-1.5 border-t border-white/[0.08] pt-3 text-[12.5px] text-[#9AA6BF]">
                  Data source: OpenStack logs in SereneDB, read-only.
                </div>
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

/* ============================================================================
 * Root
 * ========================================================================= */

export default function PolarisDashboard() {
  const [view, setView] = useState<View>('landing');
  const [role, setRole] = useState<RoleKey>('ENG');
  const [rot, setRot] = useState(0);
  const [hoverRole, setHoverRole] = useState<RoleKey | null>(null);
  const [lastHover, setLastHover] = useState<RoleKey>('ENG');
  const [showStars, setShowStars] = useState(true);
  const session = useSession(role, view === 'dash');
  const sidebar = useSidebar();

  const hoverWheel = useCallback((k: RoleKey | null) => {
    setHoverRole(k);
    if (k) setLastHover(k);
  }, []);

  const enterDash = useCallback(
    (k: RoleKey) => {
      if (view !== 'landing') return;
      setRole(k);
      setRot((r) => r + ROT_FOR[k]);
      setHoverRole(null);
      setView('dash');
    },
    [view],
  );

  const selectRole = useCallback(
    (k: RoleKey) => {
      if (k === role) return;
      setRot((r) => r + (slotOf(k, role) === 'left' ? -120 : 120));
      setRole(k);
    },
    [role],
  );

  const goLanding = useCallback(() => {
    // Snap to the nearest full turn so the dial unwinds the short way, not through many revolutions.
    setRot((r) => Math.round(r / 360) * 360);
    setHoverRole(null);
    setView('landing');
  }, []);

  const accent = ROLES[role].accent;

  return (
    <MotionConfig reducedMotion="user">
      <div
        // min-h-[100dvh] instead of h-screen + a 620px floor: the root can no longer be taller than the visible
        // viewport, so the bottom chat bar is never clipped. The dashboard scrolls inside its own column.
        className="polaris-root relative min-h-[100dvh] w-full overflow-x-hidden bg-[#030712] text-sm leading-normal text-[#E6EBF5]"
        style={
          {
            ...FONT.body,
            '--pl-dock': DOCK_VAR,
            // Horizontal shift for the docked wheel + logo: the open sidebar's width (md+ only, via md:pl-[...]).
            '--pl-side': `${view === 'dash' && sidebar.leftOpen ? SIDEBAR_W : 0}px`,
          } as CSSProperties
        }
      >
        <style dangerouslySetInnerHTML={{ __html: GLOBAL_CSS }} />

        {showStars && (
          <motion.div
            aria-hidden="true"
            className="pointer-events-none absolute inset-0 z-0"
            initial={false}
            animate={{ opacity: view === 'landing' ? 1 : 0.3 }}
            transition={{ duration: 0.8 }}
          >
            <Constellation litRole={view === 'landing' ? hoverRole : null} />
          </motion.div>
        )}

        {/* Shared elements live above the AnimatePresence boundary so they can morph, not remount. */}
        <RoleWheel
          view={view}
          role={role}
          rot={rot}
          hoverRole={hoverRole}
          onHover={hoverWheel}
          onPick={view === 'landing' ? enterDash : selectRole}
        />
        <PolarisLogo view={view} accent={accent} onClick={goLanding} />

        <AnimatePresence mode="wait">
          {view === 'landing' ? (
            <LandingView
              key="landing"
              hoverRole={hoverRole}
              lastHover={lastHover}
              onHover={hoverWheel}
              onEnter={enterDash}
            />
          ) : (
            <DashboardView
              key="dash"
              role={role}
              session={session}
              sidebar={sidebar}
              showStars={showStars}
              onToggleStars={() => setShowStars((v) => !v)}
            />
          )}
        </AnimatePresence>
      </div>
    </MotionConfig>
  );
}
