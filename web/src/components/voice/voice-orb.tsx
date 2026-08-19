'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import './voice-orb.css';

/**
 * VoiceOrb - Animated voice interaction orb
 * Based on VoiceOrbV1_4_EM from Emotion Machine SDK
 *
 * Structure: Red circle (orb) with dark bars inside
 * - User speaking: Bars animate with voice amplitude
 * - AI speaking: Bars fade out, orb pulsates and glows
 */

const SPEAKING_THRESHOLD = 0.08;
const USER_SILENCE_BEFORE_PROCESSING = 1500;

type ConversationState = 'idle' | 'listening' | 'user' | 'processing' | 'companion';

const RED_COLOR = {
  main: '#FF5372',
  glow: 'rgba(255, 83, 114, 0.6)',
};

export interface VoiceOrbProps {
  connectionState: 'disconnected' | 'connecting' | 'connected';
  isPaused?: boolean;
  isCompanionSpeaking: boolean;
  companionAmplitude: number;
  userAmplitude: number;
  onConnect: () => void;
  onDisconnect: () => void;
  onPause?: () => void;
  onResume?: () => void;
  error?: string | null;
  className?: string;
}

export function VoiceOrb({
  connectionState,
  isPaused = false,
  isCompanionSpeaking,
  companionAmplitude,
  userAmplitude,
  onConnect,
  onDisconnect,
  onPause,
  onResume,
  error,
  className,
}: VoiceOrbProps) {
  const [conversationState, setConversationState] = useState<ConversationState>('idle');
  const [waveOffset, setWaveOffset] = useState(0);
  const [showFilledOrb, setShowFilledOrb] = useState(false);
  const [isHovered, setIsHovered] = useState(false);

  const processingTimerRef = useRef<number | null>(null);
  const waveAnimationRef = useRef<number | null>(null);
  const filledOrbTimerRef = useRef<number | null>(null);

  const isConnected = connectionState === 'connected';
  const isConnecting = connectionState === 'connecting';
  const isActive = isConnected && !isPaused;

  // Update conversation state based on speaking detection
  useEffect(() => {
    if (!isActive) {
      setConversationState('idle');
      return;
    }

    const isUserSpeaking = userAmplitude > SPEAKING_THRESHOLD;

    if (isCompanionSpeaking) {
      if (processingTimerRef.current) {
        clearTimeout(processingTimerRef.current);
        processingTimerRef.current = null;
      }
      setConversationState('companion');
    } else if (isUserSpeaking) {
      if (processingTimerRef.current) {
        clearTimeout(processingTimerRef.current);
        processingTimerRef.current = null;
      }
      setConversationState('user');
    } else if (conversationState === 'user') {
      if (!processingTimerRef.current) {
        processingTimerRef.current = window.setTimeout(() => {
          setConversationState('processing');
          processingTimerRef.current = null;
        }, USER_SILENCE_BEFORE_PROCESSING);
      }
    } else if (conversationState === 'companion') {
      setConversationState('listening');
    } else if (conversationState === 'idle') {
      setConversationState('listening');
    }
  }, [isActive, userAmplitude, isCompanionSpeaking, conversationState]);

  // Wave animation for bars
  useEffect(() => {
    if (isActive && (conversationState === 'user' || conversationState === 'listening')) {
      const startTime = Date.now();
      const animateWave = () => {
        const elapsed = Date.now() - startTime;
        setWaveOffset(elapsed);
        waveAnimationRef.current = requestAnimationFrame(animateWave);
      };
      animateWave();
    } else {
      if (waveAnimationRef.current) {
        cancelAnimationFrame(waveAnimationRef.current);
        waveAnimationRef.current = null;
      }
    }

    return () => {
      if (waveAnimationRef.current) {
        cancelAnimationFrame(waveAnimationRef.current);
      }
    };
  }, [isActive, conversationState]);

  // Handle delayed filled orb transition
  useEffect(() => {
    if (conversationState === 'processing') {
      filledOrbTimerRef.current = window.setTimeout(() => {
        setShowFilledOrb(true);
      }, 150);
    } else if (conversationState === 'companion') {
      setShowFilledOrb(true);
    } else {
      setShowFilledOrb(false);
      if (filledOrbTimerRef.current) {
        clearTimeout(filledOrbTimerRef.current);
        filledOrbTimerRef.current = null;
      }
    }
  }, [conversationState]);

  // Cleanup
  useEffect(() => {
    return () => {
      if (processingTimerRef.current) clearTimeout(processingTimerRef.current);
      if (filledOrbTimerRef.current) clearTimeout(filledOrbTimerRef.current);
    };
  }, []);

  const handleClick = useCallback(() => {
    if (isConnecting) {
      // Do nothing while connecting
      return;
    }

    if (!isConnected) {
      // Not connected - start session
      onConnect();
    } else if (isPaused) {
      // Paused - resume session
      if (onResume) {
        onResume();
      } else {
        onConnect(); // Fallback to onConnect if no onResume provided
      }
    } else {
      // Active - pause session
      if (onPause) {
        onPause();
      } else {
        onDisconnect(); // Fallback to onDisconnect if no onPause provided
      }
      if (processingTimerRef.current) clearTimeout(processingTimerRef.current);
    }
  }, [isConnected, isConnecting, isPaused, onConnect, onDisconnect, onPause, onResume]);

  // Determine phase
  const isCompanionPhase = conversationState === 'companion';
  const isProcessingPhase = conversationState === 'processing';
  const isUserPhase = conversationState === 'user' || conversationState === 'listening';

  // Show bars only when user is speaking/listening and orb is outline
  const showBars = isActive && isUserPhase && !showFilledOrb;

  // Orb background: transparent with red border normally, solid red when showFilledOrb is true
  const orbBackground = showFilledOrb ? '#FF5372' : 'transparent';
  const orbBorder = '3px solid #FF5372';

  // Amplify companion amplitude for more dramatic orb animation
  const amplifiedCompanionAmp = Math.min(1, companionAmplitude * 4);

  // Orb scale and glow - pulsates when AI speaks, no glow when user speaking
  const orbScale = isCompanionPhase ? 1 + amplifiedCompanionAmp * 0.45 : 1;
  const glowScale = isCompanionPhase ? 0.5 + amplifiedCompanionAmp * 2.2 : 0;
  const glowOpacity = isCompanionPhase ? 0.4 + amplifiedCompanionAmp * 0.4 : 0;
  const boxShadowSize = isCompanionPhase ? 50 + amplifiedCompanionAmp * 110 : 0;

  const getStateText = () => {
    if (isConnecting) return 'Connecting...';
    if (!isConnected) return '';
    if (isPaused) return 'Paused';

    switch (conversationState) {
      case 'idle':
        return '';
      case 'listening':
        return 'Listening...';
      case 'user':
        return 'You are speaking';
      case 'processing':
        return 'Thinking...';
      case 'companion':
        return 'Companion speaking';
      default:
        return '';
    }
  };

  // Get the label to show inside the orb
  const getOrbLabel = () => {
    if (!isConnected) return 'Start';
    if (isPaused) return 'Continue';
    if (isHovered) return 'Pause';
    return null; // Show bars instead
  };

  const orbLabel = getOrbLabel();

  return (
    <div className={`voice-orb-container ${className || ''}`}>
      {/* Main orb area - isolated from footer to prevent layout shift */}
      <div className="voice-orb-main">
        {/* Diffuse glow layer */}
        <motion.div
          className="voice-orb-glow"
          animate={{
            scale: glowScale,
            opacity: glowOpacity,
            background: `radial-gradient(circle, ${RED_COLOR.glow} 0%, transparent 70%)`,
          }}
          transition={{ duration: 0.3, ease: 'easeOut' }}
        />

        {/* Main orb - always visible */}
        <motion.button
          className="voice-orb-button"
          onClick={handleClick}
          onMouseEnter={() => setIsHovered(true)}
          onMouseLeave={() => setIsHovered(false)}
          animate={{
            scale: orbScale,
            boxShadow: `0 0 ${boxShadowSize}px ${RED_COLOR.glow}`,
            backgroundColor: orbBackground,
            border: orbBorder,
          }}
          transition={{ duration: 0.15, ease: 'easeOut' }}
          whileHover={{ scale: isActive ? orbScale : 1.05 }}
          whileTap={{ scale: 0.95 }}
        >
          <AnimatePresence mode="wait">
            {orbLabel ? (
              <motion.span
                key={`label-${orbLabel}`}
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="voice-orb-label"
              >
                {orbLabel}
              </motion.span>
            ) : (
              <motion.div
                key="active"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="voice-orb-indicator"
              >
                <motion.div
                  className="voice-orb-bars"
                  animate={{
                    opacity: showBars ? (isProcessingPhase ? 0.5 : 1) : 0,
                  }}
                  transition={{ duration: 0.3, ease: 'easeOut' }}
                >
                  {[0, 1, 2].map((i) => {
                    // Amplify userAmplitude for visible response
                    const amplifiedAmp = Math.min(1, userAmplitude * 8);

                    // Wave animation
                    const phaseShift = i * 2.1;
                    const waveFreq = 0.012;
                    const wave = Math.sin(waveOffset * waveFreq + phaseShift);

                    // Very minimal idle bounce when user is not speaking
                    const idleBounce = Math.sin(waveOffset * 0.002 + i * 1.5) * 0.05;

                    // Wave contribution scales with amplitude for dramatic effect when speaking
                    const waveContribution = wave * 0.5 * amplifiedAmp;

                    // Base height + amplitude-driven height + wave + idle bounce
                    const baseHeight = 0.35;
                    const amplitudeHeight = amplifiedAmp * (1.2 + i * 0.3);
                    const totalScale = baseHeight + amplitudeHeight + waveContribution + idleBounce;

                    return (
                      <motion.div
                        key={i}
                        className="voice-orb-bar"
                        animate={{
                          scaleY: Math.max(0.3, Math.min(2.5, totalScale)),
                        }}
                        transition={{ duration: 0.05 }}
                      />
                    );
                  })}
                </motion.div>
              </motion.div>
            )}
          </AnimatePresence>
        </motion.button>
      </div>

      {/* Footer area - fixed height to prevent layout shift */}
      <div className="voice-orb-footer">
        <motion.div
          className="voice-orb-state"
          animate={{ opacity: isConnected || isConnecting ? 1 : 0 }}
        >
          {getStateText()}
        </motion.div>

        {error && <div className="voice-orb-error">{error}</div>}
      </div>
    </div>
  );
}
