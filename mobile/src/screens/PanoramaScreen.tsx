import React, { useState, useEffect, useRef } from 'react';
import {
  View,
  Text,
  Image,
  StyleSheet,
  ActivityIndicator,
  TouchableOpacity,
  Dimensions,
  PanResponder,
} from 'react-native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import { RootStackParamList } from '../navigation';
import { api } from '../services/api';

const { width: SCREEN_W, height: SCREEN_H } = Dimensions.get('window');

type Props = NativeStackScreenProps<RootStackParamList, 'Panorama'>;

export default function PanoramaScreen({ navigation, route }: Props) {
  const { projectId, projectName } = route.params;
  const [imageUri, setImageUri] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Pan state for scrollable panorama view
  const [offsetX, setOffsetX] = useState(0);
  const [offsetY, setOffsetY] = useState(0);
  const lastOffset = useRef({ x: 0, y: 0 });

  const panResponder = useRef(
    PanResponder.create({
      onStartShouldSetPanResponder: () => true,
      onMoveShouldSetPanResponder: () => true,
      onPanResponderGrant: () => {
        lastOffset.current = { x: offsetX, y: offsetY };
      },
      onPanResponderMove: (_, gesture) => {
        setOffsetX(lastOffset.current.x + gesture.dx);
        setOffsetY(
          Math.max(
            -(SCREEN_W / 2),
            Math.min(SCREEN_W / 2, lastOffset.current.y + gesture.dy)
          )
        );
      },
    })
  ).current;

  useEffect(() => {
    loadPanorama();
  }, []);

  const loadPanorama = async () => {
    try {
      setLoading(true);
      setError(null);
      const uri = await api.downloadPanorama(projectId);
      setImageUri(uri);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator size="large" color="#6C63FF" />
        <Text style={styles.loadingText}>Loading panorama...</Text>
      </View>
    );
  }

  if (error) {
    return (
      <View style={styles.centered}>
        <Text style={styles.errorText}>Failed to load panorama</Text>
        <Text style={styles.errorDetail}>{error}</Text>
        <TouchableOpacity style={styles.retryButton} onPress={loadPanorama}>
          <Text style={styles.retryButtonText}>Retry</Text>
        </TouchableOpacity>
      </View>
    );
  }

  // Equirectangular panoramas are 2:1 aspect ratio
  // Show at full screen width, pan to explore
  const imageWidth = SCREEN_W * 2;
  const imageHeight = SCREEN_W;

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <TouchableOpacity onPress={() => navigation.popToTop()}>
          <Text style={styles.backText}>← Home</Text>
        </TouchableOpacity>
        <Text style={styles.title}>{projectName}</Text>
        <View style={{ width: 60 }} />
      </View>

      <View style={styles.panoramaContainer} {...panResponder.panHandlers}>
        {imageUri && (
          <Image
            source={{ uri: imageUri }}
            style={[
              styles.panoramaImage,
              {
                width: imageWidth,
                height: imageHeight,
                transform: [
                  { translateX: offsetX },
                  { translateY: offsetY },
                ],
              },
            ]}
            resizeMode="cover"
          />
        )}
      </View>

      <View style={styles.hint}>
        <Text style={styles.hintText}>Drag to explore the panorama</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#000',
  },
  centered: {
    flex: 1,
    backgroundColor: '#1a1a2e',
    justifyContent: 'center',
    alignItems: 'center',
    padding: 40,
  },
  loadingText: {
    color: '#888',
    marginTop: 16,
    fontSize: 14,
  },
  errorText: {
    color: '#F44336',
    fontSize: 18,
    fontWeight: '600',
    marginBottom: 8,
  },
  errorDetail: {
    color: '#888',
    fontSize: 14,
    textAlign: 'center',
    marginBottom: 24,
  },
  retryButton: {
    backgroundColor: '#6C63FF',
    paddingHorizontal: 24,
    paddingVertical: 12,
    borderRadius: 12,
  },
  retryButtonText: {
    color: '#fff',
    fontWeight: '600',
  },
  header: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingTop: 60,
    paddingHorizontal: 20,
    paddingBottom: 12,
    backgroundColor: 'rgba(0,0,0,0.5)',
    zIndex: 10,
  },
  backText: {
    color: '#6C63FF',
    fontSize: 16,
    fontWeight: '500',
  },
  title: {
    color: '#fff',
    fontSize: 16,
    fontWeight: '600',
  },
  panoramaContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    overflow: 'hidden',
  },
  panoramaImage: {
    position: 'absolute',
  },
  hint: {
    position: 'absolute',
    bottom: 40,
    left: 0,
    right: 0,
    alignItems: 'center',
  },
  hintText: {
    color: 'rgba(255,255,255,0.5)',
    fontSize: 13,
    backgroundColor: 'rgba(0,0,0,0.5)',
    paddingHorizontal: 16,
    paddingVertical: 6,
    borderRadius: 12,
  },
});
