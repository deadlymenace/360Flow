import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  View,
  Text,
  Image,
  StyleSheet,
  ActivityIndicator,
  TouchableOpacity,
  Dimensions,
  Animated,
  PanResponder,
  GestureResponderEvent,
  PanResponderGestureState,
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
  const [imageSize, setImageSize] = useState<{ w: number; h: number } | null>(null);

  // Animated values for smooth pan and zoom
  const pan = useRef(new Animated.ValueXY({ x: 0, y: 0 })).current;
  const scale = useRef(new Animated.Value(1)).current;
  const lastOffset = useRef({ x: 0, y: 0 });
  const lastScale = useRef(1);
  const pinchStartDist = useRef(0);

  const getDistance = (touches: any[]) => {
    const dx = touches[0].pageX - touches[1].pageX;
    const dy = touches[0].pageY - touches[1].pageY;
    return Math.sqrt(dx * dx + dy * dy);
  };

  const panResponder = useRef(
    PanResponder.create({
      onStartShouldSetPanResponder: () => true,
      onMoveShouldSetPanResponder: () => true,
      onPanResponderGrant: (evt: GestureResponderEvent) => {
        lastOffset.current = {
          x: (pan.x as any)._value,
          y: (pan.y as any)._value,
        };
        lastScale.current = (scale as any)._value;
        if (evt.nativeEvent.touches.length === 2) {
          pinchStartDist.current = getDistance(evt.nativeEvent.touches);
        }
      },
      onPanResponderMove: (evt: GestureResponderEvent, gesture: PanResponderGestureState) => {
        if (evt.nativeEvent.touches.length === 2) {
          // Pinch to zoom
          const dist = getDistance(evt.nativeEvent.touches);
          if (pinchStartDist.current > 0) {
            const newScale = Math.max(0.5, Math.min(4, lastScale.current * (dist / pinchStartDist.current)));
            scale.setValue(newScale);
          }
        } else {
          // Single finger pan
          const newX = lastOffset.current.x + gesture.dx;
          const newY = lastOffset.current.y + gesture.dy;

          // Clamp Y so user can't scroll too far vertically
          const currentScale = (scale as any)._value;
          const maxY = imageSize
            ? Math.max(0, (imageSize.h * currentScale - SCREEN_H) / 2)
            : SCREEN_H;
          pan.setValue({ x: newX, y: Math.max(-maxY, Math.min(maxY, newY)) });
        }
      },
      onPanResponderRelease: (_, gesture) => {
        // Add momentum / deceleration on release
        if (Math.abs(gesture.vx) > 0.1 || Math.abs(gesture.vy) > 0.1) {
          Animated.decay(pan, {
            velocity: { x: gesture.vx, y: gesture.vy * 0.5 },
            deceleration: 0.985,
            useNativeDriver: false,
          }).start();
        }
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
      // Get actual image dimensions for proper rendering
      Image.getSize(
        uri,
        (w, h) => {
          setImageSize({ w, h });
          setImageUri(uri);
          setLoading(false);
        },
        (err) => {
          // Fall back to defaults if getSize fails
          setImageSize({ w: 4096, h: 2048 });
          setImageUri(uri);
          setLoading(false);
        }
      );
    } catch (e: any) {
      setError(e.message);
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

  // Scale image so its height fills the screen, preserving full resolution
  const aspectRatio = imageSize ? imageSize.w / imageSize.h : 2;
  const imageHeight = SCREEN_H;
  const imageWidth = imageHeight * aspectRatio;

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
          <Animated.Image
            source={{ uri: imageUri }}
            style={[
              styles.panoramaImage,
              {
                width: imageWidth,
                height: imageHeight,
                transform: [
                  { translateX: pan.x },
                  { translateY: pan.y },
                  { scale: scale },
                ],
              },
            ]}
            resizeMode="cover"
          />
        )}
      </View>

      <View style={styles.hint}>
        <Text style={styles.hintText}>Drag to explore · Pinch to zoom</Text>
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
