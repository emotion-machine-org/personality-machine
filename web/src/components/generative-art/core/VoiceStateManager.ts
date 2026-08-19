/**
 * VoiceStateManager - Manages voice input state for the composition
 *
 * Handles:
 * - User vs Companion speaking states
 * - Amplitude levels
 * - Thinking/processing states
 * - Richness/complexity signals
 * - State transitions
 */

export type VoiceState = 'idle' | 'userSpeaking' | 'companionSpeaking' | 'companionThinking';
export type Speaker = 'user' | 'companion' | null;

export interface VoiceInput {
  userAmplitude?: number;
  companionAmplitude?: number;
  isThinking?: boolean;
  richness?: number | null;
  isExcited?: boolean;
}

export interface VoiceStateInfo {
  state: VoiceState;
  speaker: Speaker;
  amplitude: number;
  richness: number;
  isThinking: boolean;
  isExcited: boolean;
  stateDuration: number;
}

export interface VoiceStateManagerOptions {
  amplitudeSmoothing?: number;
  speakingStartThreshold?: number;
  speakingStopThreshold?: number;
  silenceDelay?: number;
  richness?: number;
  onStateChange?: (newState: VoiceState, previousState: VoiceState) => void;
  onExcitement?: () => void;
}

export class VoiceStateManager {
  // Current state
  state: VoiceState;
  previousState: VoiceState;

  // Voice levels
  userAmplitude: number;
  companionAmplitude: number;

  // Smoothing (for less jittery response)
  amplitudeSmoothing: number;
  smoothedUserAmplitude: number;
  smoothedCompanionAmplitude: number;

  // Hysteresis thresholds - different values for starting vs stopping
  speakingStartThreshold: number;
  speakingStopThreshold: number;
  silenceDelay: number;

  // Richness (complexity signal from API)
  richness: number;
  targetRichness: number;

  // Timing
  lastSpeakingTime: number;
  stateStartTime: number;

  // Special states
  isThinking: boolean;
  isExcited: boolean;
  excitementEndTime: number;

  // Callbacks
  onStateChange: (newState: VoiceState, previousState: VoiceState) => void;
  onExcitement: () => void;

  constructor(options: VoiceStateManagerOptions = {}) {
    // Current state
    this.state = 'idle';
    this.previousState = 'idle';

    // Voice levels
    this.userAmplitude = 0;
    this.companionAmplitude = 0;

    // Smoothing (for less jittery response)
    // Higher value = faster response (0.7 = very responsive, 0.3 = smooth but laggy)
    this.amplitudeSmoothing = options.amplitudeSmoothing || 0.5;  // Balanced - responsive but filters noise
    this.smoothedUserAmplitude = 0;
    this.smoothedCompanionAmplitude = 0;

    // Hysteresis thresholds - different values for starting vs stopping
    this.speakingStartThreshold = options.speakingStartThreshold || 0.02;  // Lower for mic input
    this.speakingStopThreshold = options.speakingStopThreshold || 0.01;  // Even lower to stop
    this.silenceDelay = options.silenceDelay || 80;  // Very quick transition to idle

    // Richness (complexity signal from API)
    this.richness = options.richness || 0.3;
    this.targetRichness = 0.3;

    // Timing
    this.lastSpeakingTime = 0;
    this.stateStartTime = 0;

    // Special states
    this.isThinking = false;
    this.isExcited = false;
    this.excitementEndTime = 0;

    // Callbacks
    this.onStateChange = options.onStateChange || (() => {});
    this.onExcitement = options.onExcitement || (() => {});
  }

  /**
   * Update with new voice input
   */
  update(input: VoiceInput): void {
    const now = Date.now();
    const {
      userAmplitude = 0,
      companionAmplitude = 0,
      isThinking = false,
      richness = null,
      isExcited = false,
    } = input;

    // Smooth amplitude values
    this.smoothedUserAmplitude += (userAmplitude - this.smoothedUserAmplitude) * this.amplitudeSmoothing;
    this.smoothedCompanionAmplitude += (companionAmplitude - this.smoothedCompanionAmplitude) * this.amplitudeSmoothing;

    this.userAmplitude = this.smoothedUserAmplitude;
    this.companionAmplitude = this.smoothedCompanionAmplitude;

    // Update richness if provided
    if (richness !== null) {
      this.targetRichness = richness;
    }
    // Smooth richness transition
    this.richness += (this.targetRichness - this.richness) * 0.1;

    // Update thinking state
    this.isThinking = isThinking;

    // Handle excitement
    if (isExcited && !this.isExcited) {
      this.isExcited = true;
      this.excitementEndTime = now + 500;  // 500ms excitement duration
      this.onExcitement();
    }
    if (this.isExcited && now > this.excitementEndTime) {
      this.isExcited = false;
    }

    // Determine new state
    const newState = this.determineState(now);

    // Handle state transition
    if (newState !== this.state) {
      this.previousState = this.state;
      this.state = newState;
      this.stateStartTime = now;
      this.onStateChange(newState, this.previousState);
    }
  }

