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
  ULTRA_WIDE_PORTRAIT_HFOV,
  WIDE_PORTRAIT_HFOV,
  makeSegments,
} from '../models/types';
import { HapticFeedback } from '../utils/haptics';

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
  const [selectedLens, setSelectedLens] = useState<string | undefined>();
  const [hfov, setHfov] = useState(ULTRA_WIDE_PORTRAIT_HFOV);
  const [tilted, setTilted] = useState(false);
  const cameraRef = useRef<CameraView>(null);
  const initialYawRef = useRef<number | null>(null);
  const segmentsRef = useRef<CaptureSegment[]>(segments);

  useEffect(() => {
    segmentsRef.current = segments;
  }, [segments]);

  const capturedCount = segments.filter((s) => s.imageUri !== null).length;
  const isComplete = capturedCount === SEGMENT_COUNT;
  const progressPct = (capturedCount / SEGMENT_COUNT) * 100;

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
      const gravity = data.accelerationIncludingGravity;
      if (gravity) {
        const tilt = Math.atan2(Math.hypot(gravity.x, gravity.z), Math.abs(gravity.y));
        setTilted(tilt > (20 * Math.PI) / 180);
      }
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
        skipProcessing: false,
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

  const handleLenses = useCallback((event: { lenses: string[] }) => {
    const available = event.lenses ?? [];
    const ultra = available.find((name) => /ultra/i.test(name));
    if (ultra) {
      setSelectedLens(ultra);
      setHfov(ULTRA_WIDE_PORTRAIT_HFOV);
      return;
    }
    const wide = available.find((name) => /wide/i.test(name));
    if (wide) {
      setSelectedLens(wide);
      setHfov(WIDE_PORTRAIT_HFOV);
      return;
    }
    setHfov(ULTRA_WIDE_PORTRAIT_HFOV);
  }, []);

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

  const showDirectionOverlay =
    !isComplete &&
    !isAligned &&
    directionInfo &&
    directionInfo.degrees > ALIGNMENT_TOLERANCE;

  return (
    <View style={styles.container}>
      {/* Camera — no children to avoid CameraView warning */}
      <CameraView
        ref={cameraRef}
        style={StyleSheet.absoluteFill}
        facing="back"
        selectedLens={selectedLens}
        onAvailableLensesChanged={handleLenses}
      />

      {/* UI overlay positioned on top of camera */}
      <View style={styles.overlay} pointerEvents="box-none">
        {/* Top section: nav bar + progress */}
        <View>
          <View style={styles.topBar}>
            <TouchableOpacity onPress={() => navigation.goBack()}>
              <Text style={styles.backText}>← Back</Text>
            </TouchableOpacity>
            <Text style={styles.roomName}>{projectName}</Text>
            <Text style={styles.countText}>
              {capturedCount}/{SEGMENT_COUNT}
            </Text>
          </View>

          {/* Progress bar */}
          <View style={styles.progressTrack}>
            <View style={[styles.progressFill, { width: `${progressPct}%` }]} />
          </View>
          <Text style={styles.lensText}>
            {selectedLens ? `${selectedLens} · ${hfov}°` : `Ultra-wide · ${hfov}°`}
          </Text>
          {tilted ? (
            <Text style={styles.tiltWarning}>Hold the phone upright — tilt is too high</Text>
          ) : null}
        </View>

        {/* Center: directional arrow (only when not aligned) */}
        {showDirectionOverlay ? (
          <View style={styles.directionOverlay} pointerEvents="none">
            <Text style={styles.directionArrowLarge}>
              {directionInfo.direction === 'right' ? '→' : '←'}
            </Text>
            <Text style={styles.directionDegreesLarge}>
              {directionInfo.degrees}°
            </Text>
          </View>
        ) : (
          <View />
        )}

        {/* Bottom controls */}
        <View style={styles.bottomBar}>
          {isComplete ? (
            <TouchableOpacity
              style={styles.reviewButton}
              onPress={() =>
                navigation.navigate('Review', {
                  projectId,
                  projectName,
                  hfov,
                  lens: selectedLens,
                  segments: segments.map((s) => ({
                    id: s.id,
                    imageUri: s.imageUri,
                    actualYaw: s.actualYaw,
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
              ? 'Point at your starting position and capture'
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
    ...StyleSheet.absoluteFill,
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
    paddingBottom: 12,
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
  countText: {
    color: 'rgba(255,255,255,0.8)',
    fontSize: 15,
    fontWeight: '600',
  },
  progressTrack: {
    height: 4,
    backgroundColor: 'rgba(255,255,255,0.15)',
    marginHorizontal: 20,
    borderRadius: 2,
    overflow: 'hidden',
  },
  progressFill: {
    height: '100%',
    backgroundColor: '#6C63FF',
    borderRadius: 2,
  },
  lensText: {
    color: 'rgba(255,255,255,0.75)',
    fontSize: 12,
    textAlign: 'center',
    marginTop: 8,
  },
  tiltWarning: {
    color: '#FFD54F',
    fontSize: 13,
    fontWeight: '600',
    textAlign: 'center',
    marginTop: 6,
    paddingHorizontal: 20,
  },
  directionOverlay: {
    alignItems: 'center',
    justifyContent: 'center',
  },
  directionArrowLarge: {
    fontSize: 72,
    fontWeight: '700',
    color: 'rgba(255, 255, 255, 0.75)',
  },
  directionDegreesLarge: {
    fontSize: 28,
    fontWeight: '600',
    color: 'rgba(255, 255, 255, 0.6)',
    marginTop: -8,
  },
  bottomBar: {
    alignItems: 'center',
    paddingBottom: 50,
    gap: 12,
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
});
