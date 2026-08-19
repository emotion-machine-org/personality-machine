"use client";

import { motion, useReducedMotion } from "framer-motion";
import { useEffect, useState, useCallback } from "react";

// ---------------------------------------------------------------------------
// Data
// ---------------------------------------------------------------------------

type ColorTokens = {
  container?: string;
  label?: string;
  pillBg: string;
  pillText: string;
};

type PillDef = {
  id: string;
  label: string;
  selected: boolean;
  assembledLabel?: string;
};

type SectionDef = {
  id: string;
  label?: string;
  colors: ColorTokens;
  pills: PillDef[];
  scanPhase: Phase;
};

const NEUTRAL: ColorTokens = {
  pillBg: "#E4E4E4",
  pillText: "oklch(0.25 0 0)",
};

const PROFILE: ColorTokens = {
  container: "oklch(0.93 0.03 20)",
  label: "oklch(0.68 0.08 20)",
  pillBg: "oklch(0.90 0.06 20)",
  pillText: "oklch(0.48 0.17 20)",
};

const MEMORY: ColorTokens = {
  container: "oklch(0.96 0.03 95)",
  label: "oklch(0.65 0.07 95)",
  pillBg: "oklch(0.93 0.06 95)",
  pillText: "oklch(0.43 0.11 95)",
};

const FILES: ColorTokens = {
  pillBg: "oklch(0.93 0.04 250)",
  pillText: "oklch(0.40 0.11 250)",
};

const TOOLS: ColorTokens = {
  pillBg: "oklch(0.93 0.05 305)",
  pillText: "oklch(0.42 0.18 305)",
};

const BEHAVIORS: ColorTokens = {
  container: "oklch(0.96 0.03 142)",
  label: "oklch(0.65 0.05 142)",
  pillBg: "oklch(0.92 0.05 142)",
  pillText: "oklch(0.42 0.12 142)",
};

const SECTIONS: SectionDef[] = [
  {
    id: "core",
    colors: NEUTRAL,
    pills: [{ id: "core-sys", label: "Core System Prompt", selected: true }],
    scanPhase: "scan-core",
  },
  {
    id: "profile",
    label: "Profile Data",
    colors: PROFILE,
    pills: [
      { id: "comp-states", label: "Companion States", selected: true },
      { id: "user-data", label: "User Data", selected: true },
    ],
    scanPhase: "scan-profile",
  },
  {
    id: "memory",
    label: "Memory",
    colors: MEMORY,
    pills: [
      { id: "core-mem", label: "Core Memories", selected: false },
      { id: "scratchpad", label: "Scratchpad", selected: true },
    ],
    scanPhase: "scan-memory",
  },
  {
    id: "files",
    colors: FILES,
    pills: [
      { id: "file1", label: "File 1", selected: false },
      {
        id: "file2",
        label: "File 2",
        selected: true,
        assembledLabel: "File 2 Snippet",
      },
      { id: "dialogue", label: "Dialogue Examples", selected: false },
    ],
    scanPhase: "scan-files",
  },
  {
    id: "tools",
    colors: TOOLS,
    pills: [
      { id: "toolx", label: "Tool X Spec", selected: false },
      { id: "tooly", label: "Tool Y Spec", selected: false },
      {
        id: "toolz",
        label: "Tool Z Spec",
        selected: true,
        assembledLabel: "Tool Z Exec Output",
      },
    ],
    scanPhase: "scan-tools",
  },
  {
    id: "behaviors",
    label: "Behaviors",
    colors: BEHAVIORS,
    pills: [
      { id: "scheduled", label: "Scheduled", selected: false },
      {
        id: "rule-based",
        label: "Rule-based",
        selected: true,
        assembledLabel: "Rule-based*",
      },
      { id: "llm-based", label: "LLM-based", selected: false },
    ],
    scanPhase: "scan-behaviors",
  },
  {
    id: "history",
    colors: NEUTRAL,
    pills: [{ id: "chat-hist", label: "Chat History", selected: true }],
    scanPhase: "scan-history",
  },
];

// ---------------------------------------------------------------------------
// Animation phases
// ---------------------------------------------------------------------------

