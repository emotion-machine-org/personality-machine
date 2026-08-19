/**
 * Audio processing utilities for voice amplitude detection
 */

/**
 * Fast amplitude calculation from PCM Int16 data
 * Samples every Nth value to reduce CPU usage
 * Returns RMS amplitude normalized to 0-1
 */
export function calculateAmplitudeFromPCM(
  pcmData: ArrayBuffer | Int16Array,
  sampleEvery = 8
): number {
  const int16 = pcmData instanceof Int16Array ? pcmData : new Int16Array(pcmData)
  let sum = 0
  let count = 0

  for (let i = 0; i < int16.length; i += sampleEvery) {
    const sample = int16[i] / 0x8000
    sum += sample * sample
    count++
  }

  if (count === 0) return 0
  return Math.sqrt(sum / count)
}

/**
 * Apply exponential smoothing to amplitude values
 * Helps create smoother animations
 */
export function smoothAmplitude(
  currentValue: number,
  newValue: number,
  smoothingFactor = 0.3
): number {
  return currentValue * (1 - smoothingFactor) + newValue * smoothingFactor
}
