import React from 'react';
import { View, Text, StyleSheet, TouchableOpacity, ScrollView } from 'react-native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import { RootStackParamList } from '../navigation';
import { acceptAiDisclosure } from '../services/privacy';

type Props = NativeStackScreenProps<RootStackParamList, 'PrivacyConsent'>;

export default function PrivacyConsentScreen({ navigation, route }: Props) {
  const agree = () => {
    acceptAiDisclosure();
    navigation.replace('Processing', route.params);
  };

  return (
    <View style={styles.container}>
      <ScrollView contentContainerStyle={styles.content}>
        <Text style={styles.kicker}>Before your photos are uploaded</Text>
        <Text style={styles.title}>Photos are sent to 360Flow and to AI services</Text>
        <Text style={styles.body}>
          Uploading this capture sends your room photos to the 360Flow server, which stitches them into a
          panorama. That panorama is sent to World Labs to build the 3D walkthrough.
        </Text>
        <Text style={styles.body}>
          If you later choose virtual staging, the panorama is sent to Google Gemini so it can add furniture.
          Staging is optional.
        </Text>
        <Text style={styles.body}>
          360Flow does not create a user account. You can delete a capture, its photos, and its share link
          from the home screen. Deleting a capture also asks World Labs to remove the 3D world.
        </Text>
      </ScrollView>
      <View style={styles.actions}>
        <TouchableOpacity style={styles.secondary} onPress={() => navigation.goBack()}>
          <Text style={styles.secondaryText}>Not now</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.primary} onPress={agree}>
          <Text style={styles.primaryText}>Agree and upload</Text>
        </TouchableOpacity>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#1a1a2e' },
  content: { paddingTop: 72, paddingHorizontal: 24, paddingBottom: 24, gap: 16 },
  kicker: { color: '#ffe08a', fontWeight: '700', fontSize: 13, letterSpacing: 0.3 },
  title: { color: '#fff', fontSize: 26, fontWeight: '700', lineHeight: 32 },
  body: { color: '#d5d2ea', fontSize: 16, lineHeight: 24 },
  actions: { padding: 20, paddingBottom: 36, gap: 10 },
  primary: { backgroundColor: '#6C63FF', borderRadius: 14, paddingVertical: 16, alignItems: 'center' },
  primaryText: { color: '#fff', fontWeight: '700', fontSize: 16 },
  secondary: {
    borderRadius: 14,
    paddingVertical: 14,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: '#3d3866',
  },
  secondaryText: { color: '#fff', fontWeight: '600' },
});
