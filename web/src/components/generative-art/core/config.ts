/**
 * V2 Configuration
 * Band-based temporal composition system
 */

export interface TimingConfig {
  compositionFPS: number;
  renderFPS: number;
}

export interface RingsConfig {
  count: number;
  positions: number[];
}

export interface SpawningConfig {
  speechInterval: number;
  minAmplitudeToCount: number;
}

export interface LineConfig {
  lengthRange: [number, number];
  thicknessRatio: [number, number];
  taperRange: [number, number];
  cornerRadius: number;
}

export interface DotConfig {
  sizeRange: [number, number];
  softnessRange: [number, number];
}

export interface SquareConfig {
  sizeRange: [number, number];
}

export interface ElementsConfig {
  minPerBand: number;
  maxPerBand: number;
  colorVariance: number;
  line: LineConfig;
  dot: DotConfig;
  square: SquareConfig;
}

export interface DiffuseConfig {
  intensity: number;
  radius: number;
}

export interface BloomConfig {
  strength: number;
  radius: number;
  threshold: number;
}

export interface GrainConfig {
  intensity: number;
  size: number;
}

export interface VignetteConfig {
  intensity: number;
  radius: number;
}

export interface EffectsConfig {
  diffuse: DiffuseConfig;
  bloom: BloomConfig;
  chromaticAberration: number;
  grain: GrainConfig;
  vignette: VignetteConfig;
  brightness: number;
}

export interface StateConfig {
  bandCount: [number, number];
  elementsPerBand: number | [number, number];
  migrationSpeed: number;
  spawning: boolean;
  animation?: string;
  desaturate?: boolean;
  palette?: string;
  orientation?: string;
  spawnInterval?: number;
  elementType?: string;
  spawnCount?: number;
  scalePulse?: [number, number, number];
  duration?: number;
}

export interface StatesConfig {
  idle: StateConfig;
  userSpeaking: StateConfig;
  companionSpeaking: StateConfig;
  companionThinking: StateConfig;
  excitement: StateConfig;
}

export interface ShiftRotationConfig {
  minDegrees: number;
  maxDegrees: number;
  direction: 'cw' | 'ccw' | 'random' | 'alternating';
}

export interface IdleConfig {
  fadeStartDelay: number;
  fadePerRing: number;
  breatheCycle: number;
  breatheScale: number;
}

export type ColorPalette = string[];

export interface ColorPalettes {
  [key: string]: ColorPalette;
}

export interface Config {
  timing: TimingConfig;
  rings: RingsConfig;
  spawning: SpawningConfig;
  colorPalettes: ColorPalettes;
  activePalette: string;
  shiftRotation: ShiftRotationConfig;
  idle: IdleConfig;
  elements: ElementsConfig;
  palettes: ColorPalettes;
  effects: EffectsConfig;
  states: StatesConfig;
  amplitudeMappings: {
    lineCount: Record<number, number>;
  };
  richnessMappings: {
    bands: Record<number, number>;
  };
}

