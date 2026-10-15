import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  AccessibilityInfo,
  useWindowDimensions,
} from 'react-native';
import { CameraView, useCameraPermissions } from 'expo-camera';
import { DeviceMotion, DeviceMotionMeasurement } from 'expo-sensors';
import Svg, { Circle } from 'react-native-svg';
import { useIsFocused } from '@react-navigation/native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import { RootStackParamList } from '../navigation';
import {
  CaptureSegment,
  SEGMENT_COUNT,
  ULTRA_WIDE_PORTRAIT_HFOV,
  WIDE_PORTRAIT_HFOV,
  makeSegments,
} from '../models/types';
import { HapticFeedback } from '../utils/haptics';
import CaptureMiniMap from '../components/CaptureMiniMap';
import {
  AIM_HOLD_MS,
  AIM_TOLERANCE_DEG,
  Attitude,
  CameraBasis,
  MANUAL_TOLERANCE_DEG,
  Quat,
  STEADY_MAX_DEG_PER_SEC,
  TargetProjection,
  basisFromQuat,
  edgePoint,
  nearestTarget,
  projectTargets,
  quatFromAttitude,
  rotationRateDegPerSec,
  slerpQuat,
  wrap360,
} from '../capture/orientation';

type Props = NativeStackScreenProps<RootStackParamList, 'Capture'>;

type Pose = Attitude & {
  basis: CameraBasis;
  rate: number;
};

type Frame = {
  dots: TargetProjection[];
  captured: boolean[];
  suggested: number;
  alignDeg: number;
  aim: number;
  headingDeg: number;
  tilted: boolean;
  edge: { x: number; y: number; angle: number } | null;
};

const RETICLE = 88;
const RING_R = 34;
const RING = 2 * Math.PI * RING_R;

