// Shader source exports
// These are inline strings for Next.js compatibility

export const pillVertSrc = `// Pill/capsule vertex shader with instancing
precision highp float;

// Instance attributes
attribute vec3 instancePosition;  // x, y, rotation
attribute vec3 instanceScale;     // length, thickness, taper
attribute vec3 instanceColor;
attribute float instanceSeed;

// Outputs to fragment
varying vec2 vUv;
varying vec3 vColor;
varying vec2 vDimensions;  // length, thickness for SDF
varying float vTaper;
varying float vSeed;

uniform float uTime;
uniform float uAspect;
uniform float uThicknessMultiplier;

void main() {
  vUv = uv;
  vColor = instanceColor;
  float thickness = instanceScale.y * uThicknessMultiplier;
  vDimensions = vec2(instanceScale.x, thickness);
  vTaper = instanceScale.z;
  vSeed = instanceSeed;

  // Build rotation transform from instance rotation
  float rotation = instancePosition.z;
  float c = cos(rotation);
  float s = sin(rotation);
  mat2 rot = mat2(c, s, -s, c);

  // Scale the quad to pill dimensions
  // Extra 2x padding to prevent glow clipping at high zoom
  vec2 scaled = position.xy * vec2(instanceScale.x, thickness) * 2.0;

  // Rotate
  vec2 rotated = rot * scaled;

  // Translate to instance position
  vec2 pos = rotated + instancePosition.xy;

  // Output in world space, let camera handle projection
  gl_Position = projectionMatrix * modelViewMatrix * vec4(pos, 0.0, 1.0);
}`;

export const pillFragSrc = `// Rounded rectangle fragment shader with soft glow
precision highp float;

varying vec2 vUv;
varying vec3 vColor;
varying vec2 vDimensions;
varying float vTaper;
varying float vSeed;

uniform float uTime;
uniform float uEdgeNoiseScale;
uniform float uEdgeNoiseAmount;
uniform float uGlowIntensity;
uniform float uGlowRadius;

// Simple hash for noise
float hash(vec2 p) {
  return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
}

// Value noise
float noise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  f = f * f * (3.0 - 2.0 * f);

  float a = hash(i);
  float b = hash(i + vec2(1.0, 0.0));
  float c = hash(i + vec2(0.0, 1.0));
  float d = hash(i + vec2(1.0, 1.0));

  return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
}

// FBM for subtle edge variation
float fbm(vec2 p) {
  float value = 0.0;
  float amplitude = 0.5;

  for (int i = 0; i < 3; i++) {
    value += amplitude * noise(p);
    p *= 2.0;
    amplitude *= 0.5;
  }
  return value;
}

// SDF for rounded rectangle - small corner radius
float sdRoundedRect(vec2 p, vec2 halfSize, float radius) {
  vec2 q = abs(p) - halfSize + radius;
  return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - radius;
}

void main() {
  // UV centered: -0.5 to 0.5, scaled by 2 to match vertex shader padding
  vec2 p = (vUv - 0.5) * 2.0;

  // Aspect ratio of the pill quad
  float aspect = vDimensions.x / vDimensions.y;

  // Scale p to account for quad aspect
  p.x *= aspect;

  // Rectangle half-size in normalized space (same as before, glow extends into padding)
  vec2 halfSize = vec2(aspect * 0.5, 0.5);

  // Small corner radius - just slightly rounded, not full capsule
  float cornerRadius = 0.15; // Much smaller than 0.5 which would be full capsule

  // Apply subtle taper (slightly thicker in middle)
  float taperFactor = 1.0 + vTaper * 0.2 * (1.0 - 4.0 * pow(p.x / halfSize.x, 2.0));
  halfSize.y *= taperFactor;

  // SDF distance
  float d = sdRoundedRect(p, halfSize * 0.92, cornerRadius);

  // Subtle edge noise for organic feel
  vec2 noiseCoord = vUv * uEdgeNoiseScale + vSeed * 10.0;
  float edgeNoise = fbm(noiseCoord) * 2.0 - 1.0;
  d += edgeNoise * uEdgeNoiseAmount * 0.006;

  // Soft anti-aliased edge using screen-space derivatives
  float pixelWidth = fwidth(d) * 1.5;
  float shape = 1.0 - smoothstep(-pixelWidth, pixelWidth, d);

  // Subtle inner glow
  float innerGlow = exp(-abs(d) * 4.0) * 0.2;

  // Outer glow halo
  float outerGlow = exp(-max(d, 0.0) * uGlowRadius * 8.0) * uGlowIntensity * 0.3;

  // Combine
  vec3 color = vColor * (shape + innerGlow * 0.5);

  // Alpha
  float alpha = shape + outerGlow * 0.4;

  if (alpha < 0.001) discard;

  gl_FragColor = vec4(color, alpha);
}`;

export const diskVertSrc = `// Disk vertex shader with instancing
precision highp float;

attribute vec3 instancePosition;  // x, y, (unused)
attribute vec3 instanceScale;     // size, softness, (unused)
attribute vec3 instanceColor;
attribute float instanceSeed;

varying vec2 vUv;
varying vec3 vColor;
varying float vSoftness;
varying float vSeed;

uniform float uTime;
uniform float uAspect;

void main() {
  vUv = uv;
  vColor = instanceColor;
  vSoftness = instanceScale.y;
  vSeed = instanceSeed;

  // Scale the quad to disk size (extra 2x padding for glow at high zoom)
  vec2 scaled = position.xy * instanceScale.x * 4.0;

  // Translate
  vec2 pos = scaled + instancePosition.xy;

  // Output in world space
  gl_Position = projectionMatrix * modelViewMatrix * vec4(pos, 0.0, 1.0);
}`;

