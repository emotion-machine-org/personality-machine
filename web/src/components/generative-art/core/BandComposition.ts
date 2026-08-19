/**
 * BandComposition - Fixed Ring Time Snapshot System
 *
 * Core concept: Discrete time snapshots at fixed ring positions
 * - Ring 0 (center) = newest, Ring 5 (outer) = oldest
 * - Every ~50ms of speech: existing bands shift outward, new band spawns at center
 * - Amplitude at spawn determines line count
 * - Color determined by ring position (ages as bands shift outward)
 *
 * Additional features:
 * - Outer shapes: circles or squares on outermost rings
 * - Alignment modes: radial (pointing outward) or tangential (perpendicular to radius)
 */

import { createRng, randomRange, randomGaussian } from './random';
import type { ColorPalette, ColorPalettes } from './config';

export type ElementType = 'line' | 'circle' | 'square';
export type AlignmentMode = 'radial' | 'tangential';
export type VoiceState = 'idle' | 'speaking' | 'thinking';
export type Speaker = 'user' | 'companion';
export type VisualizationMode = 'rings' | 'equalizer';

export interface LineElement {
  type: 'line';
  baseAngle: number;
  angleJitter: number;
  rotationJitter: number;
  x: number;
  y: number;
  rotation: number;
  length: number;
  thickness: number;
  taper: number;
  colorVariance: number;
  seed: number;
}

export interface CircleElement {
  type: 'circle';
  baseAngle: number;
  angleJitter: number;
  x: number;
  y: number;
  size: number;
  softness: number;
  colorVariance: number;
  seed: number;
}

export interface SquareElement {
  type: 'square';
  baseAngle: number;
  angleJitter: number;
  x: number;
  y: number;
  size: number;
  rotation: number;
  colorVariance: number;
  seed: number;
}

export type BandElement = LineElement | CircleElement | SquareElement;

export interface BandOptions {
  id?: string;
  ringIndex?: number;
  radius?: number;
  elementCount?: number;
  lineCount?: number;
  elementType?: ElementType;
  alignment?: AlignmentMode;
  spawnAmplitude?: number;
  spawnTime?: number;
  speaker?: Speaker;
  seed?: number;
  spawnPalette?: string;  // Palette name at spawn time
}

export interface RenderLine {
  x: number;
  y: number;
  rotation: number;
  length: number;
  thickness: number;
  taper: number;
  color: [number, number, number];
  seed: number;
}

export interface RenderDisk {
  x: number;
  y: number;
  size: number;
  softness: number;
  color: [number, number, number];
  seed: number;
}

export interface RenderSquare {
  x: number;
  y: number;
  size: number;
  rotation: number;
  color: [number, number, number];
  seed: number;
}

export interface CompositionElements {
  lines: RenderLine[];
  disks: RenderDisk[];
  squares: RenderSquare[];
}

export interface VoiceInput {
  amplitude?: number;
  speaker?: Speaker | null;
  isThinking?: boolean;
}

export interface BandCompositionConfig {
  ringCount?: number;
  ringPositions?: number[];
  speechInterval?: number;
  minAmplitudeToCount?: number;
  minLineCount?: number;
  maxLineCount?: number;
  colorPalettes?: ColorPalettes | null;
  colorGradient?: ColorPalette;
  activePalette?: string;
  outerShapesEnabled?: boolean;
  outerShapesRingCount?: number;
  outerShapeType?: ElementType | 'random';
  defaultAlignment?: AlignmentMode;
  idleFadeStartDelay?: number;
  idleFadePerRing?: number;
  compositionFPS?: number;
}

/**
 * Single band in the composition - represents a time snapshot
 */
class Band {
  id: string;
  ringIndex: number;
  radius: number;
  elementCount: number;
  baseRotation: number;
  elementType: ElementType;
  alignment: AlignmentMode;
  spawnAmplitude: number;
  spawnTime: number;
  speaker: Speaker;
  elements: BandElement[];
  seed: number;
  spawnPalette: string;  // Palette name at spawn time - bands keep their color

