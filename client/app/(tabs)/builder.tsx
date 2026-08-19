import {
    View,
    Text,
    TextInput,
    StyleSheet,
    Pressable,
    KeyboardAvoidingView,
    Platform,
  } from 'react-native';
  import { Picker } from '@react-native-picker/picker';
  import MaterialIcons from '@expo/vector-icons/MaterialIcons';
  import { useState, useEffect } from 'react';
  import { usePipecatSession } from '../../hooks/usePipecatSession';
  import { theme } from '@/theme';


const voices = [
  { id: 'alloy', label: 'Alloy (Neutral)' },
  { id: 'nova', label: 'Nova (Young Female)' },
  { id: 'shimmer', label: 'Shimmer (Gentle Female)' },
  { id: 'echo', label: 'Echo (Male)' },
  { id: 'fable', label: 'Fable (British Male)' },
  { id: 'onyx', label: 'Onyx (Deep Male)' }
]

  export default function BuilderScreen() {
    const { isPlaying, config, setConfig, togglePlay, sessionId, startSession } = usePipecatSession();

    /* Local state so typing isn't PATCH-ed on every keystroke */
    const [name, setName] = useState('My Companion');
    const [prompt, setPrompt] = useState(config.systemPrompt);

    // Set better default prompt if empty
    useEffect(() => {
      if (!config.systemPrompt) {
        const defaultPrompt = `You are a helpful and friendly companion. You speak in a conversational, warm tone and keep your responses concise but engaging. You're here to chat, answer questions, and provide support. Keep responses to 1-2 sentences unless more detail is specifically requested.`;
        setPrompt(defaultPrompt);
        setConfig({ systemPrompt: defaultPrompt });
      }
    }, [config.systemPrompt, setConfig]);



    return (
      <KeyboardAvoidingView
        style={styles.wrapper}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <Text style={styles.h1}>Create your companion</Text>

        {/* ─── Name field ─────────────────────────────────────────────── */}
        <Text style={styles.helper}>Pick a name for your companion</Text>
        <TextInput
          //style={styles.nameInput}
          placeholder="Companion Name"
          placeholderTextColor={theme.placeholder}
          underlineColorAndroid="transparent"
          value={name}
          onChangeText={setName}
          editable={!isPlaying}
          selectionColor={theme.surface}
          style={[styles.nameInput, Platform.OS === 'web' && styles.noOutline]}
        />

        {/* ─── Prompt field ───────────────────────────────────────────── */}
        <Text style={[styles.helper, { marginTop: 24 }]}>
          Define your companion's personality, role, and speaking style
        </Text>
        <TextInput
          multiline
          textAlignVertical="top"
          placeholder="You are a helpful and friendly companion..."
          placeholderTextColor={theme.placeholder}
          underlineColorAndroid="transparent"
          value={prompt}
          onChangeText={(text) => {
            setPrompt(text);        // keep local value for instant UI feedback
            setConfig({ systemPrompt: text });  // keep shared config in-sync
          }}
          //onEndEditing={() => setConfig({ systemPrompt: prompt })}
          editable={!isPlaying}
          selectionColor={theme.surface}
          style={[styles.promptInput, Platform.OS === 'web' && styles.noOutline]}
        />

        {/* ─── Voice picker ───────────────────────────────────────────── */}
        <Text style={[styles.helper, { marginTop: 24 }]}>Choose a voice</Text>

        <View style={styles.pickerFrame}>
          <Picker
            mode="dropdown"
            enabled={!isPlaying}
            selectedValue={config.voice}
            onValueChange={(v) => setConfig({ voice: v as string })}
            dropdownIconColor={theme.textPrimary}      // iOS / Android arrow
            style={[
              styles.picker,
              Platform.OS === 'web' && styles.pickerWeb, // new web tweaks
            ]}
            itemStyle={{ color: theme.textPrimary }}
          >
            {voices.map((voice) => (
              <Picker.Item key={voice.id} label={voice.label} value={voice.id} />
            ))}
          </Picker>

          {/* custom arrow only on web */}
          {Platform.OS === 'web' && (
            <MaterialIcons
              name="expand-more"
              size={20}
              color={theme.textPrimary}
              style={styles.pickerIcon}
              pointerEvents="none"   // lets clicks fall through
            />
          )}
        </View>

        {/* ─── Debug info ────────────────────────────────────────────── */}
        {__DEV__ && (
          <View style={styles.debugInfo}>
            <Text style={styles.debugText}>Debug Info:</Text>
            <Text style={styles.debugText}>Session: {sessionId || 'None'}</Text>
            <Text style={styles.debugText}>Status: {isPlaying ? 'Active' : 'Inactive'}</Text>
            <Text style={styles.debugText}>Voice: {config.voice}</Text>
          </View>
        )}

        {/* ─── Main CTA ──────────────────────────────────────────────── */}
        <Pressable
          style={[
            styles.cta,
            { backgroundColor: isPlaying ? theme.stop : theme.surface },
          ]}
          onPress={() => {
            if (isPlaying) {
              togglePlay();           // will hit stopSession() internally
            } else {
              startSession({ systemPrompt: prompt }); // pass current text
            }
          }}
        >
          <Text
            style={[
              styles.ctaText,
              { color: isPlaying ? theme.textPrimary : theme.bg },
            ]}
          >
            {isPlaying ? 'Stop session' : 'Start Voice Conversation'}
          </Text>
        </Pressable>

        {/* ─── Instructions ──────────────────────────────────────────── */}
        <Text style={styles.instructions}>
          {isPlaying
            ? '🎤 Microphone is active. Speak naturally and wait for responses.'
            : '💡 Tip: Make sure to allow microphone access when prompted.'
          }
        </Text>
      </KeyboardAvoidingView>
    );
  }

  const styles = StyleSheet.create({
    wrapper: {
      flex: 1,
      padding: 24,
      backgroundColor: theme.bg,
    },
    h1: {
      color: theme.textPrimary,
      fontSize: 24,
      fontWeight: '400',
      marginBottom: 8,
    },
    helper: {
      color: theme.textSecondary,
      fontSize: 13,
      marginBottom: 6,
    },
    nameInput: {
      backgroundColor: theme.disabledBg,
      borderRadius: 30,
      paddingHorizontal: 20,
      paddingVertical: 10,
      color: theme.textPrimary,
    },
    promptInput: {
      backgroundColor: theme.disabledBg,
      borderRadius: 12,
      padding: 16,
      minHeight: 120,
      color: theme.textPrimary,
    },
    pickerFrame: {
      backgroundColor: theme.disabledBg,
      borderRadius: 30,
      overflow: 'hidden',
      position: 'relative',
    },
    picker: {
      color: theme.textPrimary,     // matches other inputs
      height: 48,
      paddingHorizontal: 20,

      /* -------- web specific -------- */
      ...Platform.select({
        web: {
          /* let the grey wrapper show through */
          backgroundColor: 'transparent',
          borderRadius: 30,

          /* remove the default browser border */
          borderWidth: 0,

          /* hide Chromium/Firefox focus rings you already dislike */
          outlineStyle: 'none',
        },
      }),
    },
    pickerWeb: {
      backgroundColor: 'transparent',      // let grey show through
      appearance: 'none',                  // hide default arrow
      WebkitAppearance: 'none',
      MozAppearance: 'none',
      borderWidth: 0,                      // kill default border
      outlineStyle: 'none',                // no orange ring either
    } as any,
    pickerIcon: {
      position: 'absolute',
      right: 20,
      top: '50%',
      marginTop: -12,      // half the icon size to centre vertically
    },
    debugInfo: {
      marginTop: 16,
      padding: 12,
      backgroundColor: theme.disabledBg,
      borderRadius: 8,
    },
    debugText: {
      color: theme.textSecondary,
      fontSize: 11,
      fontFamily: 'monospace',
    },
    cta: {
      alignSelf: 'center',
      marginTop: 'auto',
      marginBottom: 8,
      paddingVertical: 16,
      paddingHorizontal: 32,
      borderRadius: 30,
      width: '100%',
    },
    ctaText: {
      textAlign: 'center',
      fontSize: 16,
      fontWeight: '400',
    },
    instructions: {
      textAlign: 'center',
      color: theme.textSecondary,
      fontSize: 12,
      marginBottom: 16,
    },
    noOutline: Platform.select({
      web: {
        outlineStyle: 'none',
        outlineWidth: 0,
      } as any,
      default: {},
    }),
  });
