// Square vertex shader with instancing
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
}
