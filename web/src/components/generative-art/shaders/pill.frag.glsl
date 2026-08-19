// Rounded rectangle fragment shader with soft glow
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
}
