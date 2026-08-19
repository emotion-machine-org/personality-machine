/**
 * V2 Renderer - Band-aware rendering system
 *
 * Uses instanced rendering for efficient composition display
 * with post-processing effects (bloom, grain, etc.)
 */

import * as THREE from 'three';
import { ColorManagement } from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { ShaderPass } from 'three/addons/postprocessing/ShaderPass.js';

import {
  pillVertSrc,
  pillFragSrc,
  diskVertSrc,
  diskFragSrc,
  squareVertSrc,
  squareFragSrc,
  compositeVertSrc,
  compositeFragSrc,
} from '../shaders';

import { CONFIG } from '../core/config';
import type { CompositionElements, RenderLine, RenderDisk, RenderSquare } from '../core/BandComposition';

export type VoiceState = 'idle' | 'speaking' | 'thinking';

export interface RendererOptions {
  width?: number;
  height?: number;
  backgroundColor?: string;  // Hex color for background (default: #000000)
}

export interface EffectParams {
  bloom?: {
    strength?: number;
    radius?: number;
    threshold?: number;
  };
  diffuse?: {
    intensity?: number;
    radius?: number;
  };
  grain?: {
    intensity?: number;
  };
  chromaticAberration?: number;
  brightness?: number;
  vignette?: {
    intensity?: number;
  };
}

interface Uniforms {
  [key: string]: { value: number };
  uTime: { value: number };
  uAspect: { value: number };
  uEdgeNoiseScale: { value: number };
  uEdgeNoiseAmount: { value: number };
  uGlowIntensity: { value: number };
  uGlowRadius: { value: number };
  uThicknessMultiplier: { value: number };
}

export class Renderer {
  private container: HTMLElement;
  private width: number;
  private height: number;
  private aspect: number;

  private renderer: THREE.WebGLRenderer;
  private scene: THREE.Scene;
  private camera: THREE.OrthographicCamera;
  private composer: EffectComposer;

  // Materials
  private pillMaterial: THREE.ShaderMaterial;
  private diskMaterial: THREE.ShaderMaterial;
  private squareMaterial: THREE.ShaderMaterial;

  // Mesh references
  private linesMesh: THREE.Mesh | null = null;
  private disksMesh: THREE.Mesh | null = null;
  private squaresMesh: THREE.Mesh | null = null;

  // Animation state
  private breathePhase: number = 0;
  private breatheScale: number = 1;
  private idleIntensity: number = 0;  // Smoothly ramps up when idle
  private thinkingIntensity: number = 0;  // Smoothly ramps up when thinking
  private thinkingRotation: number = 0;  // Accumulated rotation for thinking animation

  // Shared uniforms
  private uniforms: Uniforms;

  // Post-processing
  private bloomPass: UnrealBloomPass;
  private compositePass: ShaderPass;

  private backgroundColor: string;

  constructor(container: HTMLElement, options: RendererOptions = {}) {
    this.container = container;
    this.width = options.width || window.innerWidth;
    this.height = options.height || window.innerHeight;
    this.aspect = this.width / this.height;
    this.backgroundColor = options.backgroundColor || '#000000';

    // Initialize uniforms
    this.uniforms = {
      uTime: { value: 0 },
      uAspect: { value: this.aspect },
      uEdgeNoiseScale: { value: 8.0 },
      uEdgeNoiseAmount: { value: 0.8 },
      uGlowIntensity: { value: CONFIG.effects.diffuse.intensity },
      uGlowRadius: { value: CONFIG.effects.diffuse.radius },
      uThicknessMultiplier: { value: 1.0 },
    };

    this.renderer = this.setupRenderer();
    const { scene, camera } = this.setupScene();
    this.scene = scene;
    this.camera = camera;

    const materials = this.setupMaterials();
    this.pillMaterial = materials.pillMaterial;
    this.diskMaterial = materials.diskMaterial;
    this.squareMaterial = materials.squareMaterial;

    const { composer, bloomPass, compositePass } = this.setupPostProcessing();
    this.composer = composer;
    this.bloomPass = bloomPass;
    this.compositePass = compositePass;

    this.setupEventListeners();
  }

