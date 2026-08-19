// Soft disk fragment shader
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
}
