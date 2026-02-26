import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { View, Text, TouchableOpacity, StyleSheet, Dimensions } from 'react-native';
import { CameraView, useCameraPermissions } from 'expo-camera';
import { DeviceMotion, DeviceMotionMeasurement } from 'expo-sensors';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import { RootStackParamList } from '../navigation';
import {
  CaptureSegment,
  SEGMENT_COUNT,
  SEGMENT_ANGLE,
  ALIGNMENT_TOLERANCE,
  makeSegments,
} from '../models/types';
import { HapticFeedback } from '../utils/haptics';
import RotationGuide from '../components/RotationGuide';

const { width: SCREEN_WIDTH } = Dimensions.get('window');

type Props = NativeStackScreenProps<RootStackParamList, 'Capture'>;

export default function CaptureScreen({ navigation, route }: Props) {
  const { projectId, projectName } = route.params;
  const [permission, requestPermission] = useCameraPermissions();
  const [segments, setSegments] = useState<CaptureSegment[]>(makeSegments());
  const [currentYaw, setCurrentYaw] = useState(0);
  const [currentSegmentIndex, setCurrentSegmentIndex] = useState(0);
  const [isAligned, setIsAligned] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const cameraRef = useRef<CameraView>(null);
  const initialYawRef = useRef<number | null>(null);
  const segmentsRef = useRef<CaptureSegment[]>(segments);

  useEffect(() => {
    segmentsRef.current = segments;
  }, [segments]);

  const capturedCount = segments.filter((s) => s.imageUri !== null).length;
  const isComplete = capturedCount === SEGMENT_COUNT;

  // Next uncaptured segment (sequential guidance target)
  const nextTargetIndex = useMemo(() => {
    const idx = segments.findIndex((s) => s.imageUri === null);
    return idx >= 0 ? idx : -1;
  }, [segments]);

  // Direction and degrees to next target
  const directionInfo = useMemo(() => {
    if (nextTargetIndex === -1) return null;
    const targetDeg = nextTargetIndex * SEGMENT_ANGLE;
    let delta = targetDeg - currentYaw;
    if (delta > 180) delta -= 360;
    if (delta < -180) delta += 360;
    return {
      direction: delta >= 0 ? ('right' as const) : ('left' as const),
      degrees: Math.round(Math.abs(delta)),
    };
  }, [nextTargetIndex, currentYaw]);

  useEffect(() => {
    DeviceMotion.setUpdateInterval(33); // ~30Hz
    const sub = DeviceMotion.addListener((data: DeviceMotionMeasurement) => {
      if (!data.rotation) return;

      // Convert alpha (yaw) from radians to degrees
      let yaw = (data.rotation.alpha * 180) / Math.PI;

      // Normalize yaw: set first reading as 0°
      if (initialYawRef.current === null) {
        initialYawRef.current = yaw;
      }
      yaw = yaw - initialYawRef.current;
      // Normalize to 0-360
      yaw = ((yaw % 360) + 360) % 360;

      setCurrentYaw(yaw);

      // Determine which segment we're pointing at
      const segIdx = Math.round(yaw / SEGMENT_ANGLE) % SEGMENT_COUNT;
      setCurrentSegmentIndex(segIdx);

      // Check alignment — only align with uncaptured segments
      const targetYaw = segIdx * SEGMENT_ANGLE;
      let diff = Math.abs(yaw - targetYaw);
      if (diff > 180) diff = 360 - diff;
      const alreadyCaptured = segmentsRef.current[segIdx]?.imageUri !== null;
      setIsAligned(diff <= ALIGNMENT_TOLERANCE && !alreadyCaptured);
    });

    return () => {
      sub.remove();
      DeviceMotion.removeAllListeners();
    };
  }, []);

  const handleCapture = useCallback(async () => {
    if (capturing || !cameraRef.current) return;
    setCapturing(true);

    try {
      const photo = await cameraRef.current.takePictureAsync({
        quality: 0.8,
        skipProcessing: true,
      });

      if (photo) {
        HapticFeedback.capture();
        setSegments((prev) => {
          const updated = [...prev];
          updated[currentSegmentIndex] = {
            ...updated[currentSegmentIndex],
            imageUri: photo.uri,
            actualYaw: currentYaw,
            capturedAt: new Date(),
          };
          return updated;
        });
      }
    } catch (e) {
      console.warn('Capture error:', e);
    } finally {
      setCapturing(false);
    }
  }, [capturing, currentSegmentIndex, currentYaw]);

  if (!permission) {
    return (
      <View style={styles.centered}>
        <Text style={styles.permissionText}>Requesting camera permission...</Text>
      </View>
    );
  }

  if (!permission.granted) {
    return (
      <View style={styles.centered}>
        <Text style={styles.permissionText}>Camera access is required</Text>
        <Text style={styles.permissionSubtext}>
          360Flow needs your camera to capture room photos
        </Text>
        <TouchableOpacity style={styles.grantButton} onPress={requestPermission}>
          <Text style={styles.grantButtonText}>Grant Permission</Text>
        </TouchableOpacity>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      {/* Camera — no children to avoid CameraView warning */}
      <CameraView
        ref={cameraRef}
        style={StyleSheet.absoluteFill}
        facing="back"
      />

      {/* UI overlay positioned on top of camera */}
      <View style={styles.overlay} pointerEvents="box-none">
        {/* Top bar */}
        <View style={styles.topBar}>
          <TouchableOpacity onPress={() => navigation.goBack()}>
            <Text style={styles.backText}>← Back</Text>
          </TouchableOpacity>
          <Text style={styles.roomName}>{projectName}</Text>
          <View style={{ width: 60 }} />
        </View>

        {/* Rotation guide overlay */}
        <View style={styles.guideContainer}>
          <RotationGuide
            segments={segments}
            currentYaw={currentYaw}
            currentSegmentIndex={currentSegmentIndex}
            isAligned={isAligned}
            nextTargetIndex={nextTargetIndex}
          />
        </View>

        {/* Bottom controls */}
        <View style={styles.bottomBar}>
          <Text style={styles.countText}>
            {capturedCount} of {SEGMENT_COUNT}
          </Text>

          {/* Directional guidance */}
          {!isComplete && !isAligned && directionInfo && directionInfo.degrees > ALIGNMENT_TOLERANCE && (
            <View style={styles.directionRow}>
              <Text style={styles.directionArrow}>
                {directionInfo.direction === 'right' ? '→' : '←'}
              </Text>
              <Text style={styles.directionLabel}>
                Turn {directionInfo.direction}
              </Text>
              <Text style={styles.directionDegrees}>
                {directionInfo.degrees}°
              </Text>
            </View>
          )}

          {isComplete ? (
            <TouchableOpacity
              style={styles.reviewButton}
              onPress={() =>
                navigation.navigate('Review', {
                  projectId,
                  projectName,
                  segments: segments.map((s) => ({
                    id: s.id,
                    imageUri: s.imageUri,
                  })),
                })
              }
            >
              <Text style={styles.reviewButtonText}>Review Photos →</Text>
            </TouchableOpacity>
          ) : (
            <TouchableOpacity
              style={[
                styles.captureButton,
                isAligned && styles.captureButtonAligned,
                capturing && styles.captureButtonCapturing,
              ]}
              onPress={handleCapture}
              disabled={capturing}
              activeOpacity={0.7}
            >
              <View
                style={[
                  styles.captureButtonInner,
                  capturing && styles.captureButtonInnerCapturing,
                ]}
              />
            </TouchableOpacity>
          )}

          <Text style={[styles.hintText, isAligned && styles.hintTextAligned]}>
            {isComplete
              ? 'All segments captured!'
              : isAligned
              ? 'Aligned — tap to capture'
              : capturedCount === 0
              ? 'Capture your first photo, then follow the arrow'
              : 'Follow the arrow to the next position'}
          </Text>
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#000',
  },
  overlay: {
    ...StyleSheet.absoluteFillObject,
    justifyContent: 'space-between',
  },
  centered: {
    flex: 1,
    backgroundColor: '#1a1a2e',
    justifyContent: 'center',
    alignItems: 'center',
    padding: 40,
  },
  permissionText: {
    fontSize: 18,
    fontWeight: '600',
    color: '#fff',
    marginBottom: 8,
    textAlign: 'center',
  },
  permissionSubtext: {
    fontSize: 14,
    color: '#888',
    textAlign: 'center',
    marginBottom: 24,
  },
  grantButton: {
    backgroundColor: '#6C63FF',
    paddingHorizontal: 24,
    paddingVertical: 14,
    borderRadius: 12,
  },
  grantButtonText: {
    color: '#fff',
    fontWeight: '600',
    fontSize: 16,
  },
  topBar: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingTop: 60,
    paddingHorizontal: 20,
  },
  backText: {
    color: '#fff',
    fontSize: 16,
    fontWeight: '500',
  },
  roomName: {
    color: '#fff',
    fontSize: 16,
    fontWeight: '600',
  },
  guideContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
  },
  bottomBar: {
    alignItems: 'center',
    paddingBottom: 50,
    gap: 12,
  },
  countText: {
    color: '#fff',
    fontSize: 16,
    fontWeight: '600',
  },
  captureButton: {
    width: 76,
    height: 76,
    borderRadius: 38,
    borderWidth: 4,
    borderColor: 'rgba(255,255,255,0.5)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  captureButtonAligned: {
    borderColor: '#4CAF50',
  },
  captureButtonCapturing: {
    opacity: 0.5,
  },
  captureButtonInner: {
    width: 60,
    height: 60,
    borderRadius: 30,
    backgroundColor: '#fff',
  },
  captureButtonInnerCapturing: {
    backgroundColor: '#888',
  },
  reviewButton: {
    backgroundColor: '#6C63FF',
    paddingHorizontal: 32,
    paddingVertical: 16,
    borderRadius: 14,
  },
  reviewButtonText: {
    color: '#fff',
    fontSize: 16,
    fontWeight: '700',
  },
  hintText: {
    color: 'rgba(255,255,255,0.6)',
    fontSize: 13,
  },
  hintTextAligned: {
    color: '#FFD700',
    fontWeight: '600',
  },
  directionRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingVertical: 4,
  },
  directionArrow: {
    fontSize: 28,
    fontWeight: '700',
    color: '#00E5FF',
  },
  directionLabel: {
    fontSize: 16,
    fontWeight: '600',
    color: '#fff',
  },
  directionDegrees: {
    fontSize: 16,
    fontWeight: '700',
    color: '#00E5FF',
  },
});
