import React from 'react';
import {
  View,
  Text,
  Image,
  TouchableOpacity,
  StyleSheet,
  ScrollView,
  Dimensions,
} from 'react-native';
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
  const { projectId, projectName, hfov, lens, segments } = route.params;

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
      <View style={styles.header}>
        <TouchableOpacity onPress={() => navigation.goBack()}>
          <Text style={styles.backText}>← Back</Text>
        </TouchableOpacity>
        <Text style={styles.title}>Review Photos</Text>
        <View style={{ width: 60 }} />
      </View>

      <ScrollView contentContainerStyle={styles.grid}>
        {segments.map((seg) => (
          <TouchableOpacity
            key={seg.id}
            style={styles.thumbContainer}
            onPress={() => navigation.goBack()}
            activeOpacity={0.7}
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

      <View style={styles.bottomBar}>
        <Text style={styles.statusText}>
          {capturedCount} of {SEGMENT_COUNT} segments captured
        </Text>
        <TouchableOpacity
          style={[styles.uploadButton, !allCaptured && styles.disabledButton]}
          onPress={handleUpload}
          disabled={!allCaptured}
        >
          <Text style={styles.uploadButtonText}>Upload & Stitch</Text>
        </TouchableOpacity>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#1a1a2e',
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingTop: 60,
    paddingHorizontal: 20,
    paddingBottom: 16,
  },
  backText: {
    color: '#6C63FF',
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
    paddingBottom: 40,
    alignItems: 'center',
    gap: 12,
  },
  statusText: {
    color: '#888',
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