export default function CaptureScreen({ navigation, route }: Props) {
  const { projectId, projectName } = route.params;
  const focused = useIsFocused();
  const { width, height } = useWindowDimensions();
  const [permission, requestPermission] = useCameraPermissions();
  const [segments, setSegments] = useState<CaptureSegment[]>(makeSegments());
  const [selectedLens, setSelectedLens] = useState<string | undefined>();
  const [hfov, setHfov] = useState(ULTRA_WIDE_PORTRAIT_HFOV);
  const [capturing, setCapturing] = useState(false);
  const [locked, setLocked] = useState(false);
  const [reduceMotion, setReduceMotion] = useState(false);
  const [frame, setFrame] = useState<Frame | null>(null);
  const cameraRef = useRef<CameraView>(null);
  const segmentsRef = useRef(segments);
  const originRef = useRef<number | null>(null);
  const poseRef = useRef<Pose | null>(null);
  const smoothRef = useRef<Quat | null>(null);
  const aimMsRef = useRef(0);
  const captureLock = useRef(false);
  const frameRef = useRef<Frame | null>(null);
  const hfovRef = useRef(hfov);
  const reduceRef = useRef(reduceMotion);
  const sizeRef = useRef({ width, height });
  const focusedRef = useRef(focused);

  useEffect(() => {
    segmentsRef.current = segments;
  }, [segments]);
  useEffect(() => {
    hfovRef.current = hfov;
  }, [hfov]);
  useEffect(() => {
    reduceRef.current = reduceMotion;
  }, [reduceMotion]);
  useEffect(() => {
    sizeRef.current = { width, height };
  }, [width, height]);
  useEffect(() => {
    focusedRef.current = focused;
    if (!focused) aimMsRef.current = 0;
  }, [focused]);

  useEffect(() => {
    let mounted = true;
    AccessibilityInfo.isReduceMotionEnabled().then((enabled) => {
      if (mounted) setReduceMotion(enabled);
    });
    const sub = AccessibilityInfo.addEventListener('reduceMotionChanged', setReduceMotion);
    return () => {
      mounted = false;
      sub.remove();
    };
  }, []);

  const captureAt = useCallback(async (index: number) => {
    if (captureLock.current || !cameraRef.current) return;
    if (index < 0 || index >= SEGMENT_COUNT) return;
    const pose = poseRef.current;
    if (!pose) return;
    captureLock.current = true;
    aimMsRef.current = 0;
    setCapturing(true);
    const yawAtShutter = pose.yawDeg;
    try {
      const photo = await cameraRef.current.takePictureAsync({
        quality: 0.8,
        skipProcessing: false,
      });
      if (!photo?.uri) return;
      if (originRef.current == null) originRef.current = yawAtShutter;
      const actualYaw = wrap360(yawAtShutter - originRef.current);
      setLocked(true);
      setSegments((prev) => {
        const updated = [...prev];
        updated[index] = {
          ...updated[index],
          imageUri: photo.uri,
          actualYaw,
          capturedAt: new Date(),
        };
        return updated;
      });
      HapticFeedback.capture();
    } catch (e) {
      console.warn('Capture error:', e);
    } finally {
      captureLock.current = false;
      setCapturing(false);
    }
  }, []);

  const captureRef = useRef(captureAt);
  captureRef.current = captureAt;

  useEffect(() => {
    if (!permission?.granted) return;
    DeviceMotion.setUpdateInterval(33);
    const sub = DeviceMotion.addListener((data: DeviceMotionMeasurement) => {
      if (!data.rotation) return;
      const yawRad = data.rotation.alpha;
      const pitchRad = data.rotation.beta;
      const rollRad = data.rotation.gamma;
      const sample = quatFromAttitude(yawRad, pitchRad, rollRad);
      const previous = smoothRef.current;
      const smooth = previous
        ? slerpQuat(previous, sample, reduceRef.current ? 0.18 : 0.42)
        : sample;
      smoothRef.current = smooth;
      poseRef.current = {
        yawDeg: (yawRad * 180) / Math.PI,
        pitchRad,
        rollRad,
        basis: basisFromQuat(smooth),
        rate: rotationRateDegPerSec(data.rotationRate),
      };
    });

    let raf = 0;
    let last = 0;
    const tick = (now: number) => {
      raf = requestAnimationFrame(tick);
      const minGap = reduceRef.current ? 100 : 50;
      if (now - last < minGap) return;
      const dt = last === 0 ? minGap : now - last;
      last = now;
      const pose = poseRef.current;
      if (!pose || !focusedRef.current) return;
      const { width: viewW, height: viewH } = sizeRef.current;
      if (viewW < 2 || viewH < 2) return;
      const captured = segmentsRef.current.map((segment) => segment.imageUri !== null);
      const origin = originRef.current;
      let dots: TargetProjection[] = [];
      let suggested = -1;
      let alignDeg = 180;
      let headingDeg = 0;
      if (origin == null) {
        suggested = 0;
        alignDeg = 0;
        headingDeg = 0;
        dots = [
          {
            index: 0,
            yawDeg: pose.yawDeg,
            inView: true,
            x: viewW / 2,
            y: viewH / 2,
            planeX: 0,
            planeY: -1,
            angleDeg: 0,
          },
        ];
      } else {
        dots = projectTargets(pose.basis, origin, viewW, viewH, hfovRef.current);
        headingDeg = wrap360(pose.yawDeg - origin);
        const next = nearestTarget(dots, captured);
        if (next) {
          suggested = next.index;
          alignDeg = next.angleDeg;
        }
      }
      const elevation = Math.asin(Math.min(1, Math.max(-1, pose.basis.look.z))) * (180 / Math.PI);
      const tilted = Math.abs(elevation) > 18;
      const open = suggested >= 0 && !captured[suggested];
      const aimed = open && alignDeg <= AIM_TOLERANCE_DEG && !tilted;
      const steady = pose.rate <= STEADY_MAX_DEG_PER_SEC;
      if (aimed && steady && !captureLock.current) aimMsRef.current += dt;
      else aimMsRef.current = 0;
      const aim = Math.min(1, aimMsRef.current / AIM_HOLD_MS);
      if (aim >= 1 && open) {
        aimMsRef.current = 0;
        void captureRef.current(suggested);
      }
      const suggestedDot = dots.find((dot) => dot.index === suggested);
      let edge: Frame['edge'] = null;
      if (suggestedDot && !suggestedDot.inView) {
        const offX = suggestedDot.x - viewW / 2;
        const offY = suggestedDot.y - viewH / 2;
        const behind = Math.hypot(offX, offY) < 1;
        edge = edgePoint(
          behind ? suggestedDot.planeX : offX,
          behind ? suggestedDot.planeY : offY,
          viewW,
          viewH,
          { top: 150, right: 28, bottom: 210, left: 28 },
        );
      }
      const nextFrame: Frame = {
        dots,
        captured,
        suggested,
        alignDeg,
        aim: reduceRef.current ? (aim > 0 ? 1 : 0) : aim,
        headingDeg,
        tilted,
        edge,
      };
      frameRef.current = nextFrame;
      setFrame(nextFrame);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf);
      sub.remove();
    };
  }, [permission?.granted]);

  const handleManual = useCallback(() => {
    if (originRef.current == null) {
      void captureRef.current(0);
      return;
    }
    const current = frameRef.current;
    if (!current) return;
    let openDot: TargetProjection | null = null;
    let filledDot: TargetProjection | null = null;
    for (const dot of current.dots) {
      if (current.captured[dot.index]) {
        if (!filledDot || dot.angleDeg < filledDot.angleDeg) filledDot = dot;
      } else if (!openDot || dot.angleDeg < openDot.angleDeg) {
        openDot = dot;
      }
    }
    if (openDot && openDot.angleDeg <= MANUAL_TOLERANCE_DEG) {
      void captureRef.current(openDot.index);
      return;
    }
    if (filledDot && filledDot.angleDeg <= AIM_TOLERANCE_DEG) {
      void captureRef.current(filledDot.index);
    }
  }, []);

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

  const capturedCount = segments.filter((segment) => segment.imageUri !== null).length;
  const isComplete = capturedCount === SEGMENT_COUNT;
  const progressPct = (capturedCount / SEGMENT_COUNT) * 100;
  const capturedFlags = frame?.captured ?? segments.map((segment) => segment.imageUri !== null);
  const hint = hintText(isComplete, locked, frame, capturing);

  return (
    <View style={styles.container}>
      <CameraView
        ref={cameraRef}
        style={StyleSheet.absoluteFill}
        facing="back"
        selectedLens={selectedLens}
        onAvailableLensesChanged={handleLenses}
      />

      <View style={styles.overlay} pointerEvents="box-none">
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
          <View style={styles.progressTrack}>
            <View style={[styles.progressFill, { width: `${progressPct}%` }]} />
          </View>
          <Text style={styles.lensText}>
            {selectedLens ? `${selectedLens} · ${hfov}°` : `Ultra-wide · ${hfov}°`}
          </Text>
          {frame?.tilted ? (
            <Text style={styles.tiltWarning}>Hold the phone upright — tilt is too high</Text>
          ) : null}
        </View>

        <View style={StyleSheet.absoluteFill} pointerEvents="none">
          {frame?.dots.map((dot) => {
            if (!dot.inView) return null;
            const done = capturedFlags[dot.index];
            const active = dot.index === frame.suggested && !done;
            const size = active ? 22 : 16;
            return (
              <View
                key={dot.index}
                style={[
                  styles.dot,
                  done ? styles.dotDone : active ? styles.dotNext : styles.dotOpen,
                  { width: size, height: size, borderRadius: size / 2, left: dot.x - size / 2, top: dot.y - size / 2 },
                ]}
              >
                {done ? <Text style={styles.dotCheck}>✓</Text> : null}
              </View>
            );
          })}
          <Svg
            width={RETICLE}
            height={RETICLE}
            style={[styles.reticle, { left: width / 2 - RETICLE / 2, top: height / 2 - RETICLE / 2 }]}
          >
            <Circle cx={RETICLE / 2} cy={RETICLE / 2} r={8} stroke="#fff" strokeWidth={2} fill="none" />
            <Circle cx={RETICLE / 2} cy={RETICLE / 2} r={2} fill="#fff" />
            {frame && frame.aim > 0 && !reduceMotion ? (
              <Circle
                cx={RETICLE / 2}
                cy={RETICLE / 2}
                r={RING_R}
                stroke="#7CFFB2"
                strokeWidth={4}
                fill="none"
                strokeDasharray={`${RING} ${RING}`}
                strokeDashoffset={RING * (1 - frame.aim)}
                strokeLinecap="round"
                transform={`rotate(-90 ${RETICLE / 2} ${RETICLE / 2})`}
              />
            ) : null}
          </Svg>
          {frame?.edge ? (
            <View style={[styles.edgeChip, { left: frame.edge.x - 16, top: frame.edge.y - 16 }]}>
              <Text style={[styles.edgeArrow, { transform: [{ rotate: `${frame.edge.angle}rad` }] }]}>›</Text>
            </View>
          ) : null}
        </View>

        <View style={styles.bottomBar} pointerEvents="box-none">
          <CaptureMiniMap
            captured={capturedFlags}
            headingDeg={frame?.headingDeg ?? 0}
            suggested={frame?.suggested ?? 0}
          />
          {isComplete ? (
            <TouchableOpacity
              style={styles.reviewButton}
              onPress={() =>
                navigation.navigate('Review', {
                  projectId,
                  projectName,
                  hfov,
                  lens: selectedLens,
                  segments: segments.map((segment) => ({
                    id: segment.id,
                    imageUri: segment.imageUri,
                    actualYaw: segment.actualYaw,
                  })),
                })
              }
            >
              <Text style={styles.reviewButtonText}>Review Photos →</Text>
            </TouchableOpacity>
          ) : (
            <TouchableOpacity
              style={[styles.captureButton, capturing && styles.captureButtonCapturing]}
              onPress={handleManual}
              disabled={capturing}
              activeOpacity={0.7}
            >
              <View style={[styles.captureButtonInner, capturing && styles.captureButtonInnerCapturing]} />
            </TouchableOpacity>
          )}
          <Text style={styles.hintText}>{hint}</Text>
        </View>
      </View>
    </View>
  );
}