export const diskFragSrc = `// Soft disk fragment shader
precision highp float;

varying vec2 vUv;
varying vec3 vColor;
varying float vSoftness;
varying float vSeed;

uniform float uTime;
uniform float uGlowIntensity;

// Hash for subtle variation
float hash(vec2 p) {
  return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
}

void main() {
  // Distance from center (4.0 to match vertex padding)
  vec2 centered = vUv - 0.5;
  float d = length(centered) * 4.0;

  // Soft circular falloff
  float softness = max(vSoftness, 0.1);
  float shape = 1.0 - smoothstep(1.0 - softness, 1.0, d);

  // Extra glow
  float glow = exp(-d * 2.0) * uGlowIntensity * 0.5;

  // Subtle noise variation in color
  float n = hash(vUv * 10.0 + vSeed) * 0.05;

  vec3 color = vColor * (shape + glow) + n * vColor;
  float alpha = shape + glow * 0.3;

  if (alpha < 0.001) discard;

  gl_FragColor = vec4(color, alpha);
}`;

export const squareVertSrc = `// Square vertex shader with instancing
precision highp float;

attribute vec3 instancePosition;  // x, y, rotation
attribute vec3 instanceScale;     // size, (unused), (unused)
attribute vec3 instanceColor;
attribute float instanceSeed;

varying vec2 vUv;
varying vec3 vColor;
varying float vSeed;

uniform float uTime;
uniform float uAspect;
uniform float uThicknessMultiplier;

void main() {
  vUv = uv;
  vColor = instanceColor;
  vSeed = instanceSeed;

  // Build rotation transform
  float rotation = instancePosition.z;
  float c = cos(rotation);
  float s = sin(rotation);
  mat2 rot = mat2(c, s, -s, c);

  // Scale the quad to square size (with breathing, extra 2x padding for glow at high zoom)
  float size = instanceScale.x * uThicknessMultiplier;
  vec2 scaled = position.xy * size * 4.0;

  // Rotate
  vec2 rotated = rot * scaled;

  // Translate to instance position
  vec2 pos = rotated + instancePosition.xy;

  // Output in world space
  gl_Position = projectionMatrix * modelViewMatrix * vec4(pos, 0.0, 1.0);
}`;

export const squareFragSrc = `// Square fragment shader with soft edges
precision highp float;

varying vec2 vUv;
varying vec3 vColor;
varying float vSeed;

uniform float uTime;
uniform float uGlowIntensity;

// SDF for rounded square
float sdRoundedBox(vec2 p, vec2 b, float r) {
  vec2 q = abs(p) - b + r;
  return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r;
}

void main() {
  // UV centered: -0.5 to 0.5, scaled by 2 to match vertex shader padding
  vec2 p = (vUv - 0.5) * 2.0;

  // Square half-size with small corner radius (same visual size, glow extends into padding)
  vec2 halfSize = vec2(0.45);
  float cornerRadius = 0.08;

  // SDF distance
  float d = sdRoundedBox(p, halfSize, cornerRadius);

  // Anti-aliased edge
  float pixelWidth = fwidth(d) * 1.5;
  float shape = 1.0 - smoothstep(-pixelWidth, pixelWidth, d);

  // Subtle glow
  float glow = exp(-max(d, 0.0) * 8.0) * uGlowIntensity * 0.3;

  vec3 color = vColor * shape;
  float alpha = shape + glow * 0.4;

  if (alpha < 0.001) discard;

  gl_FragColor = vec4(color, alpha);
}`;

export const compositeVertSrc = `// Simple fullscreen quad vertex shader
precision highp float;

varying vec2 vUv;

void main() {
  vUv = uv;
  gl_Position = vec4(position.xy, 0.0, 1.0);
}`;

export const compositeFragSrc = `// Composite shader with chromatic aberration, film grain, and vignette
// Bloom is handled by UnrealBloomPass, so this just adds finishing touches
precision highp float;

varying vec2 vUv;

uniform sampler2D tDiffuse;  // Input from previous pass (Three.js convention)
uniform float uTime;
uniform vec2 uResolution;

// Chromatic aberration
uniform float uChromaticAberration;

// Film grain
uniform float uGrainIntensity;
uniform float uGrainSize;

// Vignette
uniform float uVignetteIntensity;
uniform float uVignetteRadius;

// V2: Brightness control for softer film-like look
uniform float uBrightness;

// Hash for grain
float hash(vec2 p) {
  vec3 p3 = fract(vec3(p.xyx) * 0.1031);
  p3 += dot(p3, p3.yzx + 33.33);
  return fract((p3.x + p3.y) * p3.z);
}

// Animated grain
float grain(vec2 uv, float time) {
  vec2 grainUv = uv * uResolution / uGrainSize;
  return hash(grainUv + fract(time * 0.1)) * 2.0 - 1.0;
}

void main() {
  vec2 uv = vUv;
  vec2 center = vec2(0.5);

  // Chromatic aberration - subtle RGB split from center
  vec2 dir = uv - center;
  float dist = length(dir);
  vec2 offset = dir * dist * uChromaticAberration * 0.02;

  float r = texture2D(tDiffuse, uv + offset).r;
  float g = texture2D(tDiffuse, uv).g;
  float b = texture2D(tDiffuse, uv - offset).b;
  vec3 color = vec3(r, g, b);

  // Film grain
  float g1 = grain(uv, uTime);
  color += g1 * uGrainIntensity * 0.1;

  // Vignette
  float vignette = 1.0 - smoothstep(uVignetteRadius, uVignetteRadius + 0.3, dist * 1.4);
  color *= mix(1.0, vignette, uVignetteIntensity);

  // V2: Apply brightness control for softer film-like look
  color *= uBrightness;

  gl_FragColor = vec4(color, 1.0);
}`;
