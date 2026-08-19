'use client';

/**
 * GenerativeArtCanvas - React wrapper for the generative art visualization
 *
 * This component manages the Three.js lifecycle and provides a simple interface
 * for controlling the voice visualization.
 */

import { useEffect, useRef } from 'react';
import { BandComposition } from './core/BandComposition';
import { FrameController } from './core/FrameController';
import { VoiceStateManager } from './core/VoiceStateManager';
import { Renderer } from './renderers/Renderer';
import { CONFIG } from './core/config';
import type { VoiceState } from './core/VoiceStateManager';

export type { VoiceState };

export interface GenerativeArtCanvasProps {
  /** Width in pixels. Defaults to 300 */
  width?: number;
  /** Height in pixels. Defaults to 300 */
  height?: number;
  /** User's voice amplitude (0-1) */
  userAmplitude?: number;
  /** Companion's voice amplitude (0-1) */
  companionAmplitude?: number;
  /** Whether the companion is thinking/processing */
  isThinking?: boolean;
  /** Color palette name for companion (user uses warm white equalizer) */
  palette?: string;
  /** Background color (hex). Defaults to #1F1F1F */
  backgroundColor?: string;
  /** Additional CSS classes */
  className?: string;
  /** Called when the canvas is ready */
  onReady?: () => void;
}

export function GenerativeArtCanvas({
  width = 300,
  height = 300,
  userAmplitude = 0,
  companionAmplitude = 0,
  isThinking = false,
  palette = 'velvet',
  backgroundColor = '#1F1F1F',
  className = '',
  onReady,
}: GenerativeArtCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const rendererRef = useRef<Renderer | null>(null);
  const compositionRef = useRef<BandComposition | null>(null);
  const frameControllerRef = useRef<FrameController | null>(null);
  const voiceManagerRef = useRef<VoiceStateManager | null>(null);
  const isInitializedRef = useRef(false);

  // Store raw amplitude in refs for continuous animation loop access
  const rawUserAmplitudeRef = useRef(0);
  const rawCompanionAmplitudeRef = useRef(0);

  // Initialize the visualization system
  useEffect(() => {
    if (!containerRef.current || isInitializedRef.current) return;

    isInitializedRef.current = true;

    // Create composition
    const composition = new BandComposition({
      ringCount: CONFIG.rings.count,
      ringPositions: CONFIG.rings.positions,
      speechInterval: CONFIG.spawning.speechInterval,
      minAmplitudeToCount: CONFIG.spawning.minAmplitudeToCount,
      colorPalettes: CONFIG.colorPalettes,
      colorGradient: CONFIG.colorPalettes[palette] || CONFIG.colorPalettes.ember,
      activePalette: palette,
      idleFadeStartDelay: CONFIG.idle.fadeStartDelay,
      idleFadePerRing: CONFIG.idle.fadePerRing,
      compositionFPS: CONFIG.timing.compositionFPS,
    });
    compositionRef.current = composition;

    // Create voice state manager
    const voiceManager = new VoiceStateManager({
      onStateChange: (newState) => {
        // Map voice state to composition state
        if (newState === 'userSpeaking' || newState === 'companionSpeaking') {
          const speaker = newState === 'userSpeaking' ? 'user' : 'companion';
          composition.setVoiceInput({
            amplitude: voiceManager.getCurrentAmplitude(),
            speaker,
            isThinking: false,
          });
        } else if (newState === 'companionThinking') {
          composition.setVoiceInput({
            amplitude: 0.3,
            speaker: 'companion',
            isThinking: true,
          });
        } else {
          composition.setVoiceInput({
            amplitude: 0,
            speaker: null,
            isThinking: false,
          });
        }
      },
    });
    voiceManagerRef.current = voiceManager;

    // Create renderer
    const renderer = new Renderer(containerRef.current, {
      width,
      height,
      backgroundColor,
    });
    rendererRef.current = renderer;

    // Create frame controller
    const frameController = new FrameController({
      compositionFPS: CONFIG.timing.compositionFPS,
      renderFPS: CONFIG.timing.renderFPS,
      onCompositionUpdate: ({ delta }) => {
        // Get current voice state and update composition with voice input
        // This must happen EVERY frame, not just on state change!
        const stateInfo = voiceManager.getStateInfo();

        composition.setVoiceInput({
          amplitude: stateInfo.amplitude,
          speaker: stateInfo.speaker,
          isThinking: stateInfo.isThinking,
        });

        // Update composition (handles spawning, migration, etc.)
        composition.update(delta);
      },
      onRender: ({ time, delta }) => {
        // For equalizer mode: use raw amplitude from refs for continuous smooth animation
        // This bypasses VoiceStateManager's stepped updates tied to React prop changes
        if (composition.visualizationMode === 'equalizer') {
          composition.updateEqualizerFromRawAmplitude(rawUserAmplitudeRef.current);
        }

        // Get elements every render frame - this allows equalizer smoothing at 60fps
        const elements = composition.getAllElements();
        renderer.setComposition(elements);

        // Map composition state to render state
        let renderState: 'idle' | 'speaking' | 'thinking' = 'idle';
        if (composition.currentState === 'speaking') {
          renderState = 'speaking';
        } else if (composition.currentState === 'thinking') {
          renderState = 'thinking';
        }

        // Render frame
        renderer.render(time, renderState, delta);
      },
    });
    frameControllerRef.current = frameController;

    // Start animation
    frameController.start();

    // Notify ready
    onReady?.();

    // Cleanup
    return () => {
      frameController.stop();
      renderer.dispose();
      isInitializedRef.current = false;
    };
  }, [width, height, palette, backgroundColor, onReady]);

  // Update voice state and raw amplitude refs
  useEffect(() => {
    // Update refs immediately (accessible in animation loop)
    rawUserAmplitudeRef.current = userAmplitude;
    rawCompanionAmplitudeRef.current = companionAmplitude;

    if (!voiceManagerRef.current) return;

    voiceManagerRef.current.update({
      userAmplitude,
      companionAmplitude,
      isThinking,
    });
  }, [userAmplitude, companionAmplitude, isThinking]);

  // Set companion palette (user mode uses hardcoded warm white)
  useEffect(() => {
    if (!compositionRef.current) return;
    compositionRef.current.setPalette(palette);
  }, [palette]);

  // Handle resize
  useEffect(() => {
    if (!rendererRef.current) return;

    rendererRef.current.onResize(width, height);
  }, [width, height]);

  return (
    <div
      ref={containerRef}
      className={className}
      style={{
        width,
        height,
        overflow: 'hidden',
        borderRadius: '50%',
      }}
    />
  );
}

export default GenerativeArtCanvas;