type Phase =
  | "idle"
  | "scan-core"
  | "scan-profile"
  | "scan-memory"
  | "scan-files"
  | "scan-tools"
  | "scan-behaviors"
  | "scan-history"
  | "transfer"
  | "assembled"
  | "output"
  | "reset";

const PHASE_SEQUENCE: { phase: Phase; duration: number }[] = [
  { phase: "idle", duration: 900 },
  { phase: "scan-core", duration: 380 },
  { phase: "scan-profile", duration: 380 },
  { phase: "scan-memory", duration: 380 },
  { phase: "scan-files", duration: 380 },
  { phase: "scan-tools", duration: 380 },
  { phase: "scan-behaviors", duration: 380 },
  { phase: "scan-history", duration: 380 },
  { phase: "transfer", duration: 800 },
  { phase: "assembled", duration: 300 },
  { phase: "output", duration: 3000 },
  { phase: "reset", duration: 900 },
];

function usePhaseLoop() {
  const [phase, setPhase] = useState<Phase>("idle");

  const run = useCallback(() => {
    let cancelled = false;
    let t: ReturnType<typeof setTimeout>;

    function step(i: number) {
      if (cancelled) return;
      const { phase: p, duration } = PHASE_SEQUENCE[i % PHASE_SEQUENCE.length];
      setPhase(p);
      t = setTimeout(() => step(i + 1), duration);
    }

    step(0);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, []);

  useEffect(() => run(), [run]);

  return phase;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const SCAN_PHASES = PHASE_SEQUENCE.filter((p) => p.phase.startsWith("scan-")).map(
  (p) => p.phase,
);

function isScanOrLater(phase: Phase, target: Phase): boolean {
  const i = SCAN_PHASES.indexOf(target);
  const j = SCAN_PHASES.indexOf(phase);
  if (j >= 0) return j >= i;
  return ["transfer", "assembled", "output"].includes(phase);
}

function isAfterScan(phase: Phase): boolean {
  return ["transfer", "assembled", "output"].includes(phase);
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function Pill({
  label,
  colors,
  dimmed,
  highlighted,
  fullWidth,
}: {
  label: string;
  colors: ColorTokens;
  dimmed: boolean;
  highlighted: boolean;
  fullWidth?: boolean;
}) {
  return (
    <motion.span
      className={`inline-flex items-center justify-center rounded-[4px] px-2 py-1 text-sm font-medium whitespace-nowrap ${fullWidth ? "w-full" : ""}`}
      style={{ backgroundColor: colors.pillBg, color: colors.pillText }}
      animate={{
        opacity: dimmed ? 0.35 : 1,
        scale: highlighted ? 1.03 : 1,
      }}
      transition={{ duration: 0.3 }}
    >
      {label}
    </motion.span>
  );
}

function SectionGroup({
  section,
  phase,
  side,
}: {
  section: SectionDef;
  phase: Phase;
  side: "left" | "right";
}) {
  const isScanning = phase === section.scanPhase;
  const scanned = isScanOrLater(phase, section.scanPhase);
  const postScan = isAfterScan(phase);

  const pills =
    side === "left"
      ? section.pills
      : section.pills.filter((p) => p.selected);

  if (side === "right" && pills.length === 0) return null;

  const dimmedSection = side === "left" && postScan;

  const hasContainer = !!section.colors.container;

  const pillsRow = (
    <div className="flex flex-wrap gap-1">
      {pills.map((pill) => {
        const pillLabel =
          side === "right" && pill.assembledLabel
            ? pill.assembledLabel
            : pill.label;

        const dimmed =
          dimmedSection && !pill.selected
            ? true
            : dimmedSection && pill.selected
              ? false
              : side === "left" && !scanned
                ? true
                : false;

        const highlighted =
          side === "left" && isScanning && pill.selected;

        const isNeutral = section.id === "core" || section.id === "history";

        return (
          <Pill
            key={pill.id}
            label={pillLabel}
            colors={section.colors}
            dimmed={dimmed}
            highlighted={highlighted}
            fullWidth={isNeutral}
          />
        );
      })}
    </div>
  );

  // "Adjust Tone" sub-item for behaviors on the right panel
  const adjustTone =
    side === "right" &&
    section.id === "behaviors" &&
    pills.some((p) => p.id === "rule-based") ? (
      <motion.div
        className="flex items-center gap-1 mt-1"
        initial={{ opacity: 0, y: -4 }}
        animate={
          isAfterScan(phase)
            ? { opacity: 1, y: 0 }
            : { opacity: 0, y: -4 }
        }
        transition={{ duration: 0.4, delay: 0.15 }}
      >
        <div className="w-7 shrink-0" />
        <svg
          width="15"
          height="15"
          viewBox="0 0 15 15"
          fill="none"
          className="shrink-0 opacity-50"
        >
          <path
            d="M3 1 C3 9, 3 10, 12 12"
            stroke={BEHAVIORS.pillText}
            strokeWidth="1.5"
            strokeLinecap="round"
            fill="none"
          />
          <path
            d="M9 9 L12 12 L8.5 12.5"
            stroke={BEHAVIORS.pillText}
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
            fill="none"
          />
        </svg>
        <Pill
          label="Adjust Tone"
          colors={section.colors}
          dimmed={false}
          highlighted={false}
        />
      </motion.div>
    ) : null;

  const pillsWithSub = (
    <>
      {pillsRow}
      {adjustTone}
    </>

  );

  if (!hasContainer) return pillsWithSub;

  return (
    <motion.div
      className="rounded-[4px] p-1 space-y-1"
      style={{ backgroundColor: section.colors.container }}
      animate={{
        boxShadow:
          side === "left" && isScanning
            ? `inset 0 0 0 1.5px ${section.colors.pillText}40`
            : "inset 0 0 0 1.5px transparent",
      }}
      transition={{ duration: 0.25 }}
    >
      {section.label && (
        <span
          className="block text-xs font-medium"
          style={{ color: section.colors.label }}
        >
          {section.label}
        </span>
      )}
      {pillsWithSub}
    </motion.div>
  );
}

function FlowArrow({
  direction,
  visible,
}: {
  direction: "down" | "up";
  visible: boolean;
}) {
  const d = direction === "down" ? "M6 0 L6 18 M2 14 L6 18 L10 14" : "M6 18 L6 0 M2 4 L6 0 L10 4";
  return (
    <motion.svg
      width="12"
      height="18"
      viewBox="0 0 12 18"
      fill="none"
      className="mx-auto"
      animate={{ opacity: visible ? 1 : 0 }}
      transition={{ duration: 0.4 }}
    >
      <path
        d={d}
        stroke="oklch(0.15 0 0)"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </motion.svg>
  );
}

function TransferArrow({ active }: { active: boolean }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-2 shrink-0 self-center">
      <motion.div
        className="overflow-hidden text-xs text-center whitespace-nowrap"
        style={{ color: "oklch(0.15 0 0)" }}
        animate={{ opacity: active ? 1 : 0 }}
        transition={{ duration: 0.5 }}
      >
        <div className="leading-tight">
          Layer Executions
          <br />
          in a single turn
        </div>
      </motion.div>
      <motion.svg
        width="60"
        height="12"
        viewBox="0 0 60 12"
        fill="none"
        animate={{ opacity: active ? 1 : 0 }}
        transition={{ duration: 0.4 }}
      >
        <motion.path
          d="M0 6 L52 6 M48 2 L52 6 L48 10"
          stroke="oklch(0.15 0 0)"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          initial={{ pathLength: 0 }}
          animate={{ pathLength: active ? 1 : 0 }}
          transition={{ duration: 0.8, ease: "easeOut" }}
        />
      </motion.svg>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function ContextEngineDiagram({ className }: { className?: string }) {
  const prefersReduced = useReducedMotion() ?? false;
  const phase = usePhaseLoop();
  const effectivePhase: Phase = prefersReduced ? "output" : phase;

  const showInput =
    effectivePhase !== "reset";
  const showOutput =
    effectivePhase === "assembled" || effectivePhase === "output" || prefersReduced;
  const showTransfer = isAfterScan(effectivePhase) || prefersReduced;
  const showAssembled =
    ["transfer", "assembled", "output"].includes(effectivePhase) ||
    prefersReduced;

  return (
    <div
      className={`w-full max-w-4xl mx-auto p-6 md:p-10 antialiased ${className ?? ""}`}
      style={{
        backgroundColor: "oklch(1.00 0 0)",
        colorScheme: "light",
      }}
    >
      {/* Title */}
      <div className="mb-8">
        <h2
          className="text-2xl md:text-3xl font-normal tracking-tight mb-2"
          style={{ color: "oklch(0.15 0 0)" }}
        >
          Emotion Machine Context Engine
        </h2>
        <p
          className="text-sm md:text-base leading-relaxed max-w-2xl"
          style={{ color: "oklch(0.40 0 0)" }}
        >
          Emotion Machine&rsquo;s context engine dynamically assembles the
          system prompt at every turn from layered relationship context,
          including memory, knowledge, profile/session state, behavior
          injections, and conversation history.
        </p>
      </div>

      {/* 3-column grid: left panel | arrow | right panel */}
      <div className="grid md:grid-cols-[1fr_auto_1fr] gap-y-0 items-start">
        {/* Row 1: User Input above left panel */}
        <motion.div
          className="text-center mb-3"
          animate={{ opacity: showInput ? 1 : 0 }}
          transition={{ duration: 0.4 }}
        >
          <span
            className="text-sm font-medium"
            style={{ color: "oklch(0.15 0 0)" }}
          >
            User Input
          </span>
          <FlowArrow direction="down" visible={showInput} />
        </motion.div>
        <div className="hidden md:block" />
        <div className="hidden md:block" />

        {/* Row 2: Labels */}
        <span
          className="block text-sm mb-2"
          style={{ color: "oklch(0.45 0 0)" }}
        >
          Available Context
        </span>
        <div className="hidden md:block" />
        <span
          className="block text-sm mb-2"
          style={{ color: "oklch(0.45 0 0)" }}
        >
          Assembled Context
        </span>

        {/* Row 3: Panels + Transfer arrow */}
        <div className="min-w-0">
          <div
            className="rounded-[4px] p-2 space-y-2.5"
            style={{ backgroundColor: "oklch(0.97 0 0)" }}
          >
            <div
              className="rounded-[4px] border border-dashed pt-2 pb-2 px-2 space-y-2.5"
              style={{ borderColor: "oklch(0.75 0 0)" }}
            >
              {SECTIONS.map((section) => (
                <SectionGroup
                  key={section.id}
                  section={section}
                  phase={effectivePhase}
                  side="left"
                />
              ))}
            </div>
          </div>
        </div>

        <TransferArrow active={showTransfer} />

        <motion.div
          className="min-w-0"
          animate={{ opacity: showAssembled ? 1 : 0.3 }}
          transition={{ duration: 0.5 }}
        >
          <div
            className="rounded-[4px] p-2 space-y-2.5"
            style={{ backgroundColor: "oklch(0.97 0 0)" }}
          >
            <div
              className="rounded-[4px] border border-dashed pt-2 pb-2 px-2 space-y-2.5"
              style={{ borderColor: "oklch(0.75 0 0)" }}
            >
              {SECTIONS.map((section, i) => (
                <motion.div
                  key={section.id}
                  initial={{ opacity: 0, y: 6 }}
                  animate={
                    showAssembled
                      ? { opacity: 1, y: 0 }
                      : { opacity: 0, y: 6 }
                  }
                  transition={{
                    duration: 0.35,
                    delay: showAssembled ? i * 0.06 : 0,
                    ease: "easeOut",
                  }}
                >
                  <SectionGroup
                    section={section}
                    phase={effectivePhase}
                    side="right"
                  />
                </motion.div>
              ))}
            </div>
          </div>
        </motion.div>

        {/* Row 4: Companion Output below right panel */}
        <div className="hidden md:block" />
        <div className="hidden md:block" />
        <motion.div
          className="text-center mt-3"
          animate={{ opacity: showOutput ? 1 : 0 }}
          transition={{ duration: 0.4 }}
        >
          <FlowArrow direction="down" visible={showOutput} />
          <span
            className="block mt-2 text-sm font-medium"
            style={{ color: "oklch(0.15 0 0)" }}
          >
            Companion Output
          </span>
        </motion.div>
      </div>
    </div>
  );
}
