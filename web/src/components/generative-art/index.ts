// Main export
export { GenerativeArtCanvas } from './GenerativeArtCanvas';
export type { GenerativeArtCanvasProps, VoiceState } from './GenerativeArtCanvas';

// Core exports (for advanced usage)
export { BandComposition } from './core/BandComposition';
export { FrameController } from './core/FrameController';
export { VoiceStateManager } from './core/VoiceStateManager';
export { CONFIG, getInterpolatedValue } from './core/config';

// Types
export type {
  ElementType,
  AlignmentMode,
  Speaker,
  VoiceInput,
  CompositionElements,
  RenderLine,
  RenderDisk,
  RenderSquare,
} from './core/BandComposition';

export type {
  CompositionUpdateInfo,
  RenderInfo,
  FrameStats,
  FrameControllerOptions,
} from './core/FrameController';

export type {
  VoiceStateInfo,
  VoiceStateManagerOptions,
} from './core/VoiceStateManager';

export type {
  Config,
  ColorPalette,
  ColorPalettes,
} from './core/config';
