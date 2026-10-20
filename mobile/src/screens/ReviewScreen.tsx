import React, { useState } from 'react';
import {
  Alert,
  View,
  Text,
  Image,
  TouchableOpacity,
  StyleSheet,
  ScrollView,
  Dimensions,
  Modal,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import { RootStackParamList } from '../navigation';
import { SEGMENT_ANGLE, SEGMENT_COUNT } from '../models/types';
import { hasAiDisclosureConsent } from '../services/privacy';

const { width: SCREEN_WIDTH } = Dimensions.get('window');
const GRID_PADDING = 16;
const GRID_GAP = 8;
const THUMB_SIZE = (SCREEN_WIDTH - GRID_PADDING * 2 - GRID_GAP * 3) / 4;

type Props = NativeStackScreenProps<RootStackParamList, 'Review'>;

export default function ReviewScreen({ navigation, route }: Props) {
  const { projectId, projectName, hfov, lens } = route.params;
  const insets = useSafeAreaInsets();
  const [preview, setPreview] = useState<number | null>(null);
  // Photos deleted here; Capture clears them too when we go back to re-shoot.
  const [deleted, setDeleted] = useState<number[]>([]);
  const segments = route.params.segments.map((s) => (deleted.includes(s.id) ? { ...s, imageUri: null } : s));
  const previewSeg = preview != null ? segments.find((s) => s.id === preview) : null;

  const retake = (index: number) => {
    setPreview(null);
    navigation.popTo('Capture', {
      projectId,
      projectName,
      retakeIndex: index,
      retakeNonce: Date.now(),
      clearIndices: deleted,
    });
  };

  const deletePhoto = (index: number) => {
    Alert.alert(`Delete photo ${index + 1}?`, "You'll need to take it again before creating the tour.", [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Delete photo',
        style: 'destructive',
        onPress: () => {
          setPreview(null);
          setDeleted((current) => (current.includes(index) ? current : [...current, index]));
        },
      },
    ]);
  };

  const capturedCount = segments.filter((s) => s.imageUri !== null).length;
  const allCaptured = capturedCount === SEGMENT_COUNT;

  const handleUpload = () => {
    const ordered = segments.filter((s) => s.imageUri !== null);
    const params = {
      projectId,
      projectName,
      imageUris: ordered.map((s) => s.imageUri as string),
      yaws: ordered.map((s) => s.actualYaw ?? s.id * SEGMENT_ANGLE),
      hfov,
      lens,
    };
    if (hasAiDisclosureConsent()) {
      navigation.navigate('Processing', params);
      return;
    }
    navigation.navigate('PrivacyConsent', params);
  };

  return (
    <View style={styles.container}>
      <View style={[styles.header, { paddingTop: insets.top + 8 }]}>
        <TouchableOpacity
          onPress={() => navigation.goBack()}
          style={styles.hit}
          hitSlop={12}
          accessibilityRole="button"
          accessibilityLabel="Back to capture"
        >
          <Text style={styles.backText}>← Back</Text>
        </TouchableOpacity>
        <Text style={styles.title} accessibilityRole="header">Check your photos</Text>
        <View style={{ width: 60 }} />
      </View>
      <Text style={styles.subhint}>Tap any photo to retake it</Text>

      <ScrollView contentContainerStyle={styles.grid}>
        {segments.map((seg) => (
          <TouchableOpacity
            key={seg.id}
            style={styles.thumbContainer}
            onPress={() => (seg.imageUri ? setPreview(seg.id) : retake(seg.id))}
            activeOpacity={0.7}
            accessibilityRole="button"
            accessibilityLabel={seg.imageUri ? `Photo ${seg.id + 1}. Tap to preview or retake` : `Photo ${seg.id + 1} missing. Tap to capture`}
          >
            {seg.imageUri ? (
              <Image source={{ uri: seg.imageUri }} style={styles.thumb} />
            ) : (
              <View style={styles.emptyThumb}>
                <Text style={styles.emptyThumbText}>—</Text>
              </View>
            )}
            <View style={styles.badge}>
              <Text style={styles.badgeText}>{seg.id + 1}</Text>
            </View>
          </TouchableOpacity>
        ))}
      </ScrollView>

      <View style={[styles.bottomBar, { paddingBottom: insets.bottom + 16 }]}>
        <Text style={styles.statusText}>
          {capturedCount} of {SEGMENT_COUNT} photos
        </Text>
        <TouchableOpacity
          style={[styles.uploadButton, !allCaptured && styles.disabledButton]}
          onPress={handleUpload}
          disabled={!allCaptured}
          accessibilityRole="button"
          accessibilityState={{ disabled: !allCaptured }}
        >
          <Text style={styles.uploadButtonText}>Create 360° tour</Text>
        </TouchableOpacity>
      </View>

      <Modal visible={previewSeg != null} animationType="fade" onRequestClose={() => setPreview(null)}>
        <View style={[styles.previewContainer, { paddingTop: insets.top + 8, paddingBottom: insets.bottom + 16 }]}>
          <View style={styles.previewHeader}>
            <TouchableOpacity
              onPress={() => setPreview(null)}
              style={styles.hit}
              hitSlop={12}
              accessibilityRole="button"
              accessibilityLabel="Close preview"
            >
              <Text style={styles.backText}>Close</Text>
            </TouchableOpacity>
            <Text style={styles.title}>Photo {previewSeg ? previewSeg.id + 1 : ''}</Text>
            <View style={{ width: 60 }} />
          </View>
          {previewSeg?.imageUri ? (
            <Image
              source={{ uri: previewSeg.imageUri }}
              style={styles.previewImage}
              resizeMode="contain"
              accessibilityLabel={`Photo ${previewSeg.id + 1}`}
            />
          ) : null}
          {previewSeg ? (
            <TouchableOpacity style={styles.uploadButton} onPress={() => retake(previewSeg.id)} accessibilityRole="button">
              <Text style={styles.uploadButtonText}>Retake #{previewSeg.id + 1}</Text>
            </TouchableOpacity>
          ) : null}
          {previewSeg?.imageUri ? (
            <TouchableOpacity
              style={styles.deletePhotoButton}
              onPress={() => deletePhoto(previewSeg.id)}
              accessibilityRole="button"
              accessibilityLabel={`Delete photo ${previewSeg.id + 1}`}
            >
              <Text style={styles.deletePhotoText}>Delete photo</Text>
            </TouchableOpacity>
          ) : null}
        </View>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  deletePhotoButton: { marginTop: 12, minHeight: 44, alignItems: 'center', justifyContent: 'center' },
  deletePhotoText: { color: '#FF6B6B', fontWeight: '700', fontSize: 16 },
  container: {
    flex: 1,
    backgroundColor: '#1a1a2e',
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 20,
    paddingBottom: 8,
  },
  hit: { minHeight: 44, minWidth: 60, justifyContent: 'center' },
  subhint: { color: '#b8b5d1', fontSize: 14, textAlign: 'center', marginBottom: 4 },
  previewContainer: { flex: 1, backgroundColor: '#000', paddingHorizontal: 16, gap: 16 },
  previewHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  previewImage: { flex: 1, width: '100%' },
  backText: {
    color: '#8F88FF',
    fontSize: 16,
    fontWeight: '500',
  },
  title: {
    color: '#fff',
    fontSize: 18,
    fontWeight: '700',
  },
  grid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    padding: GRID_PADDING,
    gap: GRID_GAP,
  },
  thumbContainer: {
    width: THUMB_SIZE,
    height: THUMB_SIZE,
    borderRadius: 10,
    overflow: 'hidden',
  },
  thumb: {
    width: '100%',
    height: '100%',
    borderRadius: 10,
  },
  emptyThumb: {
    width: '100%',
    height: '100%',
    backgroundColor: '#333',
    borderRadius: 10,
    justifyContent: 'center',
    alignItems: 'center',
  },
  emptyThumbText: {
    color: '#666',
    fontSize: 20,
  },
  badge: {
    position: 'absolute',
    top: 4,
    right: 4,
    backgroundColor: 'rgba(0,0,0,0.6)',
    borderRadius: 10,
    paddingHorizontal: 6,
    paddingVertical: 2,
  },
  badgeText: {
    color: '#fff',
    fontSize: 11,
    fontWeight: '600',
  },
  bottomBar: {
    padding: 20,
    alignItems: 'center',
    gap: 12,
  },
  statusText: {
    color: '#b8b5d1',
    fontSize: 14,
  },
  uploadButton: {
    backgroundColor: '#6C63FF',
    paddingHorizontal: 40,
    paddingVertical: 16,
    borderRadius: 14,
    width: '100%',
    alignItems: 'center',
  },
  uploadButtonText: {
    color: '#fff',
    fontSize: 16,
    fontWeight: '700',
  },
  disabledButton: {
    opacity: 0.4,
  },
});
