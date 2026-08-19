/**
 * Random utilities with seedable RNG
 */

export type RngFunction = () => number;

/**
 * Create a seeded random number generator
 * Uses mulberry32 algorithm
 */
export function createRng(seed: number): RngFunction {
  let state = seed >>> 0;

  return function (): number {
    state = (state + 0x6d2b79f5) | 0;
    let t = Math.imul(state ^ (state >>> 15), 1 | state);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/**
 * Random integer in range [min, max]
 */
export function randomInt(min: number, max: number, rng: RngFunction = Math.random): number {
  return Math.floor(rng() * (max - min + 1)) + min;
}

/**
 * Random float in range [min, max]
 */
export function randomRange(min: number, max: number, rng: RngFunction = Math.random): number {
  return min + rng() * (max - min);
}

/**
 * Gaussian (normal) random using Box-Muller transform
 */
export function randomGaussian(mean: number = 0, stdDev: number = 1, rng: RngFunction = Math.random): number {
  const u1 = rng();
  const u2 = rng();
  const z0 = Math.sqrt(-2.0 * Math.log(u1)) * Math.cos(2.0 * Math.PI * u2);
  return z0 * stdDev + mean;
}

/**
 * Hash a string to a number (for seeding)
 */
export function hashString(str: string): number {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    const char = str.charCodeAt(i);
    hash = ((hash << 5) - hash) + char;
    hash = hash & hash;
  }
  return Math.abs(hash);
}