  private setupRenderer(): THREE.WebGLRenderer {
    // Disable Three.js color management to get exact color matching with CSS
    ColorManagement.enabled = false;

    const renderer = new THREE.WebGLRenderer({
      antialias: true,
      alpha: false,
    });
    renderer.setSize(this.width, this.height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

    // Compensated for post-processing darkening
    renderer.setClearColor(0x1F1F1F, 1);
    this.container.appendChild(renderer.domElement);
    return renderer;
  }

  private setupScene(): { scene: THREE.Scene; camera: THREE.OrthographicCamera } {
    const scene = new THREE.Scene();
    // Compensated for post-processing darkening
    scene.background = new THREE.Color(0x1F1F1F);

    // Orthographic camera for 2D composition
    const frustumSize = 1;
    const camera = new THREE.OrthographicCamera(
      -frustumSize * this.aspect,
      frustumSize * this.aspect,
      frustumSize,
      -frustumSize,
      0.1,
      100
    );
    camera.position.z = 1;

    return { scene, camera };
  }

  private setupMaterials(): {
    pillMaterial: THREE.ShaderMaterial;
    diskMaterial: THREE.ShaderMaterial;
    squareMaterial: THREE.ShaderMaterial;
  } {
    // Pill/line material
    const pillMaterial = new THREE.ShaderMaterial({
      vertexShader: pillVertSrc,
      fragmentShader: pillFragSrc,
      uniforms: this.uniforms,
      transparent: true,
      blending: THREE.AdditiveBlending,
      depthTest: false,
      depthWrite: false,
    });

    // Disk/dot material
    const diskMaterial = new THREE.ShaderMaterial({
      vertexShader: diskVertSrc,
      fragmentShader: diskFragSrc,
      uniforms: this.uniforms,
      transparent: true,
      blending: THREE.AdditiveBlending,
      depthTest: false,
      depthWrite: false,
    });

    // Square material
    const squareMaterial = new THREE.ShaderMaterial({
      vertexShader: squareVertSrc,
      fragmentShader: squareFragSrc,
      uniforms: this.uniforms,
      transparent: true,
      blending: THREE.AdditiveBlending,
      depthTest: false,
      depthWrite: false,
    });

    return { pillMaterial, diskMaterial, squareMaterial };
  }

  private setupPostProcessing(): {
    composer: EffectComposer;
    bloomPass: UnrealBloomPass;
    compositePass: ShaderPass;
  } {
    // Render target
    const renderTarget = new THREE.WebGLRenderTarget(this.width, this.height, {
      minFilter: THREE.LinearFilter,
      magFilter: THREE.LinearFilter,
      format: THREE.RGBAFormat,
    });

    const composer = new EffectComposer(this.renderer, renderTarget);

    // Main render pass
    const renderPass = new RenderPass(this.scene, this.camera);
    composer.addPass(renderPass);

    // Bloom pass
    const bloomPass = new UnrealBloomPass(
      new THREE.Vector2(this.width, this.height),
      CONFIG.effects.bloom.strength,
      CONFIG.effects.bloom.radius,
      CONFIG.effects.bloom.threshold
    );
    composer.addPass(bloomPass);

    // Composite pass (grain, chromatic aberration, vignette)
    const compositePass = new ShaderPass({
      uniforms: {
        tDiffuse: { value: null },
        uTime: { value: 0 },
        uResolution: { value: new THREE.Vector2(this.width, this.height) },
        uChromaticAberration: { value: CONFIG.effects.chromaticAberration },
        uGrainIntensity: { value: CONFIG.effects.grain.intensity },
        uGrainSize: { value: CONFIG.effects.grain.size },
        uVignetteIntensity: { value: CONFIG.effects.vignette.intensity },
        uVignetteRadius: { value: CONFIG.effects.vignette.radius },
        uBrightness: { value: CONFIG.effects.brightness },
      },
      vertexShader: compositeVertSrc,
      fragmentShader: compositeFragSrc,
    });
    composer.addPass(compositePass);

    return { composer, bloomPass, compositePass };
  }

  private setupEventListeners(): void {
    // Resize listener is handled externally via onResize method
  }

  onResize(width?: number, height?: number): void {
    this.width = width || window.innerWidth;
    this.height = height || window.innerHeight;
    this.aspect = this.width / this.height;

    this.camera.left = -this.aspect;
    this.camera.right = this.aspect;
    this.camera.updateProjectionMatrix();

    this.renderer.setSize(this.width, this.height);
    this.composer.setSize(this.width, this.height);

    this.uniforms.uAspect.value = this.aspect;
    this.compositePass.uniforms.uResolution.value.set(this.width, this.height);
  }

  /**
   * Clear existing meshes from scene
   */
  private clearScene(): void {
    if (this.linesMesh) {
      this.linesMesh.geometry.dispose();
      this.scene.remove(this.linesMesh);
      this.linesMesh = null;
    }

    if (this.disksMesh) {
      this.disksMesh.geometry.dispose();
      this.scene.remove(this.disksMesh);
      this.disksMesh = null;
    }

    if (this.squaresMesh) {
      this.squaresMesh.geometry.dispose();
      this.scene.remove(this.squaresMesh);
      this.squaresMesh = null;
    }
  }

  /**
   * Update composition from BandComposition
   */
  setComposition(composition: CompositionElements): void {
    // Clear existing meshes
    this.clearScene();

    const { lines, disks, squares } = composition;

    // Create instanced meshes
    if (lines && lines.length > 0) {
      this.createLinesMesh(lines);
    }

    if (disks && disks.length > 0) {
      this.createDisksMesh(disks);
    }

    if (squares && squares.length > 0) {
      this.createSquaresMesh(squares);
    }
  }

  /**
   * Create instanced mesh for lines
   */
  private createLinesMesh(lines: RenderLine[]): void {
    const count = lines.length;

    // Create InstancedBufferGeometry from PlaneGeometry
    const baseGeometry = new THREE.PlaneGeometry(1, 1);
    const geometry = new THREE.InstancedBufferGeometry();

    // Copy base geometry attributes
    geometry.index = baseGeometry.index;
    geometry.attributes.position = baseGeometry.attributes.position;
    geometry.attributes.normal = baseGeometry.attributes.normal;
    geometry.attributes.uv = baseGeometry.attributes.uv;

    // Instance attributes
    const instancePosition = new Float32Array(count * 3); // x, y, rotation
    const instanceScale = new Float32Array(count * 3);    // length, thickness, taper
    const instanceColor = new Float32Array(count * 3);
    const instanceSeed = new Float32Array(count);

    lines.forEach((line, i) => {
      instancePosition[i * 3 + 0] = line.x;
      instancePosition[i * 3 + 1] = line.y;
      instancePosition[i * 3 + 2] = line.rotation;

      instanceScale[i * 3 + 0] = line.length;
      instanceScale[i * 3 + 1] = line.thickness;
      instanceScale[i * 3 + 2] = line.taper;

      instanceColor[i * 3 + 0] = line.color[0];
      instanceColor[i * 3 + 1] = line.color[1];
      instanceColor[i * 3 + 2] = line.color[2];

      instanceSeed[i] = line.seed;
    });

    geometry.setAttribute('instancePosition', new THREE.InstancedBufferAttribute(instancePosition, 3));
    geometry.setAttribute('instanceScale', new THREE.InstancedBufferAttribute(instanceScale, 3));
    geometry.setAttribute('instanceColor', new THREE.InstancedBufferAttribute(instanceColor, 3));
    geometry.setAttribute('instanceSeed', new THREE.InstancedBufferAttribute(instanceSeed, 1));

    geometry.instanceCount = count;

    const mesh = new THREE.Mesh(geometry, this.pillMaterial);
    mesh.frustumCulled = false;
    this.scene.add(mesh);

    this.linesMesh = mesh;
    baseGeometry.dispose();
  }

  /**
   * Create instanced mesh for disks
   */
  private createDisksMesh(disks: RenderDisk[]): void {
    const count = disks.length;

    // Create InstancedBufferGeometry from PlaneGeometry
    const baseGeometry = new THREE.PlaneGeometry(1, 1);
    const geometry = new THREE.InstancedBufferGeometry();

    // Copy base geometry attributes
    geometry.index = baseGeometry.index;
    geometry.attributes.position = baseGeometry.attributes.position;
    geometry.attributes.normal = baseGeometry.attributes.normal;
    geometry.attributes.uv = baseGeometry.attributes.uv;

    // Instance attributes
    const instancePosition = new Float32Array(count * 3);
    const instanceScale = new Float32Array(count * 3);
    const instanceColor = new Float32Array(count * 3);
    const instanceSeed = new Float32Array(count);

    disks.forEach((disk, i) => {
      instancePosition[i * 3 + 0] = disk.x;
      instancePosition[i * 3 + 1] = disk.y;
      instancePosition[i * 3 + 2] = 0;

      instanceScale[i * 3 + 0] = disk.size;
      instanceScale[i * 3 + 1] = disk.softness;
      instanceScale[i * 3 + 2] = 0;

      instanceColor[i * 3 + 0] = disk.color[0];
      instanceColor[i * 3 + 1] = disk.color[1];
      instanceColor[i * 3 + 2] = disk.color[2];

      instanceSeed[i] = disk.seed;
    });

    geometry.setAttribute('instancePosition', new THREE.InstancedBufferAttribute(instancePosition, 3));
    geometry.setAttribute('instanceScale', new THREE.InstancedBufferAttribute(instanceScale, 3));
    geometry.setAttribute('instanceColor', new THREE.InstancedBufferAttribute(instanceColor, 3));
    geometry.setAttribute('instanceSeed', new THREE.InstancedBufferAttribute(instanceSeed, 1));

    geometry.instanceCount = count;

    const mesh = new THREE.Mesh(geometry, this.diskMaterial);
    mesh.frustumCulled = false;
    this.scene.add(mesh);

    this.disksMesh = mesh;
    baseGeometry.dispose();
  }

  /**
   * Create instanced mesh for squares
   */
  private createSquaresMesh(squares: RenderSquare[]): void {
    const count = squares.length;

    // Create InstancedBufferGeometry from PlaneGeometry
    const baseGeometry = new THREE.PlaneGeometry(1, 1);
    const geometry = new THREE.InstancedBufferGeometry();

    // Copy base geometry attributes
    geometry.index = baseGeometry.index;
    geometry.attributes.position = baseGeometry.attributes.position;
    geometry.attributes.normal = baseGeometry.attributes.normal;
    geometry.attributes.uv = baseGeometry.attributes.uv;

    // Instance attributes
    const instancePosition = new Float32Array(count * 3);
    const instanceScale = new Float32Array(count * 3);
    const instanceColor = new Float32Array(count * 3);
    const instanceSeed = new Float32Array(count);

    squares.forEach((square, i) => {
      instancePosition[i * 3 + 0] = square.x;
      instancePosition[i * 3 + 1] = square.y;
      instancePosition[i * 3 + 2] = square.rotation || 0;

      instanceScale[i * 3 + 0] = square.size;
      instanceScale[i * 3 + 1] = 0;
      instanceScale[i * 3 + 2] = 0;

      instanceColor[i * 3 + 0] = square.color[0];
      instanceColor[i * 3 + 1] = square.color[1];
      instanceColor[i * 3 + 2] = square.color[2];

      instanceSeed[i] = square.seed;
    });

    geometry.setAttribute('instancePosition', new THREE.InstancedBufferAttribute(instancePosition, 3));
    geometry.setAttribute('instanceScale', new THREE.InstancedBufferAttribute(instanceScale, 3));
    geometry.setAttribute('instanceColor', new THREE.InstancedBufferAttribute(instanceColor, 3));
    geometry.setAttribute('instanceSeed', new THREE.InstancedBufferAttribute(instanceSeed, 1));

    geometry.instanceCount = count;

    const mesh = new THREE.Mesh(geometry, this.squareMaterial);
    mesh.frustumCulled = false;
    this.scene.add(mesh);

    this.squaresMesh = mesh;
    baseGeometry.dispose();
  }

  /**
   * Update breathing animation
   * Thickness pulse ramps up smoothly when entering idle
   * Rotation animation for thinking state
   */
  private updateBreathe(state: VoiceState, deltaTime: number): void {
    // Advance phase for breathing cycle
    this.breathePhase += deltaTime * 0.001 * 0.25;

    // Smoothly ramp idle intensity (takes ~300ms to fully enter idle mode)
    if (state === 'idle') {
      this.idleIntensity += (1.0 - this.idleIntensity) * 0.08;
    } else {
      this.idleIntensity += (0.0 - this.idleIntensity) * 0.15;
    }

    // Smoothly ramp thinking intensity
    if (state === 'thinking') {
      this.thinkingIntensity += (1.0 - this.thinkingIntensity) * 0.1;
    } else {
      this.thinkingIntensity += (0.0 - this.thinkingIntensity) * 0.2;
    }

    // Thickness: pulse when idle OR thinking, smoothly blended
    const breatheAmount = (Math.sin(this.breathePhase * Math.PI * 2) + 1) * 0.5;
    const idlePulse = breatheAmount * 0.6 * this.idleIntensity;
    // Faster, more subtle pulse for thinking
    const thinkingPulsePhase = this.breathePhase * 3;  // 3x faster
    const thinkingBreathe = (Math.sin(thinkingPulsePhase * Math.PI * 2) + 1) * 0.5;
    const thinkingPulse = thinkingBreathe * 0.3 * this.thinkingIntensity;
    this.uniforms.uThicknessMultiplier.value = 1.0 + idlePulse + thinkingPulse;

    // Rotation animation for thinking state
    // IMPORTANT: Only apply rotation when actually in thinking state
    // Reset immediately when leaving thinking to prevent rotation carrying over to equalizer
    if (state === 'thinking') {
      // Slow continuous rotation during thinking
      this.thinkingRotation += deltaTime * 0.0003;

      // Apply rotation to all meshes
      if (this.linesMesh) {
        this.linesMesh.rotation.z = this.thinkingRotation;
      }
      if (this.disksMesh) {
        this.disksMesh.rotation.z = this.thinkingRotation;
      }
      if (this.squaresMesh) {
        this.squaresMesh.rotation.z = this.thinkingRotation;
      }
    } else {
      // Reset rotation immediately when not thinking
      this.thinkingRotation = 0;
      if (this.linesMesh) this.linesMesh.rotation.z = 0;
      if (this.disksMesh) this.disksMesh.rotation.z = 0;
      if (this.squaresMesh) this.squaresMesh.rotation.z = 0;
    }
  }

  /**
   * Apply excitement pulse effect
   */
  applyExcitementPulse(progress: number): void {
    // progress: 0 -> 1 over excitement duration
    // Scale: 1 -> 1.15 -> 1
    const pulseScale = 1 + Math.sin(progress * Math.PI) * 0.15;

    if (this.linesMesh) {
      this.linesMesh.scale.setScalar(pulseScale);
    }
    if (this.disksMesh) {
      this.disksMesh.scale.setScalar(pulseScale);
    }
    if (this.squaresMesh) {
      this.squaresMesh.scale.setScalar(pulseScale);
    }
  }

  /**
   * Update effect parameters
   */
  setEffectParams(params: EffectParams): void {
    if (params.bloom) {
      if (params.bloom.strength !== undefined) this.bloomPass.strength = params.bloom.strength;
      if (params.bloom.radius !== undefined) this.bloomPass.radius = params.bloom.radius;
      if (params.bloom.threshold !== undefined) this.bloomPass.threshold = params.bloom.threshold;
    }

    if (params.diffuse) {
      if (params.diffuse.intensity !== undefined) this.uniforms.uGlowIntensity.value = params.diffuse.intensity;
      if (params.diffuse.radius !== undefined) this.uniforms.uGlowRadius.value = params.diffuse.radius;
    }

    if (params.grain) {
      if (params.grain.intensity !== undefined) this.compositePass.uniforms.uGrainIntensity.value = params.grain.intensity;
    }

    if (params.chromaticAberration !== undefined) {
      this.compositePass.uniforms.uChromaticAberration.value = params.chromaticAberration;
    }

    if (params.brightness !== undefined) {
      this.compositePass.uniforms.uBrightness.value = params.brightness;
    }

    if (params.vignette) {
      if (params.vignette.intensity !== undefined) this.compositePass.uniforms.uVignetteIntensity.value = params.vignette.intensity;
    }
  }

  /**
   * Main render call
   */
  render(time: number, state: VoiceState = 'idle', deltaTime: number = 16): void {
    this.uniforms.uTime.value = time;
    this.compositePass.uniforms.uTime.value = time;

    // Update breathing animation
    this.updateBreathe(state, deltaTime);

    // Render with post-processing
    this.composer.render();
  }

  /**
   * Clean up resources
   */
  dispose(): void {
    this.clearScene();
    this.renderer.dispose();
    this.composer.dispose();

    // Remove canvas from container
    if (this.renderer.domElement.parentNode) {
      this.renderer.domElement.parentNode.removeChild(this.renderer.domElement);
    }
  }
}