function hintText(complete: boolean, locked: boolean, frame: Frame | null, capturing: boolean): string {
  if (complete) return 'All segments captured!';
  if (capturing) return 'Capturing…';
  if (!locked) return 'Hold steady to capture your starting view';
  if (frame?.tilted) return 'Level the phone, then aim at a dot';
  if (frame && frame.suggested >= 0 && frame.alignDeg <= AIM_TOLERANCE_DEG) {
    return frame.aim > 0 ? 'Hold steady' : 'Tap to capture, or hold steady';
  }
  if (frame?.edge) return `${Math.round(frame.alignDeg)}° to the next dot`;
  return 'Aim the circle at a dot';
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
  dot: {
    position: 'absolute',
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 2,
  },
  dotOpen: {
    backgroundColor: 'rgba(255,255,255,0.2)',
    borderColor: '#fff',
  },
  dotNext: {
    backgroundColor: 'rgba(0,229,255,0.35)',
    borderColor: '#00E5FF',
  },
  dotDone: {
    backgroundColor: 'rgba(76,175,80,0.85)',
    borderColor: '#4CAF50',
  },
  dotCheck: {
    color: '#fff',
    fontSize: 10,
    fontWeight: '700',
  },
  reticle: {
    position: 'absolute',
  },
  edgeChip: {
    position: 'absolute',
    width: 32,
    height: 32,
    borderRadius: 16,
    backgroundColor: 'rgba(0,0,0,0.55)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  edgeArrow: {
    color: '#00E5FF',
    fontSize: 22,
    fontWeight: '700',
    lineHeight: 24,
  },
  bottomBar: {
    alignItems: 'center',
    paddingBottom: 40,
    gap: 10,
  },
  captureButton: {
    width: 76,
    height: 76,
    borderRadius: 38,
    borderWidth: 4,
    borderColor: 'rgba(255,255,255,0.85)',
    justifyContent: 'center',
    alignItems: 'center',
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
    color: 'rgba(255,255,255,0.85)',
    fontSize: 13,
    fontWeight: '600',
  },
});
