/**
 * FrameController - Stop-motion frame rate management
 *
 * Separates composition update rate (stop-motion, e.g., 10fps)
 * from render rate (smooth, e.g., 60fps)
 */

export interface CompositionUpdateInfo {
  frame: number;
  delta: number;
  time: number;
  fps: number;
}

export interface RenderInfo {
  frame: number;
  delta: number;
  time: number;
  compositionFrame: number;
}

export interface FrameStats {
  compositionFPS: number;
  compositionFrame: number;
  actualCompositionFPS: number;
  renderFrame: number;
  actualRenderFPS: number;
  isRunning: boolean;
}

export interface FrameControllerOptions {
  compositionFPS?: number;
  renderFPS?: number;
  onCompositionUpdate?: (info: CompositionUpdateInfo) => void;
  onRender?: (info: RenderInfo) => void;
}

export class FrameController {
  // Composition updates at this rate (stop-motion feel)
  compositionFPS: number;
  compositionInterval: number;

  // Render rate (smooth display)
  renderFPS: number;
  renderInterval: number;

  // Timing state
  lastCompositionTime: number;
  lastRenderTime: number;
  compositionFrame: number;
  renderFrame: number;

  // Animation state
  isRunning: boolean;
  animationId: number | null;

  // Callbacks
  onCompositionUpdate: (info: CompositionUpdateInfo) => void;
  onRender: (info: RenderInfo) => void;

  // Performance tracking
  compositionDelta: number;
  renderDelta: number;

  constructor(options: FrameControllerOptions = {}) {
    // Composition updates at this rate (stop-motion feel)
    this.compositionFPS = options.compositionFPS || 10;
    this.compositionInterval = 1000 / this.compositionFPS;

    // Render rate (smooth display)
    this.renderFPS = options.renderFPS || 60;
    this.renderInterval = 1000 / this.renderFPS;

    // Timing state
    this.lastCompositionTime = 0;
    this.lastRenderTime = 0;
    this.compositionFrame = 0;
    this.renderFrame = 0;

    // Animation state
    this.isRunning = false;
    this.animationId = null;

    // Callbacks
    this.onCompositionUpdate = options.onCompositionUpdate || (() => {});
    this.onRender = options.onRender || (() => {});

    // Performance tracking
    this.compositionDelta = 0;
    this.renderDelta = 0;
  }

  /**
   * Start the animation loop
   */
  start(): void {
    if (this.isRunning) return;

    this.isRunning = true;
    this.lastCompositionTime = performance.now();
    this.lastRenderTime = performance.now();

    this.loop();
  }

  /**
   * Stop the animation loop
   */
  stop(): void {
    this.isRunning = false;
    if (this.animationId !== null) {
      cancelAnimationFrame(this.animationId);
      this.animationId = null;
    }
  }

  /**
   * Main animation loop
   */
  private loop = (): void => {
    if (!this.isRunning) return;

    const now = performance.now();

    // Check if composition should update (stop-motion rate)
    const compositionDelta = now - this.lastCompositionTime;
    if (compositionDelta >= this.compositionInterval) {
      this.compositionDelta = compositionDelta;
      this.lastCompositionTime = now - (compositionDelta % this.compositionInterval);
      this.compositionFrame++;

      // Call composition update callback
      this.onCompositionUpdate({
        frame: this.compositionFrame,
        delta: compositionDelta,
        time: now,
        fps: this.compositionFPS,
      });
    }

    // Render always (smooth rate)
    const renderDelta = now - this.lastRenderTime;
    this.renderDelta = renderDelta;
    this.lastRenderTime = now;
    this.renderFrame++;

    // Call render callback
    this.onRender({
      frame: this.renderFrame,
      delta: renderDelta,
      time: now,
      compositionFrame: this.compositionFrame,
    });

    // Continue loop
    this.animationId = requestAnimationFrame(this.loop);
  };

  /**
   * Set composition FPS (stop-motion rate)
   */
  setCompositionFPS(fps: number): void {
    this.compositionFPS = Math.max(1, Math.min(30, fps));
    this.compositionInterval = 1000 / this.compositionFPS;
  }

  /**
   * Get current performance stats
   */
  getStats(): FrameStats {
    return {
      compositionFPS: this.compositionFPS,
      compositionFrame: this.compositionFrame,
      actualCompositionFPS: this.compositionDelta > 0 ? 1000 / this.compositionDelta : 0,
      renderFrame: this.renderFrame,
      actualRenderFPS: this.renderDelta > 0 ? 1000 / this.renderDelta : 0,
      isRunning: this.isRunning,
    };
  }

  /**
   * Reset frame counters
   */
  reset(): void {
    this.compositionFrame = 0;
    this.renderFrame = 0;
    this.lastCompositionTime = performance.now();
    this.lastRenderTime = performance.now();
  }
}
