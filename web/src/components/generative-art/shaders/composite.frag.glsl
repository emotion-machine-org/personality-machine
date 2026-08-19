// Composite shader with chromatic aberration, film grain, and vignette
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

// Vignette (disabled for seamless background)
// uniform float uVignetteIntensity;
// uniform float uVignetteRadius;

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

  // Film grain (subtle)
  float g1 = grain(uv, uTime);
  color += g1 * uGrainIntensity * 0.1;

  gl_FragColor = vec4(color, 1.0);
}
