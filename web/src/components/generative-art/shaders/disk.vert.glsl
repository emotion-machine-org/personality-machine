// Disk vertex shader with instancing
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
}