  constructor(options: BandOptions) {
    this.id = options.id || `band-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;

    // Ring-based positioning
    this.ringIndex = options.ringIndex || 0;     // 0 = center, 5 = outermost
    this.radius = options.radius || 0.06;        // Computed from ringIndex

    // Element configuration (determined at spawn, immutable)
    this.elementCount = options.elementCount || options.lineCount || 10;
    this.baseRotation = 0;  // Accumulated rotation from shifts

    // Element type and alignment
    this.elementType = options.elementType || 'line';  // 'line' | 'circle' | 'square'
    this.alignment = options.alignment || 'radial';    // 'radial' | 'tangential'

    // Spawn state (record of voice at this moment)
    this.spawnAmplitude = options.spawnAmplitude || 0.5;
    this.spawnTime = options.spawnTime || Date.now();

    this.speaker = options.speaker || 'companion';
    this.elements = [];
    this.seed = options.seed || Math.random() * 10000;
    this.spawnPalette = options.spawnPalette || 'ember';  // Remember palette at spawn

    // Generate initial elements
    this.generateElements();
  }

  /**
   * Generate elements for this band
   */
  generateElements(): void {
    const rng = createRng(this.seed);
    this.elements = [];

    const angleStep = (Math.PI * 2) / this.elementCount;
    const baseAngle = rng() * Math.PI * 2;

    for (let i = 0; i < this.elementCount; i++) {
      const baseElementAngle = baseAngle + i * angleStep;
      const angleJitter = randomGaussian(0, 0.1, rng);
      const angle = baseElementAngle + angleJitter;

      // Position on the band's radius
      const x = Math.cos(angle) * this.radius;
      const y = Math.sin(angle) * this.radius;

      if (this.elementType === 'line') {
        // Rotation based on alignment mode
        const rotationJitter = randomGaussian(0, 0.08, rng);
        let rotation: number;
        if (this.alignment === 'tangential') {
          // Perpendicular to radius (tangent to the ring)
          rotation = angle + Math.PI / 2 + rotationJitter;
        } else {
          // Radial: pointing outward from center
          rotation = angle + rotationJitter;
        }

        // Line properties (scaled up 1.8x)
        const baseLength = randomRange(0.07, 0.16, rng);
        const thickness = baseLength * randomRange(0.18, 0.25, rng);

        this.elements.push({
          type: 'line',
          baseAngle: baseElementAngle,
          angleJitter,
          rotationJitter,
          x, y,
          rotation,
          length: baseLength,
          thickness,
          taper: randomRange(0.05, 0.15, rng),
          colorVariance: 0.05,
          seed: rng() * 1000,
        });
      } else if (this.elementType === 'circle') {
        // Circle/disk element (scaled up 1.8x)
        const size = randomRange(0.022, 0.04, rng);

        this.elements.push({
          type: 'circle',
          baseAngle: baseElementAngle,
          angleJitter,
          x, y,
          size,
          softness: randomRange(0.3, 0.5, rng),
          colorVariance: 0.05,
          seed: rng() * 1000,
        });
      } else if (this.elementType === 'square') {
        // Square element (scaled up 1.8x)
        const size = randomRange(0.027, 0.045, rng);
        // Random rotation for squares
        const rotation = rng() * Math.PI / 2;

        this.elements.push({
          type: 'square',
          baseAngle: baseElementAngle,
          angleJitter,
          x, y,
          size,
          rotation,
          colorVariance: 0.05,
          seed: rng() * 1000,
        });
      }
    }
  }

  /**
   * Shift band to next outer ring with subtle random rotation
   */
  shiftToRing(newRingIndex: number, newRadius: number): void {
    this.ringIndex = newRingIndex;
    this.radius = newRadius;

    // Apply subtle random rotation on each shift (+/-5 to +/-15 degrees)
    const rotationAmount = (5 + Math.random() * 10) * Math.PI / 180;
    const rotationDirection = Math.random() < 0.5 ? 1 : -1;
    this.baseRotation += rotationAmount * rotationDirection;

    // Update element positions with new radius and accumulated rotation
    for (const el of this.elements) {
      const angle = el.baseAngle + el.angleJitter + this.baseRotation;
      el.x = Math.cos(angle) * this.radius;
      el.y = Math.sin(angle) * this.radius;

      // Update rotation for lines
      if (el.type === 'line') {
        if (this.alignment === 'tangential') {
          el.rotation = angle + Math.PI / 2 + el.rotationJitter;
        } else {
          el.rotation = angle + el.rotationJitter;
        }
      }
    }
  }
}

/**
 * Main composition manager - Fixed Ring Model
 */
export class BandComposition {
  // Ring configuration
  ringCount: number;
  ringPositions: number[];

  // Time-based spawning
  speechInterval: number;
  minAmplitudeToCount: number;
  continuousSpeechTime: number;

  // Element count range (amplitude maps to this)
  minLineCount: number;
  maxLineCount: number;

  // Color palettes and active gradient
  colorPalettes: ColorPalettes | null;
  colorGradient: ColorPalette;
  activePaletteName: string;

  // Outer shapes configuration
  outerShapesEnabled: boolean;
  outerShapesRingCount: number;
  outerShapeType: ElementType | 'random';

  // Default alignment for lines
  defaultAlignment: AlignmentMode;

  // Idle configuration
  idleFadeStartDelay: number;
  idleFadePerRing: number;

  // Timing
  compositionFPS: number;
  frameInterval: number;
  lastUpdateTime: number;

  // State
  bands: Band[];
  currentState: VoiceState;
  currentSpeaker: Speaker | null;
  amplitude: number;
  idleStartTime: number | null;

  // Visualization mode
  visualizationMode: VisualizationMode;

  // Equalizer state (for user speaking)
  equalizerBarCount: number;
  equalizerBarHeights: number[];
  equalizerTargetHeights: number[];
  equalizerBaseThickness: number;
  equalizerSpacing: number;
  equalizerLastSmoothTime: number;
  equalizerSmoothedAmplitude: number;  // Continuous smoothing of raw amplitude

  constructor(config: BandCompositionConfig = {}) {
    // Ring configuration
    this.ringCount = config.ringCount || 6;
    this.ringPositions = config.ringPositions || [0.06, 0.12, 0.18, 0.24, 0.30, 0.36];

    // Time-based spawning
    this.speechInterval = config.speechInterval || 1000;  // 1 second
    this.minAmplitudeToCount = config.minAmplitudeToCount || 0.05;
    this.continuousSpeechTime = 0;  // Accumulated speech time

    // Element count range (amplitude maps to this)
    this.minLineCount = config.minLineCount || 5;
    this.maxLineCount = config.maxLineCount || 20;

    // Color palettes and active gradient
    this.colorPalettes = config.colorPalettes || null;
    this.colorGradient = config.colorGradient || [
      '#FFFFFF', '#FFEE88', '#FFCC44', '#FF9933', '#DD4422', '#882211'
    ];
    this.activePaletteName = config.activePalette || 'ember';

    // Outer shapes configuration
    this.outerShapesEnabled = config.outerShapesEnabled ?? false;
    this.outerShapesRingCount = config.outerShapesRingCount ?? 2;
    this.outerShapeType = config.outerShapeType || 'circle';

    // Default alignment for lines
    this.defaultAlignment = config.defaultAlignment || 'radial';

    // Idle configuration
    this.idleFadeStartDelay = config.idleFadeStartDelay ?? 3000;
    this.idleFadePerRing = config.idleFadePerRing ?? 1000;

    // Timing
    this.compositionFPS = config.compositionFPS || 10;
    this.frameInterval = 1000 / this.compositionFPS;
    this.lastUpdateTime = 0;

    // State
    this.bands = [];
    this.currentState = 'idle';
    this.currentSpeaker = null;
    this.amplitude = 0;
    this.idleStartTime = null;

    // Visualization mode - starts as rings (companion default)
    this.visualizationMode = 'rings';

    // Equalizer configuration (for user speaking)
    this.equalizerBarCount = 5;
    this.equalizerBarHeights = new Array(this.equalizerBarCount).fill(0.06);
    this.equalizerTargetHeights = new Array(this.equalizerBarCount).fill(0.06);
    this.equalizerBaseThickness = 0.035;  // Slightly thicker than ring lines
    this.equalizerSpacing = 0.08;  // More space between bars
    this.equalizerLastSmoothTime = 0;
    this.equalizerSmoothedAmplitude = 0;  // Start at 0
  }

  /**
   * Get radius for a ring index
   */
  getRingRadius(ringIndex: number): number {
    if (ringIndex < this.ringPositions.length) {
      return this.ringPositions[ringIndex];
    }
    // Extrapolate if needed (shouldn't happen normally)
    const spacing = this.ringPositions[1] - this.ringPositions[0];
    return this.ringPositions[0] + ringIndex * spacing;
  }

  /**
   * Get color for a ring index
   */
  getRingColor(ringIndex: number): string {
    const index = Math.min(ringIndex, this.colorGradient.length - 1);
    return this.colorGradient[index];
  }

  /**
   * Calculate line count from amplitude
   */
  getLineCountFromAmplitude(amplitude: number): number {
    const normalized = Math.max(0, Math.min(1, amplitude));
    const range = this.maxLineCount - this.minLineCount;
    return Math.round(this.minLineCount + normalized * range);
  }

  /**
   * Shift all existing bands outward by one ring
   * Bands that would shift past max ring are removed
   */
  shiftAllBandsOutward(): void {
    const bandsToKeep: Band[] = [];

    for (const band of this.bands) {
      const newRingIndex = band.ringIndex + 1;

      if (newRingIndex < this.ringCount) {
        // Shift to next ring
        const newRadius = this.getRingRadius(newRingIndex);
        band.shiftToRing(newRingIndex, newRadius);
        bandsToKeep.push(band);
      }
      // Else: band has aged out - don't keep it
    }

    this.bands = bandsToKeep;
  }

  /**
   * Determine element type for a given ring index
   */
  getElementTypeForRing(ringIndex: number): ElementType {
    if (!this.outerShapesEnabled) return 'line';

    // Check if this ring is in the outer shapes zone
    const outerShapeStartRing = this.ringCount - this.outerShapesRingCount;
    if (ringIndex >= outerShapeStartRing) {
      if (this.outerShapeType === 'random') {
        return Math.random() < 0.5 ? 'circle' : 'square';
      }
      return this.outerShapeType as ElementType;
    }
    return 'line';
  }

  /**
   * Spawn a new band at ring 0 (center)
   */
  spawnBandAtCenter(): Band {
    const elementCount = this.getLineCountFromAmplitude(this.amplitude);
    const elementType = this.getElementTypeForRing(0);

    const band = new Band({
      ringIndex: 0,
      radius: this.getRingRadius(0),
      elementCount,
      elementType,
      alignment: this.defaultAlignment,
      spawnAmplitude: this.amplitude,
      speaker: this.currentSpeaker || 'companion',
      seed: Date.now(),
      spawnPalette: this.activePaletteName,  // Capture current palette at spawn
    });

    this.bands.push(band);
    return band;
  }

  /**
   * Main update method - called at composition FPS
   * @param _deltaTime - Time since last update (unused, kept for API compatibility)
   */
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  update(_deltaTime: number): boolean {
    const now = Date.now();

    // Rate limiting
    if (now - this.lastUpdateTime < this.frameInterval) {
      return false;
    }
    this.lastUpdateTime = now;

    // Note: Equalizer smoothing moved to getEqualizerElements() for 60fps updates

    // RINGS MODE: Only spawn/update bands for companion
    if (this.visualizationMode !== 'rings') {
      return true;  // Skip ring logic for equalizer mode
    }

    // SPEAKING STATE: Accumulate speech time and check for spawn (companion only)
    if (this.currentState === 'speaking' && this.currentSpeaker === 'companion' && this.amplitude > this.minAmplitudeToCount) {
      this.continuousSpeechTime += this.frameInterval;  // Use frame interval for consistency
      this.idleStartTime = null;  // Reset idle timer

      // Check if we've accumulated enough speech time to spawn
      if (this.continuousSpeechTime >= this.speechInterval) {
        // Time to spawn a new band!
        this.shiftAllBandsOutward();
        this.spawnBandAtCenter();

        // Reset speech time accumulator (keep remainder for smoother timing)
        this.continuousSpeechTime = this.continuousSpeechTime % this.speechInterval;
      }
    } else {
      // Not actively speaking - handle idle state
      if (this.currentState === 'idle' || this.amplitude <= this.minAmplitudeToCount) {
        if (this.idleStartTime === null) {
          this.idleStartTime = now;
        }

        // Fade outer rings after timeout
        const idleDuration = now - this.idleStartTime;
        if (idleDuration > this.idleFadeStartDelay && this.bands.length > 0) {
          const fadeTime = idleDuration - this.idleFadeStartDelay;
          const ringsToRemove = Math.floor(fadeTime / this.idleFadePerRing);

          if (ringsToRemove > 0) {
            // Remove bands from outermost ring inward
            const maxAllowedRing = this.ringCount - 1 - ringsToRemove;
            this.bands = this.bands.filter(b => b.ringIndex <= Math.max(0, maxAllowedRing));
          }
        }
      }

      // Reset speech accumulator when not speaking
      this.continuousSpeechTime = 0;
    }

    return true;
  }

  /**
   * Set voice input state
   */
  setVoiceInput(input: VoiceInput): void {
    const {
      amplitude = 0,
      speaker = null,
      isThinking = false,
    } = input;

    const previousState = this.currentState;
    const previousSpeaker = this.currentSpeaker;
    const previousMode = this.visualizationMode;
    this.amplitude = amplitude;

    // Determine state based on input
    if (isThinking) {
      this.currentState = 'thinking';
      this.currentSpeaker = 'companion';
      this.visualizationMode = 'rings';
    } else if (speaker === 'companion') {
      this.currentState = 'speaking';
      this.currentSpeaker = 'companion';
      this.visualizationMode = 'rings';
    } else {
      // User speaking OR idle - both show equalizer (user's turn)
      this.currentState = speaker === 'user' ? 'speaking' : 'idle';
      this.currentSpeaker = speaker === 'user' ? 'user' : null;
      this.visualizationMode = 'equalizer';
    }

    // Handle mode transitions
    if (previousMode === 'equalizer' && this.visualizationMode === 'rings') {
      // Switching from user to companion - reset equalizer
      this.equalizerBarHeights = new Array(this.equalizerBarCount).fill(0.05);
      this.equalizerTargetHeights = new Array(this.equalizerBarCount).fill(0.05);
    }

    // Spawn thinking indicator when entering thinking state
    // This ensures there's something to animate (rotate/pulse) during thinking
    // Only show innermost ring during thinking - clear other bands
    if (this.currentState === 'thinking' && previousState !== 'thinking') {
      // Clear all existing bands - thinking shows only a fresh indicator
      this.bands = [];
      this.spawnThinkingIndicator();
    }

    // Spawn first band immediately when companion starts speaking
    if (this.visualizationMode === 'rings' &&
        this.currentState === 'speaking' &&
        this.currentSpeaker === 'companion' &&
        (previousState !== 'speaking' || previousSpeaker !== 'companion')) {
      this.spawnBandAtCenter();
      this.continuousSpeechTime = 0;
    }

    // Update equalizer when in equalizer mode
    if (this.visualizationMode === 'equalizer') {
      if (this.currentSpeaker === 'user') {
        this.updateEqualizerTargets(amplitude);
      } else {
        // Idle state - show minimal bars
        this.updateEqualizerTargets(0.01);
      }
    }
  }

  /**
   * Spawn a thinking indicator - subtle ring to show processing is happening
   */
  spawnThinkingIndicator(): void {
    // Spawn a subtle band at the center ring
    const band = new Band({
      ringIndex: 0,
      radius: this.getRingRadius(0),
      elementCount: 8,  // Fewer elements for subtle look
      elementType: 'line',
      alignment: this.defaultAlignment,
      spawnAmplitude: 0.3,  // Low amplitude for subtle appearance
      speaker: 'companion',
      seed: Date.now(),
      spawnPalette: this.activePaletteName,
    });

    this.bands.push(band);
  }

  /**
   * Update equalizer bar target heights based on amplitude
   */
  updateEqualizerTargets(amplitude: number): void {
    const minHeight = 0.04;
    const maxHeight = 0.45;  // Much taller max height
    const amplitudeScale = Math.min(1, amplitude * 8);  // Stronger amplification

    for (let i = 0; i < this.equalizerBarCount; i++) {
      // Create slight variation between bars - center bars react more
      const centerDistance = Math.abs(i - (this.equalizerBarCount - 1) / 2);
      const centerFactor = 1 - (centerDistance / (this.equalizerBarCount / 2)) * 0.3;

      // Use deterministic per-bar variation instead of random (prevents jitter)
      // Each bar has a slightly different "sensitivity" based on its index
      const barVariation = 0.9 + (Math.sin(i * 1.5) * 0.1);

      const targetHeight = minHeight + (maxHeight - minHeight) * amplitudeScale * centerFactor * barVariation;
      this.equalizerTargetHeights[i] = targetHeight;
    }
  }

  /**
   * Update equalizer from raw amplitude with continuous smoothing
   * Called every render frame (60fps) for smooth, responsive animation
   */
  updateEqualizerFromRawAmplitude(userAmplitude: number): void {
    // Continuous amplitude smoothing - runs every frame for fluid motion
    // Use asymmetric smoothing: faster attack, slower release
    const targetAmplitude = userAmplitude;
    const diff = targetAmplitude - this.equalizerSmoothedAmplitude;

    if (diff > 0) {
      // Attack: respond quickly to increases (0.3 = fast)
      this.equalizerSmoothedAmplitude += diff * 0.3;
    } else {
      // Release: smooth out decreases (0.15 = medium-slow)
      this.equalizerSmoothedAmplitude += diff * 0.15;
    }

    // Update targets based on smoothed amplitude
    this.updateEqualizerTargets(this.equalizerSmoothedAmplitude);
  }

  /**
   * Get color for a ring index using a specific palette
   */
  getRingColorFromPalette(ringIndex: number, paletteName: string): string {
    const palette = this.colorPalettes?.[paletteName] || this.colorGradient;
    const index = Math.min(ringIndex, palette.length - 1);
    return palette[index];
  }

  /**
   * Get equalizer elements for user speaking mode
   * Called every render frame (60fps) - smoothing happens here for smooth animation
   */
  getEqualizerElements(): CompositionElements {
    const lines: RenderLine[] = [];
    const disks: RenderDisk[] = [];
    const squares: RenderSquare[] = [];

    // Apply smoothing at render rate for smooth animation
    const now = performance.now();
    const dt = Math.min(50, now - this.equalizerLastSmoothTime); // Cap delta to avoid jumps
    this.equalizerLastSmoothTime = now;

    // Smooth lerp - 0.15 base gives nice smooth motion while still being responsive
    // Higher base = smoother but slower, lower base = faster but jittery
    const smoothing = 1 - Math.pow(0.15, dt / 16.67); // ~0.15 per frame at 60fps

    for (let i = 0; i < this.equalizerBarCount; i++) {
      this.equalizerBarHeights[i] += (this.equalizerTargetHeights[i] - this.equalizerBarHeights[i]) * smoothing;
    }

    // Warm white color for user
    const warmWhite: [number, number, number] = [1.0, 0.98, 0.95];

    const totalWidth = (this.equalizerBarCount - 1) * this.equalizerSpacing;
    const startX = -totalWidth / 2;

    for (let i = 0; i < this.equalizerBarCount; i++) {
      const x = startX + i * this.equalizerSpacing;
      const height = this.equalizerBarHeights[i];

      lines.push({
        x,
        y: 0,
        rotation: Math.PI / 2,  // Vertical orientation
        length: height,
        thickness: this.equalizerBaseThickness,
        taper: 0.1,
        color: warmWhite,
        seed: i * 1000,
      });
    }

    return { lines, disks, squares };
  }

  /**
   * Get all elements for rendering
   * Returns equalizer bars for user mode, rings for companion mode
   */
  getAllElements(): CompositionElements {
    // Return equalizer elements when in user speaking mode
    if (this.visualizationMode === 'equalizer') {
      return this.getEqualizerElements();
    }

    // Otherwise return ring elements (companion mode)
    const lines: RenderLine[] = [];
    const disks: RenderDisk[] = [];
    const squares: RenderSquare[] = [];

    for (const band of this.bands) {
      // Use the palette that was active when this band spawned
      const color = this.getRingColorFromPalette(band.ringIndex, band.spawnPalette);

      for (const el of band.elements) {
        const colorVec = this.hexToVec3(color, el.colorVariance, el.seed);

        if (el.type === 'line') {
          lines.push({
            x: el.x,
            y: el.y,
            rotation: el.rotation,
            length: el.length,
            thickness: el.thickness,
            taper: el.taper,
            color: colorVec,
            seed: el.seed,
          });
        } else if (el.type === 'circle') {
          disks.push({
            x: el.x,
            y: el.y,
            size: el.size,
            softness: el.softness,
            color: colorVec,
            seed: el.seed,
          });
        } else if (el.type === 'square') {
          squares.push({
            x: el.x,
            y: el.y,
            size: el.size,
            rotation: el.rotation,
            color: colorVec,
            seed: el.seed,
          });
        }
      }
    }

    return { lines, disks, squares };
  }

  /**
   * Convert hex color to vec3 with optional variance
   * Uses seed for deterministic variance (no flickering)
   */
  hexToVec3(hex: string, variance: number = 0, seed: number = 0): [number, number, number] {
    const r = parseInt(hex.slice(1, 3), 16) / 255;
    const g = parseInt(hex.slice(3, 5), 16) / 255;
    const b = parseInt(hex.slice(5, 7), 16) / 255;

    if (variance > 0 && seed) {
      // Use seed for deterministic variance
      const seededRandom = ((seed * 9301 + 49297) % 233280) / 233280;
      const v = (seededRandom - 0.5) * variance * 2;
      return [
        Math.max(0, Math.min(1, r + v)),
        Math.max(0, Math.min(1, g + v)),
        Math.max(0, Math.min(1, b + v)),
      ];
    }

    return [r, g, b];
  }

  /**
   * Get available palette names
   */
  getPaletteNames(): string[] {
    if (!this.colorPalettes) return [];
    return Object.keys(this.colorPalettes);
  }

  /**
   * Get current active palette name
   */
  getActivePalette(): string {
    return this.activePaletteName;
  }

  /**
   * Set active palette by name
   */
  setPalette(paletteName: string): boolean {
    if (this.colorPalettes && this.colorPalettes[paletteName]) {
      this.colorGradient = this.colorPalettes[paletteName];
      this.activePaletteName = paletteName;
      return true;
    }
    return false;
  }

  /**
   * Cycle to next palette
   */
  cyclePalette(): string | null {
    const names = this.getPaletteNames();
    if (names.length === 0) return null;

    const currentIndex = names.indexOf(this.activePaletteName);
    const nextIndex = (currentIndex + 1) % names.length;
    this.setPalette(names[nextIndex]);
    return this.activePaletteName;
  }

  /**
   * Toggle outer shapes feature
   */
  setOuterShapes(enabled: boolean, shapeType: ElementType | 'random' = 'circle', ringCount: number = 2): void {
    this.outerShapesEnabled = enabled;
    this.outerShapeType = shapeType;
    this.outerShapesRingCount = ringCount;
  }

  /**
   * Set default alignment mode
   */
  setAlignment(alignment: AlignmentMode): void {
    this.defaultAlignment = alignment;
  }

  /**
   * Get current composition info for debugging
   */
  getDebugInfo(): Record<string, string | number | boolean> {
    return {
      bandCount: this.bands.length,
      state: this.currentState,
      speaker: this.currentSpeaker || 'none',
      amplitude: Number(this.amplitude.toFixed(2)),
      speechTime: (this.continuousSpeechTime / 1000).toFixed(1) + 's',
      totalElements: this.bands.reduce((sum, b) => sum + b.elements.length, 0),
      rings: this.bands.map(b => b.ringIndex).join(',') || 'none',
      palette: this.activePaletteName,
      alignment: this.defaultAlignment,
      outerShapes: this.outerShapesEnabled ? this.outerShapeType : 'off',
    };
  }
}
