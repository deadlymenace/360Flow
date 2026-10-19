import React, { useState } from 'react';
import { View, Text, StyleSheet, TouchableOpacity } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import { RootStackParamList } from '../navigation';
import { markCaptureCoachSeen } from '../services/privacy';

type Props = NativeStackScreenProps<RootStackParamList, 'CaptureCoach'>;

const CARDS = [
  {
    icon: '💡',
    title: 'Prep the room',
    body: 'Turn the lights on, open doors and blinds, switch off fans and TVs, and stand near the middle of the room.',
  },
  {
    icon: '🔄',
    title: 'Turn on the spot',
    body: 'Hold the phone upright at chest height. Pivot your body around the phone. Don’t swing your arm.',
  },
  {
    icon: '🎯',
    title: 'Aim and hold still',
    body: 'Aim the ring at the dot and hold still. It shoots for you. 16 photos make one full turn.',
  },
];

export default function CaptureCoachScreen({ navigation, route }: Props) {
  const insets = useSafeAreaInsets();
  const [index, setIndex] = useState(0);
  const next = route.params?.next;
  const card = CARDS[index];
  const last = index === CARDS.length - 1;

  const finish = () => {
    markCaptureCoachSeen();
    if (next) navigation.replace('Capture', next);
    else navigation.goBack();
  };

  return (
    <View style={[styles.container, { paddingTop: insets.top + 16, paddingBottom: insets.bottom + 16 }]}>
      <View style={styles.topRow}>
        <Text style={styles.step} accessibilityLabel={`Tip ${index + 1} of ${CARDS.length}`}>
          {index + 1} / {CARDS.length}
        </Text>
        <TouchableOpacity onPress={finish} style={styles.skip} accessibilityRole="button" accessibilityLabel="Skip tips">
          <Text style={styles.skipText}>Skip</Text>
        </TouchableOpacity>
      </View>
      <View style={styles.card}>
        <Text style={styles.icon} accessible={false}>{card.icon}</Text>
        <Text style={styles.title} accessibilityRole="header">{card.title}</Text>
        <Text style={styles.body}>{card.body}</Text>
      </View>
      <View style={styles.dots} accessible={false}>
        {CARDS.map((_, i) => (
          <View key={i} style={[styles.dot, i === index && styles.dotOn]} />
        ))}
      </View>
      <View style={styles.actions}>
        {index > 0 ? (
          <TouchableOpacity style={styles.secondary} onPress={() => setIndex(index - 1)} accessibilityRole="button">
            <Text style={styles.secondaryText}>Back</Text>
          </TouchableOpacity>
        ) : null}
        <TouchableOpacity
          style={styles.primary}
          onPress={last ? finish : () => setIndex(index + 1)}
          accessibilityRole="button"
        >
          <Text style={styles.primaryText}>{last ? (next ? 'Start capturing' : 'Got it') : 'Next'}</Text>
        </TouchableOpacity>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#1a1a2e', paddingHorizontal: 24 },
  topRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  step: { color: '#b8b5d1', fontSize: 15, fontWeight: '600' },
  skip: { minHeight: 44, minWidth: 44, justifyContent: 'center', alignItems: 'flex-end' },
  skipText: { color: '#8F88FF', fontSize: 16, fontWeight: '600' },
  card: { flex: 1, justifyContent: 'center', alignItems: 'center', gap: 16 },
  icon: { fontSize: 64 },
  title: { color: '#fff', fontSize: 28, fontWeight: '700', textAlign: 'center' },
  body: { color: '#d5d2ea', fontSize: 17, lineHeight: 25, textAlign: 'center' },
  dots: { flexDirection: 'row', justifyContent: 'center', gap: 8, marginBottom: 24 },
  dot: { width: 8, height: 8, borderRadius: 4, backgroundColor: '#3d3866' },
  dotOn: { backgroundColor: '#8F88FF' },
  actions: { flexDirection: 'row', gap: 12 },
  primary: { flex: 1, backgroundColor: '#6C63FF', borderRadius: 14, minHeight: 52, alignItems: 'center', justifyContent: 'center' },
  primaryText: { color: '#fff', fontWeight: '700', fontSize: 16 },
  secondary: {
    flex: 1,
    borderRadius: 14,
    minHeight: 52,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: '#3d3866',
  },
  secondaryText: { color: '#fff', fontWeight: '600', fontSize: 16 },
});