  /**
   * Determine current state based on inputs
   * Uses hysteresis: higher threshold to START speaking, lower to STOP
   */
  private determineState(now: number): VoiceState {
    // Thinking takes priority
    if (this.isThinking) {
      return 'companionThinking';
    }

    // Use hysteresis for smoother transitions
    // If already speaking, use lower threshold to keep speaking
    // If idle, use higher threshold to start speaking
    const isCurrentlySpeaking = this.state === 'userSpeaking' || this.state === 'companionSpeaking';
    const threshold = isCurrentlySpeaking ? this.speakingStopThreshold : this.speakingStartThreshold;

    const userSpeaking = this.smoothedUserAmplitude > threshold;
    const companionSpeaking = this.smoothedCompanionAmplitude > threshold;

    if (userSpeaking) {
      this.lastSpeakingTime = now;
      return 'userSpeaking';
    }

    if (companionSpeaking) {
      this.lastSpeakingTime = now;
      return 'companionSpeaking';
    }

    // Check for silence delay before going to idle
    // This allows for natural pauses in speech
    if (isCurrentlySpeaking) {
      const silenceDuration = now - this.lastSpeakingTime;
      if (silenceDuration < this.silenceDelay) {
        // Stay in current speaking state during brief pauses
        return this.state;
      }
    }

    return 'idle';
  }

  /**
   * Get current state info for composition
   */
  getStateInfo(): VoiceStateInfo {
    return {
      state: this.state,
      speaker: this.getCurrentSpeaker(),
      amplitude: this.getCurrentAmplitude(),
      richness: this.richness,
      isThinking: this.isThinking,
      isExcited: this.isExcited,
      stateDuration: Date.now() - this.stateStartTime,
    };
  }

  /**
   * Get current speaker
   */
  getCurrentSpeaker(): Speaker {
    switch (this.state) {
      case 'userSpeaking':
        return 'user';
      case 'companionSpeaking':
      case 'companionThinking':
        return 'companion';
      default:
        return null;
    }
  }

  /**
   * Get current amplitude (of active speaker)
   */
  getCurrentAmplitude(): number {
    switch (this.state) {
      case 'userSpeaking':
        return this.smoothedUserAmplitude;
      case 'companionSpeaking':
        return this.smoothedCompanionAmplitude;
      case 'companionThinking':
        return 0.3;  // Moderate level for thinking
      default:
        return 0;
    }
  }

  /**
   * Set richness directly (from API metadata)
   */
  setRichness(richness: number): void {
    this.targetRichness = Math.max(0, Math.min(1, richness));
  }

  /**
   * Trigger thinking state
   */
  startThinking(): void {
    this.isThinking = true;
  }

  /**
   * End thinking state
   */
  stopThinking(): void {
    this.isThinking = false;
  }

  /**
   * Trigger excitement effect
   */
  triggerExcitement(): void {
    this.isExcited = true;
    this.excitementEndTime = Date.now() + 500;
    this.onExcitement();
  }

  /**
   * Simulate voice input (for testing)
   */
  simulateVoice(type: 'user' | 'companion' | 'thinking', amplitude: number = 0.6, duration: number = 2000): void {
    const startTime = Date.now();

    const simulate = (): void => {
      const elapsed = Date.now() - startTime;
      if (elapsed > duration) {
        // End simulation
        this.update({
          userAmplitude: 0,
          companionAmplitude: 0,
          isThinking: false,
        });
        return;
      }

      // Add some variation to amplitude
      const variation = Math.sin(elapsed * 0.01) * 0.2;
      const currentAmplitude = Math.max(0, amplitude + variation);

      switch (type) {
        case 'user':
          this.update({
            userAmplitude: currentAmplitude,
            companionAmplitude: 0,
          });
          break;
        case 'companion':
          this.update({
            userAmplitude: 0,
            companionAmplitude: currentAmplitude,
          });
          break;
        case 'thinking':
          this.update({
            userAmplitude: 0,
            companionAmplitude: 0,
            isThinking: true,
          });
          break;
      }

      requestAnimationFrame(simulate);
    };

    simulate();
  }
}
