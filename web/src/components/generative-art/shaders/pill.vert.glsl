// Pill/capsule vertex shader with instancing
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
}