export const CONFIG: Config = {
  // === TIMING ===
  timing: {
    compositionFPS: 10,      // Stop-motion update rate (10fps = 100ms steps)
    renderFPS: 60,           // Smooth display rendering
  },

  // === RINGS (Fixed concentric positions) ===
  // Scaled up 1.8x for larger composition (was 0.06-0.36, now 0.11-0.65)
  rings: {
    count: 6,                                        // Total ring positions (0-5)
    positions: [0.11, 0.22, 0.33, 0.44, 0.55, 0.65], // Fixed radii for each ring (scaled up)
  },

  // === SPAWNING (Time-based) ===
  spawning: {
    speechInterval: 50,      // Spawn new band every 50ms - very snappy!
    minAmplitudeToCount: 0.01, // Very low threshold for mic input (after smoothing)
  },

  // === COLOR PALETTES (Ring position -> color, newest to oldest) ===
  colorPalettes: {
    // Warm ember palette - companion default (current)
    ember: [
      '#FFFFFF',  // Ring 0: Pure white (newest)
      '#FFEE88',  // Ring 1: Warm white/yellow
      '#FFCC44',  // Ring 2: Golden yellow
      '#FF9933',  // Ring 3: Orange
      '#DD4422',  // Ring 4: Red-orange
      '#882211',  // Ring 5: Dark red (oldest)
    ],

    // Rose palette - inspired by frame 100 (pink monochrome)
    rose: [
      '#F0E0E8',  // Ring 0: Pale rose-white
      '#D0A0B0',  // Ring 1: Dusty pink
      '#B07090',  // Ring 2: Rose (sampled)
      '#905070',  // Ring 3: Deep rose (sampled)
      '#704060',  // Ring 4: Mauve
      '#503040',  // Ring 5: Dark plum
    ],

    // Ocean palette - cool blues for user
    ocean: [
      '#E8F0F8',  // Ring 0: Ice white
      '#B0D0E8',  // Ring 1: Pale sky
      '#70A8D0',  // Ring 2: Sky blue
      '#4080B0',  // Ring 3: Ocean
      '#285090',  // Ring 4: Deep blue
      '#182848',  // Ring 5: Navy
    ],

    // Daylight palette - yellow to blue transition
    daylight: [
      '#FFFFF0',  // Ring 0: Warm white
      '#F0E860',  // Ring 1: Bright yellow
      '#90C870',  // Ring 2: Yellow-green
      '#50A8A0',  // Ring 3: Teal
      '#4080B0',  // Ring 4: Sky blue
      '#204060',  // Ring 5: Deep blue
    ],

    // Sunset palette - warm to cool transition (frame 220)
    sunset: [
      '#F8F0D0',  // Ring 0: Warm white
      '#E8C060',  // Ring 1: Golden
      '#D08040',  // Ring 2: Orange
      '#C05050',  // Ring 3: Coral (sampled)
      '#904050',  // Ring 4: Dusty rose
      '#503040',  // Ring 5: Deep mauve
    ],

    // Velvet palette - red to purple transition
    velvet: [
      '#F07070',  // Ring 0: Bright coral red
      '#E05080',  // Ring 1: Red-pink
      '#C04090',  // Ring 2: Magenta
      '#9040A0',  // Ring 3: Purple-magenta
      '#6040A0',  // Ring 4: Violet
      '#302060',  // Ring 5: Deep purple
    ],

    // Warm white palette - for user speaking (pure warm whites)
    warmWhite: [
      '#FFFFFF',  // Ring 0: Pure white (newest)
      '#FFF8F0',  // Ring 1: Warm white
      '#FFEEDD',  // Ring 2: Cream
      '#FFE4CC',  // Ring 3: Peach cream
      '#FFDABB',  // Ring 4: Warm beige
      '#FFD0AA',  // Ring 5: Light apricot (oldest)
    ],
  },

  // Default active palette
  activePalette: 'ember',

  // === SHIFT ROTATION ===
  shiftRotation: {
    minDegrees: 5,           // Minimum rotation on shift
    maxDegrees: 15,          // Maximum rotation on shift
    direction: 'random',     // 'cw' | 'ccw' | 'random' | 'alternating'
  },

  // === IDLE BEHAVIOR ===
  idle: {
    fadeStartDelay: 0,       // Start fading immediately
    fadePerRing: 25,         // Snap shrink - 25ms per ring
    breatheCycle: 4000,      // Breathing animation cycle (ms)
    breatheScale: 0.12,      // +/- 12% scale - thicker at max, min feels good
  },

  // === ELEMENTS ===
  // Scaled up 1.8x to match ring scaling
  elements: {
    minPerBand: 8,           // Minimum elements in a band
    maxPerBand: 20,          // Maximum elements in a band
    colorVariance: 0.05,     // Subtle color variance within band (5%)

    // Line properties (scaled up 1.8x)
    line: {
      lengthRange: [0.07, 0.16],       // was [0.04, 0.09]
      thicknessRatio: [0.18, 0.25],    // ratio stays same
      taperRange: [0.05, 0.15],
      cornerRadius: 0.15,
    },

    // Dot properties (scaled up 1.8x)
    dot: {
      sizeRange: [0.027, 0.063],       // was [0.015, 0.035]
      softnessRange: [0.4, 0.6],
    },

    // Square properties (scaled up 1.8x)
    square: {
      sizeRange: [0.036, 0.09],        // was [0.02, 0.05]
    },
  },

  // === COLOR PALETTES ===
  palettes: {
    // Companion: warm, welcoming (pure warm gradient)
    companion: [
      '#FFE566',  // Bright gold (newest)
      '#FFAA33',  // Amber
      '#FF6B35',  // Orange
      '#E63946',  // Red
      '#C53060',  // Deep rose (oldest)
    ],

    // User: warm with cool edge
    user: [
      '#FFCC44',  // Warm yellow (newest)
      '#FF9955',  // Peach orange
      '#DD6677',  // Dusty rose
      '#9966AA',  // Muted purple
      '#5566BB',  // Soft blue (oldest)
    ],
  },

  // === EFFECTS ===
  effects: {
    diffuse: {
      intensity: 0.4,
      radius: 10.0,
    },
    bloom: {
      strength: 0.3,  // Reduced from 0.5 to minimize background darkening
      radius: 0.4,
      threshold: 0.4,  // Higher threshold - only bloom bright elements
    },
    chromaticAberration: 0.3,
    grain: {
      intensity: 0.12,
      size: 2.0,
    },
    vignette: {
      intensity: 0.25,
      radius: 0.8,
    },
    brightness: 0.85,
  },

  // === VOICE STATES ===
  states: {
    idle: {
      bandCount: [1, 2],
      elementsPerBand: 8,
      migrationSpeed: 0.5,    // Multiplier
      spawning: false,
      animation: 'breathe',   // Gentle scale pulse
      desaturate: true,
    },

    userSpeaking: {
      bandCount: [2, 4],
      elementsPerBand: [8, 20],  // Based on amplitude
      migrationSpeed: 1.0,
      spawning: true,
      palette: 'user',
      orientation: 'radial',
    },

    companionSpeaking: {
      bandCount: [2, 5],
      elementsPerBand: [8, 20],
      migrationSpeed: 1.0,
      spawning: true,
      palette: 'companion',
      orientation: 'radial',
    },

    companionThinking: {
      bandCount: [2, 3],
      elementsPerBand: [10, 12],
      migrationSpeed: 0.7,
      spawning: true,
      spawnInterval: 350,     // Slower spawning
      palette: 'companion',
      elementType: 'dot',
      orientation: 'tangential',
    },

    excitement: {
      bandCount: [2, 5],
      elementsPerBand: 20,
      migrationSpeed: 1.0,
      spawning: true,
      spawnCount: 3,          // Rapid spawn count
      spawnInterval: 50,      // Very fast
      scalePulse: [1.0, 1.15, 1.0],
      duration: 500,
    },
  },

  // === AMPLITUDE MAPPINGS ===
  amplitudeMappings: {
    // amplitude -> line count per band
    lineCount: {
      0.0: 5,   // Low amplitude: minimum lines
      0.3: 8,
      0.5: 12,
      0.7: 16,
      1.0: 20,  // High amplitude: maximum lines
    },
  },

  // === RICHNESS MAPPINGS ===
  richnessMappings: {
    // richness -> target band count
    bands: {
      0.0: 1,
      0.2: 2,
      0.4: 3,
      0.6: 4,
      0.8: 5,
      1.0: 6,
    },
  },
};

/**
 * Get interpolated value from mapping
 */
export function getInterpolatedValue(mapping: Record<number, number>, value: number): number {
  const keys = Object.keys(mapping).map(Number).sort((a, b) => a - b);

  if (value <= keys[0]) return mapping[keys[0]];
  if (value >= keys[keys.length - 1]) return mapping[keys[keys.length - 1]];

  for (let i = 0; i < keys.length - 1; i++) {
    if (value >= keys[i] && value < keys[i + 1]) {
      const t = (value - keys[i]) / (keys[i + 1] - keys[i]);
      return mapping[keys[i]] + t * (mapping[keys[i + 1]] - mapping[keys[i]]);
    }
  }

  return mapping[keys[0]];
}

export default CONFIG;
