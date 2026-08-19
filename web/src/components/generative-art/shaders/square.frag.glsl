// Square fragment shader with soft edges
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
}
