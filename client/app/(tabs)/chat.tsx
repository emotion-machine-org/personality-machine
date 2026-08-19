import {
    View,
    Text,
    TextInput,
    StyleSheet,
    Pressable,
    SafeAreaView,
  } from 'react-native';
  import MaterialIcons from '@expo/vector-icons/MaterialIcons';
import { router } from 'expo-router';
import React, { useState } from 'react';
  import { usePipecatSession } from '../../hooks/usePipecatSession';
  import { theme } from '@/theme';

  const API_BASE = process.env.EXPO_PUBLIC_API_URL ?? 'http://localhost:8101';

  export default function ChatScreen() {
    const { isPlaying, togglePlay, config, startSession } = usePipecatSession();

    // Local name editing state (persist via API on confirm)
    const [name, setName] = useState('Friend');
    const [editing, setEditing] = useState(false);
    const [draft, setDraft] = useState(name);

    const saveCompanionName = async (newName: string) => {
      try {
        const res = await fetch(`${API_BASE}/api/companion-name`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: newName })
        });
        if (!res.ok) {
          console.log('[name] save failed (likely auth needed):', res.status);
        }
      } catch (e) {
        console.log('[name] save error:', e);
      }
    };

    return (
      <SafeAreaView style={styles.wrapper}>
        {/* ─── Header bar ─────────────────────────────────────────────── */}
        <View style={styles.header}>
          <Pressable onPress={() => router.back()}>
            <Text style={styles.headerLink}>{'←  Back to Builder View'}</Text>
          </Pressable>
          <Text style={styles.headerLink}>Version History</Text>
        </View>

        <View style={styles.centerBlock}>
          {/* ─── Companion title (inline editable) ────────────────────── */}
          {!editing ? (
            <Pressable onPress={() => { setDraft(name); setEditing(true); }}>
              <Text style={styles.title}>{name}</Text>
            </Pressable>
          ) : (
            <View style={styles.titleEditRow}>
              <TextInput
                value={draft}
                onChangeText={setDraft}
                autoFocus
                onSubmitEditing={() => {
                  setName(draft);
                  setEditing(false);
                  saveCompanionName(draft);
                }}
                style={styles.titleInput}
                placeholder="Companion name"
                placeholderTextColor={theme.placeholder}
              />
              <Pressable
                onPress={() => {
                  setName(draft);
                  setEditing(false);
                  saveCompanionName(draft);
                }}
                style={styles.checkButton}
                accessibilityLabel="Save name"
              >
                <MaterialIcons name="check" size={22} color={theme.bg} />
              </Pressable>
            </View>
          )}
          {/* ─── Big speak button ─────────────────────────────────────── */}
          <Pressable
            style={[
              styles.speakCircle,
              { backgroundColor: isPlaying ? theme.stop : theme.surface },
            ]}
            onPress={() => {
              if (isPlaying) {
                togglePlay();           // will hit stopSession() internally
              } else {
                startSession();
              }
            }}
          >
            <MaterialIcons
              name={isPlaying ? 'stop' : 'mic'}
              size={48}
              color={isPlaying ? theme.textPrimary : theme.bg}
            />
            {!isPlaying && <Text style={styles.speakText}>PRESS TO SPEAK</Text>}
          </Pressable>

          {/* ─── Prompt preview ────────────────────────────────────────── */}
          <Text numberOfLines={1} style={styles.promptPreview}>
            {config.systemPrompt || 'You are a friend helping me with …'}
          </Text>
        </View>
      </SafeAreaView>
    );
  }

  const CIRCLE = 260;

  const styles = StyleSheet.create({
    wrapper: {
      flex: 1,
      backgroundColor: theme.bg,
      paddingTop: 8,
    },
    header: {
      flexDirection: 'row',
      justifyContent: 'space-between',
      paddingHorizontal: 16,
      marginBottom: 24,
    },
    headerLink: {
      color: theme.textSecondary,
      fontSize: 14,
    },
    title: {
      alignSelf: 'center',
      color: theme.textPrimary,
      fontSize: 32,
      fontWeight: '400',
      marginBottom: 32,
    },
    titleEditRow: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 8,
      marginBottom: 32,
    },
    titleInput: {
      minWidth: 180,
      alignSelf: 'center',
      color: theme.textPrimary,
      fontSize: 28,
      fontWeight: '400',
      paddingVertical: 6,
      paddingHorizontal: 12,
      backgroundColor: theme.disabledBg,
      borderRadius: 10,
    },
    checkButton: {
      marginLeft: 8,
      paddingHorizontal: 10,
      paddingVertical: 10,
      backgroundColor: theme.surface,
      borderRadius: 20,
    },
    speakCircle: {
      alignSelf: 'center',
      width: CIRCLE,
      height: CIRCLE,
      borderRadius: CIRCLE / 2,
      justifyContent: 'center',
      alignItems: 'center',
      gap: 8,
    },
    speakText: {
      fontSize: 14,
      color: theme.textTertiary,
    },
    centerBlock: {
        flex: 1,
        justifyContent: 'center',
        alignItems: 'center',
        paddingHorizontal: 32,
    },
    promptPreview: {
      marginTop: 32,
      textAlign: 'center',
      color: theme.textSecondary,
      fontSize: 13,
      maxWidth: '90%',
    },
  });
